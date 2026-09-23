from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo
import re
from decimal import Decimal
import json
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..database import SessionLocal, get_db
from ..strong_stock_models import (
    StrongStockBacktestJob, StrongStockDataRun, StrongStockEquitySnapshot,
    StrongStockIndustryRanking, StrongStockMarketRegime, StrongStockNotification,
    StrongStockOrder, StrongStockPosition, StrongStockRanking, StrongStockSetting,
    StrongStockTrade,
)
from ..services.strong_stock import (
    audit, close_position, dec, ensure_defaults, merged_config, performance, ranking_payload, strategy_health,
)
from ..services.strong_stock_automation import strong_stock_automation


router = APIRouter(prefix="/strong-stocks", tags=["strong-stocks"])


def user_id(x_user_id: str | None = Header(default=None, min_length=8, max_length=80)) -> str:
    return x_user_id or "demo-user"


def parse_json(value: str, fallback):
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def latest_trade_date(db: Session) -> date | None:
    return db.scalar(select(func.max(StrongStockRanking.trade_date)))


def position_payload(row: StrongStockPosition) -> dict[str, object]:
    execution = parse_json(row.execution_json, {})
    market_value = row.current_price * row.quantity
    unrealized = market_value - row.average_cost * row.quantity
    return {
        "id": row.id, "symbol": row.symbol, "name": row.name, "industry": row.industry,
        "quantity": row.quantity, "averageCost": str(row.average_cost), "currentPrice": str(row.current_price),
        "investedCapital": str(row.invested_capital), "marketValue": str(market_value),
        "unrealizedPnl": str(unrealized), "returnPct": str(unrealized / row.invested_capital * 100) if row.invested_capital else "0",
        "initialStop": str(row.initial_stop), "trailingStop": str(row.trailing_stop),
        "nextAddPrice": str(row.next_add_price) if row.next_add_price is not None else None,
        "currentScore": str(row.current_score), "entryType": row.entry_type, "strategyVersion": row.strategy_version,
        "tranches": parse_json(row.tranches_json, []), "reasons": parse_json(row.reasons_json, []),
        "warnings": parse_json(row.warnings_json, []), "status": row.status, "entryAt": row.entry_at,
        "execution": execution, "executionModel": execution.get("executionModel", "LEGACY_LAST_TRADE"),
    }


def trade_payload(row: StrongStockTrade) -> dict[str, object]:
    execution = parse_json(row.execution_json, {})
    return {
        "id": row.id, "positionId": row.position_id, "symbol": row.symbol, "name": row.name,
        "industry": row.industry, "entryType": row.entry_type, "quantity": row.quantity,
        "entryPrice": str(row.entry_price), "exitPrice": str(row.exit_price), "entryAt": row.entry_at,
        "exitAt": row.exit_at, "grossPnl": str(row.gross_pnl), "buyFee": str(row.buy_fee),
        "sellFee": str(row.sell_fee), "tax": str(row.tax), "slippage": str(row.slippage),
        "netPnl": str(row.net_pnl), "returnPct": str(row.return_pct),
        "strategyVersion": row.strategy_version, "entryReason": row.entry_reason, "exitReason": row.exit_reason,
        "execution": execution, "executionModel": execution.get("executionModel", "LEGACY_LAST_TRADE"),
    }


