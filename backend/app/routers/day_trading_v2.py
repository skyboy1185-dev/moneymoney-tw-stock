import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import SessionLocal, get_db
from ..day_trading_v2_models import (
    DayTradeV2AuditEvent, DayTradeV2BacktestJob, DayTradeV2Fill,
    DayTradeV2CandidateState, DayTradeV2Notification, DayTradeV2Order, DayTradeV2Position,
    DayTradeV2RiskDaily, DayTradeV2Robot, DayTradeV2Setting,
    DayTradeV2RuntimeState, DayTradeV2ScheduleEvent, DayTradeV2Signal,
    DayTradeV2SkipStat, DayTradeV2StrategyVersion, DayTradeV2Trade,
)
from ..services.day_trading_v2 import (
    DEFAULT_CONFIG, STRATEGIES, MinuteBar, calculate_position_size,
    calculate_trade_result, dec, evaluate_strategies, exit_action, market_gate_reasons,
    merged_config, money, performance, resolve_duplicate_signals, risk_status, signal_level,
    run_backtest,
)
from ..services.day_trading import day_trading_engine


router = APIRouter(prefix="/day-trading-v2", tags=["day-trading-v2"])
TAIPEI = ZoneInfo("Asia/Taipei")
MODE_VALUES = {"PAPER", "LIVE", "BACKTEST"}


def _user_id(x_user_id: str | None = Header(default=None, min_length=8, max_length=80)) -> str:
    return x_user_id or "demo-user"


def _json(value: str, fallback):
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=UTC) if value and value.tzinfo is None else value


def _today_bounds() -> tuple[datetime, datetime]:
    local = datetime.now(TAIPEI)
    start = datetime.combine(local.date(), datetime.min.time(), tzinfo=TAIPEI).astimezone(UTC)
    return start, start + timedelta(days=1)


def _month_bounds() -> tuple[datetime, datetime]:
    local = datetime.now(TAIPEI)
    start_local = datetime(local.year, local.month, 1, tzinfo=TAIPEI)
    end_local = datetime(local.year + (local.month == 12), (local.month % 12) + 1, 1, tzinfo=TAIPEI)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def _ensure_defaults(db: Session, user_id: str) -> tuple[DayTradeV2Setting, list[DayTradeV2Robot]]:
    setting = db.get(DayTradeV2Setting, user_id)
    if setting is None:
        setting = DayTradeV2Setting(user_id=user_id, trade_mode="PAPER", live_enabled=False, config_json=json.dumps(DEFAULT_CONFIG))
        db.add(setting)
    robots = list(db.scalars(select(DayTradeV2Robot).where(DayTradeV2Robot.user_id == user_id)).all())
    existing = {robot.strategy_id for robot in robots}
    for robot in robots:
        if robot.status == "HALTED_TODAY" and robot.status_date != datetime.now(TAIPEI).date():
            robot.status = "READY"
            robot.status_date = None
            robot.consecutive_losses = 0
    for strategy_id, name, allocation in STRATEGIES:
        if strategy_id not in existing:
            robot = DayTradeV2Robot(user_id=user_id, strategy_id=strategy_id, name=name, allocation=allocation, parameters_json="{}")
            db.add(robot)
            robots.append(robot)
        version = db.scalar(select(DayTradeV2StrategyVersion).where(
            DayTradeV2StrategyVersion.strategy_id == strategy_id,
            DayTradeV2StrategyVersion.version == "2.0.0",
        ))
        if version is None:
            db.add(DayTradeV2StrategyVersion(
                strategy_id=strategy_id, version="2.0.0",
                definition_json=json.dumps({"side": "LONG", "name": name, "lookahead": False}),
            ))
    db.commit()
    return setting, robots


def _trade_dict(row: DayTradeV2Trade) -> dict[str, object]:
    return {
        "id": row.id, "mode": row.mode, "symbol": row.symbol, "stockName": row.stock_name,
        "strategyId": row.strategy_id, "strategyVersion": row.strategy_version, "side": row.side,
        "quantity": row.quantity, "signalTime": row.signal_time, "entryOrderTime": row.entry_order_time,
        "entryFillTime": row.entry_fill_time, "entryPrice": str(row.entry_price),
        "exitSignalTime": row.exit_signal_time, "exitOrderTime": row.exit_order_time,
        "exitFillTime": row.exit_fill_time, "exitPrice": str(row.exit_price),
        "grossPnl": str(row.gross_pnl), "buyFee": str(row.buy_fee), "sellFee": str(row.sell_fee),
        "transactionTax": str(row.transaction_tax), "slippage": str(row.slippage),
        "otherCost": str(row.other_cost), "netPnl": str(row.net_pnl),
        "netReturnPct": str(row.net_return_pct), "entryReason": row.entry_reason, "exitReason": row.exit_reason,
    }


def _position_dict(row: DayTradeV2Position) -> dict[str, object]:
    unrealized = money((row.current_price - row.entry_price) * row.quantity)
    return {
        "id": row.id, "mode": row.mode, "symbol": row.symbol, "stockName": row.stock_name, "sector": row.sector,
        "strategyId": row.strategy_id, "strategyVersion": row.strategy_version, "signalId": row.signal_id,
        "side": row.side, "quantity": row.quantity, "entryPrice": str(row.entry_price),
        "currentPrice": str(row.current_price), "stopPrice": str(row.stop_price),
        "firstTargetPrice": str(row.first_target_price), "trailingStopPrice": str(row.trailing_stop_price),
        "usedCapital": str(row.used_capital), "unrealizedGrossPnl": str(unrealized),
        "entryTime": row.entry_time, "status": row.status, "entryReasons": _json(row.entry_reasons_json, []),
        "confidence": str(row.confidence), "riskReward": str(row.risk_reward),
    }


def _notification(db: Session, *, user_id: str, mode: str, event_id: str, event_type: str, title: str, message: str, payload: dict | None = None) -> None:
    exists = db.scalar(select(DayTradeV2Notification).where(
        DayTradeV2Notification.user_id == user_id, DayTradeV2Notification.event_id == event_id,
    ))
    if not exists:
        db.add(DayTradeV2Notification(user_id=user_id, event_id=event_id, mode=mode, event_type=event_type, title=title, message=message, payload_json=json.dumps(payload or {}, ensure_ascii=False)))


def _audit(db: Session, user_id: str, action: str, mode: str, details: dict | None = None, entity_id: str = "") -> None:
    db.add(DayTradeV2AuditEvent(user_id=user_id, action=action, mode=mode, entity_id=entity_id, details_json=json.dumps(details or {}, ensure_ascii=False)))


