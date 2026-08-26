from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
import json

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..adaptive_schemas import (
    AdaptiveBacktestRequest,
    AdaptiveMonitorCreate,
    AdaptiveParameterBatchUpdate,
)
from ..config import get_settings
from ..database import get_db
from ..models import (
    AdaptiveSignal,
    AdaptiveStockCandidate,
    AdaptiveStockMonitoring,
    ElectronicIndustryStrength,
    MarketRegime,
    StrategyParameter,
    SuperAIDaytradeNotification,
)
from ..services.adaptive_backtest_service import run_backtest
from ..services.adaptive_electronic_automation import adaptive_electronic_automation
from ..services.adaptive_electronic_service import (
    AUTOMATION_USER_ID,
    STATUS_LABELS,
    candidate_payload,
    regime_payload,
)
from ..services.adaptive_parameters import ensure_default_parameters
from ..services.adaptive_performance_service import performance_payload
from ..services.gmail_messaging import gmail_notification_dispatcher
from ..services.super_ai_daytrade_service import (
    SYSTEM_NAME,
    ai_score,
    cap_stop_distance,
    ensure_settings as ensure_super_ai_settings,
    levels_for_side,
    market_state,
    max_stop_distance_pct,
    notification_payload,
    risk_status,
    risk_reward,
    settings_payload,
    stop_distance_pct,
    trade_side_for,
    update_settings as update_super_ai_settings,
)


router = APIRouter(prefix="/adaptive-electronic", tags=["adaptive-electronic"])


def _user_id(x_user_id: str = Header(min_length=8, max_length=80)) -> str:
    return x_user_id


def _admin(x_admin_token: str = Header(min_length=16, max_length=200)) -> None:
    configured = get_settings().adaptive_electronic_admin_token
    if not configured:
        raise HTTPException(status_code=503, detail="管理員功能尚未設定安全權杖")
    if x_admin_token != configured:
        raise HTTPException(status_code=403, detail="管理員權限驗證失敗")


def _latest_trade_date(db: Session):
    item = db.scalar(select(MarketRegime).order_by(MarketRegime.trade_date.desc()).limit(1))
    return item.trade_date if item else None


def _super_ai_candidate_payload(
    item: AdaptiveStockCandidate,
    *,
    settings,
    regime: str,
) -> dict:
    payload = candidate_payload(item)
    side = trade_side_for(regime, item)
    entry, stop, _tp1, tp2 = levels_for_side(item, side)
    score = ai_score(item, regime, side)
    max_stop_pct = max_stop_distance_pct(side, score, Decimal(settings.max_stop_distance_pct))
    stop, capped = cap_stop_distance(entry, stop, side, max_stop_pct)
    payload["stopLossPrice"] = float(stop)
    payload["stopDistancePct"] = round(float(stop_distance_pct(entry, stop)), 2)
    payload["riskReward"] = float(risk_reward(entry, stop, tp2, side))
    payload["maxStopDistancePct"] = float(max_stop_pct)
    payload["stopDistanceCapped"] = capped
    return payload


@router.get("/status")
def automation_status(db: Session = Depends(get_db)) -> dict:
    settings = ensure_super_ai_settings(db)
    regime = db.scalar(select(MarketRegime).where(MarketRegime.is_current.is_(True)).order_by(MarketRegime.trade_date.desc()).limit(1))
    state = adaptive_electronic_automation.state
    state.update({
        "systemName": SYSTEM_NAME,
        "settings": settings_payload(settings),
        "marketState": market_state(regime.regime if regime else "UNCERTAIN"),
        "risk": risk_status(db, settings, datetime.now(UTC)),
    })
    return state


@router.get("/settings")
def super_ai_settings(db: Session = Depends(get_db)) -> dict:
    return settings_payload(ensure_super_ai_settings(db))


