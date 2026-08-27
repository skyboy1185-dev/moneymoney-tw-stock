from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, date, datetime, timedelta
import logging
from typing import Any

from sqlalchemy import func, select, text

from ..config import get_settings
from ..database import BackgroundSessionLocal as SessionLocal
from ..models import DayTradingPosition
from .automated_position_tracker import (
    AUTOMATION_USER_IDS,
    ensure_positions_for_official_recommendations,
    finalize_automatic_position_event,
    pending_automatic_position_events,
    record_official_recommendations,
)
from .day_trading import day_trading_engine
from .chip_flow_alerts import (
    electronic_chip_flow_alert_monitor,
    enrich_day_trading_large_order_confirmation,
)
from .chip_flow_repository import ChipFlowRepository
from .day_trading_cache import day_trading_cache
from .day_trading_candidate_snapshots import save_candidate_snapshots
from .day_trading_restrictions import day_trading_restrictions
from .day_trading_strategies import (
    route_signals_to_active_robot,
    strategy_context,
    strategy_eligible_signals,
)
from .day_trading_schedule import (
    TradingScheduleConfig,
    recommendation_qualification,
    stable_recommendation_selector,
    trading_session_state,
)
from .line_messaging import line_notification_dispatcher
from .official_market_data import StockQuoteRequest, official_market_data_provider
from .three_gate_price import official_three_gate_price_provider


logger = logging.getLogger(__name__)
QUOTE_HISTORY_CACHE_KEY = "day-trading-official-quote-history"
BASELINE_QUOTE_REFRESH_SECONDS = 30
PRIORITY_QUOTE_REFRESH_SECONDS = 5
ACTIVE_QUOTE_PHASES = frozenset({
    "loading", "health_check", "warmup", "scanning", "long_only", "entry_closed", "closing",
})


