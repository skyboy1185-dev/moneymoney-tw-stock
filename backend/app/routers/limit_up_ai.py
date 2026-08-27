from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..services.limit_up_ai import (
    dashboard_payload,
    ensure_limit_up_settings,
    limit_up_performance_payload,
    list_limit_up_notifications,
    mark_all_limit_up_notifications_read,
    mark_limit_up_notification_read,
    replay_today,
    run_limit_up_cycle,
    settings_payload,
    unread_limit_up_notification_count,
)


router = APIRouter(prefix="/limit-up-ai", tags=["limit-up-ai"])


def _user_id(x_user_id: str | None = Header(default=None, min_length=3, max_length=80)) -> str:
    return x_user_id or "demo-user"


class LimitUpAiSettingsUpdate(BaseModel):
    capital: float = Field(gt=0)
    minPrice: float = Field(ge=1)
    maxPrice: float = Field(gt=1)
    minAverageTurnover20d: float = Field(ge=0)
    minVolumeRatio20d: float = Field(ge=0)
    firstPositionPct: float = Field(gt=0, le=1)
    maxPositionPct: float = Field(gt=0, le=1)
    maxPositions: int = Field(ge=1, le=10)
    maxLossPerTradePct: float = Field(gt=0, le=.2)
    maxDailyLossPct: float = Field(gt=0, le=.5)
    maxConsecutiveStops: int = Field(ge=1, le=10)
    overnightTotalPct: float = Field(ge=0, le=1)
    overnightSinglePct: float = Field(ge=0, le=1)
    excludeLockedLimitUp: bool = True
    soundEnabled: bool = False


@router.get("/dashboard")
def dashboard(
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return run_limit_up_cycle(db, user_id)


@router.get("/candidates")
def candidates(
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    payload = run_limit_up_cycle(db, user_id)
    return {
        "items": payload["candidates"],
        "nearEntries": payload["nearEntries"],
        "watchlist": payload["watchlist"],
        "updatedAt": payload["updatedAt"],
    }


@router.get("/positions")
def positions(
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return {"items": dashboard_payload(db, user_id)["positions"]}


@router.get("/trades")
def trades(
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return {"items": dashboard_payload(db, user_id)["trades"]}


@router.get("/performance")
def performance(
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return limit_up_performance_payload(db, user_id)


@router.get("/notifications")
def notifications(
    limit: int = Query(default=80, ge=1, le=200),
    notification_type: str | None = Query(default=None, alias="type"),
    unread_only: bool = Query(default=False, alias="unreadOnly"),
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return list_limit_up_notifications(
        db,
        user_id,
        limit=limit,
        notification_type=notification_type or None,
        unread_only=unread_only,
    )


@router.get("/notifications/unread")
def unread_notifications(
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    return {"count": unread_limit_up_notification_count(db, user_id)}


@router.post("/notifications/{notification_id}/read")
def read_notification(
    notification_id: int,
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if not mark_limit_up_notification_read(db, user_id, notification_id):
        raise HTTPException(status_code=404, detail="notification not found")
    return {"status": "read", "id": notification_id}


@router.post("/notifications/read-all")
def read_all_notifications(
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return {"status": "read", "count": mark_all_limit_up_notifications_read(db, user_id)}


@router.get("/settings")
def get_settings(
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    item = ensure_limit_up_settings(db, user_id)
    db.commit()
    return settings_payload(item)


@router.put("/settings")
def update_settings(
    body: LimitUpAiSettingsUpdate,
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    item = ensure_limit_up_settings(db, user_id)
    item.capital = body.capital
    item.min_price = body.minPrice
    item.max_price = body.maxPrice
    item.min_average_turnover_20d = body.minAverageTurnover20d
    item.min_volume_ratio_20d = body.minVolumeRatio20d
    item.first_position_pct = body.firstPositionPct
    item.max_position_pct = body.maxPositionPct
    item.max_positions = body.maxPositions
    item.max_loss_per_trade_pct = body.maxLossPerTradePct
    item.max_daily_loss_pct = body.maxDailyLossPct
    item.max_consecutive_stops = body.maxConsecutiveStops
    item.overnight_total_pct = body.overnightTotalPct
    item.overnight_single_pct = body.overnightSinglePct
    item.exclude_locked_limit_up = body.excludeLockedLimitUp
    item.sound_enabled = body.soundEnabled
    item.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(item)
    return settings_payload(item)


@router.get("/replay/today")
def replay(
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return replay_today(db, user_id)
