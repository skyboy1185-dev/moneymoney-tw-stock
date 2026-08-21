from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import RocketCandidate, RocketNotification
from ..services.rocket_automation import rocket_radar_automation
from ..services.rocket_service import (
    backtest_payload, candidate_payload, dashboard_payload, notification_payload,
)
from ..services.rocket_trading import ensure_rocket_account


router = APIRouter(prefix="/rocket-radar", tags=["rocket-radar"])
TAIPEI = ZoneInfo("Asia/Taipei")


class RocketSettingsUpdate(BaseModel):
    broker_fee_discount: float = Field(ge=0, le=1)
    slippage_rate: float = Field(ge=0, le=.02)
    sound_enabled: bool


@router.get("/status")
def status() -> dict[str, object]:
    return rocket_radar_automation.state


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db)) -> dict[str, object]:
    return dashboard_payload(db)


@router.get("/candidate/{stock_code}")
def candidate(stock_code: str, db: Session = Depends(get_db)) -> dict[str, object]:
    item = db.scalar(select(RocketCandidate).where(
        RocketCandidate.stock_code == stock_code,
    ).order_by(RocketCandidate.trade_date.desc()).limit(1))
    if item is None:
        raise HTTPException(status_code=404, detail="股票目前不在飆股觀察池")
    return candidate_payload(item)


@router.get("/events")
def events(
    afterId: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    items = list(db.scalars(select(RocketNotification).where(
        RocketNotification.id > afterId,
    ).order_by(RocketNotification.priority, RocketNotification.id).limit(limit)).all())
    return {
        "items": [notification_payload(item) for item in items],
        "lastEventId": max((item.id for item in items), default=afterId),
    }


@router.get("/notifications")
def notifications(
    period: Literal["today", "3d", "7d", "30d", "all"] = "today",
    type: str = Query(default="", max_length=30),
    limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    now = datetime.now(UTC)
    days = {"today": 1, "3d": 3, "7d": 7, "30d": 30, "all": None}[period]
    query = select(RocketNotification)
    if period == "today":
        start = now.astimezone(TAIPEI).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
        query = query.where(RocketNotification.created_at >= start)
    elif days is not None:
        query = query.where(RocketNotification.created_at >= now - timedelta(days=days))
    if type:
        query = query.where(RocketNotification.notification_type == type)
    items = list(db.scalars(query.order_by(RocketNotification.created_at.desc()).limit(limit)).all())
    unread = db.scalar(select(func.count(RocketNotification.id)).where(RocketNotification.is_read.is_(False))) or 0
    return {"items": [notification_payload(item) for item in items], "unreadCount": unread}


@router.get("/notifications/unread")
def unread(db: Session = Depends(get_db)) -> dict[str, int]:
    count = db.scalar(select(func.count(RocketNotification.id)).where(RocketNotification.is_read.is_(False))) or 0
    return {"count": count}


@router.post("/notifications/{notification_id}/read")
def mark_read(notification_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    item = db.get(RocketNotification, notification_id)
    if item is None:
        raise HTTPException(status_code=404, detail="通知不存在")
    if not item.is_read:
        item.is_read = True; item.read_at = datetime.now(UTC); db.commit()
    return {"notificationId": item.id, "isRead": True}


@router.put("/settings")
def update_settings(body: RocketSettingsUpdate, db: Session = Depends(get_db)) -> dict[str, object]:
    now = datetime.now(UTC)
    account = ensure_rocket_account(db, now)
    account.broker_fee_discount = Decimal(str(body.broker_fee_discount))
    account.slippage_rate = Decimal(str(body.slippage_rate))
    account.sound_enabled = body.sound_enabled
    account.updated_at = now
    db.commit()
    return {
        "brokerFeeDiscount": float(account.broker_fee_discount),
        "slippageRate": float(account.slippage_rate), "soundEnabled": account.sound_enabled,
    }


@router.get("/backtest")
def backtest(
    period: Literal["1m", "3m", "6m", "1y", "2y", "all"] = "3m",
    db: Session = Depends(get_db),
) -> dict[str, object]:
    return backtest_payload(db, period)
