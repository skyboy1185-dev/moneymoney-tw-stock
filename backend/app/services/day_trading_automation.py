from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, date, datetime, timedelta
import logging
from typing import Any

from sqlalchemy import func, select, text

from ..config import get_settings
from ..database import SessionLocal
from ..models import DayTradingPosition
from .day_trading import day_trading_engine
from .day_trading_cache import day_trading_cache
from .day_trading_schedule import (
    TradingScheduleConfig,
    stable_recommendation_selector,
    trading_session_state,
)
from .line_messaging import line_notification_dispatcher
from .official_market_data import StockQuoteRequest, official_market_data_provider


logger = logging.getLogger(__name__)


class DayTradingAutomationSupervisor:
    """Keeps the trading clock alive even when no browser is connected."""

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._started_at: datetime | None = None
        self._last_scan_at: datetime | None = None
        self._last_quote_refresh_at: datetime | None = None
        self._recommendations: list[dict[str, Any]] = []
        self._restored_signal_count = 0
        self._last_phase: str | None = None
        self._last_data_status: str | None = None
        self._trading_date: str | None = None
        self._today_signal_ids: set[str] = set()
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

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._started_at = datetime.now(UTC)
        restored = day_trading_cache.get("automation-recommendations")
        if isinstance(restored, list):
            self._recommendations = restored
            self._restored_signal_count = len(restored)
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
            database_ok = False
            open_positions = 0
            try:
                with SessionLocal() as db:
                    db.execute(text("SELECT 1"))
                    open_positions = int(db.scalar(select(func.count()).select_from(
                        DayTradingPosition,
                    ).where(DayTradingPosition.status == "open")) or 0)
                    database_ok = True
            except Exception:
                database_ok = False

            quote_refresh_due = (
                self._last_quote_refresh_at is None
                or now - self._last_quote_refresh_at >= timedelta(
                    seconds=max(1.0, get_settings().quote_refresh_seconds),
                )
            )
            if quote_refresh_due:
                try:
                    seed_candidates = day_trading_engine.signals()
                    quote_requests = [
                        StockQuoteRequest(
                            symbol=str(item["symbol"]),
                            name=str(item["stockName"]),
                            market=str(item["market"]),
                        )
                        for item in seed_candidates
                    ]
                    quotes = await official_market_data_provider.get_quotes(
                        quote_requests,
                        force_refresh=True,
                    )
                    day_trading_engine.update_official_quotes(quotes)
                except Exception:
                    logger.exception("TWSE MIS quote refresh failed")
                self._last_quote_refresh_at = now

            regime = day_trading_engine.market_regime()
            recovering = day_trading_engine.sample_count < config.minimum_live_samples
            session = trading_session_state(
                config,
                now,
                data_status=regime["dataStatus"],
                quote_samples=day_trading_engine.sample_count,
                infrastructure_ok=database_ok and day_trading_cache.healthy,
                recovering=recovering,
            )
            trading_date = str(session["tradingDate"])
            if self._trading_date != trading_date:
                self._trading_date = trading_date
                self._today_signal_ids.clear()
            scan_due = (
                self._last_scan_at is None
                or now - self._last_scan_at >= timedelta(seconds=config.recommendation_refresh_seconds)
            )
            if scan_due and session["phase"] in {"warmup", "scanning"}:
                candidates = day_trading_engine.signals()
                session = trading_session_state(
                    config,
                    now,
                    data_status=regime["dataStatus"],
                    quote_samples=day_trading_engine.sample_count,
                    infrastructure_ok=database_ok and day_trading_cache.healthy,
                    recovering=False,
                )
                self._recommendations, _ = stable_recommendation_selector.select(
                    "system-automation",
                    candidates,
                    config,
                    session,
                    now=now,
                )
                self._last_scan_at = now
                day_trading_cache.put("automation-recommendations", self._recommendations, ttl=86_400)
                if session["formalSignalsAllowed"]:
                    self._today_signal_ids.update(str(item["id"]) for item in self._recommendations)
            elif session["phase"] not in {"warmup", "scanning"} or not session["formalSignalsAllowed"]:
                self._recommendations = []
            self._state = {
                "status": "running",
                "startedAt": self._started_at.isoformat() if self._started_at else None,
                "checkedAt": now.isoformat(),
                "session": session,
                "database": "healthy" if database_ok else "unavailable",
                "redis": "healthy" if day_trading_cache.healthy else "unavailable",
                "restoredOpenPositions": open_positions,
                "restoredSignalCount": self._restored_signal_count,
                "recommendedCount": len(self._recommendations),
            }
            day_trading_cache.put("automation-supervisor", self._state, ttl=180)
            line_tasks: list[Any] = []
            phase = str(session["phase"])
            data_status = str(regime["dataStatus"])
            if phase == "scanning" and self._last_phase != "scanning":
                line_tasks.append(line_notification_dispatcher.send_system_event(
                    "opening",
                    "台股開盤，機器人啟動",
                    "AI 當沖機器人已完成暖機，開始掃描正式做多與放空機會。",
                    f"system:{trading_date}:opening",
                    priority=3,
                ))
            if data_status != self._last_data_status and data_status in {"severe_delay", "disconnected", "source_error"}:
                disconnected = data_status in {"disconnected", "source_error"}
                line_tasks.append(line_notification_dispatcher.send_system_event(
                    "data_alert",
                    "行情來源中斷" if disconnected else "行情資料延遲",
                    "目前停止產生新交易訊號；既有持倉仍持續檢查出場與停損。",
                    f"system:{trading_date}:data:{data_status}",
                    priority=2,
                ))
            if phase == "summary" and self._last_phase != "summary":
                line_tasks.extend([
                    line_notification_dispatcher.send_system_event(
                        "robot_stopped",
                        "機器人停止運作",
                        "今日新進場訊號已停止，請確認所有當沖部位均已處理。",
                        f"system:{trading_date}:stopped",
                        priority=2,
                    ),
                    line_notification_dispatcher.send_system_event(
                        "closing_summary",
                        "每日收盤摘要",
                        f"今日 AI 正式推薦 {len(self._today_signal_ids)} 檔；系統已停止產生當日新訊號。",
                        f"system:{trading_date}:summary",
                        priority=3,
                    ),
                ])
            if session["formalSignalsAllowed"] and self._recommendations:
                line_tasks.append(
                    line_notification_dispatcher.send_recommendations(self._recommendations[:5]),
                )
            if line_tasks:
                await asyncio.gather(*line_tasks, return_exceptions=True)
            self._last_phase = phase
            self._last_data_status = data_status
            await asyncio.sleep(1)

    @property
    def state(self) -> dict[str, Any]:
        return self._state


day_trading_automation = DayTradingAutomationSupervisor()
