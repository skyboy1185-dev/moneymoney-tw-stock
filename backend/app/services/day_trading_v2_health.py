from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
from uuid import uuid4

from sqlalchemy import select

from ..day_trading_v2_models import (
    DayTradeV2ChallengerRun, DayTradeV2Notification, DayTradeV2OptimizationDataset, DayTradeV2OptimizationJob,
    DayTradeV2AuditEvent, DayTradeV2CalendarHoliday,
    DayTradeV2Robot, DayTradeV2StrategyDeployment, DayTradeV2StrategyHealthSnapshot,
    DayTradeV2StrategyRiskOverride, DayTradeV2StrategyVersion, DayTradeV2Trade,
)
from .day_trading_v2 import STRATEGIES, dec
from .day_trading_v2_optimization import diagnose_health, next_version


def active_version(db, user_id: str, strategy_id: str) -> str:
    deployment = db.scalar(select(DayTradeV2StrategyDeployment).where(
        DayTradeV2StrategyDeployment.user_id == user_id,
        DayTradeV2StrategyDeployment.strategy_id == strategy_id,
        DayTradeV2StrategyDeployment.role == "CHAMPION",
        DayTradeV2StrategyDeployment.status == "ACTIVE",
    ).order_by(DayTradeV2StrategyDeployment.activated_at.desc()))
    return deployment.version if deployment else "2.0.0"


def health_for_strategy(db, user_id: str, mode: str, strategy_id: str) -> DayTradeV2StrategyHealthSnapshot | None:
    return db.scalar(select(DayTradeV2StrategyHealthSnapshot).where(
        DayTradeV2StrategyHealthSnapshot.user_id == user_id,
        DayTradeV2StrategyHealthSnapshot.mode == mode,
        DayTradeV2StrategyHealthSnapshot.strategy_id == strategy_id,
    ).order_by(DayTradeV2StrategyHealthSnapshot.diagnosis_date.desc()))