def _runtime_dict(runtime: DayTradeV2RuntimeState | None, config: dict[str, object]) -> dict[str, object]:
    now = _now()
    heartbeat_at = _aware(runtime.heartbeat_at) if runtime else None
    quote_at = _aware(runtime.last_quote_at) if runtime else None
    heartbeat_stale = bool(heartbeat_at and (now - heartbeat_at).total_seconds() > int(config["heartbeatTimeoutSeconds"]))
    quote_stale = bool(runtime and runtime.scanning and (quote_at is None or (now - quote_at).total_seconds() > int(config["quoteTimeoutSeconds"])))
    status = "ALERT" if heartbeat_stale or quote_stale else runtime.status if runtime else "WAITING"
    return {
        "running": bool(runtime and runtime.status == "RUNNING" and not heartbeat_stale),
        "status": status, "phase": runtime.phase if runtime else "BEFORE_INITIALIZATION",
        "autoStart": runtime.auto_start if runtime else bool(config["autoStart"]),
        "initialized": bool(runtime and runtime.initialized),
        "receivingQuotes": bool(runtime and runtime.receiving_quotes and not quote_stale),
        "scanning": bool(runtime and runtime.scanning and not heartbeat_stale),
        "orderAllowed": bool(runtime and runtime.order_allowed and not heartbeat_stale and not quote_stale),
        "heartbeatAt": runtime.heartbeat_at if runtime else None, "lastQuoteAt": runtime.last_quote_at if runtime else None,
        "lastScanAt": runtime.last_scan_at if runtime else None, "lastBarAt": runtime.last_bar_at if runtime else None,
        "nextScanAt": runtime.next_scan_at if runtime else None, "nextEventType": runtime.next_event_type if runtime else "",
        "nextEventAt": runtime.next_event_at if runtime else None,
        "scannedStockCount": runtime.scanned_stock_count if runtime else 0,
        "candidateCount": runtime.candidate_count if runtime else 0, "signalCount": runtime.signal_count if runtime else 0,
        "orderCount": runtime.order_count if runtime else 0, "skippedCount": runtime.skipped_count if runtime else 0,
        "completedTradeCount": runtime.completed_trade_count if runtime else 0,
        "latestError": runtime.latest_error if runtime else "", "heartbeatStale": heartbeat_stale, "quoteStale": quote_stale,
    }


def _candidate_dict(row: DayTradeV2CandidateState) -> dict[str, object]:
    return {
        "symbol": row.symbol, "stockName": row.stock_name, "sector": row.sector,
        "strategyId": row.strategy_id, "confidence": str(row.confidence), "signalLevel": row.signal_level,
        "primaryReason": row.primary_reason, "reasons": _json(row.reasons_json, []),
        "quoteAt": row.quote_at, "barAt": row.bar_at, "scannedAt": row.scanned_at,
    }


def _record_skip(db: Session, user_id: str, mode: str, trading_date: date, reason: str) -> None:
    if not reason:
        return
    row = db.scalar(select(DayTradeV2SkipStat).where(
        DayTradeV2SkipStat.user_id == user_id, DayTradeV2SkipStat.mode == mode,
        DayTradeV2SkipStat.trading_date == trading_date, DayTradeV2SkipStat.reason == reason,
    ))
    if row is None:
        db.add(DayTradeV2SkipStat(user_id=user_id, mode=mode, trading_date=trading_date, reason=reason, occurrence_count=1))
    else:
        row.occurrence_count += 1


def _dashboard(db: Session, user_id: str) -> dict[str, object]:
    setting, robots = _ensure_defaults(db, user_id)
    mode = setting.trade_mode if setting.trade_mode in MODE_VALUES else "PAPER"
    config = merged_config(_json(setting.config_json, {}))
    today_start, today_end = _today_bounds()
    month_start, month_end = _month_bounds()
    all_trades = list(db.scalars(select(DayTradeV2Trade).where(DayTradeV2Trade.user_id == user_id, DayTradeV2Trade.mode == mode).order_by(DayTradeV2Trade.exit_fill_time.desc())).all())
    today_trades = [row for row in all_trades if today_start <= row.exit_fill_time < today_end]
    month_trades = [row for row in all_trades if month_start <= row.exit_fill_time < month_end]
    positions = list(db.scalars(select(DayTradeV2Position).where(
        DayTradeV2Position.user_id == user_id, DayTradeV2Position.mode == mode, DayTradeV2Position.status == "OPEN",
    )).all())
    today_perf = performance([{"netPnl": row.net_pnl} for row in today_trades], config["initialCapital"])
    month_perf = performance([{"netPnl": row.net_pnl} for row in month_trades], config["initialCapital"])
    all_perf = performance([{"netPnl": row.net_pnl} for row in all_trades], config["initialCapital"])
    realized = sum((row.net_pnl for row in today_trades), Decimal("0"))
    unrealized = sum(((row.current_price - row.entry_price) * row.quantity for row in positions), Decimal("0"))
    used = sum((row.used_capital for row in positions), Decimal("0"))
    trading_date = datetime.now(TAIPEI).date()
    runtime = db.scalar(select(DayTradeV2RuntimeState).where(
        DayTradeV2RuntimeState.user_id == user_id, DayTradeV2RuntimeState.mode == mode,
        DayTradeV2RuntimeState.trading_date == trading_date,
    ))
    runtime_data = _runtime_dict(runtime, config)
    candidate_rows = list(db.scalars(select(DayTradeV2CandidateState).where(
        DayTradeV2CandidateState.user_id == user_id, DayTradeV2CandidateState.mode == mode,
        DayTradeV2CandidateState.trading_date == trading_date,
    ).order_by(DayTradeV2CandidateState.confidence.desc()).limit(30)).all())
    held_symbols = {row.symbol for row in positions}
    candidates = [row for row in candidate_rows if row.symbol not in held_symbols][:10]
    skip_stats = list(db.scalars(select(DayTradeV2SkipStat).where(
        DayTradeV2SkipStat.user_id == user_id, DayTradeV2SkipStat.mode == mode,
        DayTradeV2SkipStat.trading_date == trading_date,
    ).order_by(DayTradeV2SkipStat.occurrence_count.desc()).limit(30)).all())
    runtime_halted = runtime_data["status"] in {"ALERT", "STOPPED", "EMERGENCY_STOP", "RISK_HALTED"}
    system_status = "HALTED" if runtime_halted or any(robot.status == "EMERGENCY_STOP" for robot in robots) else risk_status(realized, config)
    robot_items = []
    for robot in sorted(robots, key=lambda item: item.id or 0):
        robot_trades = [row for row in all_trades if row.strategy_id == robot.strategy_id]
        robot_today = [row for row in today_trades if row.strategy_id == robot.strategy_id]
        robot_month = [row for row in month_trades if row.strategy_id == robot.strategy_id]
        robot_items.append({
            "id": robot.id, "strategyId": robot.strategy_id, "name": robot.name, "enabled": robot.enabled,
            "side": robot.side, "allocation": str(robot.allocation), "status": robot.status,
            "consecutiveLosses": robot.consecutive_losses,
            "today": performance([{"netPnl": row.net_pnl} for row in robot_today], config["initialCapital"]),
            "month": performance([{"netPnl": row.net_pnl} for row in robot_month], config["initialCapital"]),
            "all": performance([{"netPnl": row.net_pnl} for row in robot_trades], config["initialCapital"]),
            "usedCapital": str(sum((row.used_capital for row in positions if row.strategy_id == robot.strategy_id), Decimal("0"))),
            "lastTradeTime": robot_trades[0].exit_fill_time if robot_trades else None,
        })
    return {
        "systemName": "超強AI當沖系統", "mode": mode, "systemStatus": system_status,
        "liveTrading": {"available": False, "enabled": False, "broker": None, "reason": "尚未設定並驗證券商API"},
        "marketData": {"realtime": "TWSE MIS", "historicalMinute": None, "backtestReady": False, "message": "尚未設定合格的歷史分鐘行情來源"},
        "config": config, "today": today_perf, "month": month_perf, "all": all_perf,
        "realizedPnl": str(money(realized)), "unrealizedPnl": str(money(unrealized)),
        "netPnl": str(money(realized + unrealized)), "usedCapital": str(money(used)),
        "availableCapital": str(money(dec(config["initialCapital"]) - used)),
        "remainingDailyRisk": str(money(max(dec(config["dailyStopLoss"]) + min(realized, Decimal("0")), Decimal("0")))),
        "positions": [_position_dict(row) for row in positions], "recentTrades": [_trade_dict(row) for row in all_trades[:100]],
        "robots": robot_items, "runtime": runtime_data,
        "topCandidates": [_candidate_dict(row) for row in candidates],
        "skipReasons": [{"reason": row.reason, "count": row.occurrence_count} for row in skip_stats],
    }


