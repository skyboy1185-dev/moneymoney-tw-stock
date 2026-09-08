"""Small observational projections: never initialize accounts or trigger robot work."""
from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..day_trading_v2_models import DayTradeV2CalendarHoliday, DayTradeV2RuntimeState, DayTradeV2Setting
from ..models import LongTermPortfolioRun, PatternRobotRun, PatternRobotSetting
from ..strong_stock_models import StrongStockAccount, StrongStockDataRun, StrongStockSetting
from .day_trading_schedule import TAIPEI, is_twse_trading_day
from .day_trading_v2 import merged_config as v2_config
from .limit_up_ai_automation import MARKET_SCAN_END, MARKET_SCAN_START, limit_up_ai_automation
from .long_term_automation import long_term_selection_automation
from .long_term_selection import LONG_TERM_SELECTION_TIME
from .pattern_robot_automation import pattern_robot_automation
from .rocket_automation import rocket_radar_automation
from .strong_stock import merged_config as strong_config
from .strong_stock_automation import strong_stock_automation
from .day_trading_v2_quotes import QUOTE_NOTICES, quote_health
from .day_trading_quote_pump import day_trading_quote_pump


V2_QUOTE_NOTICES = QUOTE_NOTICES


def _quote_provider_summary(now: datetime) -> dict:
    """Observe in-memory diagnostics only; no provider calls or initialization."""
    try:
        diagnostics = day_trading_quote_pump.diagnostics()
        projected = quote_health({}, now, 15, diagnostics)
    except Exception:
        return {}
    fields = ("activeSource", "providerMode", "ready", "entitlementReady", "entitlementReason",
              "sourceSwitchCount", "lastSourceSwitchAt", "subscriptionCount", "subscriptionLimit",
              "reconnectAttempts", "sourceHealth")
    return {key: projected[key] for key in fields if key in projected}


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _iso(value) -> str | None:
    if isinstance(value, datetime):
        return _aware(value).isoformat()
    return value if isinstance(value, str) and value else None


def _config(value: str) -> dict:
    try:
        result = json.loads(value)
        return result if isinstance(result, dict) else {}
    except (TypeError, ValueError):
        return {}


def _item(robot_id: str, scope: str) -> dict:
    return {
        "id": robot_id, "scope": scope,
        "execution": {
            "status": "unknown", "phase": None, "enabled": None,
            "lastRunAt": None, "lastSuccessAt": None, "error": None, "reason": None,
        },
        "data": {"status": "unknown", "tradeDate": None, "updatedAt": None, "reason": "尚無資料狀態"},
        "mode": {"tradeMode": None, "performanceMode": None},
    }


def _shared(robot_id: str, state: dict, active: bool, enabled: bool | None = None, *, now: datetime, interval_seconds: float) -> dict:
    item = _item(robot_id, "shared")
    raw = str(state.get("status", "")).lower()
    error = _iso(state.get("lastError"))
    status = "error" if error or raw == "error" else (
        "paused" if enabled is False else "stopped" if raw == "stopped" else
        "running" if raw in {"running", "scanning"} and active else
        "waiting" if raw in {"running", "scanning"} else "unknown"
    )
    reason = "共用服務狀態" if active else "等待排定交易時段"
    if status == "running":
        try:
            last_run = _aware(datetime.fromisoformat(str(state.get("lastRunAt"))))
        except (TypeError, ValueError):
            last_run = None
        if last_run is None:
            status, reason = "unknown", "等待首次執行紀錄"
        elif not 0 <= (now - last_run).total_seconds() <= interval_seconds:
            # A long scan may legitimately exceed one cadence. This is missing
            # recent evidence, not proof of failure, and must never imply green.
            status, reason = "unknown", "尚無近期執行紀錄，請查看最近執行時間"
    item["execution"].update(
        status=status, phase=raw or None, enabled=enabled,
        lastRunAt=_iso(state.get("lastRunAt")), lastSuccessAt=_iso(state.get("lastSuccessAt")),
        # Shared exception text can contain another user's identifiers/results.
        error="共用服務執行異常，請查看機器人詳情" if error or raw == "error" else None,
        reason=reason,
    )
    item["data"]["reason"] = "服務執行時間不代表行情資料時間"
    return item


def _daily_data(item: dict, trade_date: date | None, updated_at, expected: date, waiting: bool) -> None:
    item["data"].update(
        tradeDate=trade_date.isoformat() if trade_date else None, updatedAt=_iso(updated_at),
        status="current" if trade_date and trade_date >= expected else "waiting" if waiting else "stale" if trade_date else "unknown",
        reason=None if trade_date and trade_date >= expected else "等待排定資料更新" if waiting else "資料日期落後預期交易日" if trade_date else "尚無完成資料",
    )


def _previous_trading_day(day: date, holidays: set[date]) -> date:
    day -= timedelta(days=1)
    while not is_twse_trading_day(day, holidays):
        day -= timedelta(days=1)
    return day