def run_health_diagnosis(db, user_id: str, mode: str, config: dict[str, object], now: datetime | None = None) -> list[DayTradeV2StrategyHealthSnapshot]:
    current = now or datetime.now(UTC)
    day = current.date()
    results: list[DayTradeV2StrategyHealthSnapshot] = []
    for strategy_id, strategy_name, _ in STRATEGIES:
        trades = list(db.scalars(select(DayTradeV2Trade).where(
            DayTradeV2Trade.user_id == user_id, DayTradeV2Trade.mode == mode,
            DayTradeV2Trade.strategy_id == strategy_id,
        ).order_by(DayTradeV2Trade.exit_fill_time)).all())
        trade_metrics = [{"netPnl": row.net_pnl} for row in trades]
        recent = trade_metrics[-50:]
        daily: dict[object, Decimal] = defaultdict(lambda: Decimal("0"))
        regime_rows: dict[str, list[dict[str, object]]] = defaultdict(list)
        for row in trades:
            daily[row.exit_fill_time.date()] += row.net_pnl
            try:
                context = json.loads(row.market_context_json or "{}")
            except (TypeError, ValueError):
                context = {}
            persisted_regime = str(row.entry_market_regime or "UNKNOWN")
            regime = persisted_regime if persisted_regime != "UNKNOWN" else str(context.get("marketRegime") or context.get("regime") or "UNKNOWN")
            if regime != "UNKNOWN":
                regime_rows[regime].append({"netPnl": row.net_pnl})
        month_rows = [
            {"netPnl": row.net_pnl} for row in trades
            if row.exit_fill_time.year == day.year and row.exit_fill_time.month == day.month
        ]
        notional = sum((row.entry_price * row.quantity + row.exit_price * row.quantity for row in trades[-20:]), Decimal("0"))
        actual_slippage = sum((row.slippage for row in trades[-20:]), Decimal("0"))
        actual_slippage_bps = actual_slippage / notional * Decimal("10000") if notional else None
        version = active_version(db, user_id, strategy_id)
        definition = db.scalar(select(DayTradeV2StrategyVersion).where(
            DayTradeV2StrategyVersion.strategy_id == strategy_id,
            DayTradeV2StrategyVersion.version == version,
        ))
        baseline = json.loads(definition.oos_result_json or "{}") if definition else {}
        previous = db.scalar(select(DayTradeV2StrategyHealthSnapshot).where(
            DayTradeV2StrategyHealthSnapshot.user_id == user_id,
            DayTradeV2StrategyHealthSnapshot.mode == mode,
            DayTradeV2StrategyHealthSnapshot.strategy_id == strategy_id,
            DayTradeV2StrategyHealthSnapshot.diagnosis_date < day,
        ).order_by(DayTradeV2StrategyHealthSnapshot.diagnosis_date.desc()))
        consecutive_alerts = 1 if previous and previous.status == "ALERT" else 0
        diagnosis = diagnose_health(
            recent, trading_day_pnls=list(daily.values()), baseline=baseline,
            actual_slippage_bps=actual_slippage_bps, assumed_slippage_bps=config.get("slippageBps"), config=config,
            consecutive_alert_days=consecutive_alerts,
            month_trades=month_rows, historical_trades=trade_metrics, regime_trades=regime_rows,
        )
        snapshot = db.scalar(select(DayTradeV2StrategyHealthSnapshot).where(
            DayTradeV2StrategyHealthSnapshot.user_id == user_id,
            DayTradeV2StrategyHealthSnapshot.mode == mode,
            DayTradeV2StrategyHealthSnapshot.strategy_id == strategy_id,
            DayTradeV2StrategyHealthSnapshot.diagnosis_date == day,
        ))
        values = {
            "strategy_version": version, "status": diagnosis.status,
            "reasons_json": json.dumps(diagnosis.reasons, ensure_ascii=False),
            "metrics_json": json.dumps(diagnosis.metrics, ensure_ascii=False, default=str),
            "baseline_json": json.dumps(baseline, ensure_ascii=False, default=str),
            "recommended_action": diagnosis.recommended_action,
            "capital_multiplier": diagnosis.capital_multiplier,
            "risk_multiplier": diagnosis.risk_multiplier, "calculated_at": current,
        }
        if snapshot is None:
            snapshot = DayTradeV2StrategyHealthSnapshot(
                user_id=user_id, mode=mode, strategy_id=strategy_id, diagnosis_date=day, **values,
            )
            db.add(snapshot)
        else:
            for key, value in values.items():
                setattr(snapshot, key, value)
        override = db.scalar(select(DayTradeV2StrategyRiskOverride).where(
            DayTradeV2StrategyRiskOverride.user_id == user_id,
            DayTradeV2StrategyRiskOverride.mode == mode,
            DayTradeV2StrategyRiskOverride.strategy_id == strategy_id,
        ))
        if override is None:
            override = DayTradeV2StrategyRiskOverride(user_id=user_id, mode=mode, strategy_id=strategy_id)
            db.add(override)
        if diagnosis.status == "ALERT":
            # Risk may only stay unchanged or decrease automatically.
            current_capital = dec(override.capital_multiplier if override.capital_multiplier is not None else 1)
            current_risk = dec(override.risk_multiplier if override.risk_multiplier is not None else 1)
            override.capital_multiplier = min(current_capital, diagnosis.capital_multiplier)
            override.risk_multiplier = min(current_risk, diagnosis.risk_multiplier)
            override.paused = diagnosis.recommended_action == "PAUSE" or override.paused
            override.reason = "；".join(diagnosis.reasons)
            event_id = f"strategy-health:{user_id}:{mode}:{strategy_id}:{day}"
            if not db.scalar(select(DayTradeV2Notification).where(
                DayTradeV2Notification.user_id == user_id, DayTradeV2Notification.event_id == event_id,
            )):
                db.add(DayTradeV2Notification(
                    user_id=user_id, event_id=event_id, mode=mode, event_type="STRATEGY_HEALTH_ALERT",
                    title=f"【策略績效警戒｜{strategy_name}】",
                    message=f"{'；'.join(diagnosis.reasons)}｜處置：{'暫停新交易' if override.paused else '資金與單筆風險降為50%'}",
                    payload_json=json.dumps({"strategyId": strategy_id, "version": version}, ensure_ascii=False),
                ))
            _ensure_optimization_job(db, user_id, strategy_id, version, day)
        _auto_rollback_if_needed(db, user_id, mode, strategy_id, version, trades, config, current)
        results.append(snapshot)
    db.flush()
    return results


