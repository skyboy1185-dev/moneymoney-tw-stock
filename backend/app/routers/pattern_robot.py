from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import json
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import (
    PatternDailyEquity, PatternDetection, PatternFill, PatternOrder, PatternPosition,
    PatternRobotRun, PatternSignal, PatternTradeCycle, PatternTradeMessage, PatternWatchlist,
    WatchlistItem,
)
from ..pattern_schemas import PatternManualTrade, PatternPositionUpdate, PatternSettingsUpdate, PatternWatchlistCreate
from ..services.pattern_robot_automation import pattern_robot_automation
from ..services.theme_stock_universe import AI_RELATED_THEME_STOCKS, AI_RELATED_THEME_STOCKS_BY_SYMBOL
from ..services.pattern_robot_service import (
    BREAKOUT_RESULT_STATUSES, PATTERN_LABELS, detection_dict, ensure_pattern_settings, manual_position_trade,
    performance, performance_by_pattern, position_dict, settings_dict, trades_csv, update_settings,
)


router = APIRouter(prefix="/pattern-robot", tags=["pattern-robot"])
AI_RELATED_CODES = tuple(AI_RELATED_THEME_STOCKS_BY_SYMBOL)


def _breakout_focus(trade_date: date):
    return or_(
        PatternDetection.pattern_status.in_(["NEAR_BREAKOUT", "INTRADAY_BREAKOUT"]),
        and_(
            PatternDetection.pattern_status == "CONFIRMED_BREAKOUT",
            func.date(PatternDetection.confirmed_at) == trade_date,
        ),
    )


def _user_id(x_user_id: str = Header(min_length=8, max_length=80)) -> str:
    return x_user_id


def _page(items: list, page: int, page_size: int, serializer) -> dict:
    start = (page - 1) * page_size
    return {
        "items": [serializer(item) for item in items[start:start + page_size]],
        "page": page, "pageSize": page_size, "total": len(items),
    }


@router.get("/status")
def status(db: Session = Depends(get_db)) -> dict:
    settings = ensure_pattern_settings(db)
    latest = db.scalar(select(PatternRobotRun).order_by(PatternRobotRun.started_at.desc()).limit(1))
    reminder = db.scalar(select(PatternTradeMessage).where(
        PatternTradeMessage.message_type == "SCAN_COMPLETED",
        PatternTradeMessage.is_read.is_(False),
        or_(PatternTradeMessage.remind_after.is_(None), PatternTradeMessage.remind_after <= datetime.now(UTC)),
    ).order_by(PatternTradeMessage.created_at.desc()).limit(1))
    top = []
    if latest:
        top = list(db.scalars(select(PatternDetection).where(
            PatternDetection.trade_date == latest.trade_date,
            PatternDetection.stock_code.in_(AI_RELATED_CODES),
            _breakout_focus(latest.trade_date),
            PatternDetection.pattern_score >= settings.minimum_score,
        ).order_by(PatternDetection.pattern_score.desc()).limit(10)).all())
    return {
        **pattern_robot_automation.state, "enabled": settings.enabled,
        "universeScope": "AI_CORE_AND_EXTENDED", "universeSize": len(AI_RELATED_CODES),
        "settings": settings_dict(settings),
        "lastRun": None if latest is None else {
            "id": latest.id, "tradeDate": latest.trade_date.isoformat(), "status": latest.status,
            "scannedCount": latest.scanned_count, "matchedCount": latest.matched_count,
            "counts": json.loads(latest.counts_json), "startedAt": latest.started_at.isoformat(),
            "completedAt": latest.completed_at.isoformat() if latest.completed_at else None,
            "error": latest.error_message,
        },
        "openingReminder": None if reminder is None or not settings.opening_reminder_enabled else {
            "id": reminder.id, "title": reminder.title, "message": reminder.message,
            "createdAt": reminder.created_at.isoformat(), "top": [detection_dict(item) for item in top],
        },
    }


