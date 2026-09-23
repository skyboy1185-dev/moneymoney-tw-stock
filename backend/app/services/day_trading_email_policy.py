from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo


TAIPEI = ZoneInfo("Asia/Taipei")
ENTRY_EVENTS = {"BUY", "SHORT", "ADD", "BUY_FILLED"}
EXIT_EVENTS = {"REDUCE", "STOP_LOSS", "TAKE_PROFIT", "EXIT", "SELL_FILLED"}
SCHEDULED_EVENTS = {"PREOPEN_READY", "OPENING_RANGE_READY", "HOURLY_SUMMARY"}
CRITICAL_EVENTS = {
    "MARKET_DATA_INTERRUPTED", "BROKER_DISCONNECTED", "SYSTEM_HEARTBEAT_INTERRUPTED",
    "STRATEGY_HEALTH_ALERT", "EMERGENCY_STOP", "ROBOT_HALTED",
}


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def email_expiry(event_type: str, created_at: datetime) -> datetime:
    created = _aware(created_at)
    if event_type in ENTRY_EVENTS:
        return created + timedelta(minutes=2)
    if event_type in EXIT_EVENTS or event_type in SCHEDULED_EVENTS:
        return created + timedelta(minutes=10)
    if event_type == "DAILY_REPORT":
        return created + timedelta(minutes=60)
    return created + timedelta(minutes=30)


def email_is_deliverable(event_type: str, created_at: datetime, now: datetime | None = None) -> bool:
    current = _aware(now or datetime.now(UTC))
    created = _aware(created_at)
    local_now = current.astimezone(TAIPEI)
    local_created = created.astimezone(TAIPEI)
    if local_created.date() != local_now.date() or current > email_expiry(event_type, created):
        return False
    if event_type in ENTRY_EVENTS:
        return time(9) <= local_now.time() <= time(13, 20)
    if event_type in EXIT_EVENTS:
        return time(9) <= local_now.time() <= time(13, 30)
    if event_type == "DAILY_REPORT":
        return time(13, 30) <= local_now.time() <= time(14, 40)
    if event_type in SCHEDULED_EVENTS:
        return local_now.time() <= time(13, 30)
    return event_type in CRITICAL_EVENTS