def apply_pending_deployments(db, user_id: str, trading_date, now: datetime) -> int:
    pending = list(db.scalars(select(DayTradeV2StrategyDeployment).where(
        DayTradeV2StrategyDeployment.user_id == user_id,
        DayTradeV2StrategyDeployment.status == "PENDING_ACTIVATION",
        DayTradeV2StrategyDeployment.effective_date <= trading_date,
    )).all())
    activated = 0
    for deployment in pending:
        current_rows = list(db.scalars(select(DayTradeV2StrategyDeployment).where(
            DayTradeV2StrategyDeployment.user_id == user_id,
            DayTradeV2StrategyDeployment.strategy_id == deployment.strategy_id,
            DayTradeV2StrategyDeployment.role == "CHAMPION",
            DayTradeV2StrategyDeployment.status == "ACTIVE",
        )).all())
        for current in current_rows:
            current.status = "SUPERSEDED"
            current.disabled_at = now
            current.disabled_reason = f"由版本 {deployment.version} 取代"
        deployment.role = "CHAMPION"
        deployment.status = "ACTIVE"
        deployment.activated_at = now
        version = db.scalar(select(DayTradeV2StrategyVersion).where(
            DayTradeV2StrategyVersion.strategy_id == deployment.strategy_id,
            DayTradeV2StrategyVersion.version == deployment.version,
        ))
        if version:
            version.validation_status = "CHAMPION"
        challenger_run = db.scalar(select(DayTradeV2ChallengerRun).where(
            DayTradeV2ChallengerRun.user_id == user_id,
            DayTradeV2ChallengerRun.strategy_id == deployment.strategy_id,
            DayTradeV2ChallengerRun.challenger_version == deployment.version,
            DayTradeV2ChallengerRun.status == "APPROVED_PENDING_ACTIVATION",
        ).order_by(DayTradeV2ChallengerRun.started_at.desc()))
        if challenger_run:
            challenger_run.status = "PROMOTED"
            challenger_run.completed_at = now
        activated += 1
    db.flush()
    return activated


def _ensure_optimization_job(db, user_id: str, strategy_id: str, champion_version: str, day) -> None:
    challenger = db.scalar(select(DayTradeV2ChallengerRun).where(
        DayTradeV2ChallengerRun.user_id == user_id,
        DayTradeV2ChallengerRun.strategy_id == strategy_id,
        DayTradeV2ChallengerRun.status.in_(("RUNNING", "WAITING_APPROVAL", "APPROVED_PENDING_ACTIVATION")),
    ))
    if challenger:
        return
    active = db.scalar(select(DayTradeV2OptimizationJob).where(
        DayTradeV2OptimizationJob.user_id == user_id,
        DayTradeV2OptimizationJob.strategy_id == strategy_id,
        DayTradeV2OptimizationJob.status.in_(("QUEUED", "RUNNING")),
    ))
    if active:
        return
    dataset = db.scalar(select(DayTradeV2OptimizationDataset).where(
        DayTradeV2OptimizationDataset.user_id == user_id,
        DayTradeV2OptimizationDataset.quality_status == "READY",
    ).order_by(DayTradeV2OptimizationDataset.created_at.desc()))
    versions = list(db.scalars(select(DayTradeV2StrategyVersion.version).where(
        DayTradeV2StrategyVersion.strategy_id == strategy_id,
    )).all())
    db.add(DayTradeV2OptimizationJob(
        id=str(uuid4()), user_id=user_id, strategy_id=strategy_id,
        champion_version=champion_version, candidate_version=next_version(versions),
        trigger_type="HEALTH_ALERT", dataset_id=dataset.id if dataset else "",
        status="QUEUED" if dataset else "DATA_INSUFFICIENT",
        search_space_json="{}", walk_forward_json=json.dumps({"trainDays": 120, "validationDays": 20, "oosDays": 20, "stepDays": 20}),
        result_json=json.dumps({"reason": "等待合格歷史分鐘資料"}, ensure_ascii=False) if not dataset else "{}",
        completed_at=datetime.now(UTC) if not dataset else None,
    ))


