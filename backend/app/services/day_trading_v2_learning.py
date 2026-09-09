"""Prospective daily research using quotes recorded after enrollment.

No broker, promotion, historical backfill or parameter search on held-out days.
Each experiment freezes candidates/config and uses 20/10/10 complete sessions.
"""
from datetime import UTC, datetime, time, timedelta
import json
import logging
from uuid import uuid4

from sqlalchemy import select

from ..database import SessionLocal
from ..day_trading_v2_models import DayTradeV2LearningRun, DayTradeV2LearningDay, DayTradeV2Setting
from .day_trading_v2 import BACKTEST_ENGINE_VERSION, MinuteBar, TAIPEI, dec, merged_config, performance, run_backtest
from .day_trading_v2_research import (
    PreparedSignalEvaluator, research_candidates, regime_metrics, choose_training_routes,
    validate_routes, _policy,
)

logger = logging.getLogger(__name__)
PHASE_LENGTHS = {"TRAINING": 20, "VALIDATION": 10, "OOS": 10}


def dumps(value):
    return json.dumps(value, default=str, ensure_ascii=False)


def new_state(config, now):
    return {"version": 1, "engineVersion": BACKTEST_ENGINE_VERSION,
            "startedAt": now.isoformat(), "cycle": 1, "phase": "TRAINING", "days": 0,
            "config": merged_config(config), "candidates": research_candidates(),
            "routes": {}, "aggregate": {}, "message": "等待下一個完整交易日；盤中收集，13:40 後評估。"}


def set_enabled(db, user_id, enabled, now):
    row = db.get(DayTradeV2LearningRun, user_id)
    if row is None:
        setting = db.get(DayTradeV2Setting, user_id)
        row = DayTradeV2LearningRun(user_id=user_id, enabled=enabled, updated_at=now,
                                   state_json=dumps(new_state(json.loads(setting.config_json) if setting else {}, now)))
        db.add(row)
    row.enabled = enabled
    # Re-enabling keeps the frozen experiment; an interrupted day cannot qualify.
    return row


def learning_dashboard(db, user_id):
    row = db.get(DayTradeV2LearningRun, user_id)
    if row is None:
        return {"enabled": False, "message": "尚未啟用前瞻模擬學習"}
    state = json.loads(row.state_json)
    days = list(db.scalars(select(DayTradeV2LearningDay).where(
        DayTradeV2LearningDay.user_id == user_id).order_by(DayTradeV2LearningDay.trading_date.desc()).limit(10)))
    return {"enabled": row.enabled, "phase": state["phase"], "days": state["days"],
            "targetDays": PHASE_LENGTHS[state["phase"]], "cycle": state["cycle"],
            "updatedAt": row.updated_at, "startedAt": state["startedAt"],
            "message": state["message"], "routes": state["routes"],
            "matrix": state.get("matrix", []), "lastEvaluation": state.get("lastEvaluation"),
            "automaticallyActivated": False,
            "recentDays": [{"date": d.trading_date, "status": d.status,
                            **{k: v for k, v in json.loads(d.result_json).items()
                               if k in {"reason", "minutes", "phase", "summary", "stressSummary"}}} for d in days]}


def capture_minute(data, *, now, symbols, market_engine, regime):
    """Record only the just-completed minute, never label old bars with today's regime."""
    stamp = now.astimezone(TAIPEI).replace(second=0, microsecond=0) - timedelta(minutes=1)
    key = stamp.isoformat()
    if key in data["regimes"] or not time(9) <= stamp.time() <= time(13, 29):
        return
    captured = {}
    for symbol in symbols:
        rows = market_engine.minute_bars_for(symbol, limit=2)
        bar = next((r for r in rows if datetime.fromisoformat(str(r["timestamp"])) == stamp), None)
        if bar and dec(bar["close"]) > 0 and int(bar["volume"]) >= 0:
            captured[symbol] = bar
    # Keep gaps explicit: sampled free quotes are not exchange tick OHLC.
    if not symbols or len(captured) < max(1, len(symbols) * .8) or regime == "UNKNOWN":
        return
    for symbol, bar in captured.items():
        data["bars"].setdefault(symbol, []).append(bar)
    data["regimes"][key] = regime