@router.get("/universe")
def universe() -> dict:
    return {
        "scope": "AI_CORE_AND_EXTENDED", "count": len(AI_RELATED_THEME_STOCKS),
        "items": [{
            "stockCode": item.symbol, "stockName": item.name, "market": item.market,
            "industry": item.industry, "themes": list(item.themes),
        } for item in AI_RELATED_THEME_STOCKS],
    }


@router.post("/start")
async def start(_: str = Depends(_user_id)) -> dict:
    await pattern_robot_automation.start()
    return pattern_robot_automation.state


@router.post("/stop")
async def stop(_: str = Depends(_user_id)) -> dict:
    await pattern_robot_automation.stop()
    return pattern_robot_automation.state


@router.post("/scan")
async def scan(force: bool = False, _: str = Depends(_user_id)) -> dict:
    try:
        return await pattern_robot_automation.run_once(force=force)
    except Exception as error:
        raise HTTPException(status_code=503, detail=f"型態掃描失敗：{error}") from error


@router.get("/settings")
def get_settings(db: Session = Depends(get_db)) -> dict:
    return settings_dict(ensure_pattern_settings(db))


@router.put("/settings")
def put_settings(
    body: PatternSettingsUpdate, user_id: str = Depends(_user_id), db: Session = Depends(get_db),
) -> dict:
    item = update_settings(db, body.model_dump(exclude_none=True), user_id, datetime.now(UTC))
    db.commit()
    return settings_dict(item)


@router.get("/detections")
def detections(
    tradeDate: date | None = None, dateFrom: date | None = None, dateTo: date | None = None,
    stock: str = "", pattern: str = "", status: str = "", minScore: float = 0,
    action: str = "", notified: bool | None = None,
    page: int = Query(1, ge=1), pageSize: int = Query(50, ge=1, le=500),
    sort: Literal["score", "date", "stock"] = "score", order: Literal["asc", "desc"] = "desc",
    db: Session = Depends(get_db),
) -> dict:
    query = select(PatternDetection)
    clauses = [PatternDetection.stock_code.in_(AI_RELATED_CODES)]
    if not tradeDate and not dateFrom and not dateTo:
        latest_trade_date = db.scalar(select(func.max(PatternRobotRun.trade_date)))
        if latest_trade_date: clauses.append(PatternDetection.trade_date == latest_trade_date)
    if tradeDate: clauses.append(PatternDetection.trade_date == tradeDate)
    if dateFrom: clauses.append(PatternDetection.trade_date >= dateFrom)
    if dateTo: clauses.append(PatternDetection.trade_date <= dateTo)
    if stock: clauses.append(or_(PatternDetection.stock_code.ilike(f"%{stock}%"), PatternDetection.stock_name.ilike(f"%{stock}%")))
    if pattern: clauses.append(PatternDetection.pattern_type == pattern)
    if status:
        clauses.append(PatternDetection.pattern_status == status)
    else:
        focus_date = tradeDate or dateTo or latest_trade_date
        if focus_date:
            clauses.append(_breakout_focus(focus_date))
        else:
            clauses.append(PatternDetection.pattern_status.in_(BREAKOUT_RESULT_STATUSES))
    clauses.append(PatternDetection.pattern_score >= (minScore or ensure_pattern_settings(db).minimum_score))
    if action: clauses.append(PatternDetection.action == action)
    if notified is True: clauses.append(PatternDetection.notified_at.is_not(None))
    if notified is False: clauses.append(PatternDetection.notified_at.is_(None))
    if clauses: query = query.where(*clauses)
    column = {"score": PatternDetection.pattern_score, "date": PatternDetection.trade_date, "stock": PatternDetection.stock_code}[sort]
    query = query.order_by(column.asc() if order == "asc" else column.desc())
    rows = list(db.scalars(query).all())
    return _page(rows, page, pageSize, detection_dict)


@router.get("/detections/{stock_code}")
def stock_detections(stock_code: str, db: Session = Depends(get_db)) -> dict:
    if stock_code not in AI_RELATED_THEME_STOCKS_BY_SYMBOL:
        return {"items": []}
    rows = list(db.scalars(select(PatternDetection).where(
        PatternDetection.stock_code == stock_code,
    ).order_by(PatternDetection.trade_date.desc(), PatternDetection.pattern_score.desc())).all())
    return {"items": [detection_dict(item) for item in rows]}


