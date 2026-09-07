from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Iterable, Mapping

from .day_trading_v2 import STRATEGIES, dec, money, performance
from .day_trading_v2_controller import (
    REGIME_CRASH, REGIME_LABELS, REGIME_MILD, REGIME_RANGE, REGIME_STRONG, REGIME_UNKNOWN, REGIME_WEAK,
)


REGIME_ORDER = (REGIME_STRONG, REGIME_MILD, REGIME_RANGE, REGIME_WEAK, REGIME_CRASH)
VALID_REGIMES = set(REGIME_ORDER)


def _max_drawdown(rows: list[Mapping[str, object]]) -> Decimal:
    equity = peak = drawdown = Decimal("0")
    for row in sorted(rows, key=lambda item: str(item.get("exitTime", item.get("exit_time", "")))):
        equity += dec(row.get("netPnl", row.get("net_pnl", 0)))
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return money(drawdown)


def _fit_sort_key(row: Mapping[str, object]) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    profit_factor = (
        Decimal("999999")
        if row.get("profitFactor") is None and int(row.get("winCount", 0)) > 0 and int(row.get("lossCount", 0)) == 0
        else dec(row.get("profitFactor") or 0)
    )
    return (
        dec(row["expectancy"]), profit_factor,
        -dec(row["maxDrawdown"]), dec(row["netPnl"]),
    )


def _summary_reference(row: Mapping[str, object] | None) -> dict[str, object] | None:
    return {key: value for key, value in row.items() if key != "trades"} if row else None


def aggregate_regime_performance(
    records: Iterable[Mapping[str, object]], *, minimum_sample: int = 20,
) -> dict[str, object]:
    source_rows = list(records)
    grouped: dict[tuple[str, str], list[Mapping[str, object]]] = defaultdict(list)
    unknown_count = 0
    strategy_ids = [strategy_id for strategy_id, _, _ in STRATEGIES]
    strategy_names = {strategy_id: name for strategy_id, name, _ in STRATEGIES}
    for row in source_rows:
        strategy_id = str(row.get("strategyId", row.get("strategy_id", "")))
        regime = str(row.get("marketRegime", row.get("entry_market_regime", REGIME_UNKNOWN)))
        if strategy_id not in strategy_names or regime not in VALID_REGIMES:
            unknown_count += 1
            continue
        grouped[(strategy_id, regime)].append(row)

    result_rows: list[dict[str, object]] = []
    for strategy_id in strategy_ids:
        for regime in REGIME_ORDER:
            trades = grouped[(strategy_id, regime)]
            metrics = performance(trades)
            trade_count = int(metrics["tradeCount"])
            expectancy = money(dec(metrics["netPnl"]) / trade_count) if trade_count else Decimal("0")
            sample_sufficient = trade_count >= minimum_sample
            profitable_without_losses = int(metrics["winCount"]) > 0 and int(metrics["lossCount"]) == 0
            suitable = bool(
                sample_sufficient and expectancy > 0
                and (profitable_without_losses or dec(metrics.get("profitFactor") or 0) >= 1)
                and dec(metrics["netPnl"]) > 0
            )
            result_rows.append({
                "strategyId": strategy_id, "strategyName": strategy_names[strategy_id],
                "marketRegime": regime, "marketRegimeLabel": REGIME_LABELS[regime],
                **metrics, "expectancy": str(expectancy), "maxDrawdown": str(_max_drawdown(trades)),
                "minimumSample": minimum_sample, "sampleSufficient": sample_sufficient,
                "suitability": "SUITABLE" if suitable else "INSUFFICIENT" if not sample_sufficient else "CAUTION",
                "fitRank": None,
                "trades": [{
                    "id": row.get("id", ""), "symbol": row.get("symbol", ""),
                    "entryTime": row.get("entryTime", row.get("entry_time")),
                    "exitTime": row.get("exitTime", row.get("exit_time")),
                    "grossPnl": str(row.get("grossPnl", row.get("gross_pnl", 0))),
                    "cost": str(row.get("cost", 0)), "netPnl": str(row.get("netPnl", row.get("net_pnl", 0))),
                } for row in trades],
            })

    best_by_regime: list[dict[str, object]] = []
    for regime in REGIME_ORDER:
        eligible = [row for row in result_rows if row["marketRegime"] == regime and row["suitability"] == "SUITABLE"]
        eligible.sort(key=_fit_sort_key, reverse=True)
        for rank, row in enumerate(eligible, start=1):
            row["fitRank"] = rank
        best_by_regime.append({
            "marketRegime": regime, "marketRegimeLabel": REGIME_LABELS[regime],
            "best": _summary_reference(eligible[0] if eligible else None),
        })

    best_by_strategy: list[dict[str, object]] = []
    for strategy_id in strategy_ids:
        eligible = [row for row in result_rows if row["strategyId"] == strategy_id and row["suitability"] == "SUITABLE"]
        eligible.sort(key=_fit_sort_key, reverse=True)
        best_by_strategy.append({
            "strategyId": strategy_id, "strategyName": strategy_names[strategy_id],
            "best": _summary_reference(eligible[0] if eligible else None),
        })

    populated = [row for row in result_rows if int(row["tradeCount"]) > 0]
    profitable = [row for row in populated if dec(row["netPnl"]) > 0]
    losing = [row for row in populated if dec(row["netPnl"]) < 0]
    classified_count = len(source_rows) - unknown_count
    coverage = Decimal(classified_count) / len(source_rows) * 100 if source_rows else Decimal("0")
    return {
        "minimumSample": minimum_sample,
        "coverage": {
            "totalTrades": len(source_rows), "classifiedTrades": classified_count,
            "unknownTrades": unknown_count, "classifiedPct": str(coverage.quantize(Decimal("0.1"))),
        },
        "regimes": [{"id": regime, "label": REGIME_LABELS[regime]} for regime in REGIME_ORDER],
        "rows": result_rows, "bestByRegime": best_by_regime, "bestByStrategy": best_by_strategy,
        "mostProfitable": _summary_reference(max(profitable, key=lambda row: dec(row["netPnl"]), default=None)),
        "largestLoss": _summary_reference(min(losing, key=lambda row: dec(row["netPnl"]), default=None)),
    }
