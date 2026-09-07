import asyncio
from dataclasses import replace
import hmac
import json
import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import SessionLocal, get_db
from ..day_trading_v2_models import (
    DayTradeV2AuditEvent, DayTradeV2BacktestJob, DayTradeV2CalendarHoliday, DayTradeV2Fill,
    DayTradeV2CandidateState, DayTradeV2Notification, DayTradeV2Order, DayTradeV2Position,
    DayTradeV2RiskDaily, DayTradeV2Robot, DayTradeV2Setting,
    DayTradeV2RuntimeState, DayTradeV2ScheduleEvent, DayTradeV2Signal,
    DayTradeV2SkipStat, DayTradeV2StrategyVersion, DayTradeV2Trade,
    DayTradeV2ChallengerRun, DayTradeV2ControllerCandidate, DayTradeV2ControllerCycle,
    DayTradeV2MarketRegimeSnapshot, DayTradeV2OptimizationDataset, DayTradeV2OptimizationJob,
    DayTradeV2StrategyDeployment, DayTradeV2StrategyHealthSnapshot, DayTradeV2StrategyRiskOverride,
)
from ..services.day_trading_v2 import (
    DEFAULT_CONFIG, DEFAULT_STRATEGY_PARAMETERS, STRATEGIES, MinuteBar, calculate_position_size,
    calculate_trade_result, dec, evaluate_strategies, exit_action, market_gate_reasons,
    merged_config, money, performance, resolve_duplicate_signals, risk_status, signal_level,
    run_backtest,
)
from ..services.day_trading import day_trading_engine
from ..services.day_trading_v2_controller import (
    ControllerCandidateInput, MarketInputs, REGIME_LABELS, REGIME_UNKNOWN,
    apply_regime_hysteresis, classify_market, rank_candidates, risk_multiplier_for_regime, score_candidate,
)
from ..services.day_trading_v2_datasets import (
    MAX_UPLOAD_BYTES, DatasetValidationError, load_dataset, parse_dataset, persist_dataset, quality_json,
)
from ..services.day_trading_v2_backtests import MINUTE_DATA_START, execute_backtest
from ..services.day_trading_v2_health import (
    active_version, handle_strategy_runtime_error, health_for_strategy, run_health_diagnosis,
)
from ..services.day_trading_v2_optimization import challenger_ready, next_version, parameter_checksum


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
            version = DayTradeV2StrategyVersion(
                strategy_id=strategy_id, version="2.0.0",
                definition_json=json.dumps({"side": "LONG", "name": name, "lookahead": False}),
                parameters_json=json.dumps(DEFAULT_STRATEGY_PARAMETERS[strategy_id]),
                checksum=parameter_checksum(DEFAULT_STRATEGY_PARAMETERS[strategy_id]),
                validation_status="UNVERIFIED",
            )
            db.add(version)
        elif not _json(version.parameters_json, {}):
            # One-time additive migration for initial versions created before parameter snapshots existed.
            version.parameters_json = json.dumps(DEFAULT_STRATEGY_PARAMETERS[strategy_id])
            version.checksum = parameter_checksum(DEFAULT_STRATEGY_PARAMETERS[strategy_id])
        deployment = db.scalar(select(DayTradeV2StrategyDeployment).where(
            DayTradeV2StrategyDeployment.user_id == user_id,
            DayTradeV2StrategyDeployment.strategy_id == strategy_id,
            DayTradeV2StrategyDeployment.role == "CHAMPION",
            DayTradeV2StrategyDeployment.status.in_(("ACTIVE", "PENDING_ACTIVATION")),
        ))
        if deployment is None:
            db.add(DayTradeV2StrategyDeployment(
                id=str(uuid4()), user_id=user_id, strategy_id=strategy_id, version="2.0.0",
                role="CHAMPION", status="ACTIVE", activated_at=_now(),
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


def _percent(value: object) -> Decimal:
    raw = str(value or "0").replace("%", "").replace("+", "")
    try:
        return dec(raw)
    except Exception:
        return Decimal("0")


def _market_inputs(regime: dict[str, object], live_candidates: list[dict[str, object]]) -> MarketInputs:
    metrics = regime.get("metrics") if isinstance(regime.get("metrics"), dict) else {}
    sectors: dict[str, list[Decimal]] = {}
    for item in live_candidates:
        sector = str((item.get("themes") or [""])[0])
        if sector:
            sectors.setdefault(sector, []).append(dec(item.get("industryScore") or 50))
    sector_scores = [sum(values, Decimal("0")) / len(values) for values in sectors.values()]
    return MarketInputs(
        index_price=dec(metrics.get("weightedIndex") or 0),
        index_vwap=dec(metrics.get("vwap") or metrics.get("weightedIndex") or 0),
        trend_1m_pct=_percent(metrics.get("oneMinuteTrend")),
        trend_5m_pct=_percent(metrics.get("fiveMinuteTrend")),
        breadth_pct=dec(metrics.get("breadth") or 0),
        relative_volume=dec(metrics.get("relativeVolume") or 0),
        strong_sector_count=sum(value >= 70 for value in sector_scores),
        weak_sector_count=sum(value < 40 for value in sector_scores),
        quote_coverage_pct=dec(regime.get("quoteCoverageRatio") or 0) * 100,
        data_normal=regime.get("dataStatus") == "normal",
    )


def _regime_snapshot(
    db: Session, user_id: str, mode: str, current: datetime,
    legacy_regime: dict[str, object], live_candidates: list[dict[str, object]], config: dict[str, object],
) -> DayTradeV2MarketRegimeSnapshot:
    minutes = max(1, int(config.get("regimeUpdateMinutes", 5)))
    bucket_minute = current.minute - current.minute % minutes
    bucket = current.replace(minute=bucket_minute, second=0, microsecond=0)
    existing = db.scalar(select(DayTradeV2MarketRegimeSnapshot).where(
        DayTradeV2MarketRegimeSnapshot.user_id == user_id,
        DayTradeV2MarketRegimeSnapshot.mode == mode,
        DayTradeV2MarketRegimeSnapshot.bucket_at == bucket,
    ))
    if existing:
        return existing
    inputs = _market_inputs(legacy_regime, live_candidates)
    proposed = classify_market(inputs, config)
    previous_rows = list(db.scalars(select(DayTradeV2MarketRegimeSnapshot).where(
        DayTradeV2MarketRegimeSnapshot.user_id == user_id,
        DayTradeV2MarketRegimeSnapshot.mode == mode,
    ).order_by(DayTradeV2MarketRegimeSnapshot.bucket_at.desc()).limit(max(3, int(config.get("regimeRecoveryCycles", 3))))).all())
    effective = apply_regime_hysteresis(
        proposed, current=previous_rows[0].effective_regime if previous_rows else None,
        recent_proposals=[row.proposed_regime for row in reversed(previous_rows)],
        recovery_cycles=int(config.get("regimeRecoveryCycles", 3)),
        switch_cycles=int(config.get("regimeSwitchCycles", 2)),
    )
    snapshot = DayTradeV2MarketRegimeSnapshot(
        id=str(uuid4()), user_id=user_id, mode=mode, bucket_at=bucket,
        proposed_regime=effective.proposed, effective_regime=effective.effective,
        confidence=effective.confidence, data_blocked=effective.data_blocked,
        reasons_json=json.dumps(effective.reasons, ensure_ascii=False),
        inputs_json=json.dumps({
            "indexPrice": str(inputs.index_price), "indexVwap": str(inputs.index_vwap),
            "trend1mPct": str(inputs.trend_1m_pct), "trend5mPct": str(inputs.trend_5m_pct),
            "breadthPct": str(inputs.breadth_pct), "relativeVolume": str(inputs.relative_volume),
            "strongSectorCount": inputs.strong_sector_count, "weakSectorCount": inputs.weak_sector_count,
            "quoteCoveragePct": str(inputs.quote_coverage_pct), "dataNormal": inputs.data_normal,
        }, ensure_ascii=False),
    )
    db.add(snapshot)
    db.flush()
    return snapshot


def _controller_candidate_dict(row: DayTradeV2ControllerCandidate) -> dict[str, object]:
    return {
        "id": row.id, "cycleId": row.cycle_id, "symbol": row.symbol, "stockName": row.stock_name,
        "sector": row.sector, "strategyId": row.strategy_id, "strategyVersion": row.strategy_version,
        "signalTime": row.signal_time, "rawScore": str(row.raw_score), "finalScore": str(row.final_score),
        "rank": row.rank, "entryPrice": str(row.entry_price), "stopPrice": str(row.stop_price),
        "targetPrice": str(row.target_price), "riskReward": str(row.risk_reward),
        "plannedCapital": str(row.planned_capital), "allowed": row.allowed, "status": row.status,
        "scoreDetails": _json(row.score_details_json, {}), "reasons": _json(row.reasons_json, []),
        "blockedReasons": _json(row.blocked_reasons_json, []),
    }


def _controller_dashboard(db: Session, user_id: str, mode: str) -> dict[str, object]:
    regime = db.scalar(select(DayTradeV2MarketRegimeSnapshot).where(
        DayTradeV2MarketRegimeSnapshot.user_id == user_id,
        DayTradeV2MarketRegimeSnapshot.mode == mode,
    ).order_by(DayTradeV2MarketRegimeSnapshot.bucket_at.desc()))
    cycle = db.scalar(select(DayTradeV2ControllerCycle).where(
        DayTradeV2ControllerCycle.user_id == user_id,
        DayTradeV2ControllerCycle.mode == mode,
    ).order_by(DayTradeV2ControllerCycle.evaluated_at.desc()))
    candidates = list(db.scalars(select(DayTradeV2ControllerCandidate).where(
        DayTradeV2ControllerCandidate.cycle_id == cycle.id,
    ).order_by(DayTradeV2ControllerCandidate.allowed.desc(), DayTradeV2ControllerCandidate.final_score.desc())).all()) if cycle else []
    state = regime.effective_regime if regime else REGIME_UNKNOWN
    robots = list(db.scalars(select(DayTradeV2Robot).where(DayTradeV2Robot.user_id == user_id)).all())
    overrides = {row.strategy_id: row for row in db.scalars(select(DayTradeV2StrategyRiskOverride).where(
        DayTradeV2StrategyRiskOverride.user_id == user_id,
        DayTradeV2StrategyRiskOverride.mode == mode,
    )).all()}
    counts: dict[str, int] = {}
    for row in candidates:
        counts[row.strategy_id] = counts.get(row.strategy_id, 0) + 1
    strategy_states = []
    for robot in robots:
        override = overrides.get(robot.strategy_id)
        multiplier = min(risk_multiplier_for_regime(state), override.risk_multiplier if override else Decimal("1"))
        status = "PAUSED" if not robot.enabled or robot.status != "READY" or (override and override.paused) or multiplier == 0 else "DEWEIGHTED" if multiplier < 1 else "ENABLED"
        strategy_states.append({
            "strategyId": robot.strategy_id, "name": robot.name, "status": status,
            "riskMultiplier": str(multiplier), "candidateCount": counts.get(robot.strategy_id, 0),
        })
    return {
        "marketRegime": state, "marketRegimeLabel": REGIME_LABELS.get(state, state),
        "confidence": str(regime.confidence) if regime else "0",
        "reasons": _json(regime.reasons_json, []) if regime else ["等待第一筆完整市場資料"],
        "dataBlocked": regime.data_blocked if regime else True,
        "updatedAt": regime.bucket_at if regime else None,
        "nextUpdateAt": regime.bucket_at + timedelta(minutes=5) if regime else None,
        "cycleId": cycle.id if cycle else None, "cycleStatus": cycle.status if cycle else "WAITING",
        "selectedCandidateId": cycle.selected_candidate_id if cycle else "",
        "candidates": [_controller_candidate_dict(row) for row in candidates[:100]],
        "strategyStates": strategy_states,
    }


def _optimization_dashboard(db: Session, user_id: str, mode: str) -> dict[str, object]:
    health: list[dict[str, object]] = []
    for strategy_id, name, _ in STRATEGIES:
        row = health_for_strategy(db, user_id, mode, strategy_id)
        health.append({
            "strategyId": strategy_id, "name": name, "version": row.strategy_version if row else active_version(db, user_id, strategy_id),
            "status": row.status if row else "INSUFFICIENT", "reasons": _json(row.reasons_json, []) if row else ["尚未執行收盤後診斷"],
            "metrics": _json(row.metrics_json, {}) if row else {}, "baseline": _json(row.baseline_json, {}) if row else {},
            "recommendedAction": row.recommended_action if row else "NONE",
            "capitalMultiplier": str(row.capital_multiplier) if row else "1",
            "riskMultiplier": str(row.risk_multiplier) if row else "1",
        })
    jobs = list(db.scalars(select(DayTradeV2OptimizationJob).where(
        DayTradeV2OptimizationJob.user_id == user_id,
    ).order_by(DayTradeV2OptimizationJob.created_at.desc()).limit(30)).all())
    challengers = list(db.scalars(select(DayTradeV2ChallengerRun).where(
        DayTradeV2ChallengerRun.user_id == user_id,
    ).order_by(DayTradeV2ChallengerRun.started_at.desc()).limit(20)).all())
    deployments = list(db.scalars(select(DayTradeV2StrategyDeployment).where(
        DayTradeV2StrategyDeployment.user_id == user_id,
    ).order_by(DayTradeV2StrategyDeployment.created_at.desc()).limit(50)).all())
    datasets = list(db.scalars(select(DayTradeV2OptimizationDataset).where(
        DayTradeV2OptimizationDataset.user_id == user_id,
    ).order_by(DayTradeV2OptimizationDataset.created_at.desc()).limit(20)).all())
    return {
        "health": health,
        "jobs": [{
            "id": row.id, "strategyId": row.strategy_id, "championVersion": row.champion_version,
            "candidateVersion": row.candidate_version, "status": row.status, "progressPct": str(row.progress_pct),
            "result": _json(row.result_json, {}), "error": row.error_message, "createdAt": row.created_at,
        } for row in jobs],
        "challengers": [{
            "id": row.id, "strategyId": row.strategy_id, "championVersion": row.champion_version,
            "challengerVersion": row.challenger_version, "status": row.status,
            "fullTradingDays": row.full_trading_days, "tradeCount": row.trade_count,
            "errorCount": row.error_count, "championMetrics": _json(row.champion_metrics_json, {}),
            "challengerMetrics": _json(row.challenger_metrics_json, {}),
        } for row in challengers],
        "deployments": [{
            "id": row.id, "strategyId": row.strategy_id, "version": row.version, "role": row.role,
            "status": row.status, "approvedBy": row.approved_by, "approvedAt": row.approved_at,
            "effectiveDate": row.effective_date, "activatedAt": row.activated_at,
            "disabledReason": row.disabled_reason, "rollbackToVersion": row.rollback_to_version,
        } for row in deployments],
        "datasets": [{
            "id": row.id, "name": row.name, "format": row.data_format,
            "startDate": row.start_date, "endDate": row.end_date,
            "tradingDayCount": row.trading_day_count, "symbolCount": row.symbol_count,
            "rowCount": row.row_count, "qualityStatus": row.quality_status,
            "quality": _json(row.quality_json, {}), "checksum": row.checksum,
            "createdAt": row.created_at,
        } for row in datasets],
    }


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
    latest_dataset = db.scalar(select(DayTradeV2OptimizationDataset).where(
        DayTradeV2OptimizationDataset.user_id == user_id,
        DayTradeV2OptimizationDataset.quality_status.in_(("READY", "BACKTEST_READY")),
    ).order_by(DayTradeV2OptimizationDataset.created_at.desc()))
    from ..config import get_settings
    application_settings = get_settings()
    automatic_history_ready = bool(
        application_settings.fugle_marketdata_api_key and application_settings.dtv2_optimization_data_dir.strip()
    )
    history_source = "Fugle歷史1分鐘行情" if automatic_history_ready else (
        f"CSV／Parquet：{latest_dataset.name}" if latest_dataset else None
    )
    history_message = (
        "回測中心可自動準備1分鐘行情，也可直接上傳CSV／Parquet。"
        if automatic_history_ready else
        "Fugle自動分鐘行情尚未設定；仍可在回測中心上傳CSV／Parquet。"
    )
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
        "marketData": {
            "realtime": "TWSE MIS", "historicalMinute": history_source,
            "backtestReady": automatic_history_ready or latest_dataset is not None, "message": history_message,
        },
        "config": config, "today": today_perf, "month": month_perf, "all": all_perf,
        "realizedPnl": str(money(realized)), "unrealizedPnl": str(money(unrealized)),
        "netPnl": str(money(realized + unrealized)), "usedCapital": str(money(used)),
        "availableCapital": str(money(dec(config["initialCapital"]) - used)),
        "remainingDailyRisk": str(money(max(dec(config["dailyStopLoss"]) + min(realized, Decimal("0")), Decimal("0")))),
        "positions": [_position_dict(row) for row in positions], "recentTrades": [_trade_dict(row) for row in all_trades[:100]],
        "robots": robot_items, "runtime": runtime_data,
        "topCandidates": [_candidate_dict(row) for row in candidates],
        "skipReasons": [{"reason": row.reason, "count": row.occurrence_count} for row in skip_stats],
        "controller": _controller_dashboard(db, user_id, mode),
        "optimization": _optimization_dashboard(db, user_id, mode),
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
    if not 80 <= dec(config["controllerMinimumScore"]) <= 100:
        raise HTTPException(422, "策略總控最低分數不可低於80或高於100")
    if dec(config["controllerMinimumRiskReward"]) < 2:
        raise HTTPException(422, "策略總控風險報酬比不可低於1比2")
    if int(config["regimeUpdateMinutes"]) != 5:
        raise HTTPException(422, "市場狀態必須每5分鐘重新判斷")
    if int(config["regimeSwitchCycles"]) < 2 or int(config["regimeRecoveryCycles"]) < 2:
        raise HTTPException(422, "盤勢切換與急跌恢復至少需要連續兩次確認")
    if not Decimal("0") <= dec(config["healthAlertRiskMultiplier"]) <= Decimal("1"):
        raise HTTPException(422, "健康警戒風險乘數必須介於0至1，不可自動提高風險")
    time_keys = (
        "resetTime", "universeLoadTime", "historyLoadTime", "healthCheckTime", "candidatePoolTime",
        "readyNotificationTime", "marketOpenTime", "openingRangeReadyTime", "summary1000Time",
        "summary1100Time", "summary1200Time", "latestEntryTime", "forcedCloseTime", "marketCloseTime",
        "brokerSyncTime", "closeReportTime", "healthDiagnosisTime", "weeklyHealthCheckTime",
    )
    try:
        schedule = [datetime.strptime(str(config[key]), "%H:%M:%S").time() for key in time_keys]
    except ValueError as exc:
        raise HTTPException(422, "排程時間格式必須為HH:MM:SS") from exc
    if schedule != sorted(schedule):
        raise HTTPException(422, "每日排程時間必須依執行順序遞增")
    strategy_windows = (
        ("openingStrategyStart", "openingStrategyEnd"), ("vwapStrategyStart", "vwapStrategyEnd"),
        ("volumeStrategyStart", "volumeStrategyEnd"), ("reversalStrategyStart", "reversalStrategyEnd"),
        ("afternoonStrategyStart", "afternoonStrategyEnd"),
    )
    try:
        windows = [
            (datetime.strptime(str(config[start]), "%H:%M:%S").time(), datetime.strptime(str(config[end]), "%H:%M:%S").time())
            for start, end in strategy_windows
        ]
    except ValueError as exc:
        raise HTTPException(422, "策略有效時段格式必須為HH:MM:SS") from exc
    latest_entry = datetime.strptime(str(config["latestEntryTime"]), "%H:%M:%S").time()
    if any(start >= end or end > latest_entry for start, end in windows):
        raise HTTPException(422, "策略開始時間必須早於結束時間，且不得晚於全系統新倉截止時間")
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
    controller_decision_id: str = ""
    controller_candidate_id: str = ""
    risk_multiplier: Decimal = Field(default=Decimal("1"), ge=0, le=1)
    capital_multiplier: Decimal = Field(default=Decimal("1"), ge=0, le=1)


@router.post("/paper/entries")
def paper_entry(body: PaperEntryBody, user_id: str = Depends(_user_id), db: Session = Depends(get_db)) -> dict[str, object]:
    setting, robots = _ensure_defaults(db, user_id)
    if setting.trade_mode != "PAPER":
        raise HTTPException(409, "只有模擬交易模式可以建立模擬部位")
    robot = next((item for item in robots if item.strategy_id == body.strategy_id and item.enabled and item.status == "READY"), None)
    if robot is None:
        raise HTTPException(409, "機器人未啟用或不存在")
    if body.controller_decision_id:
        cycle = db.get(DayTradeV2ControllerCycle, body.controller_decision_id)
        controller_candidate = db.get(DayTradeV2ControllerCandidate, body.controller_candidate_id)
        if not cycle or not controller_candidate or controller_candidate.cycle_id != cycle.id or cycle.selected_candidate_id != controller_candidate.id:
            raise HTTPException(409, "自動策略委託缺少有效的總控決策")
        if controller_candidate.status not in {"SELECTED", "ORDER_FAILED"} or controller_candidate.strategy_id != body.strategy_id or controller_candidate.symbol != body.symbol:
            raise HTTPException(409, "總控候選狀態或內容不符")
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
    risk_budget = dec(config["maxRiskPerTrade"]) * body.risk_multiplier
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
        capital_limit=min(available, max(robot.allocation * body.capital_multiplier - robot_used, Decimal("0"))), lot_size=int(config["boardLotSize"]),
        allow_odd_lots=bool(config["allowOddLots"]),
    )
    if quantity <= 0:
        raise HTTPException(409, "資金不足或停損距離無效")
    now = _now()
    signal_id, order_id, position_id = body.signal_id or str(uuid4()), str(uuid4()), str(uuid4())
    risk_reward = (body.target_price - body.fill_price) / (body.fill_price - body.stop_price)
    version = active_version(db, user_id, body.strategy_id)
    db.add(DayTradeV2Signal(
        id=signal_id, user_id=user_id, mode="PAPER", strategy_id=body.strategy_id, strategy_version=version,
        symbol=body.symbol, stock_name=body.stock_name, sector=body.sector, side="LONG", signal_time=body.signal_time,
        signal_price=body.signal_price, confidence=body.confidence, risk_reward=risk_reward,
        stop_price=body.stop_price, target_price=body.target_price, status="EXECUTED",
        reasons_json=json.dumps(body.reasons, ensure_ascii=False), market_context_json=json.dumps({"sector": body.sector}, ensure_ascii=False),
        controller_decision_id=body.controller_decision_id,
    ))
    db.add(DayTradeV2Order(
        id=order_id, user_id=user_id, mode="PAPER", signal_id=signal_id, client_order_id=f"paper-{order_id}",
        broker_order_id=f"paper-{order_id}", symbol=body.symbol, side="BUY", order_price=body.fill_price,
        order_quantity=quantity, filled_quantity=quantity, status="FILLED", signal_at=body.signal_time,
        sent_at=now, broker_accepted_at=now, controller_decision_id=body.controller_decision_id,
    ))
    db.add(DayTradeV2Fill(order_id=order_id, mode="PAPER", broker_execution_id=f"paper-fill-{order_id}", price=body.fill_price, quantity=quantity, exchange_time=now, received_at=now))
    position = DayTradeV2Position(
        id=position_id, user_id=user_id, mode="PAPER", strategy_id=body.strategy_id, strategy_version=version,
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


def _legacy_scan_now(user_id: str, db: Session, coordinator_now: datetime | None = None, *, entries_enabled: bool = True) -> dict[str, object]:
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
    if not entries_enabled:
        runtime.last_scan_at = current
        runtime.next_scan_at = current + timedelta(seconds=int(config["scanIntervalSeconds"]))
        runtime.completed_trade_count = int(db.scalar(select(func.count()).select_from(DayTradeV2Trade).where(
            DayTradeV2Trade.user_id == user_id, DayTradeV2Trade.mode == "PAPER",
            DayTradeV2Trade.exit_fill_time >= start, DayTradeV2Trade.exit_fill_time < end,
        )) or 0)
        db.commit()
        return {"evaluated": 0, "executed": 0, "skipped": 0, "exits": exits, "items": []}
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


def _scan_now(user_id: str, db: Session, coordinator_now: datetime | None = None) -> dict[str, object]:
    """Run exits first, then send every strategy candidate through the sole controller gateway."""
    setting, robots = _ensure_defaults(db, user_id)
    config = merged_config(_json(setting.config_json, {}))
    if setting.trade_mode != "PAPER":
        return {"evaluated": 0, "executed": 0, "skipped": 0, "message": "目前模式不執行即時模擬訊號"}
    current = coordinator_now or _now()
    local_now = current.astimezone(TAIPEI)
    trading_date = local_now.date()
    monitor = _legacy_scan_now(user_id, db, current, entries_enabled=False)
    runtime = _today_runtime(db, user_id, "PAPER", config)
    legacy_regime = day_trading_engine.market_regime()
    live_candidates = [item for item in day_trading_engine.signals() if item.get("direction") == "long"]
    snapshot = _regime_snapshot(db, user_id, "PAPER", current, legacy_regime, live_candidates, config)
    interval = max(1, int(config["scanIntervalSeconds"]))
    cycle_epoch = int(current.timestamp()) // interval * interval
    cycle_key = f"{trading_date}:{cycle_epoch}"
    existing_cycle = db.scalar(select(DayTradeV2ControllerCycle).where(
        DayTradeV2ControllerCycle.user_id == user_id,
        DayTradeV2ControllerCycle.mode == "PAPER",
        DayTradeV2ControllerCycle.cycle_key == cycle_key,
    ))
    if existing_cycle:
        return {
            "evaluated": existing_cycle.candidate_count, "executed": int(bool(existing_cycle.selected_candidate_id)),
            "skipped": max(0, existing_cycle.candidate_count - int(bool(existing_cycle.selected_candidate_id))),
            "exits": monitor.get("exits", []), "cycleId": existing_cycle.id, "idempotent": True,
        }
    cycle = DayTradeV2ControllerCycle(
        id=str(uuid4()), user_id=user_id, mode="PAPER", cycle_key=cycle_key,
        trading_date=trading_date, evaluated_at=current, regime_snapshot_id=snapshot.id,
    )
    db.add(cycle)
    db.flush()

    open_positions = list(db.scalars(select(DayTradeV2Position).where(
        DayTradeV2Position.user_id == user_id, DayTradeV2Position.mode == "PAPER",
        DayTradeV2Position.status == "OPEN",
    )).all())
    used_capital = sum((row.used_capital for row in open_positions), Decimal("0"))
    existing_symbols = {row.symbol for row in open_positions}
    robot_map = {row.strategy_id: row for row in robots}
    overrides = {row.strategy_id: row for row in db.scalars(select(DayTradeV2StrategyRiskOverride).where(
        DayTradeV2StrategyRiskOverride.user_id == user_id,
        DayTradeV2StrategyRiskOverride.mode == "PAPER",
    )).all()}
    health_status = {}
    for strategy_id, _, _ in STRATEGIES:
        snapshot_row = health_for_strategy(db, user_id, "PAPER", strategy_id)
        health_status[strategy_id] = snapshot_row.status if snapshot_row else "INSUFFICIENT"
    version_map = {strategy_id: active_version(db, user_id, strategy_id) for strategy_id, _, _ in STRATEGIES}
    parameters: dict[str, dict[str, object]] = {}
    oos_profit_factors: dict[str, Decimal] = {}
    for strategy_id, _, _ in STRATEGIES:
        version_row = db.scalar(select(DayTradeV2StrategyVersion).where(
            DayTradeV2StrategyVersion.strategy_id == strategy_id,
            DayTradeV2StrategyVersion.version == version_map[strategy_id],
        ))
        parameters[strategy_id] = _json(version_row.parameters_json, DEFAULT_STRATEGY_PARAMETERS[strategy_id]) if version_row else DEFAULT_STRATEGY_PARAMETERS[strategy_id]
        oos_result = _json(version_row.oos_result_json, {}) if version_row else {}
        oos_profit_factors[strategy_id] = dec(oos_result.get("profitFactor") or 0)

    quote_times: list[datetime] = []
    for item in live_candidates:
        if item.get("dataSource") == "TWSE MIS" and item.get("quoteIsRealtime"):
            try:
                parsed = datetime.fromisoformat(str(item.get("quoteTimestamp")))
                quote_times.append(parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC))
            except ValueError:
                pass
    latest_quote = max(quote_times, default=None)
    quote_fresh = bool(latest_quote and abs((current - latest_quote).total_seconds()) <= int(config["quoteTimeoutSeconds"]))
    runtime.last_quote_at = latest_quote or runtime.last_quote_at
    runtime.receiving_quotes = quote_fresh
    runtime.order_allowed = bool(
        runtime.status == "RUNNING" and quote_fresh and not snapshot.data_blocked
        and local_now.time() < datetime.strptime(str(config["latestEntryTime"]), "%H:%M:%S").time()
    )
    day_start, day_end = _today_bounds()
    daily_pnl = sum(db.scalars(select(DayTradeV2Trade.net_pnl).where(
        DayTradeV2Trade.user_id == user_id, DayTradeV2Trade.mode == "PAPER",
        DayTradeV2Trade.exit_fill_time >= day_start, DayTradeV2Trade.exit_fill_time < day_end,
    )).all(), Decimal("0"))
    scored = []
    latest_bar_at: datetime | None = None
    for source in live_candidates:
        symbol = str(source.get("symbol") or "")
        raw_bars = day_trading_engine.minute_bars_for(symbol)
        if len(raw_bars) < 16:
            continue
        bars = [MinuteBar(
            timestamp=datetime.fromisoformat(str(row["timestamp"])), open=dec(row["open"]), high=dec(row["high"]),
            low=dec(row["low"]), close=dec(row["close"]), volume=int(row["volume"]),
        ) for row in raw_bars]
        latest_bar_at = max(latest_bar_at, bars[-1].timestamp) if latest_bar_at else bars[-1].timestamp
        sector = str((source.get("themes") or [""])[0])
        strategy_signals = []
        for strategy_id, _, _ in STRATEGIES:
            try:
                strategy_signals.extend(evaluate_strategies(
                    bars, strategy_parameters=parameters, enabled_strategies={strategy_id},
                ))
            except Exception as exc:
                handle_strategy_runtime_error(
                    db, user_id, "PAPER", strategy_id, version_map[strategy_id], exc, current,
                )
                runtime.latest_error = f"{strategy_id}策略錯誤，已停止該策略：{type(exc).__name__}"
        for signal in strategy_signals:
            robot = robot_map.get(signal.strategy_id)
            override = overrides.get(signal.strategy_id)
            rr = (signal.target_price - signal.entry_price) / (signal.entry_price - signal.stop_price)
            item = ControllerCandidateInput(
                key=f"v2:{symbol}:{bars[-1].timestamp.isoformat()}:{signal.strategy_id}",
                symbol=symbol, stock_name=str(source.get("stockName") or ""), sector=sector,
                strategy_id=signal.strategy_id, strategy_version=version_map[signal.strategy_id],
                signal_time=bars[-1].timestamp, raw_score=signal.confidence,
                entry_price=signal.entry_price, stop_price=signal.stop_price, target_price=signal.target_price,
                risk_reward=rr, sector_strength=dec(source.get("industryScore") or 50),
                health_status="PAUSED" if override and override.paused else health_status[signal.strategy_id],
                liquidity_score=dec(source.get("liquidityScore") or 0),
                vwap_deviation_pct=dec(source.get("vwapDeviationPercent") or 0), reasons=signal.reasons,
                oos_profit_factor=oos_profit_factors[signal.strategy_id],
                health_score=Decimal("100") if health_status[signal.strategy_id] == "NORMAL" else Decimal("25") if health_status[signal.strategy_id] == "ALERT" else Decimal("50"),
                market_stabilized=_percent((legacy_regime.get("metrics") or {}).get("oneMinuteTrend")) >= 0,
            )
            sector_positions = sum(row.sector == sector for row in open_positions)
            row = score_candidate(
                item, regime=snapshot.effective_regime, local_time=local_now.time(),
                existing_symbols=existing_symbols, sector_positions=sector_positions, config=config,
            )
            common = market_gate_reasons(
                now=current, market_crashing=snapshot.effective_regime == "E_CRASH",
                quote_reliable=quote_fresh and source.get("dataSource") == "TWSE MIS" and bool(source.get("quoteIsRealtime")),
                volume=int(source.get("volume") or 0), turnover=source.get("turnover") or 0,
                spread_pct=source.get("spreadPercentage") or 999,
                vwap_deviation_pct=source.get("vwapDeviationPercent") or 0,
                blocked=bool(source.get("tradeRestricted")), connection_ok=legacy_regime.get("dataStatus") == "normal",
                available_capital=dec(config["initialCapital"]) - used_capital,
                open_positions=len(open_positions), sector_positions=sector_positions,
                realized_pnl=daily_pnl, config=config,
            )
            if not robot or not robot.enabled or robot.status != "READY":
                common.append("機器人未啟用或已停機")
            if not runtime.order_allowed:
                common.append("系統目前禁止新委託")
            if db.get(DayTradeV2Signal, item.key):
                common.append("同一根K棒訊號已處理")
            if common:
                row = replace(row, allowed=False, blocked_reasons=tuple(dict.fromkeys((*row.blocked_reasons, *common))))
            scored.append(row)
    ranked = rank_candidates(scored)
    stored: list[tuple[object, DayTradeV2ControllerCandidate]] = []
    selected_pair = None
    regime_multiplier = risk_multiplier_for_regime(snapshot.effective_regime)
    for row in ranked:
        override = overrides.get(row.candidate.strategy_id)
        capital_multiplier = min(regime_multiplier, override.capital_multiplier if override else Decimal("1"))
        risk_multiplier = min(regime_multiplier, override.risk_multiplier if override else Decimal("1"))
        robot = robot_map[row.candidate.strategy_id]
        planned_limit = min(
            max(Decimal("0"), dec(config["initialCapital"]) - used_capital),
            robot.allocation * capital_multiplier,
        )
        quantity = calculate_position_size(
            price=row.candidate.entry_price, stop_price=row.candidate.stop_price,
            risk_budget=dec(config["maxRiskPerTrade"]) * risk_multiplier,
            capital_limit=planned_limit, lot_size=int(config["boardLotSize"]),
            allow_odd_lots=bool(config["allowOddLots"]),
        )
        allowed = row.allowed and quantity > 0 and selected_pair is None
        blocked = list(row.blocked_reasons)
        if row.allowed and quantity <= 0:
            blocked.append("資金不足或停損距離無效")
        if row.allowed and selected_pair is not None:
            blocked.append("本輪已選出較高排名訊號")
        candidate_row = DayTradeV2ControllerCandidate(
            id=str(uuid4()), cycle_id=cycle.id, user_id=user_id, mode="PAPER",
            candidate_key=f"{cycle_key}:{row.candidate.key}", symbol=row.candidate.symbol,
            stock_name=row.candidate.stock_name, sector=row.candidate.sector,
            strategy_id=row.candidate.strategy_id, strategy_version=row.candidate.strategy_version,
            signal_time=row.candidate.signal_time, raw_score=row.candidate.raw_score,
            final_score=row.final_score, rank=row.rank, entry_price=row.candidate.entry_price,
            stop_price=row.candidate.stop_price, target_price=row.candidate.target_price,
            risk_reward=row.candidate.risk_reward, planned_capital=money(row.candidate.entry_price * quantity),
            allowed=allowed, status="SELECTED" if allowed else "REJECTED",
            score_details_json=json.dumps(row.score_details, ensure_ascii=False),
            reasons_json=json.dumps(row.candidate.reasons, ensure_ascii=False),
            blocked_reasons_json=json.dumps(blocked, ensure_ascii=False),
        )
        db.add(candidate_row)
        stored.append((row, candidate_row))
        if allowed:
            selected_pair = (row, candidate_row, risk_multiplier, capital_multiplier)
        for reason in blocked:
            _record_skip(db, user_id, "PAPER", trading_date, reason)
        if not allowed and db.get(DayTradeV2Signal, row.candidate.key) is None:
            db.add(DayTradeV2Signal(
                id=row.candidate.key, user_id=user_id, mode="PAPER",
                strategy_id=row.candidate.strategy_id, strategy_version=row.candidate.strategy_version,
                symbol=row.candidate.symbol, stock_name=row.candidate.stock_name,
                sector=row.candidate.sector, side="LONG", signal_time=row.candidate.signal_time,
                signal_price=row.candidate.entry_price, confidence=row.final_score,
                risk_reward=row.candidate.risk_reward, stop_price=row.candidate.stop_price,
                target_price=row.candidate.target_price, status="SKIPPED",
                reasons_json=json.dumps(row.candidate.reasons, ensure_ascii=False),
                skip_reason="、".join(blocked) or "未取得本輪交易權",
                market_context_json=json.dumps({
                    "marketRegime": snapshot.effective_regime,
                    "rawScore": str(row.candidate.raw_score),
                    "scoreDetails": row.score_details,
                }, ensure_ascii=False),
                controller_decision_id=cycle.id,
            ))
    cycle.candidate_count = len(stored)
    cycle.status = "SELECTED" if selected_pair else "NO_TRADE"
    if not selected_pair:
        cycle.block_reason = "沒有通過總控與風控的候選訊號"
    db.flush()
    executed = 0
    if selected_pair:
        ranked_row, candidate_row, risk_multiplier, capital_multiplier = selected_pair
        cycle.selected_candidate_id = candidate_row.id
        db.flush()
        try:
            position = paper_entry(PaperEntryBody(
                signal_id=ranked_row.candidate.key, strategy_id=ranked_row.candidate.strategy_id,
                symbol=ranked_row.candidate.symbol, stock_name=ranked_row.candidate.stock_name,
                signal_price=ranked_row.candidate.entry_price, fill_price=ranked_row.candidate.entry_price,
                stop_price=ranked_row.candidate.stop_price, target_price=ranked_row.candidate.target_price,
                confidence=ranked_row.final_score, signal_time=ranked_row.candidate.signal_time,
                reasons=list(ranked_row.candidate.reasons), sector=ranked_row.candidate.sector,
                controller_decision_id=cycle.id, controller_candidate_id=candidate_row.id,
                risk_multiplier=risk_multiplier, capital_multiplier=capital_multiplier,
            ), user_id, db)
            candidate_row.status = "EXECUTED"
            executed = 1
            _notification(
                db, user_id=user_id, mode="PAPER", event_id=f"controller-selected:{cycle.id}",
                event_type="CONTROLLER_SIGNAL_SELECTED", title="【策略總控｜正式選中訊號】",
                message=(f"{ranked_row.candidate.symbol} {ranked_row.candidate.stock_name}｜{ranked_row.candidate.strategy_id}｜"
                         f"{REGIME_LABELS.get(snapshot.effective_regime)}，策略符合目前盤勢｜"
                         f"原始{ranked_row.candidate.raw_score}分，盤勢調整{ranked_row.score_details.get('regime', 0)}分，"
                         f"最終{ranked_row.final_score}分，排名第{ranked_row.rank}｜"
                         f"進場{ranked_row.candidate.entry_price}／停損{ranked_row.candidate.stop_price}／停利{ranked_row.candidate.target_price}｜"
                         f"預計資金{candidate_row.planned_capital}元｜模擬成交成功，部位{position['id']}"),
                payload={
                    "cycleId": cycle.id, "candidateId": candidate_row.id,
                    "marketRegime": snapshot.effective_regime, "rawScore": str(ranked_row.candidate.raw_score),
                    "scoreDetails": ranked_row.score_details, "finalScore": str(ranked_row.final_score),
                    "rank": ranked_row.rank, "entryPrice": str(ranked_row.candidate.entry_price),
                    "stopPrice": str(ranked_row.candidate.stop_price), "targetPrice": str(ranked_row.candidate.target_price),
                    "plannedCapital": str(candidate_row.planned_capital), "filled": True,
                },
            )
        except HTTPException as exc:
            candidate_row.status = "ORDER_FAILED"
            candidate_row.allowed = False
            candidate_row.blocked_reasons_json = json.dumps([str(exc.detail)], ensure_ascii=False)
            cycle.status = "ORDER_FAILED"
            cycle.block_reason = str(exc.detail)
            _record_skip(db, user_id, "PAPER", trading_date, str(exc.detail))
    runtime.last_scan_at = current
    runtime.last_bar_at = latest_bar_at or runtime.last_bar_at
    runtime.next_scan_at = current + timedelta(seconds=interval)
    runtime.scanned_stock_count = len(live_candidates)
    runtime.candidate_count = sum(row.final_score >= dec(config["watchThreshold"]) for row in ranked)
    runtime.signal_count += len(stored)
    runtime.order_count += executed
    runtime.skipped_count += len(stored) - executed
    from ..services.day_trading_v2_challenger import run_challenger_cycle
    run_challenger_cycle(
        db, user_id, current, live_candidates, snapshot.effective_regime, config, day_trading_engine,
    )
    for ranked_row, _candidate_row in stored:
        existing_state = db.scalar(select(DayTradeV2CandidateState).where(
            DayTradeV2CandidateState.user_id == user_id, DayTradeV2CandidateState.mode == "PAPER",
            DayTradeV2CandidateState.trading_date == trading_date,
            DayTradeV2CandidateState.symbol == ranked_row.candidate.symbol,
        ))
        if existing_state is None:
            existing_state = DayTradeV2CandidateState(
                user_id=user_id, mode="PAPER", trading_date=trading_date,
                symbol=ranked_row.candidate.symbol, scanned_at=current,
            )
            db.add(existing_state)
        existing_state.stock_name = ranked_row.candidate.stock_name
        existing_state.sector = ranked_row.candidate.sector
        existing_state.strategy_id = ranked_row.candidate.strategy_id
        existing_state.confidence = ranked_row.final_score
        existing_state.signal_level = signal_level(ranked_row.final_score, config)
        existing_state.primary_reason = ranked_row.blocked_reasons[0] if ranked_row.blocked_reasons else "通過總控候選評分"
        existing_state.reasons_json = json.dumps(ranked_row.candidate.reasons, ensure_ascii=False)
        existing_state.quote_at = latest_quote
        existing_state.bar_at = ranked_row.candidate.signal_time
        existing_state.scanned_at = current
    db.commit()
    return {
        "evaluated": len(stored), "executed": executed, "skipped": len(stored) - executed,
        "exits": monitor.get("exits", []), "cycleId": cycle.id,
        "marketRegime": snapshot.effective_regime,
        "items": [_controller_candidate_dict(row) for _, row in stored],
    }


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
    strategy_version_row = db.scalar(select(DayTradeV2StrategyVersion).where(
        DayTradeV2StrategyVersion.strategy_id == position.strategy_id,
        DayTradeV2StrategyVersion.version == position.strategy_version,
    ))
    frozen_parameters = {
        "system": config,
        "strategy": _json(strategy_version_row.parameters_json, {}) if strategy_version_row else {},
        "versionChecksum": strategy_version_row.checksum if strategy_version_row else "",
    }
    trade_id = str(uuid4())
    trade = DayTradeV2Trade(
        id=trade_id, user_id=user_id, mode=position.mode, symbol=position.symbol, stock_name=position.stock_name,
        strategy_id=position.strategy_id, strategy_version=position.strategy_version, quantity=quantity,
        signal_time=position.entry_time, entry_order_time=position.entry_time, entry_fill_time=position.entry_time,
        entry_price=position.entry_price, exit_signal_time=now, exit_order_time=now, exit_fill_time=now,
        exit_price=body.fill_price, gross_pnl=result["grossPnl"], buy_fee=result["buy_fee"], sell_fee=result["sell_fee"],
        transaction_tax=result["transaction_tax"], slippage=result["slippage"], other_cost=result["other_cost"],
        net_pnl=result["netPnl"], net_return_pct=result["netReturnPct"], entry_reason="、".join(_json(position.entry_reasons_json, [])),
        exit_reason=body.reason, strategy_parameters_json=json.dumps(frozen_parameters, ensure_ascii=False, default=str),
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


@router.get("/controller")
def controller_status(user_id: str = Depends(_user_id), db: Session = Depends(get_db)) -> dict[str, object]:
    setting, _ = _ensure_defaults(db, user_id)
    return _controller_dashboard(db, user_id, setting.trade_mode)


@router.get("/controller/decisions")
def controller_decisions(
    trading_date: date | None = None, user_id: str = Depends(_user_id), db: Session = Depends(get_db),
) -> dict[str, object]:
    setting, _ = _ensure_defaults(db, user_id)
    target = trading_date or datetime.now(TAIPEI).date()
    cycles = list(db.scalars(select(DayTradeV2ControllerCycle).where(
        DayTradeV2ControllerCycle.user_id == user_id,
        DayTradeV2ControllerCycle.mode == setting.trade_mode,
        DayTradeV2ControllerCycle.trading_date == target,
    ).order_by(DayTradeV2ControllerCycle.evaluated_at.desc()).limit(300)).all())
    return {"items": [{
        "id": cycle.id, "evaluatedAt": cycle.evaluated_at, "status": cycle.status,
        "candidateCount": cycle.candidate_count, "selectedCandidateId": cycle.selected_candidate_id,
        "blockReason": cycle.block_reason,
        "candidates": [_controller_candidate_dict(row) for row in db.scalars(select(DayTradeV2ControllerCandidate).where(
            DayTradeV2ControllerCandidate.cycle_id == cycle.id,
        ).order_by(DayTradeV2ControllerCandidate.final_score.desc())).all()],
    } for cycle in cycles]}


@router.get("/optimization")
def optimization_status(user_id: str = Depends(_user_id), db: Session = Depends(get_db)) -> dict[str, object]:
    setting, _ = _ensure_defaults(db, user_id)
    return _optimization_dashboard(db, user_id, setting.trade_mode)


@router.post("/optimization/diagnose")
def diagnose_now(user_id: str = Depends(_user_id), db: Session = Depends(get_db)) -> dict[str, object]:
    setting, _ = _ensure_defaults(db, user_id)
    config = merged_config(_json(setting.config_json, {}))
    rows = run_health_diagnosis(db, user_id, setting.trade_mode, config)
    _audit(db, user_id, "STRATEGY_HEALTH_DIAGNOSED", setting.trade_mode, {"count": len(rows)})
    db.commit()
    return _optimization_dashboard(db, user_id, setting.trade_mode)


@router.post("/optimization/datasets")
async def upload_optimization_dataset(
    request: Request, name: str = Query(min_length=1, max_length=160),
    data_format: str = Query(pattern="^(?i:CSV|PARQUET)$"),
    user_id: str = Depends(_user_id), db: Session = Depends(get_db),
) -> dict[str, object]:
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "資料檔超過512MB限制")
    content = await request.body()
    dataset_id = str(uuid4())
    try:
        _datasets, _sectors, _regimes, quality = parse_dataset(content, data_format)
        path, checksum = persist_dataset(content, dataset_id=dataset_id, data_format=data_format)
    except DatasetValidationError as exc:
        raise HTTPException(422, str(exc)) from exc
    row = DayTradeV2OptimizationDataset(
        id=dataset_id, user_id=user_id, name=name, storage_path=str(path), checksum=checksum,
        data_format=data_format.upper(), start_date=date.fromisoformat(str(quality["startDate"])),
        end_date=date.fromisoformat(str(quality["endDate"])), trading_day_count=int(quality["tradingDayCount"]),
        symbol_count=int(quality["symbolCount"]), row_count=int(quality["rowCount"]),
        quality_status="READY" if int(quality["tradingDayCount"]) >= 160 else "INSUFFICIENT",
        quality_json=quality_json(quality),
    )
    db.add(row)
    _audit(db, user_id, "OPTIMIZATION_DATASET_IMPORTED", "BACKTEST", quality, row.id)
    db.commit()
    return {"id": row.id, "qualityStatus": row.quality_status, "quality": quality, "checksum": checksum}


class OptimizationJobBody(BaseModel):
    strategy_id: str
    dataset_id: str | None = None


@router.post("/optimization/jobs")
def create_optimization_job(body: OptimizationJobBody, user_id: str = Depends(_user_id), db: Session = Depends(get_db)) -> dict[str, object]:
    _ensure_defaults(db, user_id)
    if body.strategy_id not in {row[0] for row in STRATEGIES}:
        raise HTTPException(404, "找不到策略")
    active = db.scalar(select(DayTradeV2OptimizationJob).where(
        DayTradeV2OptimizationJob.user_id == user_id,
        DayTradeV2OptimizationJob.strategy_id == body.strategy_id,
        DayTradeV2OptimizationJob.status.in_(("QUEUED", "RUNNING")),
    ))
    if active:
        raise HTTPException(409, "同一台機器人同時只能執行一個優化任務")
    dataset = db.get(DayTradeV2OptimizationDataset, body.dataset_id) if body.dataset_id else db.scalar(select(DayTradeV2OptimizationDataset).where(
        DayTradeV2OptimizationDataset.user_id == user_id,
        DayTradeV2OptimizationDataset.quality_status == "READY",
    ).order_by(DayTradeV2OptimizationDataset.created_at.desc()))
    if dataset and dataset.user_id != user_id:
        raise HTTPException(404, "找不到資料集")
    versions = list(db.scalars(select(DayTradeV2StrategyVersion.version).where(
        DayTradeV2StrategyVersion.strategy_id == body.strategy_id,
    )).all())
    job = DayTradeV2OptimizationJob(
        id=str(uuid4()), user_id=user_id, strategy_id=body.strategy_id,
        champion_version=active_version(db, user_id, body.strategy_id), candidate_version=next_version(versions),
        trigger_type="MANUAL", dataset_id=dataset.id if dataset else "",
        status="QUEUED" if dataset and dataset.quality_status == "READY" else "DATA_INSUFFICIENT",
        walk_forward_json=json.dumps({"trainDays": 120, "validationDays": 20, "oosDays": 20, "stepDays": 20}),
        result_json=json.dumps({"reason": "等待至少160個交易日的合格分鐘資料"}, ensure_ascii=False) if not dataset or dataset.quality_status != "READY" else "{}",
        completed_at=_now() if not dataset or dataset.quality_status != "READY" else None,
    )
    db.add(job)
    _audit(db, user_id, "OPTIMIZATION_JOB_CREATED", "BACKTEST", {"strategyId": body.strategy_id, "status": job.status}, job.id)
    db.commit()
    return {"id": job.id, "status": job.status, "candidateVersion": job.candidate_version}


class VersionActionBody(BaseModel):
    confirmation_version: str
    approval_code: str = ""
    reason: str = ""


def _next_trading_date(db: Session, current: date) -> date:
    holidays = set(db.scalars(select(DayTradeV2CalendarHoliday.holiday_date)).all())
    candidate = current + timedelta(days=1)
    while candidate.weekday() >= 5 or candidate in holidays:
        candidate += timedelta(days=1)
    return candidate


@router.post("/strategy-versions/{strategy_id}/{version}/approve")
def approve_strategy_version(
    strategy_id: str, version: str, body: VersionActionBody,
    user_id: str = Depends(_user_id), db: Session = Depends(get_db),
) -> dict[str, object]:
    expected = os.getenv("DTV2_STRATEGY_APPROVAL_CODE", "")
    if not expected:
        raise HTTPException(503, "尚未設定伺服器策略批准碼")
    if body.confirmation_version != version or not hmac.compare_digest(body.approval_code, expected):
        raise HTTPException(403, "版本確認或策略批准碼錯誤")
    deployment = db.scalar(select(DayTradeV2StrategyDeployment).where(
        DayTradeV2StrategyDeployment.user_id == user_id,
        DayTradeV2StrategyDeployment.strategy_id == strategy_id,
        DayTradeV2StrategyDeployment.version == version,
        DayTradeV2StrategyDeployment.role == "CHALLENGER",
        DayTradeV2StrategyDeployment.status == "WAITING_APPROVAL",
    ))
    run = db.scalar(select(DayTradeV2ChallengerRun).where(
        DayTradeV2ChallengerRun.user_id == user_id,
        DayTradeV2ChallengerRun.strategy_id == strategy_id,
        DayTradeV2ChallengerRun.challenger_version == version,
    ).order_by(DayTradeV2ChallengerRun.started_at.desc()))
    if not deployment or not run:
        raise HTTPException(409, "候選版本尚未完成驗證或模擬觀察")
    challenger_metrics = _json(run.challenger_metrics_json, {})
    champion_metrics = _json(run.champion_metrics_json, {})
    ready, reasons = challenger_ready(
        full_trading_days=run.full_trading_days, trade_count=run.trade_count,
        net_pnl=challenger_metrics.get("netPnl", 0), max_drawdown=challenger_metrics.get("maxDrawdown", 0),
        champion_max_drawdown=champion_metrics.get("maxDrawdown", 0), error_count=run.error_count,
    )
    if not ready:
        raise HTTPException(409, "；".join(reasons))
    deployment.status = "PENDING_ACTIVATION"
    deployment.approved_by = user_id
    deployment.approved_at = _now()
    deployment.effective_date = _next_trading_date(db, datetime.now(TAIPEI).date())
    _audit(db, user_id, "STRATEGY_VERSION_APPROVED", "LIVE", {"strategyId": strategy_id, "version": version, "effectiveDate": str(deployment.effective_date)}, deployment.id)
    _notification(db, user_id=user_id, mode="PAPER", event_id=f"version-approved:{deployment.id}", event_type="STRATEGY_VERSION_APPROVED", title="【策略版本已批准】", message=f"{strategy_id} {version} 將於 {deployment.effective_date} 08:30 生效")
    db.commit()
    return {"status": deployment.status, "effectiveDate": deployment.effective_date}


@router.post("/strategy-versions/{strategy_id}/{version}/reject")
def reject_strategy_version(
    strategy_id: str, version: str, body: VersionActionBody,
    user_id: str = Depends(_user_id), db: Session = Depends(get_db),
) -> dict[str, object]:
    if body.confirmation_version != version:
        raise HTTPException(422, "請輸入完整版本號確認")
    deployment = db.scalar(select(DayTradeV2StrategyDeployment).where(
        DayTradeV2StrategyDeployment.user_id == user_id,
        DayTradeV2StrategyDeployment.strategy_id == strategy_id,
        DayTradeV2StrategyDeployment.version == version,
    ))
    if not deployment or deployment.status not in {"WAITING_APPROVAL", "RUNNING"}:
        raise HTTPException(409, "候選版本狀態不可拒絕")
    deployment.status = "REJECTED"
    deployment.disabled_at = _now()
    deployment.disabled_reason = body.reason or "使用者拒絕候選版本"
    run = db.scalar(select(DayTradeV2ChallengerRun).where(
        DayTradeV2ChallengerRun.user_id == user_id,
        DayTradeV2ChallengerRun.strategy_id == strategy_id,
        DayTradeV2ChallengerRun.challenger_version == version,
    ).order_by(DayTradeV2ChallengerRun.started_at.desc()))
    if run:
        run.status = "REJECTED"
        run.completed_at = _now()
    version_row = db.scalar(select(DayTradeV2StrategyVersion).where(
        DayTradeV2StrategyVersion.strategy_id == strategy_id,
        DayTradeV2StrategyVersion.version == version,
    ))
    if version_row:
        version_row.validation_status = "REJECTED"
    _audit(db, user_id, "STRATEGY_VERSION_REJECTED", "PAPER", {"strategyId": strategy_id, "version": version, "reason": deployment.disabled_reason}, deployment.id)
    db.commit()
    return {"status": deployment.status}


@router.post("/strategy-versions/{strategy_id}/{version}/rollback")
def rollback_strategy_version(
    strategy_id: str, version: str, body: VersionActionBody,
    user_id: str = Depends(_user_id), db: Session = Depends(get_db),
) -> dict[str, object]:
    if body.confirmation_version != version:
        raise HTTPException(422, "請輸入完整版本號確認")
    current = db.scalar(select(DayTradeV2StrategyDeployment).where(
        DayTradeV2StrategyDeployment.user_id == user_id,
        DayTradeV2StrategyDeployment.strategy_id == strategy_id,
        DayTradeV2StrategyDeployment.version == version,
        DayTradeV2StrategyDeployment.status == "ACTIVE",
    ))
    previous = db.scalar(select(DayTradeV2StrategyDeployment).where(
        DayTradeV2StrategyDeployment.user_id == user_id,
        DayTradeV2StrategyDeployment.strategy_id == strategy_id,
        DayTradeV2StrategyDeployment.status == "SUPERSEDED",
    ).order_by(DayTradeV2StrategyDeployment.disabled_at.desc()))
    if not current or not previous:
        raise HTTPException(409, "沒有可回復的上一個驗證版本")
    current.status = "ROLLBACK_DISABLED"
    current.disabled_at = _now()
    current.disabled_reason = body.reason or "新版本異常，等待安全回復"
    current.rollback_to_version = previous.version
    version_row = db.scalar(select(DayTradeV2StrategyVersion).where(
        DayTradeV2StrategyVersion.strategy_id == strategy_id,
        DayTradeV2StrategyVersion.version == version,
    ))
    if version_row:
        version_row.validation_status = "ROLLBACK_DISABLED"
    pending = DayTradeV2StrategyDeployment(
        id=str(uuid4()), user_id=user_id, strategy_id=strategy_id, version=previous.version,
        role="CHAMPION", status="PENDING_ACTIVATION",
        effective_date=_next_trading_date(db, datetime.now(TAIPEI).date()), rollback_to_version=previous.version,
    )
    db.add(pending)
    robot = db.scalar(select(DayTradeV2Robot).where(
        DayTradeV2Robot.user_id == user_id, DayTradeV2Robot.strategy_id == strategy_id,
    ))
    if robot:
        robot.status = "HALTED_TODAY"
        robot.status_date = datetime.now(TAIPEI).date()
    _audit(db, user_id, "STRATEGY_VERSION_ROLLED_BACK", "LIVE", {"from": version, "to": previous.version, "effectiveDate": str(pending.effective_date)}, current.id)
    _notification(db, user_id=user_id, mode="PAPER", event_id=f"version-rollback:{current.id}", event_type="STRATEGY_VERSION_ROLLBACK", title="【策略版本緊急回復】", message=f"{strategy_id} 已停止新交易；{previous.version} 將於 {pending.effective_date} 08:30 恢復")
    db.commit()
    return {"status": "PENDING_ROLLBACK", "rollbackToVersion": previous.version, "effectiveDate": pending.effective_date}


class BacktestBody(BaseModel):
    backtest_mode: str = Field(default="PORTFOLIO", pattern="^(PORTFOLIO|INDIVIDUAL)$")
    strategy_id: str = "ALL"
    start_date: date
    end_date: date
    dataset_id: str | None = None
    datasets: dict[str, list[dict[str, object]]] | None = None
    data_source: str = Field(default="AUTO_FUGLE", pattern="^(AUTO_FUGLE|UPLOADED_DATASET|REQUEST_DATASET)$")
    universe_preset: str = Field(default="TOP_LIQUID_100", pattern="^(TOP_LIQUID_100|CUSTOM)$")
    symbols: list[str] = Field(default_factory=list, max_length=200)


def _backtest_job_dict(row: DayTradeV2BacktestJob) -> dict[str, object]:
    return {
        "id": row.id, "mode": row.backtest_mode, "strategyId": row.strategy_id,
        "startDate": row.start_date, "endDate": row.end_date, "status": row.status,
        "dataSource": row.data_source, "dataPrecision": row.data_precision,
        "datasetId": row.dataset_id, "progressPct": str(row.progress_pct),
        "progress": _json(row.progress_json, {}), "universe": _json(row.universe_json, []),
        "result": _json(row.result_json, {}), "error": row.error_message,
        "createdAt": row.created_at, "completedAt": row.completed_at,
    }


@router.post("/backtests", status_code=202)
def create_backtest(body: BacktestBody, user_id: str = Depends(_user_id), db: Session = Depends(get_db)) -> dict[str, object]:
    if body.end_date < body.start_date:
        raise HTTPException(422, "結束日期不可早於開始日期")
    if body.end_date > datetime.now(TAIPEI).date():
        raise HTTPException(422, "回測結束日期不可晚於今天")
    if body.strategy_id != "ALL" and body.strategy_id not in {item[0] for item in STRATEGIES}:
        raise HTTPException(422, "未知的回測策略")
    job_id = str(uuid4())
    if not body.dataset_id and not body.datasets and body.data_source == "AUTO_FUGLE":
        if body.start_date < MINUTE_DATA_START:
            raise HTTPException(422, f"Fugle 1分鐘歷史行情從{MINUTE_DATA_START.isoformat()}開始提供，請調整開始日期。")
        from ..config import get_settings
        if len(body.symbols) > get_settings().dtv2_backtest_max_symbols:
            raise HTTPException(422, f"自訂股票池最多{get_settings().dtv2_backtest_max_symbols}檔")
        job = DayTradeV2BacktestJob(
            id=job_id, user_id=user_id, backtest_mode=body.backtest_mode, strategy_id=body.strategy_id,
            start_date=body.start_date, end_date=body.end_date, status="QUEUED", data_source="FUGLE_AUTO",
            data_precision="1_MINUTE", dataset_id="", progress_pct=Decimal(0),
            progress_json=json.dumps({"stage": "QUEUED", "message": "等待準備Fugle 1分鐘行情"}, ensure_ascii=False),
            universe_json="[]", request_json=body.model_dump_json(exclude={"datasets"}), result_json="{}",
        )
        db.add(job)
        _audit(db, user_id, "BACKTEST_QUEUED", "BACKTEST", {
            "dataSource": "FUGLE_AUTO", "universePreset": body.universe_preset,
            "symbolCount": len(body.symbols),
        }, job_id)
        db.commit()
        db.refresh(job)
        return _backtest_job_dict(job)
    request_datasets = body.datasets
    dataset_source = "REQUEST_DATASET"
    loaded_regimes: dict[datetime, str] = {}
    if body.dataset_id:
        dataset_row = db.get(DayTradeV2OptimizationDataset, body.dataset_id)
        if dataset_row is None or dataset_row.user_id != user_id:
            raise HTTPException(404, "找不到指定的分鐘資料集")
        try:
            loaded, _loaded_sectors, loaded_regimes, _quality = load_dataset(
                dataset_row.storage_path, dataset_row.checksum, dataset_row.data_format,
            )
        except DatasetValidationError as exc:
            raise HTTPException(422, str(exc)) from exc
        request_datasets = {
            symbol: [
                {
                    "timestamp": bar.timestamp.isoformat(), "open": str(bar.open),
                    "high": str(bar.high), "low": str(bar.low), "close": str(bar.close),
                    "volume": bar.volume, "sector": _loaded_sectors.get(symbol, "未分類"),
                }
                for bar in bars
                if body.start_date <= bar.timestamp.astimezone(TAIPEI).date() <= body.end_date
            ]
            for symbol, bars in loaded.items()
        }
        request_datasets = {symbol: bars for symbol, bars in request_datasets.items() if bars}
        dataset_source = f"DATASET:{dataset_row.id}"
    if not request_datasets:
        result = {"code": "MINUTE_DATA_REQUIRED", "message": "資料精度不足：尚未設定合格的歷史分鐘行情來源，不適合驗證當沖策略。", "summary": None, "trades": []}
        status, source, precision = "DATA_INSUFFICIENT", "UNCONFIGURED", "NONE"
    else:
        parsed: dict[str, list[MinuteBar]] = {}
        sectors: dict[str, str] = {}
        try:
            for symbol, rows in request_datasets.items():
                sectors[symbol] = str((rows[0].get("sector") if rows else None) or "回測未分類")
                parsed[symbol] = [MinuteBar(
                    timestamp=datetime.fromisoformat(str(row["timestamp"])), open=dec(row["open"]), high=dec(row["high"]),
                    low=dec(row["low"]), close=dec(row["close"]), volume=int(row["volume"]),
                ) for row in rows]
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(422, f"分鐘行情格式錯誤：{exc}") from exc
        result = execute_backtest(
            parsed, sectors, loaded_regimes,
            backtest_mode=body.backtest_mode, strategy_id=body.strategy_id,
        )
        if not loaded_regimes:
            result["marketRegimeNotice"] = "此直接請求未提供大盤分鐘脈絡；正式CSV／Parquet資料集必須包含大盤欄位。"
        status, source, precision = "COMPLETED", dataset_source, "1_MINUTE"
    job = DayTradeV2BacktestJob(
        id=job_id, user_id=user_id, backtest_mode=body.backtest_mode, strategy_id=body.strategy_id,
        start_date=body.start_date, end_date=body.end_date, status=status, data_source=source,
        data_precision=precision, request_json=body.model_dump_json(exclude={"datasets"}),
        result_json=json.dumps(result, default=str, ensure_ascii=False), completed_at=_now(),
        dataset_id=body.dataset_id or "", progress_pct=Decimal(100),
        progress_json=json.dumps({"stage": status, "message": result.get("message", "回測完成")}, ensure_ascii=False),
        universe_json="[]",
    )
    db.add(job)
    _audit(db, user_id, "BACKTEST_CREATED", "BACKTEST", {"status": status}, job_id)
    db.commit()
    return {**_backtest_job_dict(job), **result}


@router.get("/backtests")
def backtests(user_id: str = Depends(_user_id), db: Session = Depends(get_db)) -> dict[str, object]:
    rows = list(db.scalars(select(DayTradeV2BacktestJob).where(DayTradeV2BacktestJob.user_id == user_id).order_by(DayTradeV2BacktestJob.created_at.desc()).limit(50)).all())
    return {"items": [_backtest_job_dict(row) for row in rows]}


@router.get("/backtests/{job_id}")
def backtest_job(job_id: str, user_id: str = Depends(_user_id), db: Session = Depends(get_db)) -> dict[str, object]:
    row = db.get(DayTradeV2BacktestJob, job_id)
    if row is None or row.user_id != user_id:
        raise HTTPException(404, "找不到回測任務")
    return _backtest_job_dict(row)


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
