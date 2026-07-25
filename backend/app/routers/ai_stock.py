from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import AIStockAddOn, AIStockAlert, AIStockMonitor, AIStockPosition
from ..schemas import (
    AIConfirmAddOn,
    AIConfirmEntry,
    AIPartialExitCreate,
    AIPositionClose,
    AIPositionUpdate,
    AIRecommendationSync,
    PortfolioSettingsUpdate,
)
from ..services.ai_stock_service import (
    ACTIVE_MONITOR_STATUSES,
    ACTIVE_POSITION_STATUSES,
    add_on_payload,
    allocation_summary,
    close_position,
    confirm_add_on,
    confirm_entry,
    get_portfolio_settings,
    monitor_payload,
    partial_exit,
    position_payload,
    settings_payload,
    sync_recommendations,
    update_portfolio_settings,
)
from ..services.day_trading_cache import day_trading_cache


router = APIRouter(tags=["ai-stock"])


def _user_id(x_user_id: str = Header(min_length=8, max_length=80)) -> str:
    return x_user_id


def _not_found(message: str) -> HTTPException:
    return HTTPException(status_code=404, detail=message)


@router.get("/portfolio/settings")
def get_settings(user_id: str = Depends(_user_id), db: Session = Depends(get_db)) -> dict:
    return settings_payload(get_portfolio_settings(db, user_id))


