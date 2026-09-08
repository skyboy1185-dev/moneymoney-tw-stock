from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
import logging
from typing import Any, Iterable

from ..config import get_settings
from ..database import BackgroundSessionLocal as SessionLocal
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
from .official_market_data import StockQuoteRequest
from .day_trading_quote_pump import day_trading_quote_pump
from .day_trading_quote_targets import QuoteTargetInputs, QuoteTargetSelector, read_quote_target_inputs
from .day_trading_source_events import observe_quote_source
from .three_gate_price import official_three_gate_price_provider


logger = logging.getLogger(__name__)
QUOTE_HISTORY_CACHE_KEY = "day-trading-official-quote-history"
AUTOMATION_SELECTION_CACHE_KEY = "automation-selection"
AUTOMATION_RANKED_CANDIDATES_CACHE_KEY = "automation-ranked-candidates"
BASELINE_QUOTE_REFRESH_SECONDS = 30
PRIORITY_QUOTE_REFRESH_SECONDS = 5
BASELINE_QUOTE_BATCH_SIZE = 80
ACTIVE_QUOTE_PHASES = frozenset({
    "loading", "health_check", "warmup", "scanning", "long_only", "entry_closed", "closing",
})


def _dedupe_stocks_by_symbol(stocks: Iterable[Any]) -> tuple[Any, ...]:
    deduped: dict[str, Any] = {}
    for stock in stocks:
        symbol = str(getattr(stock, "symbol", "") or "")
        if symbol and symbol not in deduped:
            deduped[symbol] = stock
    return tuple(deduped.values())


def _quote_requests_for_stocks(stocks: Iterable[Any]) -> list[StockQuoteRequest]:
    return [
        StockQuoteRequest(
            symbol=str(stock.symbol),
            name=str(stock.name),
            market=str(stock.market),
        )
        for stock in _dedupe_stocks_by_symbol(stocks)
        if not day_trading_restrictions.is_disposed(str(stock.symbol))
        and day_trading_restrictions.market_restrictions_available(str(stock.market))
    ]