@router.get("/dashboard")
def dashboard(user_id: str = Depends(_user_id), db: Session = Depends(get_db)) -> dict[str, object]:
    return _dashboard(db, user_id)


def _today_runtime(db: Session, user_id: str, mode: str, config: dict[str, object]) -> DayTradeV2RuntimeState:
    trading_date = datetime.now(TAIPEI).date()
    runtime = db.scalar(select(DayTradeV2RuntimeState).where(
        DayTradeV2RuntimeState.user_id == user_id, DayTradeV2RuntimeState.mode == mode,
        DayTradeV2RuntimeState.trading_date == trading_date,
    ))
    if runtime is None:
        runtime = DayTradeV2RuntimeState(
            user_id=user_id, mode=mode, trading_date=trading_date,
            auto_start=bool(config["autoStart"]), status="WAITING",
        )
        db.add(runtime)
        db.flush()
    return runtime


def _set_runtime_status(db: Session, user_id: str, action: str, status: str) -> dict[str, object]:
    setting, robots = _ensure_defaults(db, user_id)
    config = merged_config(_json(setting.config_json, {}))
    runtime = _today_runtime(db, user_id, setting.trade_mode, config)
    runtime.status = status
    runtime.initialized = runtime.initialized or status == "RUNNING"
    runtime.initialized_at = runtime.initialized_at or (_now() if runtime.initialized else None)
    runtime.started_at = runtime.started_at or (_now() if status == "RUNNING" else None)
    runtime.scanning = status == "RUNNING"
    runtime.order_allowed = False
    runtime.latest_error = "" if status == "RUNNING" else runtime.latest_error
    if status == "RUNNING":
        for robot in robots:
            if robot.enabled and robot.status in {"EMERGENCY_STOP", "DISABLED"}:
                robot.status = "READY"
    _audit(db, user_id, action, setting.trade_mode, {"runtimeStatus": status})
    db.commit()
    return _runtime_dict(runtime, config)


@router.get("/runtime")
def runtime_status(user_id: str = Depends(_user_id), db: Session = Depends(get_db)) -> dict[str, object]:
    setting, _ = _ensure_defaults(db, user_id)
    config = merged_config(_json(setting.config_json, {}))
    return _runtime_dict(_today_runtime(db, user_id, setting.trade_mode, config), config)


@router.post("/control/start-today")
def start_today(user_id: str = Depends(_user_id), db: Session = Depends(get_db)) -> dict[str, object]:
    return _set_runtime_status(db, user_id, "START_TODAY", "RUNNING")


@router.post("/control/pause")
def pause_trading(user_id: str = Depends(_user_id), db: Session = Depends(get_db)) -> dict[str, object]:
    return _set_runtime_status(db, user_id, "PAUSE_NEW_TRADES", "PAUSED")


@router.post("/control/resume")
def resume_trading(user_id: str = Depends(_user_id), db: Session = Depends(get_db)) -> dict[str, object]:
    return _set_runtime_status(db, user_id, "RESUME_TRADING", "RUNNING")


@router.post("/control/stop")
def stop_strategies(user_id: str = Depends(_user_id), db: Session = Depends(get_db)) -> dict[str, object]:
    return _set_runtime_status(db, user_id, "STOP_ALL_STRATEGIES", "STOPPED")


@router.get("/settings")
def settings(user_id: str = Depends(_user_id), db: Session = Depends(get_db)) -> dict[str, object]:
    setting, _ = _ensure_defaults(db, user_id)
    return {"mode": setting.trade_mode, "liveEnabled": False, "config": merged_config(_json(setting.config_json, {})), "liveLockReason": "尚未設定並驗證券商API"}


class SettingsBody(BaseModel):
    mode: str = "PAPER"
    config: dict[str, object] = Field(default_factory=dict)


@router.put("/settings")
def save_settings(body: SettingsBody, user_id: str = Depends(_user_id), db: Session = Depends(get_db)) -> dict[str, object]:
    if body.mode not in {"PAPER", "BACKTEST", "LIVE"}:
        raise HTTPException(422, "不支援的交易模式")
    if body.mode == "LIVE":
        raise HTTPException(409, "真實交易已鎖定：尚未設定並驗證券商API")
    setting, _ = _ensure_defaults(db, user_id)
    config = merged_config(body.config)
    if dec(config["initialCapital"]) != Decimal("3000000"):
        raise HTTPException(422, "目前共用總資金必須為3,000,000元")
    thresholds = [dec(config[key]) for key in ("generalScanThreshold", "watchThreshold", "nearEntryThreshold", "riskGateThreshold")]
    if thresholds != sorted(set(thresholds)) or thresholds[0] < 0 or thresholds[-1] > 100:
        raise HTTPException(422, "訊號分級必須依序為一般 < 觀察 < 接近進場 < 風控，且介於0至100分")
    if not 1 <= int(config["scanIntervalSeconds"]) <= 5:
        raise HTTPException(422, "進場策略掃描頻率必須介於1至5秒")
    if not 1 <= int(config["heartbeatSeconds"]) <= 30 or int(config["heartbeatTimeoutSeconds"]) <= int(config["heartbeatSeconds"]):
        raise HTTPException(422, "心跳更新必須介於1至30秒，逾時門檻必須大於更新間隔")
    time_keys = (
        "resetTime", "universeLoadTime", "historyLoadTime", "healthCheckTime", "candidatePoolTime",
        "readyNotificationTime", "marketOpenTime", "openingRangeReadyTime", "summary1000Time",
        "summary1100Time", "summary1200Time", "latestEntryTime", "forcedCloseTime", "marketCloseTime",
        "brokerSyncTime", "closeReportTime",
    )
    try:
        schedule = [datetime.strptime(str(config[key]), "%H:%M:%S").time() for key in time_keys]
    except ValueError as exc:
        raise HTTPException(422, "排程時間格式必須為HH:MM:SS") from exc
    if schedule != sorted(schedule):
        raise HTTPException(422, "每日排程時間必須依執行順序遞增")
    setting.trade_mode = body.mode
    setting.live_enabled = False
    setting.config_json = json.dumps(config)
    runtime = db.scalar(select(DayTradeV2RuntimeState).where(
        DayTradeV2RuntimeState.user_id == user_id, DayTradeV2RuntimeState.mode == body.mode,
        DayTradeV2RuntimeState.trading_date == datetime.now(TAIPEI).date(),
    ))
    if runtime:
        runtime.auto_start = bool(config["autoStart"])
    _audit(db, user_id, "SETTINGS_UPDATED", body.mode, {"config": config})
    db.commit()
    return {"mode": setting.trade_mode, "liveEnabled": False, "config": config}


