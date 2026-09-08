"""Bounded, chronological regime research. Never activates a trading strategy."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from itertools import groupby
from typing import Callable, Mapping

from .day_trading_v2 import (
    BACKTEST_ENGINE_VERSION, DEFAULT_STRATEGY_PARAMETERS, STRATEGIES, TAIPEI,
    dec, evaluate_strategies, merged_config, performance, run_backtest,
)
from .day_trading_v2_optimization import walk_forward_splits
from .day_trading_v2_regime_performance import REGIME_ORDER, _max_drawdown


class PreparedSignalEvaluator:
    """Cache only causal indicator prefixes, preserving Decimal summation order."""

    def __init__(self, datasets):
        self.days = {}
        for bars in datasets.values():
            for _, grouped in groupby(bars, key=lambda b: b.timestamp.astimezone(TAIPEI).date()):
                rows = list(grouped)
                volume, weighted = 0, Decimal(0)
                opening, values = (), []
                previous = rows[0].close
                for bar in rows:
                    volume += bar.volume
                    weighted += ((bar.high + bar.low + bar.close) / 3) * bar.volume
                    current = weighted / volume if volume else bar.close
                    local = bar.timestamp.astimezone(TAIPEI)
                    if local.hour == 9 and local.minute < 15:
                        opening = (*opening, bar)
                    values.append((id(bar), (current, previous, opening)))
                    previous = current
                self.days[id(rows[0])] = values

    def __call__(self, bars, **kwargs):
        if len(bars) < 16:
            return []
        values = self.days.get(id(bars[0]))
        if values is None or len(values) < len(bars) or values[len(bars)-1][0] != id(bars[-1]):
            return evaluate_strategies(bars, **kwargs)
        return evaluate_strategies(bars, _indicators=values[len(bars)-1][1], **kwargs)


def research_candidates():
    """Freeze the search before inspecting validation/test results; 15 trials."""
    result = []
    for strategy, _, _ in STRATEGIES:
        base = dict(DEFAULT_STRATEGY_PARAMETERS[strategy])
        variants = {
            "BASELINE": base,
            "NET_RR_1": {**base, "minimumNetRiskReward": "1"},
            "SELECTIVE": {
                **base, "minimumNetRiskReward": "1",
                "volumeMultiplier": str(dec(base["volumeMultiplier"]) * Decimal("1.2")),
                "targetRiskReward": str(dec(base["targetRiskReward"]) * Decimal("1.2")),
            },
        }
        result.extend({"id": f"{strategy}:{label}", "strategyId": strategy,
                       "parameters": params} for label, params in variants.items())
    return result


def regime_metrics(trades, *, capital=Decimal(3000000)):
    groups = defaultdict(list)
    for trade in trades:
        groups[trade.get("marketRegime", "UNKNOWN")].append(trade)
    result = {}
    for regime in REGIME_ORDER:
        rows = groups[regime]
        metrics = performance(rows, capital)
        result[regime] = {**metrics, "maxDrawdown": str(_max_drawdown(rows)),
                          "activeDays": len({str(r["entryTime"])[:10] for r in rows})}
    return result


def eligible(metrics, *, minimum_trades: int, minimum_days: int):
    factor = metrics.get("profitFactor")
    return (int(metrics["tradeCount"]) >= minimum_trades
            and int(metrics["activeDays"]) >= minimum_days
            and dec(metrics["netPnl"]) > 0
            and (factor is None and int(metrics["lossCount"]) == 0
                 or factor is not None and dec(factor) >= Decimal("1.2")))


def choose_training_routes(trials):
    routes = {}
    for regime in REGIME_ORDER:
        candidates = [t for t in trials if eligible(t["regimes"][regime], minimum_trades=30, minimum_days=10)]
        if candidates:
            winner = max(candidates, key=lambda t: (
                dec(t["regimes"][regime]["netPnl"]) / max(dec(t["regimes"][regime]["maxDrawdown"]), Decimal(1)),
                t["candidate"]["id"],
            ))
            routes[regime] = winner["candidate"]
    return routes


def validate_routes(routes, metrics):
    # A failed route becomes CASH; never choose a runner-up using validation P&L.
    return {regime: candidate for regime, candidate in routes.items()
            if eligible(metrics[regime], minimum_trades=10, minimum_days=5)}


def _policy(routes):
    return {regime: {row["strategyId"]: row["parameters"]} for regime, row in routes.items()}


def run_regime_research(datasets, sectors, regimes, *, config=None,
                        progress: Callable[[dict], None] | None = None, runner=run_backtest):
    """Train on past data, validate once, then freeze CASH/strategy for each OOS fold.

    Folds are independent 3m portfolios (or the specified capital); returns are
    not presented as one compounded account. No deployment/promotion is done.
    """
    cfg = merged_config(config)
    days = sorted({b.timestamp.astimezone(TAIPEI).date() for bars in datasets.values() for b in bars})
    folds = walk_forward_splits(days)
    if not folds:
        return {"status": "DATA_INSUFFICIENT", "requiredDays": 160, "availableDays": len(days)}
    candidates = research_candidates()
    evaluator = PreparedSignalEvaluator(datasets)
    output = {"status": "RESEARCH_ONLY", "engineVersion": BACKTEST_ENGINE_VERSION,
              "researchVersion": "1.0.0",
              "candidateCount": len(candidates), "candidates": candidates, "folds": [],
              "availableDays": len(days), "minimumTrainingTrades": 30,
              "minimumValidationTrades": 10, "automaticallyActivated": False,
              "capitalPerFold": str(cfg["initialCapital"]),
              "regimeMinutes": dict(Counter(regimes.values())),
              "unusedTailDays": [str(day) for day in days if day > folds[-1]["oos"][1]]}

    def run(bounds, *, execution_config=None, **kwargs):
        start, end = bounds
        data = {s: [b for b in bars if start <= b.timestamp.astimezone(TAIPEI).date() <= end]
                for s, bars in datasets.items()}
        return runner(data, config=execution_config or cfg, market_regime_by_time=regimes, sector_by_symbol=sectors,
                      signal_evaluator=evaluator, **kwargs)

    for index, fold in enumerate(folds):
        trials = []
        for candidate in candidates:
            result = run(fold["train"], strategy_id=candidate["strategyId"], portfolio=False,
                         strategy_parameters={candidate["strategyId"]: candidate["parameters"]})
            trials.append({"candidate": candidate, "summary": result["summary"],
                           "regimes": regime_metrics(result["trades"], capital=dec(cfg["initialCapital"]))})
            if progress:
                progress({"stage": "TRAINING", "fold": index, "trial": len(trials), "total": len(candidates)})
        selected = choose_training_routes(trials)
        validation = run(fold["validation"], strategy_policy=_policy(selected), portfolio=True)
        validation_metrics = regime_metrics(validation["trades"], capital=dec(cfg["initialCapital"]))
        frozen = validate_routes(selected, validation_metrics)
        oos = run(fold["oos"], strategy_policy=_policy(frozen), portfolio=True)
        stressed = run(fold["oos"], strategy_policy=_policy(frozen), portfolio=True,
                       execution_config={**cfg, "slippageBps": str(dec(cfg["slippageBps"]) * 2)})
        baseline = run(fold["oos"], portfolio=True)
        output["folds"].append({
            "ranges": fold, "trainingTrials": trials, "trainingRoutes": selected,
            "validationSummary": validation["summary"], "validationRegimes": validation_metrics,
            "frozenRoutes": frozen, "outOfSample": oos, "baselineOutOfSample": baseline,
            "stressedOutOfSample": stressed,
            "stressSlippageBps": str(dec(cfg["slippageBps"]) * 2),
            "outOfSampleRegimes": regime_metrics(oos["trades"], capital=dec(cfg["initialCapital"])),
            "cashRegimes": [r for r in REGIME_ORDER if r not in frozen],
        })
        if progress:
            progress({"stage": "FOLD_COMPLETE", "fold": index, "result": output})
    return output