@router.get("/watchlist")
def watchlist(user_id: str = Depends(_user_id), db: Session = Depends(get_db)) -> dict:
    rows = list(db.scalars(select(PatternWatchlist).where(
        PatternWatchlist.user_id.in_([user_id, "system-pattern-robot"]), PatternWatchlist.active.is_(True),
        PatternWatchlist.stock_code.in_(AI_RELATED_CODES),
    ).order_by(PatternWatchlist.added_at.desc())).all())
    deduplicated: dict[tuple[str, str], PatternWatchlist] = {}
    for row in rows:
        key = (row.stock_code, row.pattern_type)
        if key not in deduplicated or row.user_id == user_id:
            deduplicated[key] = row
    rows = list(deduplicated.values())
    items = []
    for row in rows:
        detection = db.get(PatternDetection, row.detection_id) if row.detection_id else db.scalar(select(PatternDetection).where(
            PatternDetection.stock_code == row.stock_code, PatternDetection.pattern_type == row.pattern_type,
        ).order_by(PatternDetection.trade_date.desc()).limit(1))
        items.append({
            "id": row.id, "stockCode": row.stock_code, "stockName": row.stock_name,
            "patternType": row.pattern_type, "patternLabel": PATTERN_LABELS.get(row.pattern_type, row.pattern_type),
            "tradePaused": row.trade_paused, "reminderOnly": row.reminder_only,
            "addedAt": row.added_at.isoformat(), "detection": detection_dict(detection) if detection else None,
        })
    return {"items": items}


@router.post("/watchlist", status_code=201)
def add_watchlist(
    body: PatternWatchlistCreate, user_id: str = Depends(_user_id), db: Session = Depends(get_db),
) -> dict:
    detection = db.get(PatternDetection, body.detectionId) if body.detectionId else db.scalar(select(PatternDetection).where(
        PatternDetection.stock_code == body.stockCode, PatternDetection.pattern_type == body.patternType,
    ).order_by(PatternDetection.trade_date.desc()).limit(1))
    if detection is None:
        raise HTTPException(status_code=404, detail="找不到型態偵測結果")
    now = datetime.now(UTC)
    item = db.scalar(select(PatternWatchlist).where(
        PatternWatchlist.user_id == user_id, PatternWatchlist.stock_code == detection.stock_code,
        PatternWatchlist.pattern_type == detection.pattern_type,
    ))
    if item is None:
        item = PatternWatchlist(
            user_id=user_id, detection_id=detection.id, stock_code=detection.stock_code,
            stock_name=detection.stock_name, pattern_type=detection.pattern_type,
            reminder_only=body.reminderOnly, added_at=now, updated_at=now,
        )
        db.add(item)
    else:
        item.active, item.removed_at, item.removed_reason = True, None, None
        item.detection_id, item.reminder_only, item.updated_at = detection.id, body.reminderOnly, now
    # “加入監控區”沿用現有網站監控清單，同時保留機器人自己的候選狀態。
    existing_shared = db.scalar(select(WatchlistItem).where(
        WatchlistItem.user_id == user_id, WatchlistItem.symbol == detection.stock_code,
    ))
    if existing_shared is None:
        db.add(WatchlistItem(
            user_id=user_id, symbol=detection.stock_code, name=detection.stock_name,
            added_at=now, added_price=float(detection.current_price), added_score=float(detection.pattern_score),
            original_robot_id="pattern-robot", original_robot_name="型態選股機器人",
            original_reasons_json=detection.reasons_json,
        ))
    db.commit()
    db.refresh(item)
    return {"id": item.id, "stockCode": item.stock_code, "patternType": item.pattern_type}


@router.delete("/watchlist/{item_id}", status_code=204)
def remove_watchlist(item_id: int, user_id: str = Depends(_user_id), db: Session = Depends(get_db)) -> Response:
    item = db.scalar(select(PatternWatchlist).where(
        PatternWatchlist.id == item_id,
        PatternWatchlist.user_id.in_([user_id, "system-pattern-robot"]),
    ))
    if item is None: raise HTTPException(status_code=404, detail="找不到觀察項目")
    item.active, item.removed_at, item.removed_reason = False, datetime.now(UTC), "MANUAL"
    item.updated_at = datetime.now(UTC)
    db.commit()
    return Response(status_code=204)


