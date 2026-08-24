from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..config import get_settings
from ..database import SessionLocal
from ..models import AIStockLineDeliveryLog, AIStockLineGroup
from .gmail_messaging import gmail_notification_dispatcher
from .line_messaging import LineMessagingClient, LineNotificationEvent


AI_STOCK_OFFICIAL_ACCOUNT_NAME = "超強AI當沖系統"
logger = logging.getLogger(__name__)


class AIStockLineNotificationDispatcher:
    """AI 選股專用 LINE 通道，不與當沖官方帳號共用憑證或群組。"""

    def __init__(self) -> None:
        self.client = LineMessagingClient(
            access_token_setting="ai_stock_line_channel_access_token",
            channel_secret_setting="ai_stock_line_channel_secret",
            enabled_setting="ai_stock_line_notifications_enabled",
        )
        self._lock = asyncio.Lock()

    def _active_group_ids(self) -> list[str]:
        with SessionLocal() as db:
            group_ids = list(db.scalars(
                select(AIStockLineGroup.group_id)
                .where(AIStockLineGroup.active.is_(True))
                .order_by(AIStockLineGroup.bound_at),
            ).all())
        fallback = get_settings().ai_stock_line_target_group_id
        if fallback and fallback not in group_ids:
            group_ids.append(fallback)
        return group_ids

    async def dispatch_many(self, events: list[LineNotificationEvent]) -> int:
        if not events:
            return 0
        sent = 0
        async with self._lock:
            for event in sorted(events, key=lambda item: item.priority):
                sent += await self._dispatch(event)
        return sent

    async def _dispatch(self, event: LineNotificationEvent) -> int:
        try:
            await gmail_notification_dispatcher.dispatch(
                event_type=event.event_type,
                action=event.action,
                message=event.message,
                dedupe_key=event.dedupe_key,
                signal_id=event.signal_id,
                symbol=event.symbol,
                channel_name=AI_STOCK_OFFICIAL_ACCOUNT_NAME,
            )
        except Exception:
            logger.exception("AI stock Gmail notification failed for %s", event.dedupe_key)
        if not get_settings().ai_stock_line_notifications_enabled:
            return 0
        successful = 0
        for group_id in self._active_group_ids():
            with SessionLocal() as db:
                if event.cooldown_entry and event.symbol:
                    cutoff = datetime.now(UTC) - timedelta(minutes=3)
                    recent = db.scalar(select(AIStockLineDeliveryLog.id).where(
                        AIStockLineDeliveryLog.group_id == group_id,
                        AIStockLineDeliveryLog.symbol == event.symbol,
                        AIStockLineDeliveryLog.event_type == "ai_initial_entry",
                        AIStockLineDeliveryLog.status.in_(["pending", "sent"]),
                        AIStockLineDeliveryLog.created_at >= cutoff,
                    ).limit(1))
                    if recent is not None:
                        continue
                log = AIStockLineDeliveryLog(
                    group_id=group_id,
                    event_type=event.event_type,
                    signal_id=event.signal_id,
                    symbol=event.symbol,
                    action=event.action,
                    priority=event.priority,
                    dedupe_key=event.dedupe_key,
                    status="pending",
                    attempts=0,
                    message_preview=event.message[:5000],
                    created_at=datetime.now(UTC),
                )
                db.add(log)
                try:
                    db.commit()
                    db.refresh(log)
                except IntegrityError:
                    db.rollback()
                    continue

            result = await self.client.push_text(group_id, event.message)
            with SessionLocal() as db:
                stored = db.get(AIStockLineDeliveryLog, log.id)
                if stored is None:
                    continue
                stored.attempts = result.attempts
                stored.response_status = result.response_status
                stored.error_message = result.error
                stored.status = "sent" if result.success else "failed"
                if result.success:
                    stored.sent_at = datetime.now(UTC)
                    group = db.scalar(select(AIStockLineGroup).where(
                        AIStockLineGroup.group_id == group_id,
                    ))
                    if group:
                        group.last_push_at = stored.sent_at
                    successful += 1
                db.commit()
        return successful


ai_stock_line_dispatcher = AIStockLineNotificationDispatcher()
