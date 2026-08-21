from __future__ import annotations

import asyncio
import logging

from ..config import get_settings
from ..database import BackgroundSessionLocal as SessionLocal
from .large_holders import fetch_latest_distribution_bundle, persist_latest_distribution


logger = logging.getLogger(__name__)


class LargeHolderAutomation:
    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if not get_settings().large_holder_auto_sync_enabled or self._task is not None:
            return
        self._task = asyncio.create_task(self._loop(), name="large-holder-weekly-sync")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                rows, directory = await fetch_latest_distribution_bundle()
                await asyncio.to_thread(self._persist, rows, directory)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("TDCC weekly shareholder distribution sync failed")
            await asyncio.sleep(max(3_600, get_settings().large_holder_sync_interval_seconds))

    @staticmethod
    def _persist(rows, directory) -> None:
        with SessionLocal() as db:
            result = persist_latest_distribution(db, rows, directory)
            logger.info(
                "TDCC shareholder distribution sync: status=%s report_date=%s summaries=%s",
                result.get("status"),
                result.get("reportDate"),
                result.get("summaryCount"),
            )


large_holder_automation = LargeHolderAutomation()