@router.get("/signals")
def signals(action: str = "", page: int = 1, pageSize: int = 100, db: Session = Depends(get_db)) -> dict:
    query = select(PatternSignal).where(PatternSignal.stock_code.in_(AI_RELATED_CODES))
    if action: query = query.where(PatternSignal.action == action)
    rows = list(db.scalars(query.order_by(PatternSignal.signal_time.desc())).all())
    return _page(rows, page, pageSize, lambda item: {
        "id": item.id, "detectionId": item.detection_id, "tradeDate": item.trade_date.isoformat(),
        "stockCode": item.stock_code, "stockName": item.stock_name, "patternType": item.pattern_type,
        "signalType": item.signal_type, "action": item.action, "signalPrice": float(item.signal_price),
        "quantity": item.quantity, "reasons": json.loads(item.reasons_json), "signalTime": item.signal_time.isoformat(),
    })


@router.get("/orders")
def orders(
    status: str = "", performanceMode: str = "PAPER_LIVE",
    page: int = 1, pageSize: int = 100, db: Session = Depends(get_db),
) -> dict:
    query = select(PatternOrder).where(PatternOrder.performance_mode == performanceMode)
    if status: query = query.where(PatternOrder.status == status)
    rows = list(db.scalars(query.order_by(PatternOrder.created_at.desc())).all())
    return _page(rows, page, pageSize, lambda item: {
        "id": item.id, "signalId": item.signal_id, "stockCode": item.stock_code,
        "action": item.order_action, "status": item.status, "quantity": item.quantity,
        "filledQuantity": item.filled_quantity, "orderPrice": float(item.order_price),
        "rejectionReason": item.rejection_reason, "createdAt": item.created_at.isoformat(),
    })


@router.get("/positions")
def positions(status: str = "OPEN", performanceMode: str = "PAPER_LIVE", db: Session = Depends(get_db)) -> dict:
    query = select(PatternPosition).where(PatternPosition.performance_mode == performanceMode)
    if status: query = query.where(PatternPosition.status == status)
    rows = list(db.scalars(query.order_by(PatternPosition.updated_at.desc())).all())
    return {"items": [position_dict(item) for item in rows]}


def _manual(position_id: int, body: PatternManualTrade, action: str, db: Session) -> dict:
    try:
        item = manual_position_trade(
            db, position_id, action=action, quantity=body.quantity, price=body.price,
            reason=body.reason, at=datetime.now(UTC),
        )
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    return position_dict(item)


@router.post("/positions/{position_id}/manual-add")
def manual_add(position_id: int, body: PatternManualTrade, _: str = Depends(_user_id), db: Session = Depends(get_db)) -> dict:
    return _manual(position_id, body, "ADD", db)


@router.post("/positions/{position_id}/manual-reduce")
def manual_reduce(position_id: int, body: PatternManualTrade, _: str = Depends(_user_id), db: Session = Depends(get_db)) -> dict:
    return _manual(position_id, body, "REDUCE", db)


@router.post("/positions/{position_id}/manual-exit")
def manual_exit(position_id: int, body: PatternManualTrade, _: str = Depends(_user_id), db: Session = Depends(get_db)) -> dict:
    return _manual(position_id, body, "EXIT", db)