def dashboard_payload(db: Session, uid: str) -> dict[str, object]:
    setting, account, config = ensure_defaults(db, uid)
    trading_date = latest_trade_date(db)
    regime = db.scalar(select(StrongStockMarketRegime).where(StrongStockMarketRegime.trade_date == trading_date)) if trading_date else None
    rankings = list(db.scalars(select(StrongStockRanking).where(StrongStockRanking.trade_date == trading_date).order_by(StrongStockRanking.rank).limit(100)).all()) if trading_date else []
    industries = list(db.scalars(select(StrongStockIndustryRanking).where(StrongStockIndustryRanking.trade_date == trading_date).order_by(StrongStockIndustryRanking.rank).limit(5)).all()) if trading_date else []
    positions = list(db.scalars(select(StrongStockPosition).where(StrongStockPosition.user_id == uid, StrongStockPosition.status == "OPEN").order_by(StrongStockPosition.entry_at.desc())).all())
    trades = list(db.scalars(select(StrongStockTrade).where(StrongStockTrade.user_id == uid).order_by(StrongStockTrade.exit_at.desc()).limit(30)).all())
    pending_orders = list(db.scalars(select(StrongStockOrder).where(StrongStockOrder.user_id == uid, StrongStockOrder.status.in_(("PENDING", "PARTIALLY_FILLED"))).order_by(StrongStockOrder.created_at)).all())
    latest_run = db.scalar(select(StrongStockDataRun).order_by(StrongStockDataRun.started_at.desc()).limit(1))
    notifications = list(db.scalars(select(StrongStockNotification).where(StrongStockNotification.user_id == uid).order_by(StrongStockNotification.created_at.desc()).limit(30)).all())
    equity = list(db.scalars(select(StrongStockEquitySnapshot).where(StrongStockEquitySnapshot.user_id == uid).order_by(StrongStockEquitySnapshot.trade_date).limit(400)).all())
    return {
        "systemName": "強勢股策略", "mode": "PAPER", "liveTradingAvailable": False,
        "paperEnabled": setting.paper_enabled and not account.trading_paused, "config": config,
        "marketRegime": {
            "tradeDate": regime.trade_date if regime else None, "regime": regime.regime if regime else "DATA_INSUFFICIENT",
            "label": regime.label if regime else "資料尚未完成", "confidence": str(regime.confidence) if regime else "0",
            "suggestedExposurePct": str(regime.suggested_exposure_pct) if regime else "0",
            "reasons": parse_json(regime.reasons_json, []) if regime else ["等待第一份完整盤後資料"],
        },
        "performance": performance(db, uid), "strategyHealth": strategy_health(db, uid), "rankings": [ranking_payload(row) for row in rankings],
        "industries": [{"rank": row.rank, "industry": row.industry, "score": str(row.score), "percentile": str(row.percentile), "memberCount": row.member_count, "details": parse_json(row.details_json, {})} for row in industries],
        "positions": [position_payload(row) for row in positions], "trades": [trade_payload(row) for row in trades],
        "pendingOrders": [{"id": row.id, "symbol": row.symbol, "name": row.name, "limitPrice": str(row.limit_price),
            "quantity": row.quantity, "filledQuantity": row.filled_quantity, "remainingQuantity": row.quantity-row.filled_quantity,
            "validDate": row.valid_date, "entryType": row.entry_type, "status": row.status, "reason": row.reason,
            "execution": parse_json(row.execution_json, {})} for row in pending_orders],
        "equityCurve": [{"date": row.trade_date, "cash": str(row.cash), "marketValue": str(row.market_value), "totalEquity": str(row.total_equity), "dailyPnl": str(row.daily_pnl), "drawdownPct": str(row.drawdown_pct)} for row in equity],
        "notifications": [{"id": row.id, "eventType": row.event_type, "title": row.title, "message": row.message, "priority": row.priority, "read": row.read, "createdAt": row.created_at} for row in notifications],
        "dataStatus": {
            "status": latest_run.status if latest_run else "WAITING", "latestTradeDate": latest_run.trade_date if latest_run else None,
            "lastSuccessfulUpdate": latest_run.completed_at if latest_run and latest_run.status == "COMPLETED" else None,
            "sources": parse_json(latest_run.source_json, {}) if latest_run else {}, "missing": parse_json(latest_run.missing_json, []) if latest_run else [],
            "error": latest_run.error_message if latest_run else "尚未完成第一次盤後更新",
            "historicalBacktestReady": False,
            "automation": strong_stock_automation.state,
        },
        "notice": "純做多選股與模擬交易；與當沖資金及庫存完全分離，不保證獲利。",
    }