def _next_trading_day(db, value):
    holidays = set(db.scalars(select(DayTradeV2CalendarHoliday.holiday_date)).all())
    value += timedelta(days=1)
    while value.weekday() >= 5 or value in holidays:
        value += timedelta(days=1)
    return value


def handle_strategy_runtime_error(
    db, user_id: str, mode: str, strategy_id: str, version: str,
    error: Exception, now: datetime,
) -> bool:
    """Stop a faulty strategy immediately and queue its last verified version for the next session."""
    reason = f"策略執行錯誤：{type(error).__name__}: {str(error)[:300]}"
    robot = db.scalar(select(DayTradeV2Robot).where(
        DayTradeV2Robot.user_id == user_id, DayTradeV2Robot.strategy_id == strategy_id,
    ))
    if robot:
        robot.status = "HALTED_TODAY"
        robot.status_date = now.date()
    deployment = db.scalar(select(DayTradeV2StrategyDeployment).where(
        DayTradeV2StrategyDeployment.user_id == user_id,
        DayTradeV2StrategyDeployment.strategy_id == strategy_id,
        DayTradeV2StrategyDeployment.version == version,
        DayTradeV2StrategyDeployment.status == "ACTIVE",
    ))
    rolled_back = False
    if deployment and version != "2.0.0":
        previous = db.scalar(select(DayTradeV2StrategyDeployment).where(
            DayTradeV2StrategyDeployment.user_id == user_id,
            DayTradeV2StrategyDeployment.strategy_id == strategy_id,
            DayTradeV2StrategyDeployment.status == "SUPERSEDED",
        ).order_by(DayTradeV2StrategyDeployment.disabled_at.desc()))
        pending = db.scalar(select(DayTradeV2StrategyDeployment).where(
            DayTradeV2StrategyDeployment.user_id == user_id,
            DayTradeV2StrategyDeployment.strategy_id == strategy_id,
            DayTradeV2StrategyDeployment.status == "PENDING_ACTIVATION",
        ))
        if previous and not pending:
            deployment.status = "ROLLBACK_DISABLED"
            deployment.disabled_at = now
            deployment.disabled_reason = reason
            deployment.rollback_to_version = previous.version
            db.add(DayTradeV2StrategyDeployment(
                id=str(uuid4()), user_id=user_id, strategy_id=strategy_id, version=previous.version,
                role="CHAMPION", status="PENDING_ACTIVATION",
                effective_date=_next_trading_day(db, now.date()), rollback_to_version=previous.version,
            ))
            rolled_back = True
    event_id = f"strategy-runtime-error:{user_id}:{mode}:{strategy_id}:{version}:{now.date()}"
    if not db.scalar(select(DayTradeV2Notification).where(
        DayTradeV2Notification.user_id == user_id, DayTradeV2Notification.event_id == event_id,
    )):
        db.add(DayTradeV2Notification(
            user_id=user_id, event_id=event_id, mode=mode, event_type="STRATEGY_RUNTIME_ERROR",
            title="【策略版本執行錯誤】", message=f"{strategy_id} {version} 已停止新交易。{reason}",
            payload_json=json.dumps({"strategyId": strategy_id, "version": version, "rollbackQueued": rolled_back}, ensure_ascii=False),
        ))
    db.add(DayTradeV2AuditEvent(
        user_id=user_id, action="STRATEGY_RUNTIME_ERROR", mode=mode,
        entity_type="STRATEGY_VERSION", entity_id=f"{strategy_id}:{version}",
        details_json=json.dumps({"reason": reason, "rollbackQueued": rolled_back}, ensure_ascii=False),
    ))
    return rolled_back