class RobotBody(BaseModel):
    enabled: bool
    allocation: Decimal | None = None


@router.patch("/robots/{strategy_id}")
def update_robot(strategy_id: str, body: RobotBody, user_id: str = Depends(_user_id), db: Session = Depends(get_db)) -> dict[str, object]:
    _, robots = _ensure_defaults(db, user_id)
    robot = next((item for item in robots if item.strategy_id == strategy_id), None)
    if robot is None:
        raise HTTPException(404, "找不到機器人")
    robot.enabled = body.enabled
    robot.status = "READY" if body.enabled else "DISABLED"
    if body.allocation is not None:
        if body.allocation < 0 or body.allocation > Decimal("1050000"):
            raise HTTPException(422, "單台機器人資金不可超過總資金35%")
        robot.allocation = money(body.allocation)
    _audit(db, user_id, "ROBOT_UPDATED", "PAPER", {"strategyId": strategy_id, "enabled": body.enabled, "allocation": str(robot.allocation)})
    db.commit()
    return {"strategyId": robot.strategy_id, "enabled": robot.enabled, "allocation": str(robot.allocation)}


class PaperEntryBody(BaseModel):
    signal_id: str | None = None
    strategy_id: str
    symbol: str = Field(pattern=r"^[0-9A-Z]{2,12}$")
    stock_name: str = ""
    signal_price: Decimal = Field(gt=0)
    fill_price: Decimal = Field(gt=0)
    stop_price: Decimal = Field(gt=0)
    target_price: Decimal = Field(gt=0)
    confidence: Decimal = Field(ge=0, le=100)
    signal_time: datetime
    reasons: list[str] = Field(default_factory=list)
    sector: str = ""


@router.post("/paper/entries")
def paper_entry(body: PaperEntryBody, user_id: str = Depends(_user_id), db: Session = Depends(get_db)) -> dict[str, object]:
    setting, robots = _ensure_defaults(db, user_id)
    if setting.trade_mode != "PAPER":
        raise HTTPException(409, "只有模擬交易模式可以建立模擬部位")
    robot = next((item for item in robots if item.strategy_id == body.strategy_id and item.enabled and item.status == "READY"), None)
    if robot is None:
        raise HTTPException(409, "機器人未啟用或不存在")
    config = merged_config(_json(setting.config_json, {}))
    open_positions = list(db.scalars(select(DayTradeV2Position).where(
        DayTradeV2Position.user_id == user_id, DayTradeV2Position.mode == "PAPER", DayTradeV2Position.status == "OPEN",
    )).all())
    if any(row.symbol == body.symbol for row in open_positions):
        raise HTTPException(409, "重複股票訊號，未執行")
    if len(open_positions) >= int(config["maxOpenPositions"]):
        raise HTTPException(409, "超過同時持倉上限")
    if body.sector and sum(row.sector == body.sector for row in open_positions) >= int(config["maxSectorPositions"]):
        raise HTTPException(409, "超過產業集中限制")
    if body.confidence < dec(config["minimumConfidence"]):
        raise HTTPException(409, "訊號分數不足")
    now_local = _now().astimezone(TAIPEI)
    if now_local.time() >= datetime.strptime(str(config["latestEntryTime"]), "%H:%M:%S").time():
        raise HTTPException(409, "13:20後禁止建立新部位")
    used = sum((row.used_capital for row in open_positions), Decimal("0"))
    available = dec(config["initialCapital"]) - used
    risk_budget = dec(config["maxRiskPerTrade"])
    day_start, day_end = _today_bounds()
    daily_trades = list(db.scalars(select(DayTradeV2Trade).where(
        DayTradeV2Trade.user_id == user_id, DayTradeV2Trade.mode == "PAPER",
        DayTradeV2Trade.exit_fill_time >= day_start, DayTradeV2Trade.exit_fill_time < day_end,
    )).all())
    daily_pnl = sum((row.net_pnl for row in daily_trades), Decimal("0"))
    state = risk_status(daily_pnl, config)
    if state == "HALTED":
        raise HTTPException(409, "已達每日虧損上限，系統停機")
    if state == "REDUCED":
        risk_budget /= 2
    robot_used = sum((row.used_capital for row in open_positions if row.strategy_id == body.strategy_id), Decimal("0"))
    quantity = calculate_position_size(
        price=body.fill_price, stop_price=body.stop_price, risk_budget=risk_budget,
        capital_limit=min(available, max(robot.allocation - robot_used, Decimal("0"))), lot_size=int(config["boardLotSize"]),
        allow_odd_lots=bool(config["allowOddLots"]),
    )
    if quantity <= 0:
        raise HTTPException(409, "資金不足或停損距離無效")
    now = _now()
    signal_id, order_id, position_id = body.signal_id or str(uuid4()), str(uuid4()), str(uuid4())
    risk_reward = (body.target_price - body.fill_price) / (body.fill_price - body.stop_price)
    db.add(DayTradeV2Signal(
        id=signal_id, user_id=user_id, mode="PAPER", strategy_id=body.strategy_id, strategy_version="2.0.0",
        symbol=body.symbol, stock_name=body.stock_name, side="LONG", signal_time=body.signal_time,
        signal_price=body.signal_price, confidence=body.confidence, risk_reward=risk_reward,
        stop_price=body.stop_price, target_price=body.target_price, status="EXECUTED",
        reasons_json=json.dumps(body.reasons, ensure_ascii=False), market_context_json=json.dumps({"sector": body.sector}, ensure_ascii=False),
    ))
    db.add(DayTradeV2Order(
        id=order_id, user_id=user_id, mode="PAPER", signal_id=signal_id, client_order_id=f"paper-{order_id}",
        broker_order_id=f"paper-{order_id}", symbol=body.symbol, side="BUY", order_price=body.fill_price,
        order_quantity=quantity, filled_quantity=quantity, status="FILLED", signal_at=body.signal_time,
        sent_at=now, broker_accepted_at=now,
    ))
    db.add(DayTradeV2Fill(order_id=order_id, mode="PAPER", broker_execution_id=f"paper-fill-{order_id}", price=body.fill_price, quantity=quantity, exchange_time=now, received_at=now))
    position = DayTradeV2Position(
        id=position_id, user_id=user_id, mode="PAPER", strategy_id=body.strategy_id, strategy_version="2.0.0",
        signal_id=signal_id, symbol=body.symbol, stock_name=body.stock_name, sector=body.sector, quantity=quantity,
        entry_price=body.fill_price, current_price=body.fill_price, stop_price=body.stop_price,
        first_target_price=body.target_price, trailing_stop_price=body.stop_price,
        used_capital=money(body.fill_price * quantity), entry_time=now, entry_reasons_json=json.dumps(body.reasons, ensure_ascii=False),
        confidence=body.confidence, risk_reward=risk_reward,
    )
    db.add(position)
    _notification(db, user_id=user_id, mode="PAPER", event_id=f"fill:{order_id}", event_type="BUY_FILLED", title="【超強AI當沖系統｜模擬交易｜買進成交】", message=f"{body.symbol} {body.stock_name}｜{quantity:,}股｜成交均價 {body.fill_price}")
    _audit(db, user_id, "PAPER_ENTRY_FILLED", "PAPER", {"quantity": quantity, "price": str(body.fill_price)}, position_id)
    db.commit()
    return _position_dict(position)