@router.get("/dashboard")
def dashboard(uid: str = Depends(user_id), db: Session = Depends(get_db)) -> dict[str, object]:
    return dashboard_payload(db, uid)


@router.get("/rankings")
def rankings(
    search: str = "", industry: str = "", min_score: Decimal = Query(default=Decimal("0"), ge=0, le=100),
    status: str = "", limit: int = Query(default=100, ge=1, le=1000), db: Session = Depends(get_db),
) -> dict[str, object]:
    trading_date = latest_trade_date(db)
    if trading_date is None:
        return {"tradeDate": None, "items": []}
    query = select(StrongStockRanking).where(StrongStockRanking.trade_date == trading_date, StrongStockRanking.total_score >= min_score)
    if search:
        query = query.where(or_(StrongStockRanking.symbol.contains(search), StrongStockRanking.name.contains(search)))
    if industry:
        query = query.where(StrongStockRanking.industry == industry)
    if status:
        query = query.where(StrongStockRanking.status == status)
    rows = list(db.scalars(query.order_by(StrongStockRanking.rank).limit(limit)).all())
    return {"tradeDate": trading_date, "items": [ranking_payload(row) for row in rows]}


@router.get("/stocks/{symbol}")
def stock_detail(symbol: str, db: Session = Depends(get_db)) -> dict[str, object]:
    rows = list(db.scalars(select(StrongStockRanking).where(StrongStockRanking.symbol == symbol).order_by(StrongStockRanking.trade_date.desc()).limit(120)).all())
    if not rows:
        raise HTTPException(404, "找不到強勢股評分資料")
    return {"current": ranking_payload(rows[0]), "history": [ranking_payload(row) for row in reversed(rows)]}


@router.get("/positions")
def positions(uid: str = Depends(user_id), db: Session = Depends(get_db)) -> dict[str, object]:
    rows = list(db.scalars(select(StrongStockPosition).where(StrongStockPosition.user_id == uid, StrongStockPosition.status == "OPEN")).all())
    return {"items": [position_payload(row) for row in rows]}


@router.get("/trades")
def trades(uid: str = Depends(user_id), db: Session = Depends(get_db)) -> dict[str, object]:
    rows = list(db.scalars(select(StrongStockTrade).where(StrongStockTrade.user_id == uid).order_by(StrongStockTrade.exit_at.desc()).limit(500)).all())
    return {"items": [trade_payload(row) for row in rows]}


@router.get("/performance")
def performance_endpoint(uid: str = Depends(user_id), db: Session = Depends(get_db)) -> dict[str, object]:
    return performance(db, uid)


@router.get("/notifications")
def notifications(uid: str = Depends(user_id), db: Session = Depends(get_db)) -> dict[str, object]:
    rows = list(db.scalars(select(StrongStockNotification).where(StrongStockNotification.user_id == uid).order_by(StrongStockNotification.created_at.desc()).limit(200)).all())
    return {"unread": sum(not row.read for row in rows), "items": [{"id": row.id, "eventType": row.event_type, "title": row.title, "message": row.message, "priority": row.priority, "read": row.read, "createdAt": row.created_at} for row in rows]}


@router.post("/notifications/{notification_id}/read")
def read_notification(notification_id: int, uid: str = Depends(user_id), db: Session = Depends(get_db)) -> dict[str, object]:
    row = db.get(StrongStockNotification, notification_id)
    if row is None or row.user_id != uid:
        raise HTTPException(404, "找不到通知")
    row.read = True; db.commit()
    return {"status": "READ"}


class SettingsBody(BaseModel):
    paper_enabled: bool = True
    config: dict[str, object] = Field(default_factory=dict)


@router.get("/settings")
def get_settings(uid: str = Depends(user_id), db: Session = Depends(get_db)) -> dict[str, object]:
    setting, account, config = ensure_defaults(db, uid)
    return {"paperEnabled": setting.paper_enabled, "tradingPaused": account.trading_paused, "config": config, "liveTradingAvailable": False}