class DayTradingAutomationSupervisor:
    """Keeps the trading clock alive even when no browser is connected."""

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._started_at: datetime | None = None
        self._last_scan_at: datetime | None = None
        self._last_baseline_quote_refresh_at: datetime | None = None
        self._last_priority_quote_refresh_at: datetime | None = None
        self._last_quote_snapshot_at: datetime | None = None
        self._recommendations: list[dict[str, Any]] = []
        self._restored_signal_count = 0
        self._restored_quote_samples = 0
        self._last_candidate_snapshot_count = 0
        self._last_phase: str | None = None
        self._last_data_status: str | None = None
        self._trading_date: str | None = None
        self._today_signal_ids: set[str] = set()
        self._quote_coverage_count = 0
        self._warmed_symbol_count = 0
        self._universe_signature: tuple[str, ...] = ()
        self._state: dict[str, Any] = {"status": "stopped"}

    def _config(self) -> TradingScheduleConfig:
        app_settings = get_settings()
        holidays: set[date] = set()
        for raw in app_settings.twse_holidays.split(","):
            try:
                holidays.add(date.fromisoformat(raw.strip()))
            except ValueError:
                continue
        return TradingScheduleConfig(timezone=app_settings.twse_timezone, holidays=frozenset(holidays))

    @staticmethod
    def _confirm_continuous_large_orders(
        candidates: list[dict[str, Any]],
        now: datetime,
    ) -> list[dict[str, Any]]:
        try:
            with SessionLocal() as db:
                return enrich_day_trading_large_order_confirmation(
                    candidates,
                    ChipFlowRepository(db),
                    electronic_chip_flow_alert_monitor.rules,
                    as_of=now,
                )
        except Exception:
            logger.exception("Failed to enrich day-trading large-order confirmation")
            return candidates

    async def _send_recommendations_and_track(
        self,
        recommendations: list[dict[str, Any]],
        config: TradingScheduleConfig,
        session: dict[str, Any],
        now: datetime,
    ) -> int:
        recommendations = day_trading_restrictions.filter_candidates(recommendations)
        recommendations = [
            signal
            for signal in recommendations
            if signal.get("isOfficialRecommendation")
            and recommendation_qualification(signal, config, session, now)[0]
        ]
        if not recommendations:
            return 0
        try:
            with SessionLocal() as db:
                created = ensure_positions_for_official_recommendations(
                    db,
                    recommendations,
                    config=config,
                    session=session,
                    now=now,
                )
                record_official_recommendations(
                    db,
                    recommendations,
                    config=config,
                    session=session,
                    now=now,
                )
                db.commit()
            if created:
                logger.info(
                    "Created %s automatic day-trading position(s): %s",
                    len(created),
                    ", ".join(position.symbol for position in created),
                )
        except Exception:
            logger.exception("Failed to persist automatic day-trading positions")
        try:
            return await line_notification_dispatcher.send_recommendations(recommendations)
        except Exception:
            logger.exception("Automatic recommendation LINE notification failed")
            return 0

    async def _monitor_automatic_positions(
        self,
        *,
        data_status: str,
        phase: str,
    ) -> tuple[int, int]:
        force_close = phase in {"closing", "summary"}
        try:
            with SessionLocal() as db:
                events = pending_automatic_position_events(
                    db,
                    day_trading_engine.quote_for,
                    data_status=data_status,
                    force_close=force_close,
                    risk_for=day_trading_engine.position_risk_for,
                )
                db.commit()
        except Exception:
            logger.exception("Automatic day-trading position evaluation failed")
            return 0, 0
        sent = 0
        for event in events:
            outbound = {
                key: value
                for key, value in event.items()
                if not key.startswith("_")
            }
            try:
                with SessionLocal() as db:
                    finalize_automatic_position_event(db, event)
                    db.commit()
            except Exception:
                logger.exception(
                    "Failed to finalize automatic position event for position %s",
                    event.get("_positionId"),
                )
            try:
                sent += await line_notification_dispatcher.send_position_event(outbound)
            except Exception:
                logger.exception(
                    "Automatic position LINE notification failed for %s",
                    outbound.get("position", {}).get("symbol"),
                )
        return len(events), sent

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._started_at = datetime.now(UTC)
        await day_trading_restrictions.refresh(self._started_at, force=True)
        self._restored_quote_samples = day_trading_engine.restore_official_quote_history(
            day_trading_cache.get(QUOTE_HISTORY_CACHE_KEY),
            self._started_at,
        )
        self._quote_coverage_count = day_trading_engine.quote_coverage_count
        self._warmed_symbol_count = day_trading_engine.warmed_symbol_count
        restored = day_trading_cache.get("automation-recommendations")
        if isinstance(restored, list):
            config = self._config()
            regime = day_trading_engine.market_regime()
            session = trading_session_state(
                config,
                self._started_at,
                data_status=regime["dataStatus"],
                quote_samples=day_trading_engine.sample_count,
                infrastructure_ok=day_trading_cache.ready_for_formal_signals,
            )
            self._recommendations = [
                signal
                for signal in day_trading_restrictions.filter_candidates(restored)
                if signal.get("isOfficialRecommendation")
                and recommendation_qualification(signal, config, session, self._started_at)[0]
            ]
            self._restored_signal_count = len(self._recommendations)
        self._task = asyncio.create_task(self._run(), name="day-trading-automation")

    async def stop(self) -> None:
        if not self._task:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        while True:
            now = datetime.now(UTC)
            config = self._config()
            momentum_universe = electronic_chip_flow_alert_monitor.stock_universe_snapshot()
            day_trading_engine.set_stock_universe(momentum_universe)
            universe_signature = day_trading_engine.stock_universe_symbols
            if universe_signature != self._universe_signature:
                self._universe_signature = universe_signature
                self._quote_coverage_count = day_trading_engine.quote_coverage_count
                self._warmed_symbol_count = day_trading_engine.warmed_symbol_count
            await day_trading_restrictions.refresh(now)
            database_ok = False
            open_positions = 0
            automatic_open_positions = 0
            open_position_symbols: set[str] = set()
            try:
                with SessionLocal() as db:
                    db.execute(text("SELECT 1"))
                    open_positions = int(db.scalar(select(func.count()).select_from(
                        DayTradingPosition,
                    ).where(DayTradingPosition.status == "open")) or 0)
                    automatic_open_positions = int(db.scalar(
                        select(func.count())
                        .select_from(DayTradingPosition)
                        .where(
                            DayTradingPosition.status == "open",
                            DayTradingPosition.user_id.in_(AUTOMATION_USER_IDS),
                        )
                    ) or 0)
                    open_position_symbols = {
                        str(symbol) for symbol in db.scalars(
                            select(DayTradingPosition.symbol).where(
                                DayTradingPosition.status == "open",
                            )
                        ).all()
                        if symbol
                    }
                    database_ok = True
            except Exception:
                database_ok = False

            electronic_chip_flow_alert_monitor.set_day_trading_priority_symbols({
                *(str(item.get("symbol")) for item in self._recommendations),
                *open_position_symbols,
            })
            priority_symbols = set(
                electronic_chip_flow_alert_monitor.high_frequency_symbols_snapshot()
            )
            clock_session = trading_session_state(
                config,
                now,
                data_status="normal",
                quote_samples=day_trading_engine.sample_count,
                infrastructure_ok=True,
            )
            quote_monitoring_active = str(clock_session["phase"]) in ACTIVE_QUOTE_PHASES
            baseline_quote_due = quote_monitoring_active and (
                self._last_baseline_quote_refresh_at is None
                or now - self._last_baseline_quote_refresh_at
                >= timedelta(seconds=BASELINE_QUOTE_REFRESH_SECONDS)
            )
            priority_quote_due = quote_monitoring_active and (
                self._last_priority_quote_refresh_at is None
                or now - self._last_priority_quote_refresh_at
                >= timedelta(seconds=max(
                    PRIORITY_QUOTE_REFRESH_SECONDS,
                    get_settings().quote_refresh_seconds,
                ))
            )
            quote_refresh_due = baseline_quote_due or priority_quote_due
            if quote_refresh_due:
                try:
                    selected_stocks = (
                        momentum_universe
                        if baseline_quote_due
                        else tuple(
                            stock for stock in momentum_universe
                            if stock.symbol in priority_symbols
                        )
                    )
                    quote_requests = [
                        StockQuoteRequest(
                            symbol=stock.symbol,
                            name=stock.name,
                            market=stock.market,
                        )
                        for stock in selected_stocks
                        if not day_trading_restrictions.is_disposed(stock.symbol)
                        and day_trading_restrictions.market_restrictions_available(stock.market)
                    ]
                    quote_requests.append(StockQuoteRequest(
                        symbol="t00",
                        name="加權指數",
                        market="上市",
                    ))
                    quotes = await official_market_data_provider.get_quotes(
                        quote_requests,
                        force_refresh=True,
                    )
                    day_trading_engine.update_official_quotes(quotes)
                    self._quote_coverage_count = day_trading_engine.quote_coverage_count
                    if baseline_quote_due:
                        self._warmed_symbol_count = day_trading_engine.warmed_symbol_count
                        try:
                            three_gate_prices = await official_three_gate_price_provider.get_levels(
                                tuple(stock.symbol for stock in selected_stocks)
                            )
                            day_trading_engine.update_three_gate_prices(three_gate_prices)
                        except Exception:
                            logger.exception("Official three-gate price refresh failed")
                    snapshot_due = (
                        self._last_quote_snapshot_at is None
                        or now - self._last_quote_snapshot_at >= timedelta(minutes=1)
                    )
                    if quotes and snapshot_due:
                        day_trading_cache.put(
                            QUOTE_HISTORY_CACHE_KEY,
                            day_trading_engine.export_official_quote_history(now),
                            ttl=28_800,
                        )
                        self._last_quote_snapshot_at = now
                except Exception:
                    logger.exception("TWSE MIS quote refresh failed")
                if baseline_quote_due:
                    self._last_baseline_quote_refresh_at = now
                    self._last_priority_quote_refresh_at = now
                elif priority_quote_due:
                    self._last_priority_quote_refresh_at = now

            regime = day_trading_engine.market_regime()
            recovering = day_trading_engine.sample_count < config.minimum_live_samples
            session = trading_session_state(
                config,
                now,
                data_status=regime["dataStatus"],
                quote_samples=day_trading_engine.sample_count,
                infrastructure_ok=database_ok and day_trading_cache.ready_for_formal_signals,
                recovering=recovering,
            )
            strategy = strategy_context(regime, session)
            trading_date = str(session["tradingDate"])
            if self._trading_date != trading_date:
                self._trading_date = trading_date
                self._today_signal_ids.clear()
            scan_due = (
                self._last_scan_at is None
                or now - self._last_scan_at >= timedelta(seconds=config.recommendation_refresh_seconds)
            )
            if scan_due and session["phase"] in {"warmup", "scanning", "long_only"}:
                candidates = self._confirm_continuous_large_orders(
                    day_trading_restrictions.enrich_short_eligibility(
                        day_trading_restrictions.filter_candidates(
                            day_trading_engine.signals(),
                        ),
                    ),
                    now,
                )
                candidates = strategy_eligible_signals(route_signals_to_active_robot(
                    candidates,
                    strategy["activeRobot"],
                ))
                session = trading_session_state(
                    config,
                    now,
                    data_status=regime["dataStatus"],
                    quote_samples=day_trading_engine.sample_count,
                    infrastructure_ok=database_ok and day_trading_cache.ready_for_formal_signals,
                    recovering=False,
                )
                strategy = strategy_context(regime, session)
                self._recommendations, _ranked_candidates = stable_recommendation_selector.select(
                    "system-automation",
                    candidates,
                    config,
                    session,
                    now=now,
                )
                self._last_candidate_snapshot_count = 0
                if _ranked_candidates:
                    try:
                        with SessionLocal() as db:
                            self._last_candidate_snapshot_count = save_candidate_snapshots(
                                db,
                                _ranked_candidates,
                                config=config,
                                snapshot_at=now,
                            )
                            db.commit()
                    except Exception:
                        logger.exception("Failed to persist day-trading candidate snapshots")
                self._last_scan_at = now
                day_trading_cache.put("automation-recommendations", self._recommendations, ttl=86_400)
                if session["formalSignalsAllowed"]:
                    self._today_signal_ids.update(str(item["id"]) for item in self._recommendations)
            elif session["phase"] not in {"warmup", "scanning", "long_only"} or not session["formalSignalsAllowed"]:
                self._recommendations = []
            self._state = {
                "status": "running",
                "startedAt": self._started_at.isoformat() if self._started_at else None,
                "checkedAt": now.isoformat(),
                "session": session,
                "database": "healthy" if database_ok else "unavailable",
                "redis": day_trading_cache.status,
                "cacheMode": day_trading_cache.mode,
                "cacheReadyForFormalSignals": day_trading_cache.ready_for_formal_signals,
                "restoredOpenPositions": open_positions,
                "automaticOpenPositions": automatic_open_positions,
                "restoredSignalCount": self._restored_signal_count,
                "restoredQuoteSamples": self._restored_quote_samples,
                "recommendedCount": len(self._recommendations),
                "candidateSnapshotCount": self._last_candidate_snapshot_count,
                "candidateUniverseCount": len(day_trading_engine.stock_universe_symbols),
                "candidateUniverseSource": "large-order-momentum-radar",
                "quoteCoverageCount": self._quote_coverage_count,
                "threeGateCoverageCount": day_trading_engine.three_gate_coverage_count,
                "warmedSymbolCount": self._warmed_symbol_count,
                "highFrequencyTrackingCount": len(priority_symbols),
                "baselineQuoteRefreshSeconds": BASELINE_QUOTE_REFRESH_SECONDS,
                "priorityQuoteRefreshSeconds": PRIORITY_QUOTE_REFRESH_SECONDS,
                "disposalRestrictions": day_trading_restrictions.state,
                "activeRobot": strategy["activeRobot"],
            }
            day_trading_cache.put("automation-supervisor", self._state, ttl=180)
            line_tasks: list[Any] = []
            phase = str(session["phase"])
            data_status = str(regime["dataStatus"])
            if (
                phase in ACTIVE_QUOTE_PHASES
                and data_status != self._last_data_status
                and data_status in {"severe_delay", "disconnected", "source_error"}
            ):
                disconnected = data_status in {"disconnected", "source_error"}
                line_tasks.append(line_notification_dispatcher.send_system_event(
                    "data_alert",
                    "行情來源中斷" if disconnected else "行情資料延遲",
                    "目前停止產生新交易訊號；既有持倉仍持續檢查出場與停損。",
                    f"system:{trading_date}:data:{data_status}",
                    priority=2,
                ))
            if phase == "summary" and self._last_phase != "summary":
                line_tasks.append(line_notification_dispatcher.send_system_event(
                    "closing_summary",
                    "每日收盤摘要",
                    f"今日 AI 正式推薦 {len(self._today_signal_ids)} 檔；系統已停止產生當日新訊號。",
                    f"system:{trading_date}:summary",
                    priority=3,
                ))
            if session["formalSignalsAllowed"] and self._recommendations:
                line_tasks.append(
                    self._send_recommendations_and_track(
                        self._recommendations[:config.maximum_recommendations],
                        config,
                        session,
                        now,
                    ),
                )
            if quote_refresh_due:
                evaluated, exits_sent = await self._monitor_automatic_positions(
                    data_status=data_status,
                    phase=phase,
                )
                self._state["automaticPositionEvents"] = evaluated
                self._state["automaticExitMessagesSent"] = exits_sent
            if line_tasks:
                await asyncio.gather(*line_tasks, return_exceptions=True)
            self._last_phase = phase
            self._last_data_status = data_status
            await asyncio.sleep(1)

    @property
    def state(self) -> dict[str, Any]:
        return self._state


day_trading_automation = DayTradingAutomationSupervisor()