class DayTradingAutomationSupervisor:
    """Keeps the trading clock alive even when no browser is connected."""

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._quote_target_task: asyncio.Task[None] | None = None
        self._quote_target_selector = QuoteTargetSelector()
        self._quote_inputs = QuoteTargetInputs(frozenset(), ())
        self._quote_target_state: dict[str, Any] = {"targetRefreshError": None}
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
        self._baseline_quote_cursor = 0
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

    def _baseline_quote_slice(self, stocks: tuple[Any, ...]) -> tuple[Any, ...]:
        if len(stocks) <= BASELINE_QUOTE_BATCH_SIZE:
            self._baseline_quote_cursor = 0
            return stocks
        start = self._baseline_quote_cursor % len(stocks)
        end = start + BASELINE_QUOTE_BATCH_SIZE
        selected = (
            stocks[start:end]
            if end <= len(stocks)
            else (*stocks[start:], *stocks[:end - len(stocks)])
        )
        self._baseline_quote_cursor = end % len(stocks)
        return tuple(selected)

    @staticmethod
    def _read_quote_inputs(now: datetime) -> QuoteTargetInputs:
        with SessionLocal() as db:
            return read_quote_target_inputs(db, now, AUTOMATION_USER_IDS)

    async def _refresh_quote_targets(self, now: datetime) -> None:
        # Database and pool discovery do not share the heavy signal/notification loop.
        universe = _dedupe_stocks_by_symbol(electronic_chip_flow_alert_monitor.stock_universe_snapshot())
        try:
            self._quote_inputs = await asyncio.to_thread(self._read_quote_inputs, now)
            self._quote_target_state["targetRefreshError"] = None
            self._quote_target_state["targetDatabaseLastSuccessAt"] = now.isoformat()
        except Exception as exc:
            logger.exception("Quote priority target refresh failed; retaining previous held subscriptions")
            self._quote_target_state["targetRefreshError"] = str(exc)
        electronic_chip_flow_alert_monitor.set_day_trading_priority_symbols({
            *(str(item.get("symbol")) for item in self._recommendations),
            *self._quote_inputs.held_symbols,
        })
        targets = self._quote_target_selector.select(
            universe, self._quote_inputs,
            electronic_chip_flow_alert_monitor.high_frequency_symbols_snapshot(now),
        )
        # Retain history before changing the candidate universe, including unresolved holdings.
        day_trading_engine.set_quote_tracking_symbols(targets.tracking_symbols)
        day_trading_engine.set_stock_universe(universe)
        clock = trading_session_state(
            self._config(), now, data_status="normal",
            quote_samples=day_trading_engine.sample_count, infrastructure_ok=True,
        )
        # Keep overflow demand visible to the pump, whose diagnostics are also
        # consumed directly by V2. The pump applies the final capacity limit.
        baseline_requests = {request.symbol: request for request in targets.baseline}
        requested_priority = (
            *targets.priority,
            *(baseline_requests[symbol] for symbol in targets.overflow_symbols if symbol in baseline_requests),
        )
        day_trading_quote_pump.update_targets(
            requested_priority, targets.baseline,
            mandatory_symbols=targets.mandatory_symbols,
            enabled=str(clock["phase"]) in ACTIVE_QUOTE_PHASES,
        )
        self._quote_target_state.update({
            "targetRefreshedAt": now.isoformat(),
            "heldCount": len(self._quote_inputs.held_symbols),
            "candidateOverflowCount": targets.candidate_overflow_count,
            "overCapacity": bool(targets.overflow_symbols) or bool(day_trading_quote_pump.state.get("overCapacity")),
            "priorityOverflowSymbols": list(targets.overflow_symbols),
            "unresolvedSymbols": list(targets.unresolved_symbols),
            "unresolvedReason": "無已知交易所資料，保留追蹤但不猜測上市或上櫃" if targets.unresolved_symbols else None,
        })
        diagnostics = {**day_trading_quote_pump.diagnostics(), **self._quote_target_state}
        await asyncio.to_thread(
            day_trading_cache.put, "day-trading-quote-pump",
            diagnostics, ttl=600,
        )
        try:
            await asyncio.to_thread(observe_quote_source, diagnostics, now)
        except Exception:
            logger.exception("Quote source transition observation failed")

    async def _run_quote_targets(self) -> None:
        while True:
            started = asyncio.get_running_loop().time()
            try:
                await self._refresh_quote_targets(datetime.now(UTC))
            except Exception:
                logger.exception("Quote target scheduler failed")
            await asyncio.sleep(max(0.1, PRIORITY_QUOTE_REFRESH_SECONDS - (asyncio.get_running_loop().time() - started)))

    def _scan_candidates(self, now: datetime) -> list[dict[str, Any]]:
        return self._confirm_continuous_large_orders(
            day_trading_restrictions.enrich_short_eligibility(
                day_trading_restrictions.filter_candidates(day_trading_engine.signals()),
            ), now,
        )

    @staticmethod
    def _persist_candidate_snapshots(candidates: list[dict[str, Any]], config: TradingScheduleConfig, now: datetime) -> int:
        with SessionLocal() as db:
            count = save_candidate_snapshots(db, candidates, config=config, snapshot_at=now)
            db.commit()
            return count

    @staticmethod
    def _persist_recommendations(recommendations: list[dict[str, Any]], config: TradingScheduleConfig, session: dict[str, Any], now: datetime) -> list[str]:
        with SessionLocal() as db:
            created = ensure_positions_for_official_recommendations(db, recommendations, config=config, session=session, now=now)
            record_official_recommendations(db, recommendations, config=config, session=session, now=now)
            db.commit()
            return [position.symbol for position in created]

    @staticmethod
    def _evaluate_position_events(data_status: str, force_close: bool) -> list[dict[str, Any]]:
        with SessionLocal() as db:
            events = pending_automatic_position_events(
                db, day_trading_engine.quote_for, data_status=data_status, force_close=force_close,
                risk_for=day_trading_engine.position_risk_for,
            )
            db.commit()
            return events

    @staticmethod
    def _finalize_position_event(event: dict[str, Any]) -> None:
        with SessionLocal() as db:
            finalize_automatic_position_event(db, event)
            db.commit()

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
            created = await asyncio.to_thread(self._persist_recommendations, recommendations, config, session, now)
            if created:
                logger.info(
                    "Created %s automatic day-trading position(s): %s",
                    len(created),
                    ", ".join(created),
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
            events = await asyncio.to_thread(self._evaluate_position_events, data_status, force_close)
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
                await asyncio.to_thread(self._finalize_position_event, event)
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
        if self._task or self._quote_target_task:
            await self.stop()
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
                data_quality_mode=str(regime.get("dataQualityMode") or "live"),
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
        await day_trading_quote_pump.start()
        self._quote_target_task = asyncio.create_task(self._run_quote_targets(), name="day-trading-quote-targets")
        self._task = asyncio.create_task(self._run(), name="day-trading-automation")

    async def stop(self) -> None:
        tasks = [task for task in (self._quote_target_task, self._task) if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._quote_target_task = self._task = None
        await day_trading_quote_pump.stop()

    async def _run(self) -> None:
        while True:
            now = datetime.now(UTC)
            config = self._config()
            momentum_universe = _dedupe_stocks_by_symbol(
                electronic_chip_flow_alert_monitor.stock_universe_snapshot()
            )
            await day_trading_restrictions.refresh(now)
            database_ok = self._quote_target_state.get("targetRefreshedAt") is not None and not self._quote_target_state.get("targetRefreshError")
            open_positions = self._quote_inputs.legacy_open_count
            automatic_open_positions = self._quote_inputs.automatic_open_count
            pump_state = day_trading_quote_pump.state
            clock_session = trading_session_state(
                config, now, data_status="normal",
                quote_samples=day_trading_engine.sample_count, infrastructure_ok=True,
            )
            quote_monitoring_active = str(clock_session["phase"]) in ACTIVE_QUOTE_PHASES
            quote_refresh_due = quote_monitoring_active and (
                self._last_priority_quote_refresh_at is None
                or now - self._last_priority_quote_refresh_at >= timedelta(seconds=PRIORITY_QUOTE_REFRESH_SECONDS)
            )
            if quote_refresh_due:
                self._last_priority_quote_refresh_at = now
            baseline_quote_due = quote_monitoring_active and (
                self._last_baseline_quote_refresh_at is None
                or now - self._last_baseline_quote_refresh_at >= timedelta(seconds=BASELINE_QUOTE_REFRESH_SECONDS)
            )
            # Slow three-gate enrichment is intentionally outside the independent quote pump.
            if baseline_quote_due:
                self._last_baseline_quote_refresh_at = now
                selected_stocks = self._baseline_quote_slice(momentum_universe)
                try:
                    levels = await official_three_gate_price_provider.get_levels(tuple(stock.symbol for stock in selected_stocks))
                    day_trading_engine.update_three_gate_prices(levels)
                except Exception:
                    logger.exception("Official three-gate price refresh failed")
            self._quote_coverage_count = day_trading_engine.quote_coverage_count
            self._warmed_symbol_count = day_trading_engine.warmed_symbol_count
            if pump_state.get("lastSuccessAt") and (
                self._last_quote_snapshot_at is None or now - self._last_quote_snapshot_at >= timedelta(minutes=1)
            ):
                await asyncio.to_thread(
                    day_trading_cache.put, QUOTE_HISTORY_CACHE_KEY,
                    day_trading_engine.export_official_quote_history(now), ttl=28_800,
                )
                self._last_quote_snapshot_at = now

            regime = day_trading_engine.market_regime()
            recovering = day_trading_engine.sample_count < config.minimum_live_samples
            session = trading_session_state(
                config,
                now,
                data_status=regime["dataStatus"],
                data_quality_mode=str(regime.get("dataQualityMode") or "live"),
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
                candidates = await asyncio.to_thread(self._scan_candidates, now)
                candidates = strategy_eligible_signals(route_signals_to_active_robot(
                    candidates,
                    strategy["activeRobot"],
                ))
                session = trading_session_state(
                    config,
                    now,
                    data_status=regime["dataStatus"],
                    data_quality_mode=str(regime.get("dataQualityMode") or "live"),
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
                selection_cache = {
                    "recommended": self._recommendations,
                    "candidates": _ranked_candidates,
                    "totalRecommended": len(self._recommendations),
                    "maximumRecommendations": config.maximum_recommendations,
                    "summary": (
                        f"已選出 {len(self._recommendations)} 檔當沖機會"
                        if self._recommendations
                        else "目前沒有符合風控條件的股票，持續掃描中"
                    ),
                    "session": session,
                    "regime": {**regime, **strategy},
                    "tradingDate": trading_date,
                    "updatedAt": now.isoformat(),
                    "source": "automation_cache",
                }
                self._last_candidate_snapshot_count = 0
                if _ranked_candidates:
                    try:
                        self._last_candidate_snapshot_count = await asyncio.to_thread(
                            self._persist_candidate_snapshots, _ranked_candidates, config, now,
                        )
                    except Exception:
                        logger.exception("Failed to persist day-trading candidate snapshots")
                self._last_scan_at = now
                await asyncio.to_thread(day_trading_cache.put, "automation-recommendations", self._recommendations, ttl=86_400)
                await asyncio.to_thread(day_trading_cache.put, AUTOMATION_SELECTION_CACHE_KEY, selection_cache, ttl=45)
                await asyncio.to_thread(day_trading_cache.put,
                    AUTOMATION_RANKED_CANDIDATES_CACHE_KEY,
                    {
                        "items": _ranked_candidates,
                        "recommendedTotal": len(self._recommendations),
                        "maximumRecommendations": config.maximum_recommendations,
                        "summary": selection_cache["summary"],
                        "tradingDate": trading_date,
                        "updatedAt": now.isoformat(),
                        "source": "automation_cache",
                    },
                    ttl=45,
                )
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
                "highFrequencyTrackingCount": pump_state.get("priorityCount", 0),
                "quotePump": {**pump_state, **self._quote_target_state},
                "baselineQuoteRefreshSeconds": BASELINE_QUOTE_REFRESH_SECONDS,
                "priorityQuoteRefreshSeconds": PRIORITY_QUOTE_REFRESH_SECONDS,
                "disposalRestrictions": day_trading_restrictions.state,
                "activeRobot": strategy["activeRobot"],
            }
            await asyncio.to_thread(day_trading_cache.put, "automation-supervisor", self._state, ttl=180)
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
        return {**self._state, "quotePump": {**day_trading_quote_pump.state, **self._quote_target_state}}


day_trading_automation = DayTradingAutomationSupervisor()
