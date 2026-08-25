from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo


TAIPEI = ZoneInfo("Asia/Taipei")
ENTRY_START = time(9, 0)
ENTRY_CUTOFF = time(13, 0)


def adaptive_entry_window_open(
    at: datetime,
    market_open: bool,
    trade_date: date | None = None,
) -> bool:
    """Allow new AI entries only during the executable 09:00-13:00 window."""
    if not market_open:
        return False
    local = at.replace(tzinfo=TAIPEI) if at.tzinfo is None else at.astimezone(TAIPEI)
    if local.weekday() >= 5:
        return False
    if trade_date is not None and local.date() != trade_date:
        return False
    return ENTRY_START <= local.time().replace(tzinfo=None) < ENTRY_CUTOFF
