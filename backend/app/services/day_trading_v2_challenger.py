from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal
import json
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from ..day_trading_v2_models import (
    DayTradeV2ChallengerEvent, DayTradeV2ChallengerPosition, DayTradeV2ChallengerRun, DayTradeV2ChallengerTrade,
    DayTradeV2Notification, DayTradeV2Robot, DayTradeV2StrategyDeployment, DayTradeV2StrategyVersion,
)
from .day_trading_v2 import (
    DEFAULT_STRATEGY_PARAMETERS, MinuteBar, calculate_position_size, calculate_trade_result,
    dec, evaluate_strategies, performance,
)
from .day_trading_v2_controller import (
    ControllerCandidateInput, REGIME_CRASH, REGIME_UNKNOWN, score_candidate,
)
from .day_trading_v2_optimization import calculate_health_window, challenger_ready


TAIPEI = ZoneInfo("Asia/Taipei")


def _version_parameters(db, strategy_id: str, version: str) -> dict[str, object]:
    row = db.scalar(select(DayTradeV2StrategyVersion).where(
        DayTradeV2StrategyVersion.strategy_id == strategy_id,
        DayTradeV2StrategyVersion.version == version,
    ))
    return json.loads(row.parameters_json or "{}") if row else DEFAULT_STRATEGY_PARAMETERS[strategy_id]


def _metrics(db, run_id: str, role: str) -> dict[str, object]:
    rows = list(db.scalars(select(DayTradeV2ChallengerTrade).where(
        DayTradeV2ChallengerTrade.run_id == run_id,
        DayTradeV2ChallengerTrade.role == role,
    ).order_by(DayTradeV2ChallengerTrade.exit_time)).all())
    result = performance([
        {"grossPnl": row.net_pnl + row.cost, "cost": row.cost, "netPnl": row.net_pnl}
        for row in rows
    ])
    health = calculate_health_window([{"netPnl": row.net_pnl} for row in rows])
    result["maxDrawdown"] = str(health.max_drawdown)
    return result


def _record_event(
    db, *, run: DayTradeV2ChallengerRun, role: str, version: str, event_type: str,
    event_id: str, occurred_at: datetime, signal_key: str = "", symbol: str = "",
    payload: dict[str, object] | None = None,
) -> None:
    if db.scalar(select(DayTradeV2ChallengerEvent).where(DayTradeV2ChallengerEvent.event_id == event_id)):
        return
    db.add(DayTradeV2ChallengerEvent(
        id=str(uuid4()), event_id=event_id, run_id=run.id, role=role,
        event_type=event_type, strategy_id=run.strategy_id, strategy_version=version,
        signal_key=signal_key, symbol=symbol, occurred_at=occurred_at,
        payload_json=json.dumps(payload or {}, default=str, ensure_ascii=False),
    ))