@router.patch("/settings")
def save_settings(body: SettingsBody, uid: str = Depends(user_id), db: Session = Depends(get_db)) -> dict[str, object]:
    setting, _account, _config = ensure_defaults(db, uid)
    allowed = set(merged_config())
    unknown = set(body.config) - allowed
    if unknown:
        raise HTTPException(422, f"不支援的設定：{', '.join(sorted(unknown))}")
    merged = merged_config(body.config)
    positive_numbers = (
        "minimumPrice", "minimumAverageTurnover20d", "minimumDataCompleteness", "minimumWatchScore",
        "minimumEntryScore", "minimumRiskReward", "maximumDailyRisePct", "maximumDistanceMa20Pct",
        "minimumBreakoutVolumeRatio", "maximumPositionCapital", "initialPositionCapital",
        "maximumPositionPct", "maximumIndustryPct", "maximumOpenPositions", "riskPerTrade",
        "commissionRate", "commissionDiscount", "minimumCommission", "taxRate", "slippageBps",
        "maximumHoldingDays", "minimumHoldingDays", "dataReadyPollMinutes",
    )
    try:
        if any(dec(merged[key]) < 0 for key in positive_numbers):
            raise ValueError("數值不可為負數")
        if not 0 <= dec(merged["minimumDataCompleteness"]) <= 1:
            raise ValueError("資料完整度必須介於0與1")
        if not 0 <= dec(merged["minimumWatchScore"]) <= dec(merged["minimumEntryScore"]) <= 100:
            raise ValueError("觀察及進場分數必須介於0與100，且進場門檻不得低於觀察門檻")
        if sum(int(str(merged[key])) for key in ("firstEntryPct", "confirmationAddPct", "trendAddPct")) != 100:
            raise ValueError("三段部位比例合計必須為100%")
        if int(str(merged["minimumHoldingDays"])) > int(str(merged["maximumHoldingDays"])):
            raise ValueError("最短持有天數不可大於最長持有天數")
        time.fromisoformat(str(merged["dataReadyStartTime"]))
        time.fromisoformat(str(merged["dataReadyEndTime"]))
    except (ArithmeticError, TypeError, ValueError) as exc:
        raise HTTPException(422, f"強勢股設定無效：{exc}") from exc
    setting.paper_enabled = body.paper_enabled
    setting.config_json = json.dumps(merged, ensure_ascii=False)
    audit(db, uid, "SETTINGS_UPDATED", details={"paperEnabled": body.paper_enabled, "keys": sorted(body.config)})
    db.commit()
    return get_settings(uid, db)


@router.post("/paper/start")
def start_paper(uid: str = Depends(user_id), db: Session = Depends(get_db)) -> dict[str, object]:
    setting, account, _ = ensure_defaults(db, uid)
    setting.paper_enabled = True; account.trading_paused = False; audit(db, uid, "PAPER_STARTED"); db.commit()
    return {"status": "RUNNING", "mode": "PAPER"}


@router.post("/paper/pause")
def pause_paper(uid: str = Depends(user_id), db: Session = Depends(get_db)) -> dict[str, object]:
    _setting, account, _ = ensure_defaults(db, uid)
    account.trading_paused = True; audit(db, uid, "PAPER_PAUSED"); db.commit()
    return {"status": "PAUSED", "mode": "PAPER"}


class CloseBody(BaseModel):
    price: Decimal = Field(gt=0)
    reason: str = Field(default="使用者手動模擬賣出", min_length=2, max_length=300)


@router.post("/positions/{position_id}/close")
def close(position_id: str, body: CloseBody, uid: str = Depends(user_id), db: Session = Depends(get_db)) -> dict[str, object]:
    row = db.get(StrongStockPosition, position_id)
    if row is None or row.user_id != uid or row.status != "OPEN":
        raise HTTPException(404, "找不到未平倉的強勢股模擬部位")
    trade = close_position(db, row, body.price, datetime.now(UTC), body.reason)
    return trade_payload(trade)