@router.put("/settings")
def update_super_ai_settings_endpoint(
    values: dict,
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> dict:
    row = update_super_ai_settings(db, values, user_id, datetime.now(UTC))
    db.commit()
    return settings_payload(row)


@router.get("/market-regime")
def market_regime(db: Session = Depends(get_db)) -> dict:
    item = db.scalar(select(MarketRegime).where(MarketRegime.is_current.is_(True)).order_by(MarketRegime.trade_date.desc()).limit(1))
    if item is None:
        return {
            "regime": "UNCERTAIN", "regimeLabel": "盤勢不明・等待正式資料",
            "confidence": 0, "activeStrategy": "暫不啟用進場策略",
            "exposureMin": 20, "exposureMax": 40,
            "reasons": ["尚未完成首次官方市場資料掃描"],
            "missingFields": ["market_scan"], "updatedAt": None,
        }
    return regime_payload(item)


@router.get("/candidates")
def candidates(
    strategy: str = Query(default="", pattern=r"^(|CRASH|RECOVERY|RANGE|BREAKOUT)$"),
    minimumScore: float = Query(default=0, ge=0, le=100),
    industry: str = Query(default="", max_length=80),
    status: str = Query(default="", max_length=40),
    db: Session = Depends(get_db),
) -> dict:
    trade_date = _latest_trade_date(db)
    if trade_date is None:
        return {"tradeDate": None, "items": [], "message": "目前沒有適合進場的電子股"}
    query = select(AdaptiveStockCandidate).where(
        AdaptiveStockCandidate.trade_date == trade_date,
        AdaptiveStockCandidate.total_score >= Decimal(str(minimumScore)),
    )
    if strategy: query = query.where(AdaptiveStockCandidate.strategy_type == strategy)
    if industry: query = query.where(AdaptiveStockCandidate.sub_industry == industry)
    if status: query = query.where(AdaptiveStockCandidate.candidate_status == status)
    items = list(db.scalars(query.order_by(AdaptiveStockCandidate.rank)).all())
    settings = ensure_super_ai_settings(db)
    regime = db.scalar(select(MarketRegime).where(MarketRegime.is_current.is_(True)).order_by(MarketRegime.trade_date.desc()).limit(1))
    regime_key = regime.regime if regime is not None else "UNCERTAIN"
    return {
        "tradeDate": trade_date.isoformat(),
        "items": [_super_ai_candidate_payload(item, settings=settings, regime=regime_key) for item in items],
        "message": None if items else "目前沒有適合進場的電子股",
        "updatedAt": max((item.updated_at for item in items), default=None),
    }


@router.get("/industries")
def industries(db: Session = Depends(get_db)) -> dict:
    trade_date = _latest_trade_date(db)
    if trade_date is None:
        return {"tradeDate": None, "items": []}
    items = db.scalars(select(ElectronicIndustryStrength).where(
        ElectronicIndustryStrength.trade_date == trade_date,
    ).order_by(ElectronicIndustryStrength.strength_rank)).all()
    return {"tradeDate": trade_date.isoformat(), "items": [{
        "subIndustry": item.sub_industry, "strengthScore": float(item.strength_score),
        "strengthRank": item.strength_rank, "return1d": float(item.return_1d) if item.return_1d is not None else None,
        "return3d": float(item.return_3d) if item.return_3d is not None else None,
        "return5d": float(item.return_5d) if item.return_5d is not None else None,
        "return20d": float(item.return_20d) if item.return_20d is not None else None,
        "advanceRatio": float(item.advance_ratio) if item.advance_ratio is not None else None,
        "newHighRatio": float(item.new_high_ratio) if item.new_high_ratio is not None else None,
        "volumeGrowth": float(item.volume_growth) if item.volume_growth is not None else None,
        "continuationDays": item.continuation_days,
        "scoreBreakdown": json.loads(item.score_breakdown_json),
    } for item in items]}


@router.get("/stock/{stock_code}")
def stock_detail(stock_code: str, db: Session = Depends(get_db)) -> dict:
    item = db.scalar(select(AdaptiveStockCandidate).where(
        AdaptiveStockCandidate.stock_code == stock_code,
    ).order_by(AdaptiveStockCandidate.trade_date.desc()).limit(1))
    if item is None:
        raise HTTPException(status_code=404, detail="此股票目前不在電子股候選清單")
    return candidate_payload(item)


@router.post("/monitor", status_code=201)
def add_monitor(
    body: AdaptiveMonitorCreate,
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> dict:
    candidate = db.scalar(select(AdaptiveStockCandidate).where(
        AdaptiveStockCandidate.stock_code == body.stock_code,
    ).order_by(AdaptiveStockCandidate.trade_date.desc()).limit(1))
    if candidate is None:
        raise HTTPException(status_code=404, detail="股票不在目前候選清單")
    item = db.scalar(select(AdaptiveStockMonitoring).where(
        AdaptiveStockMonitoring.user_id == user_id,
        AdaptiveStockMonitoring.stock_code == body.stock_code,
    ))
    now = datetime.now(UTC)
    if item is None:
        item = AdaptiveStockMonitoring(
            user_id=user_id, stock_code=candidate.stock_code, stock_name=candidate.stock_name,
            strategy_type=candidate.strategy_type, added_date=candidate.trade_date,
            trigger_price=candidate.breakout_price, entry_price=None,
            stop_loss_price=candidate.stop_loss_price, target_price_1=candidate.target_price_1,
            target_price_2=candidate.target_price_2, allocation_percent=candidate.allocation_percent,
            health_score=candidate.health_score, monitor_status="monitoring", updated_at=now,
        )
        db.add(item)
    else:
        item.monitor_status = "monitoring"
        item.removed_reason = None
        item.updated_at = now
    db.commit(); db.refresh(item)
    return {"status": "monitoring", "stockCode": item.stock_code, "stockName": item.stock_name}


@router.delete("/monitor/{stock_code}", status_code=204)
def delete_monitor(
    stock_code: str,
    user_id: str = Depends(_user_id),
    db: Session = Depends(get_db),
) -> Response:
    item = db.scalar(select(AdaptiveStockMonitoring).where(
        AdaptiveStockMonitoring.user_id == user_id,
        AdaptiveStockMonitoring.stock_code == stock_code,
        AdaptiveStockMonitoring.monitor_status == "monitoring",
    ))
    if item is None:
        raise HTTPException(status_code=404, detail="監控股票不存在")
    item.monitor_status = "removed"; item.removed_reason = "使用者手動移除"; item.updated_at = datetime.now(UTC)
    db.commit()
    return Response(status_code=204)


@router.get("/monitoring")
def monitoring(user_id: str = Depends(_user_id), db: Session = Depends(get_db)) -> dict:
    items = db.scalars(select(AdaptiveStockMonitoring).where(
        AdaptiveStockMonitoring.user_id.in_([user_id, AUTOMATION_USER_ID]),
        AdaptiveStockMonitoring.monitor_status == "monitoring",
    ).order_by(AdaptiveStockMonitoring.updated_at.desc())).all()
    seen: set[str] = set()
    result = []
    for item in items:
        if item.stock_code in seen: continue
        seen.add(item.stock_code)
        result.append({
            "stockCode": item.stock_code, "stockName": item.stock_name,
            "strategyType": item.strategy_type, "addedDate": item.added_date.isoformat(),
            "triggerPrice": float(item.trigger_price), "stopLossPrice": float(item.stop_loss_price),
            "targetPrice1": float(item.target_price_1), "targetPrice2": float(item.target_price_2),
            "allocationPercent": float(item.allocation_percent), "healthScore": float(item.health_score),
            "monitorStatus": item.monitor_status, "lastSignal": item.last_signal,
            "updatedAt": item.updated_at.isoformat(),
        })
    return {"items": result, "updatedAt": datetime.now(UTC).isoformat()}


@router.get("/signals")
def signals(limit: int = Query(default=50, ge=1, le=200), db: Session = Depends(get_db)) -> dict:
    items = db.scalars(select(AdaptiveSignal).order_by(AdaptiveSignal.created_at.desc()).limit(limit)).all()
    return {"items": [{
        "id": item.id, "stockCode": item.stock_code, "stockName": item.stock_name,
        "signalType": item.signal_type, "action": item.action, "strategyType": item.strategy_type,
        "price": float(item.price) if item.price is not None else None,
        "healthScore": float(item.health_score) if item.health_score is not None else None,
        "reasons": json.loads(item.reasons_json), "linePushStatus": item.line_push_status,
        "createdAt": item.created_at.isoformat(),
    } for item in items]}


@router.get("/performance")
def performance(
    limit: int = Query(default=100, ge=1, le=500),
    month: str = Query(default="", pattern=r"^(|\d{4}-(0[1-9]|1[0-2]))$"),
    db: Session = Depends(get_db),
) -> dict:
    return performance_payload(db, limit, month or None)


@router.get("/notifications")
def notifications(
    source: str = Query(default="SUPER_AI_DAYTRADE", max_length=40),
    category: str = Query(default="", max_length=40),
    symbol: str = Query(default="", max_length=12),
    unreadOnly: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict:
    query = select(SuperAIDaytradeNotification).where(SuperAIDaytradeNotification.source == source)
    if category:
        query = query.where(SuperAIDaytradeNotification.category == category)
    if symbol:
        query = query.where(SuperAIDaytradeNotification.symbol == symbol)
    if unreadOnly:
        query = query.where(SuperAIDaytradeNotification.is_read.is_(False))
    rows = list(db.scalars(query.order_by(SuperAIDaytradeNotification.created_at.desc()).limit(limit)).all())
    return {"items": [notification_payload(row) for row in rows], "total": len(rows)}


@router.post("/notifications/{notification_id}/mark-read")
def mark_notification_read(notification_id: int, db: Session = Depends(get_db)) -> dict:
    row = db.get(SuperAIDaytradeNotification, notification_id)
    if row is None:
        raise HTTPException(status_code=404, detail="notification not found")
    row.is_read = True
    row.read_at = datetime.now(UTC)
    db.commit()
    return {"status": "read", "id": notification_id}


@router.post("/notifications/mark-all-read")
def mark_all_notifications_read(db: Session = Depends(get_db)) -> dict:
    rows = list(db.scalars(select(SuperAIDaytradeNotification).where(
        SuperAIDaytradeNotification.source == "SUPER_AI_DAYTRADE",
        SuperAIDaytradeNotification.is_read.is_(False),
    )).all())
    now = datetime.now(UTC)
    for row in rows:
        row.is_read = True
        row.read_at = now
    db.commit()
    return {"updated": len(rows)}


@router.delete("/notifications/{notification_id}", status_code=204)
def delete_notification(notification_id: int, db: Session = Depends(get_db)) -> Response:
    row = db.get(SuperAIDaytradeNotification, notification_id)
    if row is None:
        raise HTTPException(status_code=404, detail="notification not found")
    db.delete(row)
    db.commit()
    return Response(status_code=204)


@router.post("/email/test")
async def test_super_ai_email() -> dict:
    if not gmail_notification_dispatcher.configured:
        raise HTTPException(status_code=409, detail="Gmail 尚未完成設定")
    sent = await gmail_notification_dispatcher.dispatch(
        event_type="super_ai_daytrade_test",
        action="測試Email",
        message=f"【{SYSTEM_NAME}｜Email測試】\n\n這是一封通知設定測試信，不是真實交易訊號。",
        dedupe_key=f"super-ai-email-test:{datetime.now(UTC).timestamp()}",
        symbol="SYSTEM",
        channel_name=SYSTEM_NAME,
    )
    return {"sent": sent, "systemName": SYSTEM_NAME}


@router.get("/parameters")
def parameters(db: Session = Depends(get_db)) -> dict:
    ensure_default_parameters(db)
    items = db.scalars(select(StrategyParameter).order_by(
        StrategyParameter.parameter_group, StrategyParameter.parameter_name,
    )).all()
    return {"items": [{
        "parameterGroup": item.parameter_group, "parameterName": item.parameter_name,
        "parameterValue": float(item.parameter_value), "description": item.description,
        "isEnabled": item.is_enabled, "updatedAt": item.updated_at.isoformat(),
    } for item in items]}


@router.put("/parameters")
def update_parameters(
    body: AdaptiveParameterBatchUpdate,
    _: None = Depends(_admin),
    db: Session = Depends(get_db),
) -> dict:
    ensure_default_parameters(db)
    now = datetime.now(UTC)
    for update in body.items:
        item = db.scalar(select(StrategyParameter).where(
            StrategyParameter.parameter_group == update.parameter_group,
            StrategyParameter.parameter_name == update.parameter_name,
        ))
        if item is None:
            item = StrategyParameter(
                parameter_group=update.parameter_group, parameter_name=update.parameter_name,
                parameter_value=Decimal(str(update.parameter_value)), description=update.description,
                is_enabled=update.is_enabled, updated_at=now,
            )
            db.add(item)
        else:
            item.parameter_value = Decimal(str(update.parameter_value)); item.description = update.description
            item.is_enabled = update.is_enabled; item.updated_at = now
    db.commit()
    return {"updated": len(body.items), "updatedAt": now.isoformat()}


@router.post("/recalculate")
async def recalculate(
    send_notifications: bool = True,
    _: None = Depends(_admin),
) -> dict:
    return await adaptive_electronic_automation.run_once(
        force=True,
        send_notifications=send_notifications,
    )


@router.post("/backtest")
def backtest(body: AdaptiveBacktestRequest) -> dict:
    try:
        return run_backtest(body)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