def _scan_now(user_id: str, db: Session, coordinator_now: datetime | None = None) -> dict[str, object]:
    """Evaluate verified MIS bars. The coordinator calls this every five seconds."""
    setting, _ = _ensure_defaults(db, user_id)
    config = merged_config(_json(setting.config_json, {}))
    if setting.trade_mode != "PAPER":
        return {"evaluated": 0, "executed": 0, "skipped": 0, "message": "目前模式不執行即時模擬訊號"}
    current = coordinator_now or _now()
    local_now = current.astimezone(TAIPEI)
    trading_date = local_now.date()
    runtime = _today_runtime(db, user_id, "PAPER", config)
    regime = day_trading_engine.market_regime()
    candidates = day_trading_engine.signals()
    realtime_quote_times: list[datetime] = []
    for candidate in candidates:
        if candidate.get("dataSource") != "TWSE MIS" or not candidate.get("quoteIsRealtime"):
            continue
        try:
            quote_time = datetime.fromisoformat(str(candidate.get("quoteTimestamp")))
            realtime_quote_times.append(quote_time if quote_time.tzinfo else quote_time.replace(tzinfo=UTC))
        except (TypeError, ValueError):
            continue
    latest_quote = max(realtime_quote_times, default=None)
    if latest_quote:
        runtime.last_quote_at = latest_quote
    entry_cutoff = datetime.strptime(str(config["latestEntryTime"]), "%H:%M:%S").time()
    market_open = datetime.strptime(str(config["marketOpenTime"]), "%H:%M:%S").time()
    market_close = datetime.strptime(str(config["marketCloseTime"]), "%H:%M:%S").time()
    in_market_hours = market_open <= local_now.time() < market_close
    quote_fresh = bool(latest_quote and abs((current - latest_quote).total_seconds()) <= int(config["quoteTimeoutSeconds"]))
    runtime.receiving_quotes = quote_fresh
    runtime.order_allowed = bool(
        runtime.status == "RUNNING" and in_market_hours and local_now.time() < entry_cutoff
        and quote_fresh and regime.get("dataStatus") == "normal"
    )
    if in_market_hours and not quote_fresh:
        runtime.latest_error = "行情中斷或延遲，已禁止建立新部位"
        _notification(
            db, user_id=user_id, mode="PAPER", event_id=f"market-data-interrupted:{user_id}:{trading_date}",
            event_type="MARKET_DATA_INTERRUPTED", title="行情中斷警報",
            message="超過行情逾時門檻仍未收到可靠即時行情，系統已停止建立新部位。",
        )
    elif quote_fresh and runtime.latest_error.startswith("行情中斷"):
        runtime.latest_error = ""
    existing_positions = list(db.scalars(select(DayTradeV2Position).where(
        DayTradeV2Position.user_id == user_id, DayTradeV2Position.mode == "PAPER", DayTradeV2Position.status == "OPEN",
    )).all())
    force_close_at = datetime.strptime(str(config["forcedCloseTime"]), "%H:%M:%S").time()
    exits: list[dict[str, object]] = []
    for position in list(existing_positions):
        quote = day_trading_engine.quote_for(position.symbol)
        if quote is not None and quote > 0:
            position.current_price = dec(quote)
            risk_distance = position.entry_price - position.stop_price
            if position.current_price > position.first_target_price:
                position.trailing_stop_price = max(position.trailing_stop_price, position.current_price - risk_distance)
        action = (
            {"reason": "收盤前強制平倉", "percentage": 100}
            if local_now.time() >= force_close_at
            else exit_action(
                current_price=position.current_price, stop_price=position.stop_price,
                first_target_price=position.first_target_price,
                trailing_stop_price=position.trailing_stop_price,
            )
        )
        if action:
            trade = close_position(position.id, CloseBody(
                fill_price=position.current_price, reason=str(action["reason"]), percentage=int(action["percentage"]),
            ), user_id, db)
            if int(action["percentage"]) < 100 and position.status == "OPEN":
                position.first_target_price = Decimal("99999999")
                position.trailing_stop_price = max(position.trailing_stop_price, position.entry_price)
                db.commit()
            exits.append(trade)
    if exits:
        existing_positions = list(db.scalars(select(DayTradeV2Position).where(
            DayTradeV2Position.user_id == user_id, DayTradeV2Position.mode == "PAPER", DayTradeV2Position.status == "OPEN",
        )).all())
    used = sum((row.used_capital for row in existing_positions), Decimal("0"))
    start, end = _today_bounds()
    daily_pnl = sum(db.scalars(select(DayTradeV2Trade.net_pnl).where(
        DayTradeV2Trade.user_id == user_id, DayTradeV2Trade.mode == "PAPER",
        DayTradeV2Trade.exit_fill_time >= start, DayTradeV2Trade.exit_fill_time < end,
    )).all(), Decimal("0"))
    day_risk = risk_status(daily_pnl, config)
    if day_risk == "HALTED":
        runtime.status = "RISK_HALTED"
        runtime.order_allowed = False
        _notification(
            db, user_id=user_id, mode="PAPER", event_id=f"daily-loss-limit:{user_id}:{trading_date}",
            event_type="DAILY_LOSS_LIMIT", title="已達每日虧損上限",
            message=f"今日已實現損益{money(daily_pnl)}元，五台機器人停止建立新部位。",
        )
    elif day_risk == "REDUCED":
        _notification(
            db, user_id=user_id, mode="PAPER", event_id=f"risk-reduced:{user_id}:{trading_date}",
            event_type="RISK_REDUCED", title="系統進入減半模式",
            message=f"今日已實現損益{money(daily_pnl)}元，後續新部位風險額度減半。",
        )
    evaluated = executed = skipped = generated = 0
    latest_bar_at: datetime | None = None
    items: list[dict[str, object]] = []
    for candidate in candidates:
        if candidate.get("direction") != "long":
            continue
        symbol = str(candidate.get("symbol") or "")
        if not symbol:
            continue
        raw_bars = day_trading_engine.minute_bars_for(symbol)
        winner = None
        duplicates = []
        bars: list[MinuteBar] = []
        candidate_reasons: list[str] = []
        if len(raw_bars) < 16:
            candidate_reasons.append("分鐘行情尚未累積完成")
        else:
            bars = [MinuteBar(
                timestamp=datetime.fromisoformat(str(row["timestamp"])), open=dec(row["open"]), high=dec(row["high"]),
                low=dec(row["low"]), close=dec(row["close"]), volume=int(row["volume"]),
            ) for row in raw_bars]
            winner, duplicates = resolve_duplicate_signals(evaluate_strategies(bars))
            latest_bar_at = max(latest_bar_at, bars[-1].timestamp) if latest_bar_at else bars[-1].timestamp
        evaluated += 1
        score = winner.confidence if winner else dec(candidate.get("confidenceScore") or 0)
        strategy_id = winner.strategy_id if winner else ""
        if winner is None and not candidate_reasons:
            candidate_reasons.append(str((candidate.get("warnings") or ["策略條件尚未完全符合"])[0]))
        if winner and score < dec(config["riskGateThreshold"]):
            candidate_reasons.append("信心分數不足")
        previous_candidate = db.scalar(select(DayTradeV2CandidateState).where(
            DayTradeV2CandidateState.user_id == user_id, DayTradeV2CandidateState.mode == "PAPER",
            DayTradeV2CandidateState.trading_date == trading_date, DayTradeV2CandidateState.symbol == symbol,
        ))
        primary_reason = candidate_reasons[0] if candidate_reasons else "已達下單前風控門檻"
        if previous_candidate is None:
            previous_candidate = DayTradeV2CandidateState(
                user_id=user_id, mode="PAPER", trading_date=trading_date, symbol=symbol,
                stock_name=str(candidate.get("stockName") or ""), sector=str((candidate.get("themes") or [""])[0]),
                scanned_at=current,
            )
            db.add(previous_candidate)
            previous_reason = ""
        else:
            previous_reason = previous_candidate.primary_reason
        previous_candidate.stock_name = str(candidate.get("stockName") or "")
        previous_candidate.sector = str((candidate.get("themes") or [""])[0])
        previous_candidate.strategy_id = strategy_id
        previous_candidate.confidence = score
        previous_candidate.signal_level = signal_level(score, config)
        previous_candidate.primary_reason = primary_reason
        previous_candidate.reasons_json = json.dumps(candidate_reasons or list(winner.reasons if winner else []), ensure_ascii=False)
        previous_candidate.quote_at = latest_quote if candidate.get("quoteIsRealtime") else None
        previous_candidate.bar_at = bars[-1].timestamp if bars else None
        previous_candidate.scanned_at = current
        if primary_reason and primary_reason != previous_reason:
            _record_skip(db, user_id, "PAPER", trading_date, primary_reason)
        if winner is None:
            continue
        signal_key = f"v2:{symbol}:{bars[-1].timestamp.isoformat()}:{winner.strategy_id}"
        if db.get(DayTradeV2Signal, signal_key):
            continue
        reasons = market_gate_reasons(
            now=current, market_crashing=dec(regime.get("score", 0)) <= -60,
            quote_reliable=quote_fresh and candidate.get("dataSource") == "TWSE MIS" and bool(candidate.get("quoteIsRealtime")),
            volume=int(candidate.get("volume") or 0), turnover=candidate.get("turnover") or 0,
            spread_pct=candidate.get("spreadPercentage") or 999,
            vwap_deviation_pct=candidate.get("vwapDeviationPercent") or 0,
            blocked=bool(candidate.get("tradeRestricted")), connection_ok=regime.get("dataStatus") == "normal" and quote_fresh,
            available_capital=dec(config["initialCapital"]) - used,
            open_positions=len(existing_positions), sector_positions=sum(
                row.sector == str((candidate.get("themes") or [""])[0]) for row in existing_positions
            ), realized_pnl=daily_pnl, config=config,
        )
        if winner.confidence < max(dec(config["minimumConfidence"]), dec(config["riskGateThreshold"])):
            reasons.append("訊號分數不足")
        if runtime.status != "RUNNING":
            reasons.append("系統目前不允許下單")
        risk_reward = (winner.target_price - winner.entry_price) / (winner.entry_price - winner.stop_price)
        if risk_reward < dec(config["minimumRiskReward"]):
            reasons.append("風險報酬比不足")
        for duplicate in duplicates:
            duplicate_id = f"v2:{symbol}:{bars[-1].timestamp.isoformat()}:{duplicate.strategy_id}"
            if not db.get(DayTradeV2Signal, duplicate_id):
                db.add(DayTradeV2Signal(
                    id=duplicate_id, user_id=user_id, mode="PAPER", strategy_id=duplicate.strategy_id,
                    strategy_version="2.0.0", symbol=symbol, stock_name=str(candidate.get("stockName") or ""),
                    signal_time=bars[-1].timestamp, signal_price=duplicate.entry_price, confidence=duplicate.confidence,
                    risk_reward=(duplicate.target_price - duplicate.entry_price) / (duplicate.entry_price - duplicate.stop_price),
                    stop_price=duplicate.stop_price, target_price=duplicate.target_price, status="SKIPPED",
                    reasons_json=json.dumps(duplicate.reasons, ensure_ascii=False), skip_reason="重複訊號，未執行",
                ))
                skipped += 1
                generated += 1
                _record_skip(db, user_id, "PAPER", trading_date, "重複訊號，未執行")
        if reasons:
            unique_reasons = list(dict.fromkeys(reasons))
            db.add(DayTradeV2Signal(
                id=signal_key, user_id=user_id, mode="PAPER", strategy_id=winner.strategy_id,
                strategy_version="2.0.0", symbol=symbol, stock_name=str(candidate.get("stockName") or ""),
                signal_time=bars[-1].timestamp, signal_price=winner.entry_price, confidence=winner.confidence,
                risk_reward=risk_reward, stop_price=winner.stop_price, target_price=winner.target_price,
                status="SKIPPED", reasons_json=json.dumps(winner.reasons, ensure_ascii=False),
                skip_reason="、".join(unique_reasons), market_context_json=json.dumps(regime, default=str, ensure_ascii=False),
            ))
            skipped += 1; generated += 1
            for reason in unique_reasons:
                _record_skip(db, user_id, "PAPER", trading_date, reason)
            items.append({"symbol": symbol, "status": "SKIPPED", "reason": "、".join(unique_reasons)})
            continue
        try:
            position = paper_entry(PaperEntryBody(
                signal_id=signal_key,
                strategy_id=winner.strategy_id, symbol=symbol, stock_name=str(candidate.get("stockName") or ""),
                signal_price=winner.entry_price, fill_price=winner.entry_price, stop_price=winner.stop_price,
                target_price=winner.target_price, confidence=winner.confidence, signal_time=bars[-1].timestamp,
                reasons=list(winner.reasons), sector=str((candidate.get("themes") or [""])[0]),
            ), user_id, db)
            executed += 1
            generated += 1
            created_position = db.get(DayTradeV2Position, str(position["id"]))
            if created_position is not None:
                existing_positions.append(created_position)
            used += dec(position["usedCapital"])
            items.append({"symbol": symbol, "status": "EXECUTED", "positionId": position["id"]})
        except HTTPException as exc:
            db.add(DayTradeV2Signal(
                id=signal_key, user_id=user_id, mode="PAPER", strategy_id=winner.strategy_id,
                strategy_version="2.0.0", symbol=symbol, stock_name=str(candidate.get("stockName") or ""),
                signal_time=bars[-1].timestamp, signal_price=winner.entry_price, confidence=winner.confidence,
                risk_reward=risk_reward, stop_price=winner.stop_price, target_price=winner.target_price,
                status="SKIPPED", reasons_json=json.dumps(winner.reasons, ensure_ascii=False), skip_reason=str(exc.detail),
            ))
            skipped += 1
            generated += 1
            _record_skip(db, user_id, "PAPER", trading_date, str(exc.detail))
            items.append({"symbol": symbol, "status": "SKIPPED", "reason": str(exc.detail)})
    runtime.last_scan_at = current
    runtime.last_bar_at = latest_bar_at or runtime.last_bar_at
    runtime.next_scan_at = current + timedelta(seconds=int(config["scanIntervalSeconds"]))
    runtime.scanned_stock_count = int(db.scalar(select(func.count()).select_from(DayTradeV2CandidateState).where(
        DayTradeV2CandidateState.user_id == user_id, DayTradeV2CandidateState.mode == "PAPER",
        DayTradeV2CandidateState.trading_date == trading_date,
    )) or 0)
    runtime.candidate_count = int(db.scalar(select(func.count()).select_from(DayTradeV2CandidateState).where(
        DayTradeV2CandidateState.user_id == user_id, DayTradeV2CandidateState.mode == "PAPER",
        DayTradeV2CandidateState.trading_date == trading_date,
        DayTradeV2CandidateState.confidence >= dec(config["watchThreshold"]),
    )) or 0)
    runtime.signal_count += generated
    runtime.order_count += executed
    runtime.skipped_count += skipped
    runtime.completed_trade_count = int(db.scalar(select(func.count()).select_from(DayTradeV2Trade).where(
        DayTradeV2Trade.user_id == user_id, DayTradeV2Trade.mode == "PAPER",
        DayTradeV2Trade.exit_fill_time >= start, DayTradeV2Trade.exit_fill_time < end,
    )) or 0)
    db.commit()
    return {"evaluated": evaluated, "executed": executed, "skipped": skipped, "exits": exits, "items": items}