@router.get("/data-status")
def data_status(db: Session = Depends(get_db)) -> dict[str, object]:
    latest = db.scalar(select(StrongStockDataRun).order_by(StrongStockDataRun.started_at.desc()).limit(1))
    return {
        "status": latest.status if latest else "WAITING", "latestTradeDate": latest.trade_date if latest else None,
        "lastSuccessfulUpdate": latest.completed_at if latest and latest.status == "COMPLETED" else None,
        "sources": parse_json(latest.source_json, {}) if latest else {}, "missing": parse_json(latest.missing_json, []) if latest else [],
        "error": latest.error_message if latest else "尚未完成第一次盤後更新", "historicalBacktestReady": False,
    }


class BacktestBody(BaseModel):
    start_date: date
    end_date: date
    benchmark: str = "0050"
    mode: Literal['FULL', 'FREE_PRICE_VOLUME'] = 'FULL'
    symbols: list[str] = Field(default_factory=list, max_length=30)


@router.get('/backtest-coverage')
def backtest_coverage(start_date: date, end_date: date, db: Session = Depends(get_db)) -> dict[str, object]:
    if end_date < start_date or (end_date - start_date).days > 366 * 10:
        raise HTTPException(422, '請選擇正確日期，範圍最多十年')
    from ..services.strong_stock_history import history_coverage
    return history_coverage(db, start_date, end_date)


@router.post("/backtests")
def create_backtest(body: BacktestBody, background_tasks: BackgroundTasks, uid: str = Depends(user_id), db: Session = Depends(get_db)) -> dict[str, object]:
    if body.end_date < body.start_date:
        raise HTTPException(422, "結束日期不可早於開始日期")
    if (body.end_date - body.start_date).days > 366 * 10:
        raise HTTPException(422, '日期範圍最多十年')
    if body.mode == 'FREE_PRICE_VOLUME':
        from ..services.strong_stock_backtest import DEFAULT_SYMBOLS, run_backtest
        if body.benchmark != '0050':
            raise HTTPException(422, '免費版基準為 0050')
        today = datetime.now(ZoneInfo('Asia/Taipei')).date()
        end = min(body.end_date, today - timedelta(days=1))
        if body.start_date > end or (end - body.start_date).days > 366 * 5:
            raise HTTPException(422, '請選擇已完成交易日，回測範圍最多五年')
        symbols = list(dict.fromkeys(s.strip().upper() for s in body.symbols)) or DEFAULT_SYMBOLS
        if any(not re.fullmatch(r'[0-9]{4}\.(TW|TWO)', s) or s == '0050.TW' for s in symbols):
            raise HTTPException(422, '股票代碼格式：2330.TW（上市）或 3693.TWO（上櫃），基準 ETF 不納入股票池')
        # A process restart can interrupt background work. Do not leave stale jobs running forever.
        stale = list(db.scalars(select(StrongStockBacktestJob).where(
            StrongStockBacktestJob.user_id == uid, StrongStockBacktestJob.status == 'RUNNING',
            StrongStockBacktestJob.created_at < datetime.now(UTC) - timedelta(minutes=5))).all())
        for previous in stale:
            previous.status = 'FAILED'
            previous.result_json = json.dumps({'message': '回測逾時或服務重啟，請重新執行。'}, ensure_ascii=False)
            previous.completed_at = datetime.now(UTC)
        db.flush()
        active = db.scalar(select(StrongStockBacktestJob.id).where(
            StrongStockBacktestJob.user_id == uid, StrongStockBacktestJob.status == 'RUNNING'))
        if active:
            raise HTTPException(409, '已有回測執行中，請等待完成')
        job = StrongStockBacktestJob(id=str(uuid4()), user_id=uid, start_date=body.start_date, end_date=end,
                                    status='RUNNING', request_json=json.dumps({**body.model_dump(mode='json'), 'symbols': symbols}))
        db.add(job)
        db.commit()
        background_tasks.add_task(run_backtest, job.id, symbols, body.start_date, end)
        return {'id': job.id, 'status': job.status, 'result': {}}
    from ..services.strong_stock_history import history_coverage
    if body.symbols:
        raise HTTPException(422, '正式策略使用當時保存的完整股票池，不使用自訂價量名單')
    coverage = history_coverage(db, body.start_date, body.end_date)
    if coverage['ready']:
        from ..services.strong_stock_replay import run_job
        active = db.scalar(select(StrongStockBacktestJob.id).where(
            StrongStockBacktestJob.user_id == uid, StrongStockBacktestJob.status == 'RUNNING',
            StrongStockBacktestJob.created_at >= datetime.now(UTC) - timedelta(minutes=5)))
        if active:
            raise HTTPException(409, '已有回測執行中，請等待完成')
        setting = db.get(StrongStockSetting, uid)
        parameters = merged_config(parse_json(setting.config_json, {}) if setting else {})
        job = StrongStockBacktestJob(id=str(uuid4()), user_id=uid, start_date=body.start_date,
                                    end_date=body.end_date, status='RUNNING',
                                    request_json=json.dumps({**body.model_dump(mode='json'), 'parameters': parameters}),
                                    data_status_json=json.dumps(coverage, ensure_ascii=False))
        db.add(job)
        db.commit()
        background_tasks.add_task(run_job, job.id)
        return {'id': job.id, 'status': job.status, 'result': {}}
    job = StrongStockBacktestJob(
        id=str(uuid4()), user_id=uid, start_date=body.start_date, end_date=body.end_date,
        status="DATA_INSUFFICIENT", data_status_json=json.dumps(coverage, ensure_ascii=False),
        request_json=body.model_dump_json(), result_json=json.dumps(coverage, ensure_ascii=False), completed_at=datetime.now(UTC),
    )
    db.add(job); audit(db, uid, "BACKTEST_BLOCKED_DATA_INSUFFICIENT", "BACKTEST", job.id, {"missing": coverage['missing']}); db.commit()
    return {"id": job.id, "status": job.status, "result": parse_json(job.result_json, {})}