def _v2(db: Session, uid: str, now: datetime, trading: bool) -> dict:
    item = _item("day-trading-v2", "user")
    setting = db.get(DayTradeV2Setting, uid)
    if setting is None:
        item["execution"]["reason"] = "目前使用者尚未初始化"
        return item
    cfg = v2_config(_config(setting.config_json))
    item["mode"]["tradeMode"] = setting.trade_mode
    local = now.astimezone(TAIPEI)
    runtime = db.scalar(select(DayTradeV2RuntimeState).where(
        DayTradeV2RuntimeState.user_id == uid, DayTradeV2RuntimeState.mode == setting.trade_mode,
        DayTradeV2RuntimeState.trading_date == local.date(),
    ))
    if runtime is None:
        item["execution"]["reason"] = "目前模式尚無今日執行紀錄"
        return item
    active = trading and time.fromisoformat(str(cfg["marketOpenTime"])) <= local.time() < time.fromisoformat(str(cfg["marketCloseTime"]))
    raw = runtime.status
    failed = raw in {"ALERT", "EMERGENCY_STOP"}
    paused = raw in {"PAUSED", "RISK_HALTED"}
    status = "error" if failed else "paused" if paused else "stopped" if raw == "STOPPED" else "running" if raw == "RUNNING" and active else "waiting"
    quote_notice = runtime.latest_error if runtime.latest_error in V2_QUOTE_NOTICES else None
    error = None if quote_notice else runtime.latest_error or None
    # Missing/stale heartbeat is an execution failure only while running in-session.
    if active and raw == "RUNNING" and (runtime.heartbeat_at is None or (now - _aware(runtime.heartbeat_at)).total_seconds() > int(cfg["heartbeatTimeoutSeconds"])):
        status, error = "error", error or "執行心跳逾時"
    elif error:
        status = "error"
    item["execution"].update(
        status=status, phase=runtime.phase, enabled=runtime.auto_start,
        lastRunAt=_iso(runtime.heartbeat_at), lastSuccessAt=_iso(runtime.last_scan_at), error=error,
        reason=None if active else "等待下一交易時段",
    )
    quote = runtime.last_quote_at
    stale = quote is None or not 0 <= (now - _aware(quote)).total_seconds() <= int(cfg["quoteTimeoutSeconds"])
    item["data"].update(
        status="waiting" if not active else "stale" if stale and runtime.scanning else "unknown" if quote is None else "stale" if stale else "current",
        tradeDate=_aware(quote).astimezone(TAIPEI).date().isoformat() if quote else None,
        updatedAt=_iso(quote), reason="非交易時段不要求即時行情" if not active else "行情逾時或尚未收到行情" if stale else None,
    )
    if quote_notice:
        # These exact messages describe source availability, not worker failure.
        # Keep an explicit ALERT/EMERGENCY_STOP or heartbeat failure above intact.
        item["data"].update(
            status="waiting" if not active else "unavailable" if quote is None else "stale",
            reason=f"非交易時段；最近行情紀錄：{quote_notice}" if not active else quote_notice,
        )
    if setting.trade_mode == "PAPER":
        provider = _quote_provider_summary(now)
        if provider:
            item["data"]["provider"] = provider
        if provider.get("providerMode") == "shadow":
            reason = "備援驗證中，尚未啟用 Fugle 正式報價"
            if provider.get("entitlementReady") is False:
                reason += "；Fugle 帳號額度尚未就緒"
            item["data"]["reason"] = "；".join(filter(None, (item["data"]["reason"], reason)))
        elif provider.get("providerMode") in {"primary", "degraded"}:
            source = {"FUGLE_WS": "Fugle 即時行情", "FUGLE_REST": "Fugle REST 備援",
                      "TWSE_MIS": "TWSE MIS 備援", "MIXED": "逐檔使用不同已驗證來源", "NONE": "尚無可用報價"}.get(str(provider.get("activeSource")))
            if source:
                item["data"]["reason"] = "；".join(filter(None, (item["data"]["reason"], source)))
            if active and provider.get("activeSource") == "NONE":
                item["data"]["status"] = "stale" if quote else "unavailable"
    return item


