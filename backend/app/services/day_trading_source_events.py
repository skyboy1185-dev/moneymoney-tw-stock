"""Durable, debounced source changes for existing active PAPER users only."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..config import get_settings
from ..database import BackgroundSessionLocal
from ..day_trading_v2_models import DayTradeV2Notification, DayTradeV2RuntimeState, DayTradeV2Setting
from .day_trading_cache import day_trading_cache

TAIPEI = ZoneInfo("Asia/Taipei")
STABLE_SECONDS = 10
STATE_TTL = 172_800
SOURCE_NAMES = {"FUGLE_WS": "Fugle 即時串流", "FUGLE_REST": "Fugle 備援報價", "TWSE_MIS": "TWSE MIS"}


def _is_current_leader() -> bool:
    # The supervisor snapshot may predate cache/DB waits in this worker thread.
    # Recheck the live owner instead of trusting a previously true isLeader.
    from .day_trading_quote_pump import day_trading_quote_pump
    current = day_trading_quote_pump.diagnostics()
    adapter = current.get("sourceHealth") or current.get("fugle") or {}
    return bool(current.get("enabled") and adapter.get("isLeader")
                and current.get("providerMode") in {"primary", "degraded"})


def _condition(diagnostics: dict[str, Any]) -> str:
    """An ordinary index REST quote isn't a stock-feed fallback."""
    active = diagnostics.get("activeSource")
    counts = diagnostics.get("activeSourceCounts")
    if isinstance(counts, dict):
        stock_counts = {source: int(counts.get(source, 0)) for source in SOURCE_NAMES}
        index_source = diagnostics.get("indexSource")
        if index_source in stock_counts:
            stock_counts[index_source] = max(0, stock_counts[index_source] - 1)
        if not any(stock_counts.values()):
            return "dead"
    if active in {None, "NONE"}:
        return "dead"
    return "primary" if diagnostics.get("providerMode") == "primary" else "backup"


def _read_state(cache, key: str) -> dict | None:
    # DayTradingCache's general-purpose get/put suppress Redis failures and may
    # fall back to process memory. Source-event IDs need durable pendingSince.
    if cache.mode == "redis":
        raw = cache._redis.get(f"moneymoney:{key}")
        return json.loads(raw) if raw else None
    if get_settings().app_env != "development":
        raise RuntimeError("Source event persistence unavailable")
    return cache.get(key)


def _write_state(cache, key: str, state: dict) -> None:
    if not _is_current_leader():
        return
    if cache.mode == "redis":
        cache._redis.setex(f"moneymoney:{key}", STATE_TTL, json.dumps(state))
    else:
        cache.put(key, state, ttl=STATE_TTL)


def _event(previous: str, current: str, sources: list[str]) -> tuple[str, str, str]:
    source_text = "、".join(SOURCE_NAMES[source] for source in sources) or "可用行情來源"
    guard = "恢復接收行情後仍須通過原有逐股報價時效與交易風控，才可送單。"
    if current == "dead":
        return ("QUOTE_SOURCE_UNAVAILABLE", "行情來源全部中斷",
                "目前股票行情的主要與備援來源均無可用報價。系統保留原有安全保護，不使用過期行情送單。")
    if previous == "dead":
        return ("QUOTE_SOURCE_RECOVERED", "行情來源已恢復",
                f"股票行情已由{source_text}恢復接收。{guard}")
    if current == "primary":
        return ("QUOTE_SOURCE_RECOVERED", "主要行情來源已恢復",
                f"股票行情已切回 Fugle 即時串流。{guard}")
    return ("QUOTE_SOURCE_SWITCHED", "行情已切換備援來源",
            f"部分或全部股票行情已切換至{source_text}。各股票仍須通過原有報價時效與交易風控，才可送單。")


