from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, date, datetime, timedelta
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


class DayTradingAutomationSupervisor:
    """Keeps the trading clock alive even when no browser is connected."""

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._started_at: datetime | None = None
        self._last_scan_at: datetime | None = None
        self._recommendations: list[dict[str, Any]] = []
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
                "recommendedCount": len(self._recommendations),
            }
            day_trading_cache.put("automation-supervisor", self._state, ttl=180)
            await asyncio.sleep(1)

    @property
    def state(self) -> dict[str, Any]:
        return self._state


day_trading_automation = DayTradingAutomationSupervisor()
