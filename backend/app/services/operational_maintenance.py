from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime, time, timedelta
import logging
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session, sessionmaker

from ..config import get_settings
from ..database import BackgroundSessionLocal, cleanup_expired_operational_data, database_runtime_status


logger = logging.getLogger(__name__)
TAIPEI = ZoneInfo("Asia/Taipei")
DAILY_CLEANUP_CLOCK = time(15, 35)


def next_daily_cleanup_after(
    now: datetime,
    *,
    clock: time = DAILY_CLEANUP_CLOCK,
    timezone: ZoneInfo = TAIPEI,
) -> datetime:
    current = now if now.tzinfo else now.replace(tzinfo=UTC)
    local = current.astimezone(timezone)
    target_local = datetime.combine(local.date(), clock, timezone)
    if local >= target_local:
        target_local += timedelta(days=1)
    return target_local.astimezone(UTC)


class OperationalMaintenanceAutomation:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session] = BackgroundSessionLocal,
        cleanup: Callable[..., dict[str, int]] = cleanup_expired_operational_data,
        cleanup_clock: time = DAILY_CLEANUP_CLOCK,
    ) -> None:
        self._session_factory = session_factory
        self._cleanup = cleanup
        self._cleanup_clock = cleanup_clock
        self._task: asyncio.Task[None] | None = None
        self._state: dict[str, object] = {
            "status": "stopped",
            "cleanupTime": self._cleanup_clock.strftime("%H:%M"),
            "timezone": "Asia/Taipei",
            "lastRunAt": None,
            "lastSuccessAt": None,
            "lastError": None,
            "lastResult": None,
            "nextRunAt": None,
        }

    @property
    def state(self) -> dict[str, object]:
        return dict(self._state)

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        now = datetime.now(UTC)
        self._state.update({
            "status": "running",
            "lastError": None,
            "nextRunAt": next_daily_cleanup_after(now, clock=self._cleanup_clock).isoformat(),
        })
        self._task = asyncio.create_task(self._run(), name="operational-maintenance")
        logger.warning(
            "operational maintenance scheduled daily at %s Asia/Taipei; nextRunAt=%s",
            self._state["cleanupTime"],
            self._state["nextRunAt"],
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        self._state["status"] = "stopped"

    async def run_once(self, now: datetime | None = None) -> dict[str, object]:
        return await asyncio.to_thread(self._run_once_sync, now)

    def _run_once_sync(self, now: datetime | None = None) -> dict[str, object]:
        current = now or datetime.now(UTC)
        self._state["lastRunAt"] = current.isoformat()
        try:
            deleted = self._cleanup(retention_days=3, intraday_snapshot_retention_hours=2)
            db_status: dict[str, object] = {}
            with self._session_factory() as session:
                db_status = database_runtime_status(
                    session,
                    get_settings().expected_database_host,
                )
            result = {
                "status": "completed",
                "deleted": deleted,
                "databaseSizeMB": db_status.get("databaseSizeMB"),
                "databaseHost": db_status.get("host"),
                "matchesExpectedHost": db_status.get("matchesExpectedHost"),
            }
            self._state.update({
                "status": "running",
                "lastSuccessAt": datetime.now(UTC).isoformat(),
                "lastError": None,
                "lastResult": result,
            })
            logger.warning("operational maintenance cleanup completed: %s", result)
            return result
        except Exception as error:
            result = {"status": "error", "error": str(error)[:500]}
            self._state.update({
                "status": "running",
                "lastError": str(error)[:500],
                "lastResult": result,
            })
            logger.exception("operational maintenance cleanup failed")
            return result

    async def _run(self) -> None:
        while True:
            now = datetime.now(UTC)
            next_run = next_daily_cleanup_after(now, clock=self._cleanup_clock)
            self._state["nextRunAt"] = next_run.isoformat()
            await asyncio.sleep(max(1, (next_run - now).total_seconds()))
            await self.run_once()


operational_maintenance_automation = OperationalMaintenanceAutomation()