@router.put("/portfolio/settings")
def put_settings(
    body: PortfolioSettingsUpdate,
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return settings_payload(update_portfolio_settings(db, user_id, body))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/portfolio/allocation")
def get_allocation(user_id: str = Depends(_user_id), db: Session = Depends(get_db)) -> dict:
    payload = allocation_summary(db, user_id)
    payload["cacheMode"] = day_trading_cache.mode
    payload["cacheHealthy"] = day_trading_cache.healthy
    return payload


@router.get("/ai-stock-dashboard")
def get_dashboard(user_id: str = Depends(_user_id), db: Session = Depends(get_db)) -> dict:
    monitors = db.scalars(select(AIStockMonitor).where(
        AIStockMonitor.user_id == user_id,
    ).order_by(AIStockMonitor.updated_at.desc())).all()
    positions = db.scalars(select(AIStockPosition).where(
        AIStockPosition.user_id == user_id,
    ).order_by(AIStockPosition.updated_at.desc())).all()
    alerts = db.scalars(select(AIStockAlert).where(
        AIStockAlert.user_id == user_id,
    ).order_by(AIStockAlert.created_at.desc()).limit(50)).all()
    monitor_map = {item.id: item for item in monitors}
    return {
        "settings": settings_payload(get_portfolio_settings(db, user_id)),
        "allocation": {**allocation_summary(db, user_id), "cacheMode": day_trading_cache.mode, "cacheHealthy": day_trading_cache.healthy},
        "waiting": [monitor_payload(item) for item in monitors if item.monitor_status in ACTIVE_MONITOR_STATUSES],
        "positions": [
            position_payload(item, monitor_map.get(item.monitor_id))
            for item in positions if item.position_status in ACTIVE_POSITION_STATUSES
        ],
        "ended": [
            position_payload(item, monitor_map.get(item.monitor_id))
            for item in positions if item.position_status == "closed"
        ],
        "alerts": [{
            "id": item.id, "monitorId": item.monitor_id, "positionId": item.position_id,
            "signalId": item.signal_id, "alertType": item.alert_type,
            "alertLevel": item.alert_level, "action": item.action,
            "price": float(item.price), "reason": item.reason,
            "linePushStatus": item.line_push_status,
            "readAt": item.read_at.isoformat() if item.read_at else None,
            "createdAt": item.created_at.isoformat(),
        } for item in alerts],
        "updatedAt": datetime.now(UTC).isoformat(),
        "disclaimer": "僅供研究參考，不構成投資建議。",
    }


@router.get("/ai-stock-monitor")
def list_monitors(user_id: str = Depends(_user_id), db: Session = Depends(get_db)) -> dict:
    items = db.scalars(select(AIStockMonitor).where(
        AIStockMonitor.user_id == user_id,
    ).order_by(AIStockMonitor.total_score.desc())).all()
    return {"items": [monitor_payload(item) for item in items]}


@router.post("/ai-stock-monitor")
@router.post("/ai-stock-monitor/sync")
def sync_monitors(
    body: AIRecommendationSync,
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> dict:
    items = sync_recommendations(db, user_id, body.items)
    return {"items": [monitor_payload(item) for item in items], "count": len(items)}


@router.post("/ai-stock-monitor/{monitor_id}/confirm-entry", status_code=201)
def confirm_monitor_entry(
    monitor_id: int,
    body: AIConfirmEntry,
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> dict:
    try:
        item = confirm_entry(
            db, user_id, monitor_id,
            entry_price=body.actual_entry_price, quantity=body.quantity,
            entry_time=body.entry_time, custom_stop_loss=body.custom_stop_loss,
            line_exit_notifications=body.line_exit_notifications,
            add_on_enabled=body.add_on_enabled,
        )
        return position_payload(item, db.get(AIStockMonitor, item.monitor_id))
    except LookupError as error:
        raise _not_found(str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/ai-stock-monitor/{monitor_id}/ignore")
def ignore_monitor(
    monitor_id: int,
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> dict:
    item = db.scalar(select(AIStockMonitor).where(
        AIStockMonitor.id == monitor_id, AIStockMonitor.user_id == user_id,
    ))
    if item is None:
        raise _not_found("AI監控項目不存在")
    item.monitor_status = "ignored"
    item.updated_at = datetime.now(UTC)
    db.commit()
    return monitor_payload(item)


@router.post("/ai-stock-monitor/{monitor_id}/continue-monitoring")
def continue_monitor(
    monitor_id: int,
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> dict:
    item = db.scalar(select(AIStockMonitor).where(
        AIStockMonitor.id == monitor_id, AIStockMonitor.user_id == user_id,
    ))
    if item is None:
        raise _not_found("AI監控項目不存在")
    if item.monitor_status in {"position", "ended", "removed"}:
        raise HTTPException(status_code=409, detail="目前狀態不可切回等待進場")
    item.monitor_status = "monitoring"
    item.updated_at = datetime.now(UTC)
    db.commit()
    return monitor_payload(item)


@router.delete("/ai-stock-monitor/{monitor_id}", status_code=204)
def delete_monitor(
    monitor_id: int,
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> Response:
    item = db.scalar(select(AIStockMonitor).where(
        AIStockMonitor.id == monitor_id, AIStockMonitor.user_id == user_id,
    ))
    if item is None:
        raise _not_found("AI監控項目不存在")
    position = db.scalar(select(AIStockPosition.id).where(
        AIStockPosition.monitor_id == item.id,
        AIStockPosition.position_status.in_(ACTIVE_POSITION_STATUSES),
    ))
    if position is not None:
        raise HTTPException(status_code=409, detail="此股票仍有未結束持倉，不可移除監控")
    item.monitor_status = "removed"
    item.updated_at = datetime.now(UTC)
    db.commit()
    return Response(status_code=204)


@router.get("/ai-stock-positions")
def list_positions(user_id: str = Depends(_user_id), db: Session = Depends(get_db)) -> dict:
    items = db.scalars(select(AIStockPosition).where(
        AIStockPosition.user_id == user_id,
    ).order_by(AIStockPosition.updated_at.desc())).all()
    monitor_ids = {item.monitor_id for item in items}
    monitors = {
        item.id: item for item in db.scalars(
            select(AIStockMonitor).where(AIStockMonitor.id.in_(monitor_ids))
        ).all()
    } if monitor_ids else {}
    return {"items": [
        position_payload(item, monitors.get(item.monitor_id)) for item in items
    ]}


@router.get("/ai-stock-positions/{position_id}")
def get_position(
    position_id: int,
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> dict:
    item = db.scalar(select(AIStockPosition).where(
        AIStockPosition.id == position_id, AIStockPosition.user_id == user_id,
    ))
    if item is None:
        raise _not_found("持倉不存在")
    return position_payload(item, db.get(AIStockMonitor, item.monitor_id))


@router.patch("/ai-stock-positions/{position_id}")
def patch_position(
    position_id: int,
    body: AIPositionUpdate,
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> dict:
    item = db.scalar(select(AIStockPosition).where(
        AIStockPosition.id == position_id, AIStockPosition.user_id == user_id,
    ))
    if item is None:
        raise _not_found("持倉不存在")
    values = body.model_dump(exclude_none=True)
    if "stop_loss" in values and values["stop_loss"] < item.stop_loss:
        raise HTTPException(status_code=400, detail="持倉停損不可向下放寬")
    if (
        "trailing_stop" in values
        and item.trailing_stop is not None
        and values["trailing_stop"] < item.trailing_stop
    ):
        raise HTTPException(status_code=400, detail="移動停利只能向上調整")
    for key, value in values.items():
        setattr(item, key, value)
    item.updated_at = datetime.now(UTC)
    db.commit()
    return position_payload(item, db.get(AIStockMonitor, item.monitor_id))


@router.post("/ai-stock-positions/{position_id}/calculate-allocation")
def calculate_position_allocation_endpoint(
    position_id: int,
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> dict:
    if db.scalar(select(AIStockPosition.id).where(
        AIStockPosition.id == position_id, AIStockPosition.user_id == user_id,
    )) is None:
        raise _not_found("持倉不存在")
    return allocation_summary(db, user_id)


@router.get("/ai-stock-positions/{position_id}/add-ons")
def get_add_ons(
    position_id: int,
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> dict:
    position = db.scalar(select(AIStockPosition.id).where(
        AIStockPosition.id == position_id, AIStockPosition.user_id == user_id,
    ))
    if position is None:
        raise _not_found("持倉不存在")
    items = db.scalars(select(AIStockAddOn).where(
        AIStockAddOn.position_id == position_id,
    ).order_by(AIStockAddOn.add_on_number)).all()
    return {"items": [add_on_payload(item) for item in items]}


@router.post("/ai-stock-positions/{position_id}/confirm-add-on")
def confirm_position_add_on(
    position_id: int,
    body: AIConfirmAddOn,
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> dict:
    try:
        item = confirm_add_on(
            db, user_id, position_id, actual_price=body.actual_price,
            actual_quantity=body.actual_quantity, add_on_time=body.add_on_time,
            fee=body.fee,
            accept_new_stop_loss=body.accept_new_stop_loss,
        )
        return position_payload(item, db.get(AIStockMonitor, item.monitor_id))
    except LookupError as error:
        raise _not_found(str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/ai-stock-positions/{position_id}/decline-add-on")
def decline_add_on(
    position_id: int,
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> dict:
    item = db.scalar(select(AIStockAddOn).join(AIStockPosition).where(
        AIStockAddOn.position_id == position_id,
        AIStockAddOn.status == "suggested",
        AIStockPosition.user_id == user_id,
    ).order_by(AIStockAddOn.add_on_number).limit(1))
    if item is None:
        raise _not_found("待處理加碼建議不存在")
    item.status = "declined"
    position = db.get(AIStockPosition, item.position_id)
    if position:
        position.latest_action = "持有中"
        position.position_status = "holding"
        position.updated_at = datetime.now(UTC)
    db.commit()
    return add_on_payload(item)


@router.post("/ai-stock-positions/{position_id}/disable-add-on")
def disable_add_on(
    position_id: int,
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> dict:
    item = db.scalar(select(AIStockPosition).where(
        AIStockPosition.id == position_id, AIStockPosition.user_id == user_id,
    ))
    if item is None:
        raise _not_found("持倉不存在")
    item.add_on_enabled = False
    item.latest_action = "禁止加碼"
    item.updated_at = datetime.now(UTC)
    db.commit()
    return position_payload(item, db.get(AIStockMonitor, item.monitor_id))


@router.post("/ai-stock-positions/{position_id}/partial-exit")
def confirm_partial_exit(
    position_id: int,
    body: AIPartialExitCreate,
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> dict:
    try:
        item = partial_exit(
            db, user_id, position_id, quantity=body.quantity,
            exit_price=body.exit_price, exit_time=body.exit_time,
            fee=body.fee, tax=body.tax,
        )
        return position_payload(item, db.get(AIStockMonitor, item.monitor_id))
    except LookupError as error:
        raise _not_found(str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/ai-stock-positions/{position_id}/close")
def confirm_close(
    position_id: int,
    body: AIPositionClose,
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> dict:
    try:
        item = close_position(
            db, user_id, position_id, quantity=body.quantity,
            exit_price=body.exit_price, exit_time=body.exit_time,
            fee=body.fee, tax=body.tax, reason=body.reason,
        )
        return position_payload(item, db.get(AIStockMonitor, item.monitor_id))
    except LookupError as error:
        raise _not_found(str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/ai-stock-positions/{position_id}/continue-monitoring")
def continue_monitoring(
    position_id: int,
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> dict:
    item = db.scalar(select(AIStockPosition).where(
        AIStockPosition.id == position_id, AIStockPosition.user_id == user_id,
    ))
    if item is None:
        raise _not_found("持倉不存在")
    if item.position_status == "closed":
        raise HTTPException(status_code=409, detail="已全部賣出的持倉不可恢復")
    item.position_status = "holding"
    item.latest_action = "持有中"
    item.updated_at = datetime.now(UTC)
    db.commit()
    return position_payload(item, db.get(AIStockMonitor, item.monitor_id))


@router.get("/ai-stock-alerts")
def list_alerts(user_id: str = Depends(_user_id), db: Session = Depends(get_db)) -> dict:
    items = db.scalars(select(AIStockAlert).where(
        AIStockAlert.user_id == user_id,
    ).order_by(AIStockAlert.created_at.desc()).limit(100)).all()
    return {"items": [{
        "id": item.id, "signalId": item.signal_id, "symbol": None,
        "alertType": item.alert_type, "alertLevel": item.alert_level,
        "action": item.action, "price": float(item.price), "reason": item.reason,
        "linePushStatus": item.line_push_status,
        "readAt": item.read_at.isoformat() if item.read_at else None,
        "createdAt": item.created_at.isoformat(),
    } for item in items]}


@router.patch("/ai-stock-alerts/{alert_id}/read")
def read_alert(
    alert_id: int,
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> dict:
    item = db.scalar(select(AIStockAlert).where(
        AIStockAlert.id == alert_id, AIStockAlert.user_id == user_id,
    ))
    if item is None:
        raise _not_found("通知不存在")
    item.read_at = datetime.now(UTC)
    db.commit()
    return {"read": True, "readAt": item.read_at.isoformat()}