@router.put("/positions/{position_id}")
def edit_position(
    position_id: int, body: PatternPositionUpdate, user_id: str = Depends(_user_id), db: Session = Depends(get_db),
) -> dict:
    item = db.get(PatternPosition, position_id)
    if item is None: raise HTTPException(status_code=404, detail="找不到持倉")
    before = position_dict(item)
    values = body.model_dump(exclude_none=True, exclude={"reason"})
    mapping = {"stopLossPrice": "stop_loss_price", "takeProfit1": "take_profit_1", "takeProfit2": "take_profit_2", "trailingStopPrice": "trailing_stop_price", "autoTradePaused": "auto_trade_paused", "note": "note"}
    for source, target in mapping.items():
        if source in values: setattr(item, target, Decimal(str(values[source])) if "Price" in source else values[source])
    item.updated_at = datetime.now(UTC)
    after = position_dict(item)
    db.add(PatternTradeMessage(
        signal_id=None, message_type="MANUAL", message_version=1, stock_code=item.stock_code,
        stock_name=item.stock_name, pattern_type=item.primary_pattern, action="MANUAL",
        title="型態選股機器人｜手動修改持倉", message=f"{user_id}：{body.reason}",
        reasons_json=json.dumps({"before": before, "after": after}, ensure_ascii=False), created_at=datetime.now(UTC),
    ))
    db.commit()
    return after


@router.get("/trades")
def trades(
    dateFrom: date | None = None, dateTo: date | None = None, stock: str = "", pattern: str = "",
    pnl: Literal["profit", "loss", "all"] = "all", performanceMode: str = "PAPER_LIVE",
    page: int = 1, pageSize: int = 100, db: Session = Depends(get_db),
) -> dict:
    query = select(PatternTradeCycle).where(PatternTradeCycle.performance_mode == performanceMode)
    if dateFrom: query = query.where(PatternTradeCycle.first_entry_at >= datetime.combine(dateFrom, datetime.min.time(), UTC))
    if dateTo: query = query.where(PatternTradeCycle.first_entry_at < datetime.combine(dateTo + timedelta(days=1), datetime.min.time(), UTC))
    if stock: query = query.where(or_(PatternTradeCycle.stock_code.ilike(f"%{stock}%"), PatternTradeCycle.stock_name.ilike(f"%{stock}%")))
    if pattern: query = query.where(PatternTradeCycle.primary_pattern == pattern)
    if pnl == "profit": query = query.where(PatternTradeCycle.realized_pnl > 0)
    if pnl == "loss": query = query.where(PatternTradeCycle.realized_pnl < 0)
    rows = list(db.scalars(query.order_by(PatternTradeCycle.first_entry_at.desc())).all())
    def serialize(item):
        fill_query = select(PatternFill).join(PatternSignal, PatternFill.signal_id == PatternSignal.id).where(
            PatternSignal.stock_code == item.stock_code,
            PatternFill.filled_at >= item.first_entry_at,
        )
        if item.closed_at is not None:
            fill_query = fill_query.where(PatternFill.filled_at <= item.closed_at)
        fills = list(db.scalars(fill_query.order_by(PatternFill.filled_at)).all())
        serialized_fills = []
        for fill in fills:
            signal = db.get(PatternSignal, fill.signal_id)
            serialized_fills.append({
                "side": fill.side, "action": signal.action if signal else fill.side,
                "price": float(fill.filled_price), "quantity": fill.quantity,
                "filledAt": fill.filled_at.isoformat(),
            })
        return {
            "tradeId": item.id, "stockCode": item.stock_code, "stockName": item.stock_name,
            "primaryPattern": item.primary_pattern, "allPatterns": json.loads(item.all_patterns_json),
            "patternScore": float(item.pattern_score), "status": item.status,
            "firstEntryAt": item.first_entry_at.isoformat(), "closedAt": item.closed_at.isoformat() if item.closed_at else None,
            "buyQuantity": item.cumulative_buy_quantity, "buyAmount": float(item.cumulative_buy_amount),
            "sellQuantity": item.cumulative_sell_quantity, "sellAmount": float(item.cumulative_sell_amount),
            "realizedPnl": float(item.realized_pnl), "unrealizedPnl": float(item.unrealized_pnl),
            "netPnl": float(item.realized_pnl) + float(item.unrealized_pnl), "tradingCost": float(item.trading_cost),
            "mfe": float(item.mfe), "mae": float(item.mae), "exitReason": item.exit_reason,
            "fills": serialized_fills,
        }
    return _page(rows, page, pageSize, serialize)