@router.get("/backtests")
def backtests(uid: str = Depends(user_id), db: Session = Depends(get_db)) -> dict[str, object]:
    rows = list(db.scalars(select(StrongStockBacktestJob).where(StrongStockBacktestJob.user_id == uid).order_by(StrongStockBacktestJob.created_at.desc()).limit(100)).all())
    return {"items": [{"id": row.id, "mode": parse_json(row.request_json, {}).get('mode', 'FULL'), "startDate": row.start_date, "endDate": row.end_date, "status": row.status, "dataStatus": {k: v for k, v in parse_json(row.data_status_json, {}).items() if k != 'minutePrices'}, "result": parse_json(row.result_json, {}), "createdAt": row.created_at, "completedAt": row.completed_at} for row in rows]}


@router.get('/backtests/{job_id}')
def backtest_detail(job_id: str, uid: str = Depends(user_id), db: Session = Depends(get_db)) -> dict[str, object]:
    job = db.get(StrongStockBacktestJob, job_id)
    if job is None or job.user_id != uid:
        raise HTTPException(404, '找不到回測任務')
    created = job.created_at.replace(tzinfo=UTC) if job.created_at.tzinfo is None else job.created_at
    if job.status == 'RUNNING' and datetime.now(UTC) - created > timedelta(minutes=5):
        job.status = 'FAILED'
        job.result_json = json.dumps({'message': '回測逾時或服務重啟，請重新執行。'}, ensure_ascii=False)
        job.completed_at = datetime.now(UTC)
        db.commit()
    return {'id': job.id, 'status': job.status, 'result': parse_json(job.result_json, {})}


@router.get("/stream")
async def stream(uid: str = Depends(user_id)) -> StreamingResponse:
    async def events():
        while True:
            try:
                with SessionLocal() as db:
                    payload = dashboard_payload(db, uid)
                yield f"event: dashboard\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
            except asyncio.CancelledError:
                break
            except Exception as exc:
                yield f"event: error\ndata: {json.dumps({'message': str(exc)}, ensure_ascii=False)}\n\n"
            await asyncio.sleep(15)
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
