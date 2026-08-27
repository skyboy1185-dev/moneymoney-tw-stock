from __future__ import annotations

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo


TAIPEI = ZoneInfo("Asia/Taipei")
ENTRY_START = time(9, 0)
ENTRY_CUTOFF = time(12, 0)


def taipei_datetime(at: datetime) -> datetime:
    """Normalize timestamps before evaluating Taiwan-session rules.

    External scan payloads are UTC-oriented.  If a caller accidentally passes a
    naive timestamp, treat it as UTC instead of attaching Asia/Taipei directly;
    otherwise 04:18 UTC could be read as 04:18 Taipei and slip past the noon
    entry cutoff.
    """
    source = at.replace(tzinfo=UTC) if at.tzinfo is None else at
    return source.astimezone(TAIPEI)


def adaptive_entry_window_open(
    at: datetime,
    market_open: bool,
    trade_date: date | None = None,
) -> bool:
    """Allow new AI entries only during the executable 09:00-12:00 window."""
    if not market_open:
        return False
    local = taipei_datetime(at)
    if local.weekday() >= 5:
        return False
    if trade_date is not None and local.date() != trade_date:
        return False
    return ENTRY_START <= local.time().replace(tzinfo=None) < ENTRY_CUTOFF