def observe_quote_source(diagnostics: dict[str, Any], now: datetime, *,
                         session_factory=None, cache=None) -> int:
    """Called in a worker thread every 5s; returns newly recorded user events.

    The stream lease owner supplies its current diagnostics. No users/settings
    are created, and this function never dispatches email itself.
    """
    current = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
    local = current.astimezone(TAIPEI)
    adapter = diagnostics.get("sourceHealth") or diagnostics.get("fugle") or {}
    if (not diagnostics.get("enabled") or not adapter.get("isLeader")
            or diagnostics.get("providerMode") not in {"primary", "degraded"}
            or local.weekday() >= 5 or not time(9) <= local.time() <= time(13, 30)):
        return 0
    if not _is_current_leader():
        return 0
    cache = day_trading_cache if cache is None else cache
    session_factory = BackgroundSessionLocal if session_factory is None else session_factory
    key = f"day-trading-source-events:{local.date().isoformat()}"
    condition = _condition(diagnostics)
    state = _read_state(cache, key)
    if not isinstance(state, dict) or state.get("previous") not in {"primary", "backup", "dead"}:
        _write_state(cache, key, {"previous": condition, "pending": None, "pendingSince": None})
        return 0
    previous = state["previous"]
    if condition == previous:
        if state.get("pending"):
            _write_state(cache, key, {"previous": previous, "pending": None, "pendingSince": None})
        return 0
    if state.get("pending") != condition or not state.get("pendingSince"):
        _write_state(cache, key, {"previous": previous, "pending": condition, "pendingSince": current.isoformat()})
        return 0
    try:
        since = datetime.fromisoformat(state["pendingSince"])
        elapsed = (current - since).total_seconds()
    except (ValueError, TypeError):
        elapsed = -1
    if elapsed < 0:
        _write_state(cache, key, {"previous": previous, "pending": condition, "pendingSince": current.isoformat()})
        return 0
    if elapsed < STABLE_SECONDS:
        return 0
    identity = f"{local.date()}:{state['pendingSince']}:{previous}:{condition}"
    event_id = "quote-source:" + hashlib.sha256(identity.encode()).hexdigest()
    counts = diagnostics.get("activeSourceCounts") or {}
    sources = [source for source in SOURCE_NAMES if int(counts.get(source, 0)) > 0]
    event_type, title, message = _event(previous, condition, sources)
    payload = json.dumps({"from": previous, "to": condition, "sources": sources,
                          "pendingSince": state["pendingSince"]}, ensure_ascii=False)
    created = 0
    with session_factory() as db:
        users = list(db.scalars(select(DayTradeV2Setting.user_id).join(
            DayTradeV2RuntimeState, DayTradeV2RuntimeState.user_id == DayTradeV2Setting.user_id,
        ).where(DayTradeV2Setting.trade_mode == "PAPER", DayTradeV2RuntimeState.mode == "PAPER",
                DayTradeV2RuntimeState.trading_date == local.date(),
                DayTradeV2RuntimeState.status.in_(["RUNNING", "PAUSED"]))).unique())
        dialect = db.get_bind().dialect.name
        if dialect not in {"postgresql", "sqlite"}:
            raise RuntimeError("Unsupported source-event database dialect")
        insert = pg_insert if dialect == "postgresql" else sqlite_insert
        if not _is_current_leader():
            db.rollback()
            return 0
        for user_id in users:
            result = db.execute(insert(DayTradeV2Notification).values(
                user_id=user_id, event_id=event_id, mode="PAPER", event_type=event_type,
                title=title, message=message, payload_json=payload, created_at=current,
                read=False, email_sent=False,
            ).on_conflict_do_nothing(index_elements=["user_id", "event_id"]))
            created += max(0, result.rowcount)
        if not _is_current_leader():
            db.rollback()
            return 0
        db.commit()
    # A crash/cache outage here replays the SAME pendingSince event ID; the
    # per-user unique constraint suppresses duplicates before state advances.
    _write_state(cache, key, {"previous": condition, "pending": None, "pendingSince": None})
    return created