def _update_run_readiness(db, run: DayTradeV2ChallengerRun) -> None:
    champion = _metrics(db, run.id, "CHAMPION")
    challenger = _metrics(db, run.id, "CHALLENGER")
    run.champion_metrics_json = json.dumps(champion, ensure_ascii=False)
    run.challenger_metrics_json = json.dumps(challenger, ensure_ascii=False)
    run.trade_count = int(challenger["tradeCount"])
    version_row = db.scalar(select(DayTradeV2StrategyVersion).where(
        DayTradeV2StrategyVersion.strategy_id == run.strategy_id,
        DayTradeV2StrategyVersion.version == run.challenger_version,
    ))
    if version_row:
        version_row.simulation_result_json = json.dumps({
            "fullTradingDays": run.full_trading_days, "errorCount": run.error_count,
            "champion": champion, "challenger": challenger,
        }, ensure_ascii=False)
    ready, _ = challenger_ready(
        full_trading_days=run.full_trading_days, trade_count=run.trade_count,
        net_pnl=challenger["netPnl"], max_drawdown=challenger["maxDrawdown"],
        champion_max_drawdown=champion["maxDrawdown"], error_count=run.error_count,
    )
    if ready and run.status == "RUNNING":
        run.status = "WAITING_APPROVAL"
        run.completed_at = datetime.now(run.started_at.tzinfo)
        if version_row:
            version_row.validation_status = "WAITING_APPROVAL"
        deployment = db.scalar(select(DayTradeV2StrategyDeployment).where(
            DayTradeV2StrategyDeployment.user_id == run.user_id,
            DayTradeV2StrategyDeployment.strategy_id == run.strategy_id,
            DayTradeV2StrategyDeployment.version == run.challenger_version,
        ))
        if deployment:
            deployment.status = "WAITING_APPROVAL"
        event_id = f"challenger-awaiting-approval:{run.id}"
        if not db.scalar(select(DayTradeV2Notification).where(
            DayTradeV2Notification.user_id == run.user_id,
            DayTradeV2Notification.event_id == event_id,
        )):
            db.add(DayTradeV2Notification(
                user_id=run.user_id, event_id=event_id, mode="PAPER",
                event_type="CHALLENGER_WAITING_APPROVAL", title="【挑戰者策略等待批准】",
                message=f"{run.strategy_id} {run.challenger_version} 已完成平行模擬；未經人工批准不會替換正式策略。",
                payload_json=json.dumps({"runId": run.id, "version": run.challenger_version}),
            ))