def robot_health_summary(db: Session, uid: str, *, now: datetime | None = None) -> dict:
    now = _aware(now or datetime.now(UTC))
    local = now.astimezone(TAIPEI)
    settings = get_settings()
    holidays = set()
    for value in settings.twse_holidays.split(","):
        try:
            holidays.add(date.fromisoformat(value.strip()))
        except ValueError:
            pass
    # Disable autoflush even if a caller supplies a session with pending changes.
    with db.no_autoflush:
        holidays.update(db.scalars(select(DayTradeV2CalendarHoliday.holiday_date)).all())
        trading = is_twse_trading_day(local.date(), holidays)
        previous = _previous_trading_day(local.date(), holidays)
        v2 = _v2(db, uid, now, trading)
        limit_state = limit_up_ai_automation.state
        limit = _shared("limit-up-ai", limit_state, trading and MARKET_SCAN_START <= local.time() <= MARKET_SCAN_END,
                        now=now, interval_seconds=float(limit_state.get("intervalSeconds", 15)))
        limit["mode"]["tradeMode"] = "PAPER"

        pattern_setting = db.get(PatternRobotSetting, 1)
        pattern = _shared("pattern-robot", pattern_robot_automation.state, trading and time(9) <= local.time() <= time(13, 40), pattern_setting.enabled if pattern_setting else None,
                          now=now, interval_seconds=max(60, settings.pattern_robot_scan_interval_seconds))
        if pattern_setting:
            pattern["mode"].update(tradeMode=pattern_setting.robot_mode, performanceMode=pattern_setting.performance_mode)
        run = db.scalar(select(PatternRobotRun).order_by(PatternRobotRun.started_at.desc()).limit(1))
        successful = db.scalar(select(PatternRobotRun).where(PatternRobotRun.status == "COMPLETED").order_by(PatternRobotRun.started_at.desc()).limit(1))
        waiting = not trading or local.time() < time(9)
        _daily_data(pattern, successful.trade_date if successful else None, successful.completed_at if successful else None, previous if waiting else local.date(), waiting)
        if run and run.status in {"FAILED", "ERROR"}:
            pattern["execution"].update(status="error", error="最近一次型態掃描失敗")

        rocket = _shared("rocket-radar", rocket_radar_automation.state, trading and time(8, 50) <= local.time() <= time(14, 10), settings.rocket_radar_enabled,
                         now=now, interval_seconds=max(10, settings.rocket_radar_scan_interval_seconds))
        rocket["mode"]["tradeMode"] = "PAPER"
        long_term = _shared("long-term", long_term_selection_automation.state, trading and local.time() >= time.fromisoformat(LONG_TERM_SELECTION_TIME),
                            now=now, interval_seconds=300)  # Existing worker sleep cadence.
        long_term["mode"]["tradeMode"] = "PAPER"
        # Both long-term portfolios must have a completed selection for freshness.
        portfolio_runs = [db.scalar(select(LongTermPortfolioRun).where(LongTermPortfolioRun.portfolio_mode == mode).order_by(LongTermPortfolioRun.trade_date.desc()).limit(1)) for mode in ("long_only", "focused_long")]
        oldest = min(portfolio_runs, key=lambda row: row.trade_date) if all(portfolio_runs) else None
        waiting = not trading or local.time() < time.fromisoformat(LONG_TERM_SELECTION_TIME)
        _daily_data(long_term, oldest.trade_date if oldest else None, oldest.ran_at if oldest else None, previous if waiting else local.date(), waiting)

        strong_setting = db.get(StrongStockSetting, uid)
        account = db.get(StrongStockAccount, uid)
        cfg = strong_config(_config(strong_setting.config_json) if strong_setting else {})
        active = trading and (time(9) <= local.time() <= time(13, 30) or time.fromisoformat(str(cfg["dataReadyStartTime"])) <= local.time() <= time.fromisoformat(str(cfg["dataReadyEndTime"])))
        enabled = strong_setting.paper_enabled and not account.trading_paused if strong_setting and account else None
        strong_state = strong_stock_automation.state
        strong = _shared("strong-stocks", strong_state, active, enabled, now=now,
                         interval_seconds=float(strong_state.get("nextCheckSeconds", 60)))
        strong["scope"] = "user"
        if strong["execution"]["status"] != "unknown":
            strong["execution"]["reason"] = "使用者模擬交易設定；執行服務與盤後資料共用"
        strong["mode"]["tradeMode"] = account.mode if account else None
        if enabled is None:
            strong["execution"].update(status="unknown", reason="目前使用者尚未初始化")
        latest = db.scalar(select(StrongStockDataRun).order_by(StrongStockDataRun.started_at.desc()).limit(1))
        successful = db.scalar(select(StrongStockDataRun).where(StrongStockDataRun.status == "COMPLETED").order_by(StrongStockDataRun.trade_date.desc()).limit(1))
        waiting = not trading or local.time() < time.fromisoformat(str(cfg["dataReadyEndTime"]))
        _daily_data(strong, successful.trade_date if successful else None, successful.completed_at if successful else None, previous if waiting else local.date(), waiting)
        if latest and latest.status in {"FAILED", "ERROR"}:
            strong["data"].update(status="unavailable", reason="最近一次共用盤後資料更新失敗")
        elif latest and latest.status == "DATA_INSUFFICIENT":
            strong["data"].update(status="waiting", reason="盤後資料尚未完整")

    return {"generatedAt": now.isoformat(), "market": {"localDate": local.date().isoformat(), "isTradingDay": trading}, "items": [v2, limit, pattern, rocket, long_term, strong]}