def quality_reason(data):
    stamps = sorted(data["regimes"])
    if len(stamps) < 240:
        return f"有效分鐘 {len(stamps)}/240，缺資料的日期不計入訓練。"
    if datetime.fromisoformat(stamps[0]).time() > time(9, 5) or datetime.fromisoformat(stamps[-1]).time() < time(13, 25):
        return "缺少開盤或收盤資料，不計入完整交易日。"
    return ""


def evaluate_day(state, data, runner=run_backtest):
    cfg = state["config"]
    datasets = {symbol: [MinuteBar(datetime.fromisoformat(r["timestamp"]), dec(r["open"]),
                    dec(r["high"]), dec(r["low"]), dec(r["close"]), int(r["volume"])) for r in rows]
                for symbol, rows in data["bars"].items()}
    # Incomplete individual stocks must not manufacture a close or a continuous path.
    datasets = {s: bars for s, bars in datasets.items() if len(bars) >= 240
                and bars[0].timestamp.time() <= time(9, 5) and bars[-1].timestamp.time() >= time(13, 25)
                and all((b.timestamp-a.timestamp).total_seconds() <= 120 for a, b in zip(bars, bars[1:]))}
    if not datasets:
        raise ValueError("沒有具備完整開收盤資料的股票")
    regimes = {datetime.fromisoformat(k): v for k, v in data["regimes"].items()}
    evaluator = PreparedSignalEvaluator(datasets)
    def run(**kwargs):
        execution = kwargs.pop("config", cfg)
        return runner(datasets, config=execution, market_regime_by_time=regimes,
                      signal_evaluator=evaluator, sector_by_symbol=data.get("sectors", {}), **kwargs)
    phase = state["phase"]
    result = {"phase": phase, "cycle": state["cycle"], "minutes": len(regimes), "trials": []}
    if phase == "TRAINING":
        for candidate in state["candidates"]:
            output = run(strategy_id=candidate["strategyId"], portfolio=False,
                         strategy_parameters={candidate["strategyId"]: candidate["parameters"]})
            result["trials"].append({"candidate": candidate, "trades": output["trades"], "summary": output["summary"]})
    else:
        output = run(strategy_policy=_policy(state["routes"]), portfolio=True)
        stress = run(strategy_policy=_policy(state["routes"]), portfolio=True,
                     config={**cfg, "slippageBps": str(dec(cfg["slippageBps"]) * 2)})
        result.update(trades=output["trades"], summary=output["summary"],
                      stressTrades=stress["trades"], stressSummary=stress["summary"])
    advance(state, result)
    return result


def advance(state, result):
    """Only the training partition selects; later partitions can only reject."""
    aggregate = state["aggregate"]
    for trial in result["trials"]:
        aggregate.setdefault(trial["candidate"]["id"], []).extend(trial["trades"])
    for key in ("trades", "stressTrades"):
        aggregate.setdefault(key, []).extend(result.get(key, []))
    state["days"] += 1
    phase = state["phase"]
    if phase == "TRAINING":
        trials = [{"candidate": c, "regimes": regime_metrics(aggregate.get(c["id"], []))} for c in state["candidates"]]
        state["matrix"] = trials
    state["message"] = f"已完成 {state['days']}/{PHASE_LENGTHS[phase]} 個完整交易日；未成交不代表獲利。"
    if state["days"] < PHASE_LENGTHS[phase]:
        return
    if phase == "TRAINING":
        state["routes"] = choose_training_routes(trials)
        state["phase"] = "VALIDATION"
    elif phase == "VALIDATION":
        approved = validate_routes(state["routes"], regime_metrics(aggregate["trades"]))
        state["routes"] = validate_routes(approved, regime_metrics(aggregate["stressTrades"]))
        state["phase"] = "OOS"
    else:
        approved = validate_routes(state["routes"], regime_metrics(aggregate["trades"]))
        approved = validate_routes(approved, regime_metrics(aggregate["stressTrades"]))
        state["lastEvaluation"] = {"cycle": state["cycle"], "qualifiedRoutes": approved,
            "summary": performance(aggregate["trades"]), "stressSummary": performance(aggregate["stressTrades"]),
            "automaticallyActivated": False}
        state["cycle"] += 1
        state["phase"] = "TRAINING"
        state["routes"] = {}
    state["days"] = 0
    state["aggregate"] = {}
    state["message"] = "已完成階段評估，參數保持固定並等待後續交易日。沒有通過門檻的盤勢保持空手。"


