"""Email delivery loop for persisted unified intraday events."""

import asyncio
from datetime import UTC, datetime
import logging

from sqlalchemy import select

from ..database import SessionLocal
from ..day_trading_v2_models import DayTradeV2Notification
from .gmail_messaging import gmail_notification_dispatcher


logger = logging.getLogger(__name__)
EMAIL_EVENT_TYPES = {
    "BUY_FILLED", "SELL_FILLED", "ROBOT_HALTED", "EMERGENCY_STOP",
    "MARKET_DATA_INTERRUPTED", "BROKER_DISCONNECTED", "RISK_REDUCED", "DAILY_LOSS_LIMIT",
}


class DayTradingV2NotificationAutomation:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="day-trading-v2-email")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await self._task
        self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.dispatch_pending()
            except Exception:
                logger.exception("day-trading-v2 email dispatch failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=10)
            except TimeoutError:
                pass

    async def dispatch_pending(self) -> int:
        if not gmail_notification_dispatcher.configured:
            return 0
        with SessionLocal() as db:
            rows = list(db.scalars(select(DayTradeV2Notification).where(
                DayTradeV2Notification.email_sent.is_(False),
                DayTradeV2Notification.event_type.in_(EMAIL_EVENT_TYPES),
            ).order_by(DayTradeV2Notification.created_at).limit(20)).all())
        delivered = 0
        for row in rows:
            sent = await gmail_notification_dispatcher.dispatch(
                event_type=f"day_trading_v2_{row.event_type.lower()}", action=row.event_type,
                message=f"{row.title}\n\n{row.message}", dedupe_key=f"dtv2-email:{row.event_id}",
                signal_id=row.event_id, channel_name="超強AI當沖系統",
            )
            with SessionLocal() as db:
                stored = db.get(DayTradeV2Notification, row.id)
                if stored:
                    stored.email_attempted_at = datetime.now(UTC)
                    if sent:
                        stored.email_sent = True
                        delivered += 1
                    db.commit()
        return delivered


day_trading_v2_notification_automation = DayTradingV2NotificationAutomation()
