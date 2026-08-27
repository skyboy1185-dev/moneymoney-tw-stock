from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime, time
import logging
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..database import BackgroundSessionLocal
from ..models import LimitUpAiSettings
from .day_trading_schedule import is_twse_trading_day
from .limit_up_ai import run_limit_up_cycle


logger = logging.getLogger(__name__)
TAIPEI = ZoneInfo("Asia/Taipei")
DEFAULT_USER_ID = "demo-user"
MARKET_SCAN_START = time(9, 0)
MARKET_SCAN_END = time(13, 35)


class LimitUpAiAutomation:
    def __init__(
        self,
        *,
        interval_seconds: int = 15,
        idle_interval_seconds: int = 60,
        max_users_per_cycle: int = 20,
        session_factory: sessionmaker[Session] = BackgroundSessionLocal,
        runner: Callable[[Session, str, datetime | None], dict[str, object]] = run_limit_up_cycle,
    ) -> None:
        self.interval_seconds = max(5, interval_seconds)
        self.idle_interval_seconds = max(15, idle_interval_seconds)
        self.max_users_per_cycle = max(1, max_users_per_cycle)
        self._session_factory = session_factory
        self._runner = runner
        self._task: asyncio.Task[None] | None = None
        self._state: dict[str, object] = {
            "status": "stopped",
            "startedAt": None,
            "lastRunAt": None,
            "lastSuccessAt": None,
            "lastError": None,
            "lastResult": None,
            "lastUserCount": 0,
            "cycleCount": 0,
            "intervalSeconds": self.interval_seconds,
        }

    @property
    def state(self) -> dict[str, object]:
        return dict(self._state)

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        now = datetime.now(UTC)
        self._state.update({"status": "running", "startedAt": now.isoformat(), "lastError": None})
        self._task = asyncio.create_task(self._run(), name="limit-up-ai-automation")

    async def stop(self) -> None:
        if not self._task:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        self._state["status"] = "stopped"

    def status(self) -> dict[str, object]:
        local = datetime.now(UTC).astimezone(TAIPEI)
        return {
            **self.state,
            "marketSessionActive": self._is_market_session(local),
            "marketTime": local.isoformat(),
        }

    async def run_once(self, now: datetime | None = None, *, force: bool = False, user_id: str | None = None) -> dict[str, object]:
        return await asyncio.to_thread(self._run_once_sync, now, force=force, user_id=user_id)

    def _active_user_ids(self, db: Session, explicit_user_id: str | None = None) -> list[str]:
        if explicit_user_id:
            return [explicit_user_id]
        rows = list(db.scalars(select(LimitUpAiSettings.user_id).order_by(LimitUpAiSettings.updated_at.desc())).all())
        user_ids: list[str] = []
        for candidate in [DEFAULT_USER_ID, *rows]:
            if candidate and candidate not in user_ids:
                user_ids.append(candidate)
            if len(user_ids) >= self.max_users_per_cycle:
                break
        return user_ids or [DEFAULT_USER_ID]

    def _run_once_sync(self, now: datetime | None = None, *, force: bool = False, user_id: str | None = None) -> dict[str, object]:
        current = now or datetime.now(UTC)
        local = current.astimezone(TAIPEI)
        self._state["lastRunAt"] = current.isoformat()
        if not force and not self._is_market_session(local):
            result: dict[str, object] = {
                "status": "waiting_market_session",
                "marketTime": local.isoformat(),
            }
            self._state.update({"status": "running", "lastResult": result, "lastError": None})
            return result

        processed: list[dict[str, object]] = []
        with self._session_factory() as db:
            user_ids = self._active_user_ids(db, user_id)
            self._state["lastUserCount"] = len(user_ids)
            for active_user_id in user_ids:
                payload = self._runner(db, active_user_id, current)
                summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
                processed.append({
                    "userId": active_user_id,
                    "candidateCount": summary.get("candidateCount", 0) if isinstance(summary, dict) else 0,
                    "actionableCount": summary.get("actionableCount", 0) if isinstance(summary, dict) else 0,
                    "openPositionCount": summary.get("openPositionCount", 0) if isinstance(summary, dict) else 0,
                })

        result = {
            "status": "scanned",
            "marketTime": local.isoformat(),
            "processedUsers": processed,
        }
        self._state.update({
            "status": "running",
            "lastSuccessAt": datetime.now(UTC).isoformat(),
            "lastResult": result,
            "lastError": None,
            "cycleCount": int(self._state.get("cycleCount") or 0) + 1,
        })
        return result

    def _is_market_session(self, local: datetime) -> bool:
        return (
            is_twse_trading_day(local.date())
            and MARKET_SCAN_START <= local.time() <= MARKET_SCAN_END
        )

    async def _run(self) -> None:
        while True:
            try:
                local = datetime.now(UTC).astimezone(TAIPEI)
                await self.run_once()
                interval = self.interval_seconds if self._is_market_session(local) else self.idle_interval_seconds
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.exception("Limit-up AI scan failed")
                self._state.update({"status": "error", "lastError": str(error)[:500]})
                interval = self.idle_interval_seconds
            await asyncio.sleep(interval)


limit_up_ai_automation = LimitUpAiAutomation()