@router.post("/scan-now")
def scan_now(user_id: str = Depends(_user_id), db: Session = Depends(get_db)) -> dict[str, object]:
    return _scan_now(user_id, db)


class CloseBody(BaseModel):
    fill_price: Decimal = Field(gt=0)
    reason: str = "手動平倉"
    percentage: int = Field(default=100, ge=1, le=100)


@router.post("/positions/{position_id}/close")
def close_position(position_id: str, body: CloseBody, user_id: str = Depends(_user_id), db: Session = Depends(get_db)) -> dict[str, object]:
    position = db.get(DayTradeV2Position, position_id)
    if position is None or position.user_id != user_id or position.status != "OPEN":
        raise HTTPException(404, "找不到未平倉部位")
    setting, robots = _ensure_defaults(db, user_id)
    config = merged_config(_json(setting.config_json, {}))
    quantity = position.quantity if body.percentage == 100 else max(1, position.quantity * body.percentage // 100)
    now = _now()
    result = calculate_trade_result(
        entry_price=position.entry_price, exit_price=body.fill_price, quantity=quantity,
        commission_rate=config["commissionRate"], commission_discount=config["commissionDiscount"],
        minimum_commission=config["minimumCommission"], tax_rate=config["dayTradeTaxRate"],
        slippage_bps=config["slippageBps"], other_cost=config["otherCost"],
    )
    trade_id = str(uuid4())
    trade = DayTradeV2Trade(
        id=trade_id, user_id=user_id, mode=position.mode, symbol=position.symbol, stock_name=position.stock_name,
        strategy_id=position.strategy_id, strategy_version=position.strategy_version, quantity=quantity,
        signal_time=position.entry_time, entry_order_time=position.entry_time, entry_fill_time=position.entry_time,
        entry_price=position.entry_price, exit_signal_time=now, exit_order_time=now, exit_fill_time=now,
        exit_price=body.fill_price, gross_pnl=result["grossPnl"], buy_fee=result["buy_fee"], sell_fee=result["sell_fee"],
        transaction_tax=result["transaction_tax"], slippage=result["slippage"], other_cost=result["other_cost"],
        net_pnl=result["netPnl"], net_return_pct=result["netReturnPct"], entry_reason="、".join(_json(position.entry_reasons_json, [])),
        exit_reason=body.reason, strategy_parameters_json=setting.config_json,
    )
    db.add(trade)
    position.quantity -= quantity
    position.used_capital = money(position.entry_price * position.quantity)
    position.current_price = body.fill_price
    if position.quantity == 0:
        position.status = "CLOSED"
    robot = next((item for item in robots if item.strategy_id == position.strategy_id), None)
    if robot:
        robot.consecutive_losses = robot.consecutive_losses + 1 if result["netPnl"] < 0 else 0
        if robot.consecutive_losses >= int(config["maxConsecutiveLosses"]):
            robot.status = "HALTED_TODAY"
            robot.status_date = datetime.now(TAIPEI).date()
            _notification(db, user_id=user_id, mode=position.mode, event_id=f"robot-halt:{robot.id}:{date.today()}", event_type="ROBOT_HALTED", title="單台機器人停機", message=f"{robot.name}連續虧損達上限，今日停止新交易")
    _notification(db, user_id=user_id, mode=position.mode, event_id=f"exit:{trade_id}", event_type="SELL_FILLED", title=f"【超強AI當沖系統｜{'模擬交易' if position.mode == 'PAPER' else '真實交易'}｜{body.reason}】", message=f"{position.symbol}｜本筆淨損益 {result['netPnl']:+,.2f}元")
    _audit(db, user_id, "POSITION_CLOSED" if position.quantity == 0 else "POSITION_PARTIAL_EXIT", position.mode, {"reason": body.reason, "quantity": quantity, "netPnl": str(result["netPnl"])}, position.id)
    db.commit()
    return _trade_dict(trade)


@router.post("/emergency-stop")
def emergency_stop(user_id: str = Depends(_user_id), db: Session = Depends(get_db)) -> dict[str, object]:
    setting, robots = _ensure_defaults(db, user_id)
    config = merged_config(_json(setting.config_json, {}))
    for robot in robots:
        robot.status = "EMERGENCY_STOP"
    runtime = _today_runtime(db, user_id, setting.trade_mode, config)
    runtime.status = "EMERGENCY_STOP"
    runtime.scanning = False
    runtime.order_allowed = False
    _notification(db, user_id=user_id, mode=setting.trade_mode, event_id=f"emergency:{uuid4()}", event_type="EMERGENCY_STOP", title="全系統緊急停機", message="已停止所有後續新委託；未平倉部位仍需執行平倉流程")
    _audit(db, user_id, "EMERGENCY_STOP", setting.trade_mode)
    db.commit()
    return {"status": "HALTED", "message": "已停止所有新委託"}


@router.post("/cancel-all")
def cancel_all(user_id: str = Depends(_user_id), db: Session = Depends(get_db)) -> dict[str, object]:
    setting, _ = _ensure_defaults(db, user_id)
    orders = list(db.scalars(select(DayTradeV2Order).where(
        DayTradeV2Order.user_id == user_id, DayTradeV2Order.mode == setting.trade_mode,
        DayTradeV2Order.status.in_(["PENDING", "ACCEPTED", "PARTIALLY_FILLED"]),
    )).all())
    for order in orders:
        order.status = "CANCELLED"
    _audit(db, user_id, "CANCEL_ALL_ORDERS", setting.trade_mode, {"count": len(orders)})
    db.commit()
    return {"cancelled": len(orders)}


@router.get("/notifications")
def notifications(user_id: str = Depends(_user_id), db: Session = Depends(get_db)) -> dict[str, object]:
    rows = list(db.scalars(select(DayTradeV2Notification).where(DayTradeV2Notification.user_id == user_id).order_by(DayTradeV2Notification.created_at.desc()).limit(200)).all())
    return {"unread": sum(not row.read for row in rows), "items": [{"id": row.id, "eventId": row.event_id, "mode": row.mode, "eventType": row.event_type, "title": row.title, "message": row.message, "read": row.read, "createdAt": row.created_at} for row in rows]}


class BacktestBody(BaseModel):
    backtest_mode: str = "PORTFOLIO"
    strategy_id: str = "ALL"
    start_date: date
    end_date: date
    datasets: dict[str, list[dict[str, object]]] | None = None


@router.post("/backtests")
def create_backtest(body: BacktestBody, user_id: str = Depends(_user_id), db: Session = Depends(get_db)) -> dict[str, object]:
    if body.end_date < body.start_date:
        raise HTTPException(422, "結束日期不可早於開始日期")
    job_id = str(uuid4())
    if not body.datasets:
        result = {"code": "MINUTE_DATA_REQUIRED", "message": "資料精度不足：尚未設定合格的歷史分鐘行情來源，不適合驗證當沖策略。", "summary": None, "trades": []}
        status, source, precision = "DATA_INSUFFICIENT", "UNCONFIGURED", "NONE"
    else:
        parsed: dict[str, list[MinuteBar]] = {}
        try:
            for symbol, rows in body.datasets.items():
                parsed[symbol] = [MinuteBar(
                    timestamp=datetime.fromisoformat(str(row["timestamp"])), open=dec(row["open"]), high=dec(row["high"]),
                    low=dec(row["low"]), close=dec(row["close"]), volume=int(row["volume"]),
                ) for row in rows]
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(422, f"分鐘行情格式錯誤：{exc}") from exc
        if body.backtest_mode == "INDIVIDUAL" and body.strategy_id == "ALL":
            individual = {
                strategy: run_backtest(parsed, strategy_id=strategy, portfolio=False)
                for strategy, _, _ in STRATEGIES
            }
            result = {"individual": individual, "message": "各機器人分別使用獨立3,000,000元；結果不可直接加總。"}
        else:
            result = run_backtest(parsed, strategy_id=body.strategy_id, portfolio=body.backtest_mode == "PORTFOLIO")
        status, source, precision = "COMPLETED", "REQUEST_DATASET", "1_MINUTE"
    job = DayTradeV2BacktestJob(
        id=job_id, user_id=user_id, backtest_mode=body.backtest_mode, strategy_id=body.strategy_id,
        start_date=body.start_date, end_date=body.end_date, status=status, data_source=source,
        data_precision=precision, request_json=body.model_dump_json(exclude={"datasets"}),
        result_json=json.dumps(result, default=str, ensure_ascii=False), completed_at=_now(),
    )
    db.add(job)
    _audit(db, user_id, "BACKTEST_CREATED", "BACKTEST", {"status": status}, job_id)
    db.commit()
    return {"id": job.id, "status": job.status, "dataSource": source, "dataPrecision": precision, **result}


@router.get("/backtests")
def backtests(user_id: str = Depends(_user_id), db: Session = Depends(get_db)) -> dict[str, object]:
    rows = list(db.scalars(select(DayTradeV2BacktestJob).where(DayTradeV2BacktestJob.user_id == user_id).order_by(DayTradeV2BacktestJob.created_at.desc()).limit(50)).all())
    return {"items": [{"id": row.id, "mode": row.backtest_mode, "strategyId": row.strategy_id, "startDate": row.start_date, "endDate": row.end_date, "status": row.status, "dataSource": row.data_source, "dataPrecision": row.data_precision, "result": _json(row.result_json, {}), "createdAt": row.created_at} for row in rows]}


@router.get("/stream")
async def stream(user_id: str = Depends(_user_id)) -> StreamingResponse:
    async def events():
        while True:
            try:
                with SessionLocal() as db:
                    payload = _dashboard(db, user_id)
                yield f"event: dashboard\ndata: {json.dumps(payload, default=str, ensure_ascii=False)}\n\n"
            except asyncio.CancelledError:
                break
            except Exception as exc:
                yield f"event: error\ndata: {json.dumps({'message': str(exc)}, ensure_ascii=False)}\n\n"
            await asyncio.sleep(3)
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