def run_challenger_cycle(
    db, user_id: str, current: datetime, live_candidates: list[dict[str, object]],
    regime: str, config: dict[str, object], market_engine,
) -> int:
    """Shadow-only execution. This module deliberately has no broker adapter."""
    runs = list(db.scalars(select(DayTradeV2ChallengerRun).where(
        DayTradeV2ChallengerRun.user_id == user_id,
        DayTradeV2ChallengerRun.status == "RUNNING",
    )).all())
    if not runs:
        return 0
    quote_map = {str(item.get("symbol")): dec(item.get("price") or 0) for item in live_candidates}
    local_time = current.astimezone(TAIPEI).time()
    changes = 0
    for run in runs:
        for role, version in (("CHAMPION", run.champion_version), ("CHALLENGER", run.challenger_version)):
            positions = list(db.scalars(select(DayTradeV2ChallengerPosition).where(
                DayTradeV2ChallengerPosition.run_id == run.id,
                DayTradeV2ChallengerPosition.role == role,
                DayTradeV2ChallengerPosition.status == "OPEN",
            )).all())
            for position in positions:
                price = quote_map.get(position.symbol, Decimal("0"))
                force_close = local_time >= time(13, 25)
                if price and (force_close or price <= position.stop_price or price >= position.target_price):
                    exit_reason = "收盤前強制平倉" if force_close else "停損" if price <= position.stop_price else "停利"
                    result = calculate_trade_result(
                        entry_price=position.entry_price, exit_price=price, quantity=position.quantity,
                        commission_rate=config["commissionRate"], commission_discount=config["commissionDiscount"],
                        minimum_commission=config["minimumCommission"], tax_rate=config["dayTradeTaxRate"],
                        slippage_bps=config["slippageBps"], other_cost=config["otherCost"],
                    )
                    db.add(DayTradeV2ChallengerTrade(
                        id=str(uuid4()), run_id=run.id, role=role, symbol=position.symbol,
                        entry_time=position.opened_at, exit_time=current, quantity=position.quantity,
                        entry_price=position.entry_price, exit_price=price,
                        net_pnl=result["netPnl"], cost=result["total"],
                    ))
                    position.status = "CLOSED"
                    position.closed_at = current
                    _record_event(
                        db, run=run, role=role, version=version, event_type="EXIT",
                        event_id=f"shadow-exit:{position.id}", occurred_at=current,
                        signal_key=position.signal_key, symbol=position.symbol,
                        payload={
                            "entryTime": position.opened_at, "exitTime": current,
                            "entryPrice": position.entry_price, "exitPrice": price,
                            "quantity": position.quantity, "exitReason": exit_reason,
                            "grossPnl": result["grossPnl"], "buyFee": result["buy_fee"],
                            "sellFee": result["sell_fee"], "transactionTax": result["transaction_tax"],
                            "slippage": result["slippage"], "otherCost": result["other_cost"],
                            "totalCost": result["total"], "netPnl": result["netPnl"],
                            "marketRegime": regime,
                        },
                    )
                    changes += 1
            db.flush()
            open_count = int(db.scalar(select(func.count()).select_from(DayTradeV2ChallengerPosition).where(
                DayTradeV2ChallengerPosition.run_id == run.id,
                DayTradeV2ChallengerPosition.role == role,
                DayTradeV2ChallengerPosition.status == "OPEN",
            )) or 0)
            if open_count or regime in {REGIME_CRASH, REGIME_UNKNOWN} or local_time >= time(13, 20):
                continue
            params = _version_parameters(db, run.strategy_id, version)
            scored = []
            for source in live_candidates:
                symbol = str(source.get("symbol") or "")
                raw_bars = market_engine.minute_bars_for(symbol)
                if len(raw_bars) < 16:
                    continue
                bars = [MinuteBar(
                    timestamp=datetime.fromisoformat(str(row["timestamp"])), open=dec(row["open"]), high=dec(row["high"]),
                    low=dec(row["low"]), close=dec(row["close"]), volume=int(row["volume"]),
                ) for row in raw_bars]
                try:
                    shadow_signals = evaluate_strategies(
                        bars, strategy_parameters={run.strategy_id: params},
                        enabled_strategies={run.strategy_id},
                    )
                except Exception as exc:
                    run.error_count += 1
                    _record_event(
                        db, run=run, role=role, version=version, event_type="ERROR",
                        event_id=f"shadow-error:{run.id}:{role}:{symbol}:{current.isoformat()}",
                        occurred_at=current, symbol=symbol,
                        payload={"errorType": type(exc).__name__, "message": str(exc)[:500]},
                    )
                    continue
                for signal in shadow_signals:
                    if signal.strategy_id != run.strategy_id:
                        continue
                    sector = str((source.get("themes") or [""])[0])
                    rr = (signal.target_price - signal.entry_price) / (signal.entry_price - signal.stop_price)
                    candidate = ControllerCandidateInput(
                        key=f"shadow:{run.id}:{role}:{symbol}:{bars[-1].timestamp.isoformat()}",
                        symbol=symbol, stock_name=str(source.get("stockName") or ""), sector=sector,
                        strategy_id=run.strategy_id, strategy_version=version, signal_time=bars[-1].timestamp,
                        raw_score=signal.confidence, entry_price=signal.entry_price,
                        stop_price=signal.stop_price, target_price=signal.target_price, risk_reward=rr,
                        sector_strength=dec(source.get("industryScore") or 50),
                        liquidity_score=dec(source.get("liquidityScore") or 0),
                        vwap_deviation_pct=dec(source.get("vwapDeviationPercent") or 0), reasons=signal.reasons,
                    )
                    evaluated = score_candidate(candidate, regime=regime, local_time=local_time, config=config)
                    if not evaluated.allowed:
                        _record_event(
                            db, run=run, role=role, version=version, event_type="SIGNAL_REJECTED",
                            event_id=f"shadow-rejected:{candidate.key}", occurred_at=current,
                            signal_key=candidate.key, symbol=symbol,
                            payload={
                                "rawScore": candidate.raw_score, "finalScore": evaluated.final_score,
                                "blockedReasons": evaluated.blocked_reasons, "reasons": candidate.reasons,
                                "marketRegime": regime, "entryPrice": candidate.entry_price,
                                "stopPrice": candidate.stop_price, "targetPrice": candidate.target_price,
                                "riskReward": candidate.risk_reward,
                            },
                        )
                    elif not db.scalar(select(DayTradeV2ChallengerPosition).where(
                        DayTradeV2ChallengerPosition.run_id == run.id,
                        DayTradeV2ChallengerPosition.role == role,
                        DayTradeV2ChallengerPosition.signal_key == candidate.key,
                    )):
                        scored.append(evaluated)
            if scored:
                winner = max(scored, key=lambda row: (row.final_score, row.candidate.risk_reward))
                for ranked_out in scored:
                    if ranked_out.candidate.key != winner.candidate.key:
                        _record_event(
                            db, run=run, role=role, version=version, event_type="RANKED_OUT",
                            event_id=f"shadow-ranked-out:{ranked_out.candidate.key}", occurred_at=current,
                            signal_key=ranked_out.candidate.key, symbol=ranked_out.candidate.symbol,
                            payload={
                                "finalScore": ranked_out.final_score,
                                "selectedSignalKey": winner.candidate.key,
                                "reason": "同輪候選由更高分訊號取得模擬交易權",
                            },
                        )
                robot = db.scalar(select(DayTradeV2Robot).where(
                    DayTradeV2Robot.user_id == user_id, DayTradeV2Robot.strategy_id == run.strategy_id,
                ))
                capital = robot.allocation if robot else Decimal("300000")
                quantity = calculate_position_size(
                    price=winner.candidate.entry_price, stop_price=winner.candidate.stop_price,
                    risk_budget=config["maxRiskPerTrade"], capital_limit=capital,
                    lot_size=int(config["boardLotSize"]), allow_odd_lots=bool(config["allowOddLots"]),
                )
                if quantity:
                    position_id = str(uuid4())
                    db.add(DayTradeV2ChallengerPosition(
                        id=position_id, run_id=run.id, role=role, strategy_version=version,
                        signal_key=winner.candidate.key, symbol=winner.candidate.symbol, quantity=quantity,
                        entry_price=winner.candidate.entry_price, stop_price=winner.candidate.stop_price,
                        target_price=winner.candidate.target_price, opened_at=current,
                    ))
                    _record_event(
                        db, run=run, role=role, version=version, event_type="ENTRY",
                        event_id=f"shadow-entry:{position_id}", occurred_at=current,
                        signal_key=winner.candidate.key, symbol=winner.candidate.symbol,
                        payload={
                            "entryPrice": winner.candidate.entry_price,
                            "stopPrice": winner.candidate.stop_price,
                            "targetPrice": winner.candidate.target_price,
                            "quantity": quantity, "rawScore": winner.candidate.raw_score,
                            "finalScore": winner.final_score, "riskReward": winner.candidate.risk_reward,
                            "reasons": winner.candidate.reasons, "scoreDetails": winner.score_details,
                            "marketRegime": regime, "parameters": params,
                        },
                    )
                    changes += 1
                else:
                    _record_event(
                        db, run=run, role=role, version=version, event_type="RISK_REJECTED",
                        event_id=f"shadow-risk-rejected:{winner.candidate.key}", occurred_at=current,
                        signal_key=winner.candidate.key, symbol=winner.candidate.symbol,
                        payload={"reason": "資金或單筆風險限制導致可買股數為0"},
                    )
        db.flush()
        _update_run_readiness(db, run)
    return changes


def count_completed_challenger_day(db, user_id: str, trading_date) -> int:
    runs = list(db.scalars(select(DayTradeV2ChallengerRun).where(
        DayTradeV2ChallengerRun.user_id == user_id,
        DayTradeV2ChallengerRun.status == "RUNNING",
    )).all())
    for run in runs:
        if run.last_counted_date != trading_date:
            run.full_trading_days += 1
            run.last_counted_date = trading_date
        _update_run_readiness(db, run)
    return len(runs)
