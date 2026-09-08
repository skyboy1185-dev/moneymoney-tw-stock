"""Provider-neutral validation; receipt time never substitutes for exchange time."""
from datetime import datetime
from math import isfinite
from typing import Any
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")
TRUSTED_SOURCES = frozenset({"TWSE MIS", "FUGLE"})


def value(quote: Any, name: str, default: Any = None) -> Any:
    aliases = {"source": "dataSource", "is_realtime": "quoteIsRealtime", "quote_timestamp": "quoteTimestamp"}
    if isinstance(quote, dict):
        camel = aliases.get(name, name.split("_")[0] + "".join(part.title() for part in name.split("_")[1:]))
        return quote.get(name, quote.get(camel, default))
    return getattr(quote, name, default)


def quote_time(quote):
    try:
        stamp = datetime.fromisoformat(str(value(quote, "quote_timestamp")))
        return stamp if stamp.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


def positive(number):
    if isinstance(number, bool):
        return False
    try:
        return isfinite(float(number)) and float(number) > 0
    except (TypeError, ValueError, OverflowError):
        return False


def _valid_quote(quote, now=None, max_age_seconds=None, *, require_book=False, require_realtime=True):
    if quote is None or value(quote, "source") not in TRUSTED_SOURCES:
        return False
    if ((require_realtime and not value(quote, "is_realtime", False))
            or value(quote, "quote_kind", "trade") not in {"trade", "index"}
            or value(quote, "session", "regular") != "regular"
            or value(quote, "is_trial", False) or value(quote, "is_halted", False)
            or not positive(value(quote, "price"))):
        return False
    try:
        raw_volume = value(quote, "volume", 0)
        if isinstance(raw_volume, bool):
            return False
        volume = float(raw_volume)
        if not isfinite(volume) or volume < 0:
            return False
    except (TypeError, ValueError, OverflowError):
        return False
    for field in ("previous_close", "open", "high", "low"):
        number = value(quote, field)
        if number is not None and not positive(number):
            return False
    for field in ("change", "change_percent"):
        number = value(quote, field)
        if number is not None:
            if isinstance(number, bool):
                return False
            try:
                if not isfinite(float(number)):
                    return False
            except (TypeError, ValueError, OverflowError):
                return False
    stamp = quote_time(quote)
    if stamp is None:
        return False
    local_stamp = stamp.astimezone(TAIPEI)
    if not 540 <= local_stamp.hour * 60 + local_stamp.minute <= 810:
        return False
    if now is not None:
        age = (now - stamp).total_seconds()
        if stamp.astimezone(TAIPEI).date() != now.astimezone(TAIPEI).date() or age < 0:
            return False
        if max_age_seconds is not None and age > max_age_seconds:
            return False
    if require_book:
        if now is None or max_age_seconds is None:
            return False
        try:
            book = datetime.fromisoformat(str(value(quote, "book_timestamp")))
            if book.tzinfo is None or not 0 <= (now - book).total_seconds() <= max_age_seconds:
                return False
        except (TypeError, ValueError):
            return False
        bid, ask = value(quote, "best_bid"), value(quote, "best_ask")
        if not positive(bid) or not positive(ask) or float(bid) > float(ask):
            return False
    return True


def trusted_snapshot(quote, now=None):
    """Verified provenance/quality for availability, independent of quote age.

    This does not grant entry permission: a delayed regular-session trade remains
    an observed snapshot even after its realtime flag expires.
    """
    return _valid_quote(quote, now, require_realtime=False)


def trusted_quote(quote, now=None, max_age_seconds=None, *, require_book=False):
    return _valid_quote(quote, now, max_age_seconds, require_book=require_book)


def negative_quote_time(quote, now):
    """Ordering for verified halt/trial observations, never a trade timestamp."""
    if value(quote, "source") not in TRUSTED_SOURCES or not (
        value(quote, "is_halted", False) or value(quote, "is_trial", False)
    ):
        return None
    stamps = []
    for field in ("book_timestamp", "received_at", "quote_timestamp"):
        try:
            stamp = datetime.fromisoformat(str(value(quote, field)))
            if stamp.tzinfo is not None and stamp <= now and stamp.astimezone(TAIPEI).date() == now.astimezone(TAIPEI).date():
                stamps.append(stamp)
        except (TypeError, ValueError):
            pass
    return max(stamps, default=None)


def same_quote_segment(previous, current):
    return (all(value(previous, field) == value(current, field)
                for field in ("source", "quote_kind", "session", "volume_unit", "continuity"))
            and previous.quote_timestamp[:10] == current.quote_timestamp[:10]
            and current.volume >= previous.volume)


def latest_quote_segment(history):
    """Only the last uninterrupted provider/session/cumulative-volume sequence."""
    segment = []
    for quote in sorted(history, key=lambda item: item.quote_timestamp):
        if not trusted_quote(quote):
            segment = []
            continue
        if segment and not same_quote_segment(segment[-1], quote):
            segment = []
        segment.append(quote)
    return segment
