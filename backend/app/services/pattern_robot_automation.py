from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, date, datetime, time
import logging
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select

from ..config import get_settings
from ..database import BackgroundSessionLocal as SessionLocal
from ..models import PatternRobotRun, PatternRobotSetting
from ..pattern_schemas import PatternScanPayload
from .day_trading_schedule import is_twse_trading_day
from .pattern_robot_service import ensure_pattern_settings, process_pattern_scan


logger = logging.getLogger(__name__)
TAIPEI = ZoneInfo("Asia/Taipei")


def _scanner_url() -> str:
    settings = get_settings()
    url = settings.pattern_robot_scanner_url.strip()
    if not url and settings.adaptive_electronic_scanner_url:
        url = settings.adaptive_electronic_scanner_url.replace("/adaptive-electronic/scan", "/pattern-robot/scanner")
    if not url or urlparse(url).scheme not in {"http", "https"}:
        raise RuntimeError("PATTERN_ROBOT_SCANNER_URL 尚未設定")
    return url


def _is_trading_day(day: date) -> bool:
    holidays: set[date] = set()
    for raw in get_settings().twse_holidays.split(","):
        try:
            holidays.add(date.fromisoformat(raw.strip()))
        except ValueError:
            continue
    return is_twse_trading_day(day, holidays)


async def fetch_pattern_scan_payload() -> PatternScanPayload:
    settings = get_settings()
    headers = {"Accept": "application/json", "User-Agent": "TWSE-Pattern-Robot/1.0"}
    if settings.adaptive_electronic_scanner_token:
        headers["X-Adaptive-Scanner-Token"] = settings.adaptive_electronic_scanner_token
    async with httpx.AsyncClient(
        timeout=settings.pattern_robot_scanner_timeout_seconds, follow_redirects=True,
    ) as client:
        response = await client.get(_scanner_url(), headers=headers)
        response.raise_for_status()
        return PatternScanPayload.model_validate(response.json())


class PatternRobotAutomation:
    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._run_lock = asyncio.Lock()
        self._state = {
            "status": "stopped", "lastRunAt": None, "lastSuccessAt": None,
            "lastResult": None, "lastError": None, "nextRunAt": None,
        }
        self._last_close_scan_date: date | None = None

    @property
    def state(self) -> dict:
        return dict(self._state)

    async def start(self, *, persist: bool = True) -> None:
        with SessionLocal() as db:
            item = ensure_pattern_settings(db)
            if persist:
                item.enabled = True
                item.updated_at = datetime.now(UTC)
            db.commit()
        if self._task and not self._task.done():
            self._state["status"] = "running"
            return
        self._state["status"] = "running"
        self._task = asyncio.create_task(self._run(), name="pattern-robot-automation")

    async def stop(self, *, persist: bool = True) -> None:
        if persist:
            with SessionLocal() as db:
                item = ensure_pattern_settings(db)
                item.enabled = False
                item.updated_at = datetime.now(UTC)
                db.commit()
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._state["status"] = "stopped"

    async def run_once(self, *, force: bool = False) -> dict:
        async with self._run_lock:
            now = datetime.now(UTC)
            local = now.astimezone(TAIPEI)
            self._state.update({"status": "scanning", "lastRunAt": now.isoformat(), "lastError": None})
            if not _is_trading_day(local.date()):
                result = {"status": "skipped_non_trading_day"}
                self._state.update({"status": "running", "lastResult": result})
                return result
            if local.time() < time(9, 0) and not force:
                result = {"status": "skipped_pre_open"}
                self._state.update({"status": "running", "lastResult": result})
                return result
            payload = await fetch_pattern_scan_payload()
            with SessionLocal() as db:
                try:
                    result = process_pattern_scan(db, payload, force=force)
                except Exception as error:
                    db.rollback()
                    run = db.scalar(select(PatternRobotRun).where(
                        PatternRobotRun.trade_date == payload.trade_date,
                        PatternRobotRun.run_type == "OPEN_SCAN",
                    ))
                    if run:
                        run.status = "FAILED"
                        run.error_message = str(error)[:2000]
                        run.completed_at = datetime.now(UTC)
                        db.commit()
                    raise
            self._state.update({
                "status": "running", "lastSuccessAt": datetime.now(UTC).isoformat(),
                "lastResult": result, "lastError": None,
            })
            return result

    async def _run(self) -> None:
        while True:
            interval = max(60, get_settings().pattern_robot_scan_interval_seconds)
            try:
                now = datetime.now(UTC)
                local = now.astimezone(TAIPEI)
                with SessionLocal() as db:
                    settings = ensure_pattern_settings(db)
                    completed = db.scalar(select(PatternRobotRun.id).where(
                        PatternRobotRun.trade_date == local.date(), PatternRobotRun.run_type == "OPEN_SCAN",
                        PatternRobotRun.status == "COMPLETED",
                    ))
                    enabled = settings.enabled
                    db.commit()
                should_open = completed is None and local.time() >= time(9, 0)
                should_monitor = completed is not None and time(9, 0) <= local.time() <= time(13, 40)
                should_close = completed is not None and local.time() > time(13, 40) and self._last_close_scan_date != local.date()
                if enabled and _is_trading_day(local.date()) and (should_open or should_monitor or should_close):
                    # A restart after 09:00 immediately performs a missing opening scan.
                    # Subsequent cycles update intraday status without inserting another run row.
                    await self.run_once(force=completed is not None)
                    if should_close:
                        self._last_close_scan_date = local.date()
                self._state["nextRunAt"] = (datetime.now(UTC).timestamp() + interval)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.exception("Pattern robot automation cycle failed")
                self._state.update({"status": "error", "lastError": str(error)[:500]})
            await asyncio.sleep(interval)


pattern_robot_automation = PatternRobotAutomation()