def process_learning_cycle(now=None):
    from .day_trading import day_trading_engine
    from .day_trading_v2_automation import _configured_holidays
    from .day_trading_v2_schedule import is_trading_day
    from ..routers.day_trading_v2 import _quote_observation, _regime_snapshot
    current = now or datetime.now(UTC)
    local = current.astimezone(TAIPEI)
    with SessionLocal() as db:
        ids = list(db.scalars(select(DayTradeV2LearningRun.user_id).where(DayTradeV2LearningRun.enabled.is_(True))))
    for user_id in ids:
        try:
            with SessionLocal() as db:
                row = db.scalar(select(DayTradeV2LearningRun).where(DayTradeV2LearningRun.user_id == user_id)
                                .with_for_update(skip_locked=True))
                if row is None or not row.enabled:
                    continue
                state = json.loads(row.state_json)
                # Engine changes require a fresh experiment; do not mix execution models.
                if state["engineVersion"] != BACKTEST_ENGINE_VERSION:
                    state["message"] = "執行引擎版本已變更，已暫停；需建立新版實驗。"
                    row.enabled = False
                    row.state_json = dumps(state)
                    db.commit()
                    continue
                if not is_trading_day(local.date(), _configured_holidays(db)) or local.time() < time(9):
                    continue
                pending = list(db.scalars(select(DayTradeV2LearningDay).where(
                    DayTradeV2LearningDay.user_id == user_id, DayTradeV2LearningDay.status == "COLLECTING")
                    .order_by(DayTradeV2LearningDay.trading_date)))
                today = next((d for d in pending if d.trading_date == local.date()), None)
                exists = db.scalar(select(DayTradeV2LearningDay.id).where(
                    DayTradeV2LearningDay.user_id == user_id, DayTradeV2LearningDay.trading_date == local.date()))
                if today is None and not exists and local.time() < time(13, 40):
                    symbols = list(day_trading_engine.stock_universe_symbols)
                    today = DayTradeV2LearningDay(id=str(uuid4()), user_id=user_id, trading_date=local.date(),
                        status="COLLECTING", data_json=dumps({"symbols": symbols, "bars": {}, "regimes": {}, "sectors": {}}), result_json="{}")
                    db.add(today)
                    pending.append(today)
                if today and local.time() < time(13, 31):
                    data = json.loads(today.data_json)
                    if not data["symbols"]:
                        data["symbols"] = list(day_trading_engine.stock_universe_symbols)
                    observation = _quote_observation(current, state["config"])
                    for candidate in observation["candidates"]:
                        data["sectors"].setdefault(str(candidate.get("symbol") or ""), str((candidate.get("themes") or [""])[0]))
                    snap = _regime_snapshot(db, user_id, "LEARNING", current, observation["regime"], observation["candidates"], state["config"])
                    regime = snap.effective_regime if observation["fresh"] and not snap.data_blocked else "UNKNOWN"
                    capture_minute(data, now=current, symbols=data["symbols"], market_engine=day_trading_engine, regime=regime)
                    today.data_json = dumps(data)
                    state["message"] = f"盤中資料收集中：{len(data['regimes'])} 個有效分鐘；13:40 後計算績效。"
                for day in pending:
                    if day.trading_date == local.date() and local.time() < time(13, 40):
                        continue
                    data = json.loads(day.data_json)
                    reason = quality_reason(data)
                    if reason:
                        day.status = "INCOMPLETE"
                        day.result_json = dumps({"reason": reason, "minutes": len(data["regimes"])})
                        state["message"] = reason
                    else:
                        try:
                            day.result_json = dumps(evaluate_day(state, data))
                            day.status = "COMPLETED"
                        except ValueError as exc:
                            day.status = "INCOMPLETE"
                            day.result_json = dumps({"reason": str(exc), "minutes": len(data["regimes"])})
                            state["message"] = str(exc)
                row.updated_at = current
                row.state_json = dumps(state)
                db.commit()
        except Exception as exc:
            logger.exception("forward learning cycle failed")
            with SessionLocal() as db:
                row = db.get(DayTradeV2LearningRun, user_id)
                if row:
                    state = json.loads(row.state_json)
                    state["message"] = f"評估暫停並等待重試：{type(exc).__name__}: {str(exc)[:200]}"
                    row.state_json = dumps(state)
                    db.commit()