@router.get("/messages")
def messages(unreadOnly: bool = False, page: int = 1, pageSize: int = 100, db: Session = Depends(get_db)) -> dict:
    latest_trade_date = db.scalar(select(func.max(PatternRobotRun.trade_date)))
    query = select(PatternTradeMessage).outerjoin(
        PatternSignal, PatternTradeMessage.signal_id == PatternSignal.id,
    ).where(PatternTradeMessage.message_type != "WATCH")
    if latest_trade_date:
        query = query.where(or_(
            and_(
                PatternTradeMessage.signal_id.is_not(None),
                PatternSignal.trade_date == latest_trade_date,
                PatternSignal.stock_code.in_(AI_RELATED_CODES),
            ),
            and_(
                PatternTradeMessage.signal_id.is_(None),
                PatternTradeMessage.message_type.in_(["SCAN_COMPLETED", "MANUAL"]),
                func.date(PatternTradeMessage.created_at) == latest_trade_date,
            ),
        ))
    if unreadOnly: query = query.where(PatternTradeMessage.is_read.is_(False))
    rows = list(db.scalars(query.order_by(PatternTradeMessage.created_at.desc())).all())
    return _page(rows, page, pageSize, lambda item: {
        "id": item.id, "messageType": item.message_type, "stockCode": item.stock_code,
        "stockName": item.stock_name, "patternType": item.pattern_type, "action": item.action,
        "title": item.title, "message": item.message, "price": float(item.price) if item.price else None,
        "quantity": item.quantity, "amount": float(item.amount) if item.amount else None,
        "cashImpact": float(item.cash_impact) if item.cash_impact else None,
        "positionImpact": item.position_impact, "reasons": json.loads(item.reasons_json),
        "isRead": item.is_read, "createdAt": item.created_at.isoformat(),
    })


@router.post("/messages/{message_id}/mark-read")
def mark_read(message_id: int, snoozeMinutes: int = Query(0, ge=0, le=1440), db: Session = Depends(get_db)) -> dict:
    item = db.get(PatternTradeMessage, message_id)
    if item is None: raise HTTPException(status_code=404, detail="找不到訊息")
    now = datetime.now(UTC)
    if snoozeMinutes:
        item.remind_after, item.is_read = now + timedelta(minutes=snoozeMinutes), False
    else:
        item.is_read, item.read_at, item.displayed_at = True, now, now
    db.commit()
    return {"id": item.id, "isRead": item.is_read, "remindAfter": item.remind_after}


@router.get("/performance")
def get_performance(performanceMode: str = "PAPER_LIVE", db: Session = Depends(get_db)) -> dict:
    return performance(db, performanceMode)


@router.get("/performance/by-pattern")
def get_performance_by_pattern(performanceMode: str = "PAPER_LIVE", db: Session = Depends(get_db)) -> dict:
    return {"items": performance_by_pattern(db, performanceMode)}


@router.get("/equity-curve")
def equity_curve(
    period: Literal["7d", "30d", "3m", "1y", "all"] = "30d", performanceMode: str = "PAPER_LIVE",
    db: Session = Depends(get_db),
) -> dict:
    days = {"7d": 7, "30d": 30, "3m": 92, "1y": 366, "all": None}[period]
    query = select(PatternDailyEquity).where(PatternDailyEquity.performance_mode == performanceMode)
    if days: query = query.where(PatternDailyEquity.trade_date >= date.today() - timedelta(days=days))
    rows = list(db.scalars(query.order_by(PatternDailyEquity.trade_date)).all())
    return {"items": [{
        "tradeDate": item.trade_date.isoformat(), "cash": float(item.cash), "marketValue": float(item.market_value),
        "totalEquity": float(item.total_equity), "dailyPnl": float(item.daily_pnl),
        "cumulativePnl": float(item.cumulative_pnl), "drawdownPct": float(item.drawdown_pct),
    } for item in rows]}


@router.get("/export")
def export(performanceMode: str = "PAPER_LIVE", db: Session = Depends(get_db)):
    data = trades_csv(db, performanceMode)
    return StreamingResponse(
        iter([data]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="pattern-trades-{performanceMode.lower()}.csv"'},
    )
