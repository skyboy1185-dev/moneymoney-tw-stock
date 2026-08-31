from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from ..models import DayTradingCandidateSnapshot
from .day_trading_schedule import (
    TradingScheduleConfig,
    recommendation_qualification,
    trading_session_state,
)


CANDIDATE_SNAPSHOT_LIMIT = 20


def _as_number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _as_datetime(value: Any, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            parsed = fallback
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _trading_date(config: TradingScheduleConfig, value: datetime) -> date:
    return value.astimezone(ZoneInfo(config.timezone)).date()


def _payload(row: DayTradingCandidateSnapshot) -> dict[str, Any]:
    try:
        payload = json.loads(row.payload_json)
    except (TypeError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return {
        **payload,
        "id": row.signal_id,
        "symbol": row.symbol,
        "stockName": row.stock_name,
        "market": row.market,
        "direction": row.direction,
        "rank": row.rank,
        "snapshotAt": row.snapshot_at.isoformat(),
        "originalOfficialRecommendation": bool(row.is_official_recommendation),
    }


def save_candidate_snapshots(
    db: Session,
    candidates: list[dict[str, Any]],
    *,
    config: TradingScheduleConfig,
    snapshot_at: datetime,
    limit: int = CANDIDATE_SNAPSHOT_LIMIT,
) -> int:
    """Persist the ranked Top-N candidate list from one automation scan."""
    raw_current = snapshot_at.astimezone(UTC) if snapshot_at.tzinfo else snapshot_at.replace(tzinfo=UTC)
    current = raw_current.replace(second=0, microsecond=0)
    trading_date = _trading_date(config, current)
    if db.scalar(select(DayTradingCandidateSnapshot.id).where(
        DayTradingCandidateSnapshot.trading_date == trading_date,
        DayTradingCandidateSnapshot.snapshot_at == current,
    ).limit(1)):
        return 0
    saved = 0
    for fallback_rank, candidate in enumerate(candidates[:limit], start=1):
        signal_id = str(candidate.get("id") or "")
        if not signal_id:
            continue
        rank = int(candidate.get("rank") or fallback_rank)
        payload = {
            **candidate,
            "rank": rank,
            "snapshotAt": current.isoformat(),
            "originalOfficialRecommendation": bool(candidate.get("isOfficialRecommendation")),
        }
        db.add(DayTradingCandidateSnapshot(
            signal_id=signal_id[:100],
            trading_date=trading_date,
            snapshot_at=current,
            symbol=str(candidate.get("symbol") or "")[:12],
            stock_name=str(candidate.get("stockName") or "")[:80],
            market=str(candidate.get("market") or "")[:20],
            direction=str(candidate.get("direction") or "")[:12],
            rank=rank,
            is_official_recommendation=bool(candidate.get("isOfficialRecommendation")),
            confidence_score=_as_number(candidate.get("confidenceScore")),
            health_score=_as_number(candidate.get("healthScore")),
            confirmation_score=_as_number(candidate.get("confirmationScore")),
            large_order_force=_as_number(candidate.get("largeOrderForce")),
            risk_reward_ratio=_as_number(candidate.get("riskRewardRatio")),
            liquidity_score=_as_number(candidate.get("liquidityScore")),
            payload_json=json.dumps(payload, ensure_ascii=False, default=str),
        ))
        saved += 1
    if not saved:
        return 0
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return 0
    except SQLAlchemyError:
        db.rollback()
        return 0
    return saved


def replay_candidate_snapshots(
    db: Session,
    config: TradingScheduleConfig,
    *,
    trading_date: date,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Replay today's saved candidates with the latest official-entry rules."""
    rows = db.scalars(
        select(DayTradingCandidateSnapshot)
        .where(DayTradingCandidateSnapshot.trading_date == trading_date)
        .order_by(DayTradingCandidateSnapshot.snapshot_at.desc(), DayTradingCandidateSnapshot.rank.asc())
        .limit(max(limit * 4, limit))
    ).all()
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if row.signal_id in seen:
            continue
        seen.add(row.signal_id)
        payload = _payload(row)
        snapshot_at = _as_datetime(payload.get("snapshotAt"), row.snapshot_at)
        session = trading_session_state(
            config,
            snapshot_at,
            data_status=str(payload.get("dataStatus", "normal")),
            quote_samples=int(_as_number(payload.get("liveSampleCount", payload.get("quoteSamples", 10)))),
            infrastructure_ok=True,
        )
        passed, failures = recommendation_qualification(payload, config, session, snapshot_at)
        items.append({
            **payload,
            "snapshotAt": snapshot_at.isoformat(),
            "wouldBeOfficialRecommendation": passed,
            "replayFailures": failures,
            "qualificationFailures": failures,
        })
        if len(items) >= limit:
            break
    return items