def _auto_rollback_if_needed(db, user_id, mode, strategy_id, version, trades, config, now) -> None:
    deployment = db.scalar(select(DayTradeV2StrategyDeployment).where(
        DayTradeV2StrategyDeployment.user_id == user_id,
        DayTradeV2StrategyDeployment.strategy_id == strategy_id,
        DayTradeV2StrategyDeployment.version == version,
        DayTradeV2StrategyDeployment.status == "ACTIVE",
    ))
    if not deployment or version == "2.0.0":
        return
    previous = db.scalar(select(DayTradeV2StrategyDeployment).where(
        DayTradeV2StrategyDeployment.user_id == user_id,
        DayTradeV2StrategyDeployment.strategy_id == strategy_id,
        DayTradeV2StrategyDeployment.status == "SUPERSEDED",
    ).order_by(DayTradeV2StrategyDeployment.disabled_at.desc()))
    if not previous:
        return
    version_trades = [row for row in trades if row.strategy_version == version]
    streak = 0
    for row in reversed(version_trades):
        if row.net_pnl < 0:
            streak += 1
        else:
            break
    health = diagnose_health([{"netPnl": row.net_pnl} for row in version_trades], config=config)
    window = health.metrics.get("last50", {})
    reasons = []
    if streak >= int(config.get("versionRollbackConsecutiveLosses", 3)):
        reasons.append("新版本連續虧損超過限制")
    if dec(window.get("maxDrawdown", 0)) >= dec(config.get("versionRollbackDrawdown", "24000")):
        reasons.append("新版本最大回撤超過限制")
    if len(version_trades) >= 10:
        slippage = sum((row.slippage for row in version_trades), Decimal("0"))
        notional = sum((row.entry_price * row.quantity + row.exit_price * row.quantity for row in version_trades), Decimal("0"))
        actual_bps = slippage / notional * Decimal("10000") if notional else Decimal("0")
        if actual_bps >= dec(config.get("slippageBps", "5")) * dec(config.get("healthSlippageMultiple", "2")):
            reasons.append("新版本實際滑價明顯異常")
    if not reasons:
        return
    pending = db.scalar(select(DayTradeV2StrategyDeployment).where(
        DayTradeV2StrategyDeployment.user_id == user_id,
        DayTradeV2StrategyDeployment.strategy_id == strategy_id,
        DayTradeV2StrategyDeployment.status == "PENDING_ACTIVATION",
    ))
    if pending:
        return
    deployment.status = "ROLLBACK_DISABLED"
    deployment.disabled_at = now
    deployment.disabled_reason = "；".join(reasons)
    deployment.rollback_to_version = previous.version
    effective = _next_trading_day(db, now.date())
    rollback = DayTradeV2StrategyDeployment(
        id=str(uuid4()), user_id=user_id, strategy_id=strategy_id, version=previous.version,
        role="CHAMPION", status="PENDING_ACTIVATION", effective_date=effective,
        rollback_to_version=previous.version,
    )
    db.add(rollback)
    robot = db.scalar(select(DayTradeV2Robot).where(
        DayTradeV2Robot.user_id == user_id, DayTradeV2Robot.strategy_id == strategy_id,
    ))
    if robot:
        robot.status = "HALTED_TODAY"
        robot.status_date = now.date()
    event_id = f"automatic-rollback:{deployment.id}"
    db.add(DayTradeV2Notification(
        user_id=user_id, event_id=event_id, mode=mode, event_type="STRATEGY_VERSION_ROLLBACK",
        title="【策略版本自動安全回復】",
        message=f"{strategy_id} {version} 已停止新交易；原因：{'；'.join(reasons)}；{previous.version} 將於 {effective} 生效。",
        payload_json=json.dumps({"from": version, "to": previous.version, "reasons": reasons}, ensure_ascii=False),
    ))
    db.add(DayTradeV2AuditEvent(
        user_id=user_id, action="STRATEGY_VERSION_AUTO_ROLLBACK", mode=mode,
        entity_type="STRATEGY_VERSION", entity_id=deployment.id,
        details_json=json.dumps({"from": version, "to": previous.version, "reasons": reasons}, ensure_ascii=False),
    ))
