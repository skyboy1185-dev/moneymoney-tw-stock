"""Core rules for the unified long-only intraday system.

The module is deliberately independent from HTTP and broker SDKs so the exact
same money, risk and strategy rules are used by backtest, paper and live modes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, time
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Iterable, Mapping, Protocol, Sequence
from zoneinfo import ZoneInfo


TAIPEI = ZoneInfo("Asia/Taipei")
ZERO = Decimal("0")
CENT = Decimal("0.01")
BACKTEST_ENGINE_VERSION = "3.0.1"

STRATEGIES = (
    ("OPENING_RANGE_BREAKOUT", "開盤15分鐘區間突破", Decimal("750000")),
    ("VWAP_TREND_PULLBACK", "VWAP多方趨勢回踩", Decimal("750000")),
    ("VOLUME_HIGH_BREAKOUT", "爆量突破前高", Decimal("600000")),
    ("FALSE_BREAKDOWN_REVERSAL", "支撐假跌破反轉", Decimal("300000")),
    ("AFTERNOON_STRENGTH_BREAKOUT", "尾盤強勢突破", Decimal("600000")),
)

DEFAULT_CONFIG: dict[str, object] = {
    "initialCapital": "3000000",
    "maxRiskPerTrade": "6000",
    "dailyReduceLoss": "12000",
    "dailyStopLoss": "24000",
    "monthlyMaxDrawdown": "150000",
    "maxRobotCapitalPct": "35",
    "maxOpenPositions": 3,
    "maxSectorPositions": 2,
    "maxConsecutiveLosses": 3,
    "latestEntryTime": "13:20:00",
    "forcedCloseTime": "13:25:00",
    "minimumConfidence": "80",
    "minimumRiskReward": "1.8",
    "minimumVolume": 100000,
    "minimumTurnover": "20000000",
    "maximumSpreadPct": "0.5",
    "maximumVwapDeviationPct": "1.5",
    "boardLotSize": 1000,
    "allowOddLots": False,
    "commissionRate": "0.001425",
    "commissionDiscount": "0.2",
    "minimumCommission": "20",
    "dayTradeTaxRate": "0.0015",
    "slippageBps": "5",
    "maximumFillChasePct": "1.0",
    "otherCost": "0",
    "forceCloseEnabled": True,
    "autoStart": True,
    "heartbeatSeconds": 10,
    "heartbeatTimeoutSeconds": 45,
    "quoteTimeoutSeconds": 15,
    "scanIntervalSeconds": 5,
    "generalScanThreshold": "0",
    "watchThreshold": "60",
    "nearEntryThreshold": "70",
    "riskGateThreshold": "80",
    "resetTime": "08:30:00",
    "universeLoadTime": "08:35:00",
    "historyLoadTime": "08:40:00",
    "healthCheckTime": "08:45:00",
    "candidatePoolTime": "08:50:00",
    "readyNotificationTime": "08:55:00",
    "marketOpenTime": "09:00:00",
    "openingRangeReadyTime": "09:15:00",
    "summary1000Time": "10:00:00",
    "summary1100Time": "11:00:00",
    "summary1200Time": "12:00:00",
    "marketCloseTime": "13:30:00",
    "brokerSyncTime": "13:35:00",
    "closeReportTime": "13:40:00",
    "emailReady": True,
    "emailOpeningRange": True,
    "emailHourlySummary": False,
    "emailCloseReport": True,
    "controllerMinimumScore": "80",
    "controllerMinimumRiskReward": "2",
    "controllerMinLiquidityScore": "70",
    "regimeUpdateMinutes": 5,
    "regimeSwitchCycles": 2,
    "regimeRecoveryCycles": 3,
    "regimeMinCoveragePct": "80",
    "regimeCrash1mPct": "-0.5",
    "regimeCrash5mPct": "-1.0",
    "regimeCrashBreadthPct": "20",
    "regimeCrashSpreadRatio": "2",
    "regimeStrongVwapDeviationPct": "0.15",
    "regimeStrongTrend5mPct": "0.30",
    "regimeStrongBreadthPct": "60",
    "regimeStrongRelativeVolume": "1.05",
    "regimeStrongSectorCount": 3,
    "regimeWeakVwapDeviationPct": "-0.15",
    "regimeWeakTrend5mPct": "-0.25",
    "regimeWeakBreadthPct": "40",
    "regimeMildVwapDeviationPct": "-0.05",
    "regimeMildTrend5mPct": "0",
    "regimeMildBreadthPct": "52",
    "regimeMildRelativeVolume": "0.9",
    "openingStrategyStart": "09:15:00",
    "openingStrategyEnd": "11:00:00",
    "vwapStrategyStart": "09:05:00",
    "vwapStrategyEnd": "13:20:00",
    "volumeStrategyStart": "09:05:00",
    "volumeStrategyEnd": "13:20:00",
    "reversalStrategyStart": "09:15:00",
    "reversalStrategyEnd": "12:30:00",
    "afternoonStrategyStart": "12:30:00",
    "afternoonStrategyEnd": "13:20:00",
    "healthDiagnosisTime": "13:45:00",
    "weeklyHealthCheckTime": "14:00:00",
    "healthMinTrades": 20,
    "healthMinRegimeTrades": 10,
    "healthMinProfitFactor": "1",
    "healthConsecutiveLossLimit": 5,
    "health20DayDrawdownLimit": "60000",
    "healthSlippageMultiple": "2",
    "healthPauseAfterAlertDays": 2,
    "healthAlertRiskMultiplier": "0.5",
    "optimizationMinOosTrades": 100,
    "optimizationMinProfitFactor": "1.2",
    "optimizationMaxTopTwoProfitSharePct": "35",
    "optimizationMaxSymbolProfitSharePct": "25",
    "optimizationMaxSectorProfitSharePct": "40",
    "optimizationMinNonNegativeMonthPct": "60",
    "optimizationMaxParticipationPct": "5",
    "versionRollbackConsecutiveLosses": 3,
    "versionRollbackDrawdown": "24000",
    "versionRollbackSignalLowPct": "50",
    "versionRollbackSignalHighPct": "200",
}


DEFAULT_STRATEGY_PARAMETERS: dict[str, dict[str, object]] = {
    "OPENING_RANGE_BREAKOUT": {"volumeMultiplier": "1.2", "baseScore": 68, "targetRiskReward": "2"},
    "VWAP_TREND_PULLBACK": {"pullbackRangePct": "0.3", "volumeMultiplier": "0.9", "baseScore": 70, "targetRiskReward": "2"},
    "VOLUME_HIGH_BREAKOUT": {"lookbackBars": 30, "volumeMultiplier": "1.8", "baseScore": 72, "targetRiskReward": "2"},
    "FALSE_BREAKDOWN_REVERSAL": {"lookbackBars": 20, "confirmationBars": 4, "volumeMultiplier": "1.0", "baseScore": 67, "targetRiskReward": "2"},
    "AFTERNOON_STRENGTH_BREAKOUT": {"lookbackBars": 30, "volumeMultiplier": "1.1", "baseScore": 69, "targetRiskReward": "2"},
}


def signal_level(score: object, config: Mapping[str, object] | None = None) -> str:
    cfg = merged_config(config)
    value = dec(score)
    if value >= dec(cfg["riskGateThreshold"]):
        return "RISK_GATE"
    if value >= dec(cfg["nearEntryThreshold"]):
        return "NEAR_ENTRY"
    if value >= dec(cfg["watchThreshold"]):
        return "WATCH"
    return "GENERAL"


def dec(value: object) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value or 0))


def money(value: object) -> Decimal:
    return dec(value).quantize(CENT, rounding=ROUND_HALF_UP)


def merged_config(custom: Mapping[str, object] | None = None) -> dict[str, object]:
    result = dict(DEFAULT_CONFIG)
    if custom:
        result.update({key: value for key, value in custom.items() if key in result})
    return result


def run_backtest(
    datasets: Mapping[str, Sequence[MinuteBar]], *, strategy_id: str = "ALL",
    config: Mapping[str, object] | None = None, portfolio: bool = True,
    strategy_parameters: Mapping[str, Mapping[str, object]] | None = None,
    market_regime_by_time: Mapping[datetime, str] | None = None,
    sector_by_symbol: Mapping[str, str] | None = None,
    controller_filter: bool | None = None,
) -> dict[str, object]:
    """Run a causal intraday backtest using the same controller and risk gates.

    Indicators only see completed bars from one Taipei trading day. Orders use
    the next bar, capital stays reserved while positions are open, and every
    accepted trade must have a same-day force-close bar.
    """
    from .day_trading_v2_controller import (
        ControllerCandidateInput, REGIME_MILD, REGIME_UNKNOWN,
        rank_candidates, risk_multiplier_for_regime, score_candidate,
    )

    cfg = merged_config(config)
    initial = dec(cfg["initialCapital"])
    enabled = {item[0] for item in STRATEGIES} if strategy_id == "ALL" else {strategy_id}
    regime_map = market_regime_by_time or {}
    has_verified_regimes = market_regime_by_time is not None
    apply_controller = True if controller_filter is None else controller_filter
    force_close = time.fromisoformat(str(cfg["forcedCloseTime"]))
    latest_entry = time.fromisoformat(str(cfg["latestEntryTime"]))
    allocation_by_strategy = {sid: amount for sid, _name, amount in STRATEGIES}
    skip_counts: dict[str, int] = {}

    def skip(reason: str) -> None:
        skip_counts[reason] = skip_counts.get(reason, 0) + 1

    def taipei_date(value: datetime):
        return value.astimezone(TAIPEI).date() if value.tzinfo else value.date()

    def taipei_time(value: datetime):
        return value.astimezone(TAIPEI).time() if value.tzinfo else value.time()

    candidates: list[dict[str, object]] = []
    trading_days: set[object] = set()
    for symbol, raw_bars in datasets.items():
        days: dict[object, list[MinuteBar]] = {}
        for bar in sorted(raw_bars, key=lambda item: item.timestamp):
            days.setdefault(taipei_date(bar.timestamp), []).append(bar)
        previous_bars: list[MinuteBar] | None = None
        for day, bars in sorted(days.items(), key=lambda item: item[0]):
            trading_days.add(day)
            previous_high = max((bar.high for bar in previous_bars), default=None) if previous_bars else None
            previous_low = min((bar.low for bar in previous_bars), default=None) if previous_bars else None
            for index in range(15, len(bars) - 1):
                signal_time = bars[index].timestamp
                fill_bar = bars[index + 1]
                evaluation_filter = None if strategy_id == "ALL" else {strategy_id}
                for signal in evaluate_strategies(
                    bars[:index + 1], previous_high=previous_high, previous_low=previous_low,
                    strategy_parameters=strategy_parameters, enabled_strategies=evaluation_filter,
                ):
                    if signal.strategy_id in enabled:
                        candidates.append({
                            "signalTime": signal_time, "symbol": symbol, "signal": signal,
                            "fillBar": fill_bar, "dayBars": bars, "fillIndex": index + 1,
                        })
            previous_bars = bars

    scored_rows: list[tuple[dict[str, object], object]] = []
    for row in candidates:
        signal_time = row["signalTime"]
        symbol = str(row["symbol"])
        signal = row["signal"]
        fill_bar = row["fillBar"]
        day_bars = row["dayBars"]
        fill_index = int(row["fillIndex"])
        assert isinstance(signal_time, datetime) and isinstance(signal, StrategySignal)
        assert isinstance(fill_bar, MinuteBar) and isinstance(day_bars, Sequence)
        if signal.confidence < dec(cfg["minimumConfidence"]):
            skip("CONFIDENCE_BELOW_MINIMUM")
            continue
        if taipei_time(fill_bar.timestamp) >= latest_entry:
            skip("AFTER_LATEST_ENTRY_TIME")
            continue
        entry = fill_bar.open * (Decimal("1") + dec(cfg["slippageBps"]) / Decimal("10000"))
        chase_pct = (entry - signal.entry_price) / signal.entry_price * 100 if signal.entry_price else Decimal("999")
        if chase_pct > dec(cfg["maximumFillChasePct"]):
            skip("NEXT_BAR_CHASE_TOO_LARGE")
            continue
        if entry <= signal.stop_price or signal.target_price <= entry:
            skip("INVALID_LEVELS_AFTER_FILL")
            continue
        actual_rr = (signal.target_price - entry) / (entry - signal.stop_price)
        required_rr = max(
            dec(cfg["minimumRiskReward"]),
            dec(cfg["controllerMinimumRiskReward"]) if apply_controller else ZERO,
        )
        if actual_rr < required_rr:
            skip("RISK_REWARD_BELOW_MINIMUM_AFTER_FILL")
            continue
        future = [bar for bar in day_bars[fill_index:] if taipei_time(bar.timestamp) <= force_close]
        close_candidates = [bar for bar in future if taipei_time(bar.timestamp) >= force_close]
        if not future or not close_candidates:
            skip("FORCED_CLOSE_MINUTE_MISSING")
            continue
        row["entry"] = entry
        row["actualRiskReward"] = actual_rr
        row["future"] = future
        row["forceCloseBar"] = close_candidates[-1]
        regime = regime_map.get(signal_time, REGIME_MILD if not has_verified_regimes else REGIME_UNKNOWN)
        risk_reward = ((signal.target_price - signal.entry_price) /
                       (signal.entry_price - signal.stop_price))
        controller_input = ControllerCandidateInput(
            key=f"backtest:{signal_time.isoformat()}:{symbol}:{signal.strategy_id}",
            symbol=symbol, stock_name=symbol, sector=(sector_by_symbol or {}).get(symbol, "UNKNOWN"),
            strategy_id=signal.strategy_id, strategy_version=f"BACKTEST-{BACKTEST_ENGINE_VERSION}",
            signal_time=signal_time, raw_score=signal.confidence,
            entry_price=signal.entry_price, stop_price=signal.stop_price,
            target_price=signal.target_price, risk_reward=risk_reward,
            sector_strength=Decimal("50"), liquidity_score=Decimal("100"),
        )
        scored = score_candidate(
            controller_input, regime=regime, local_time=taipei_time(signal_time), config=cfg,
        ) if apply_controller else None
        if scored is not None and not scored.allowed:
            for reason in scored.blocked_reasons:
                skip(f"CONTROLLER:{reason}")
            continue
        scored_rows.append((row, scored))

    selected: list[tuple[dict[str, object], object]] = []
    if portfolio and apply_controller:
        grouped: dict[datetime, list[tuple[dict[str, object], object]]] = {}
        for row, scored in scored_rows:
            grouped.setdefault(row["signalTime"], []).append((row, scored))
        for rows in grouped.values():
            ranked = rank_candidates(item[1] for item in rows)
            winner = next((item for item in ranked if item.allowed), None)
            if winner is None:
                continue
            for row, scored in rows:
                if scored.candidate.key == winner.candidate.key:
                    selected.append((row, scored))
                else:
                    skip("LOWER_RANKED_CANDIDATE")
    else:
        selected = scored_rows
    selected.sort(key=lambda item: (
        item[0]["signalTime"], -item[0]["signal"].confidence, item[0]["symbol"],
    ))

    trades: list[dict[str, object]] = []
    active: list[dict[str, object]] = []
    realized_total = ZERO
    realized_by_day: dict[object, Decimal] = {}
    traded_keys: set[tuple[str, object]] = set()
    for row, scored in selected:
        signal_time = row["signalTime"]
        symbol = str(row["symbol"])
        signal = row["signal"]
        fill_bar = row["fillBar"]
        day_bars = row["dayBars"]
        fill_index = int(row["fillIndex"])
        assert isinstance(signal_time, datetime) and isinstance(signal, StrategySignal)
        assert isinstance(fill_bar, MinuteBar) and isinstance(day_bars, Sequence)
        day = taipei_date(signal_time)

        still_active: list[dict[str, object]] = []
        for position in active:
            if position["exitTime"] <= signal_time:
                pnl = dec(position["netPnl"])
                realized_total += pnl
                position_day = position["day"]
                realized_by_day[position_day] = realized_by_day.get(position_day, ZERO) + pnl
            else:
                still_active.append(position)
        active = still_active
        day_key = (symbol, day)
        if day_key in traded_keys:
            skip("DUPLICATE_SYMBOL_SIGNAL")
            continue
        if len(active) >= int(cfg["maxOpenPositions"]):
            skip("MAX_OPEN_POSITIONS")
            continue
        if any(position["symbol"] == symbol for position in active):
            skip("DUPLICATE_OPEN_SYMBOL")
            continue
        sector = (sector_by_symbol or {}).get(symbol, "UNKNOWN")
        if sum(position["sector"] == sector for position in active) >= int(cfg["maxSectorPositions"]):
            skip("MAX_SECTOR_POSITIONS")
            continue
        status = risk_status(realized_by_day.get(day, ZERO), cfg)
        if status == "HALTED":
            skip("DAILY_LOSS_HALTED")
            continue

        regime = regime_map.get(signal_time, REGIME_MILD if not has_verified_regimes else REGIME_UNKNOWN)
        regime_risk = risk_multiplier_for_regime(regime) if has_verified_regimes else Decimal("1")
        if regime_risk <= 0:
            skip("MARKET_REGIME_BLOCKED")
            continue
        entry = dec(row["entry"])
        actual_rr = dec(row["actualRiskReward"])
        future = row["future"]
        exit_bar = row["forceCloseBar"]
        assert isinstance(future, Sequence) and isinstance(exit_bar, MinuteBar)
        exit_reason = "FORCED_CLOSE"
        for bar in future:
            if bar.low <= signal.stop_price:
                exit_bar, exit_reason = bar, "STOP_LOSS"
                break
            if bar.high >= signal.target_price:
                exit_bar, exit_reason = bar, "PROFIT_TARGET"
                break
        raw_exit = (
            signal.stop_price if exit_reason == "STOP_LOSS"
            else signal.target_price if exit_reason == "PROFIT_TARGET"
            else exit_bar.close
        )
        exit_price = raw_exit * (Decimal("1") - dec(cfg["slippageBps"]) / Decimal("10000"))

        reserved = sum(dec(position["entryCapital"]) for position in active)
        available = initial + realized_total - reserved
        if portfolio:
            strategy_reserved = sum(
                dec(position["entryCapital"]) for position in active
                if position["strategyId"] == signal.strategy_id
            )
            strategy_available = allocation_by_strategy.get(signal.strategy_id, initial) - strategy_reserved
            capital_limit = min(available, strategy_available)
        else:
            capital_limit = available
        risk_budget = dec(cfg["maxRiskPerTrade"]) * regime_risk
        if status == "REDUCED":
            risk_budget /= 2
        quantity = calculate_position_size(
            price=entry, stop_price=signal.stop_price, risk_budget=risk_budget,
            capital_limit=capital_limit, lot_size=int(cfg["boardLotSize"]),
            allow_odd_lots=bool(cfg["allowOddLots"]),
        )
        if quantity <= 0:
            skip("INSUFFICIENT_CAPITAL_OR_RISK_BUDGET")
            continue
        entry_capital = money(entry * quantity)
        if entry_capital > available:
            skip("CAPITAL_RESERVATION_EXCEEDED")
            continue

        trade_result = calculate_trade_result(
            entry_price=entry, exit_price=exit_price, quantity=quantity,
            commission_rate=cfg["commissionRate"], commission_discount=cfg["commissionDiscount"],
            minimum_commission=cfg["minimumCommission"], tax_rate=cfg["dayTradeTaxRate"],
            slippage_bps=0, other_cost=cfg["otherCost"],
        )
        trade = {
            "symbol": symbol, "strategyId": signal.strategy_id, "signalTime": signal_time,
            "marketRegime": regime if has_verified_regimes else REGIME_UNKNOWN,
            "marketRegimeVerified": signal_time in regime_map,
            "controllerFinalScore": str(scored.final_score) if scored is not None else str(signal.confidence),
            "entryTime": fill_bar.timestamp, "entryPrice": str(money(entry)), "quantity": quantity,
            "stopPrice": str(money(signal.stop_price)), "targetPrice": str(money(signal.target_price)),
            "actualRiskReward": str(actual_rr.quantize(Decimal("0.0001"))),
            "exitTime": exit_bar.timestamp, "exitPrice": str(money(exit_price)), "exitReason": exit_reason,
            "grossPnl": str(trade_result["grossPnl"]), "cost": str(trade_result["total"]),
            "netPnl": str(trade_result["netPnl"]), "buyTurnover": str(trade_result["buyTurnover"]),
            "sellTurnover": str(trade_result["sellTurnover"]), "totalTurnover": str(trade_result["totalTurnover"]),
            "listedCommission": str(trade_result["listedCommission"]),
            "paidCommission": str(trade_result["paidCommission"]),
            "commissionRebate": str(trade_result["commissionRebate"]),
            "commissionRate": str(cfg["commissionRate"]), "commissionDiscount": str(cfg["commissionDiscount"]),
            "minimumCommission": str(cfg["minimumCommission"]),
        }
        trades.append(trade)
        active.append({
            "symbol": symbol, "sector": sector, "strategyId": signal.strategy_id,
            "entryCapital": entry_capital, "exitTime": exit_bar.timestamp,
            "netPnl": trade_result["netPnl"], "day": day,
        })
        traded_keys.add(day_key)

    ending = initial + sum((dec(row["netPnl"]) for row in trades), ZERO)
    result: dict[str, object] = {
        "engineVersion": BACKTEST_ENGINE_VERSION,
        "validationStatus": "VALIDATED",
        "validationChecks": {
            "dailyIndicatorReset": True, "nextBarFillRevalidated": True,
            "sameDayForcedExit": True, "capitalReservedUntilExit": True,
            "controllerApplied": apply_controller,
        },
        "tradingDayCount": len(trading_days),
        "skipReasons": [
            {"reason": reason, "count": count} for reason, count in sorted(skip_counts.items())
        ],
        "summary": performance(trades, initial), "trades": trades,
        "endingCapital": str(money(ending)),
    }
    if strategy_id == "ALL":
        result["strategySummaries"] = performance_by_strategy(
            trades, [item[0] for item in STRATEGIES], initial,
        )
    return result


def calculate_position_size(
    *, price: object, stop_price: object, risk_budget: object, capital_limit: object,
    lot_size: int = 1000, allow_odd_lots: bool = False,
) -> int:
    entry = dec(price)
    risk_per_share = entry - dec(stop_price)
    if entry <= 0 or risk_per_share <= 0 or dec(risk_budget) <= 0 or dec(capital_limit) <= 0:
        return 0
    by_risk = int((dec(risk_budget) / risk_per_share).to_integral_value(rounding=ROUND_DOWN))
    by_capital = int((dec(capital_limit) / entry).to_integral_value(rounding=ROUND_DOWN))
    quantity = min(by_risk, by_capital)
    if not allow_odd_lots:
        quantity = (quantity // lot_size) * lot_size
    return max(quantity, 0)


@dataclass(frozen=True)
class Costs:
    buy_fee: Decimal
    sell_fee: Decimal
    transaction_tax: Decimal
    slippage: Decimal
    other_cost: Decimal
    total: Decimal


def calculate_costs(
    *, entry_price: object, exit_price: object, quantity: int,
    commission_rate: object = "0.001425", commission_discount: object = "0.2",
    minimum_commission: object = "20", tax_rate: object = "0.0015",
    slippage_bps: object = "5", other_cost: object = 0,
) -> Costs:
    buy_value = dec(entry_price) * quantity
    sell_value = dec(exit_price) * quantity
    minimum = dec(minimum_commission)
    effective_rate = dec(commission_rate) * dec(commission_discount)
    buy_fee = money(max(buy_value * effective_rate, minimum) if quantity else ZERO)
    sell_fee = money(max(sell_value * effective_rate, minimum) if quantity else ZERO)
    tax = money(sell_value * dec(tax_rate))
    slippage = money((buy_value + sell_value) * dec(slippage_bps) / Decimal("10000"))
    other = money(other_cost)
    total = money(buy_fee + sell_fee + tax + slippage + other)
    return Costs(buy_fee, sell_fee, tax, slippage, other, total)


def calculate_trade_result(*, entry_price: object, exit_price: object, quantity: int, **cost_options: object) -> dict[str, Decimal]:
    gross = money((dec(exit_price) - dec(entry_price)) * quantity)
    costs = calculate_costs(entry_price=entry_price, exit_price=exit_price, quantity=quantity, **cost_options)
    net = money(gross - costs.total)
    capital = dec(entry_price) * quantity
    sell_value = dec(exit_price) * quantity
    commission_rate = dec(cost_options.get("commission_rate", "0.001425"))
    commission_discount = dec(cost_options.get("commission_discount", "0.2"))
    minimum_commission = dec(cost_options.get("minimum_commission", "20"))
    listed_buy_fee = money(max(capital * commission_rate, minimum_commission) if quantity else ZERO)
    listed_sell_fee = money(max(sell_value * commission_rate, minimum_commission) if quantity else ZERO)
    listed_commission = money(listed_buy_fee + listed_sell_fee)
    paid_commission = money(costs.buy_fee + costs.sell_fee)
    commission_rebate = money(max(listed_commission - paid_commission, ZERO))
    net_return = (net / capital * 100).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP) if capital else ZERO
    return {
        "grossPnl": gross, "netPnl": net, "netReturnPct": net_return,
        "buyTurnover": money(capital), "sellTurnover": money(sell_value),
        "totalTurnover": money(capital + sell_value),
        "listedCommission": listed_commission, "paidCommission": paid_commission,
        "commissionRebate": commission_rebate, "commissionDiscount": commission_discount,
        **asdict(costs),
    }


def _backtest_amounts(row: Mapping[str, object]) -> dict[str, Decimal]:
    if "totalTurnover" in row or "total_turnover" in row:
        buy_turnover = dec(row.get("buyTurnover", row.get("buy_turnover", 0)))
        sell_turnover = dec(row.get("sellTurnover", row.get("sell_turnover", 0)))
        total_turnover = dec(row.get("totalTurnover", row.get("total_turnover", buy_turnover + sell_turnover)))
        listed_commission = dec(row.get("listedCommission", row.get("listed_commission", 0)))
        paid_commission = dec(row.get("paidCommission", row.get("paid_commission", 0)))
        commission_rebate = dec(row.get(
            "commissionRebate", row.get("commission_rebate", max(listed_commission - paid_commission, ZERO)),
        ))
        return {
            "buyTurnover": buy_turnover, "sellTurnover": sell_turnover,
            "totalTurnover": total_turnover, "listedCommission": listed_commission,
            "paidCommission": paid_commission, "commissionRebate": commission_rebate,
            "commissionDiscount": dec(row.get("commissionDiscount", row.get("commission_discount", "0.2"))),
        }
    if not all(key in row for key in ("entryPrice", "exitPrice", "quantity")):
        return {
            "buyTurnover": ZERO, "sellTurnover": ZERO, "totalTurnover": ZERO,
            "listedCommission": ZERO, "paidCommission": ZERO, "commissionRebate": ZERO,
            "commissionDiscount": Decimal("0.2"),
        }
    entry_price = dec(row["entryPrice"])
    exit_price = dec(row["exitPrice"])
    quantity = int(row["quantity"])
    rate = dec(row.get("commissionRate", "0.001425"))
    discount = dec(row.get("commissionDiscount", "0.2"))
    minimum = dec(row.get("minimumCommission", "20"))
    buy_turnover = money(entry_price * quantity)
    sell_turnover = money(exit_price * quantity)
    listed = money(
        (max(buy_turnover * rate, minimum) if quantity else ZERO)
        + (max(sell_turnover * rate, minimum) if quantity else ZERO)
    )
    paid = money(
        (max(buy_turnover * rate * discount, minimum) if quantity else ZERO)
        + (max(sell_turnover * rate * discount, minimum) if quantity else ZERO)
    )
    return {
        "buyTurnover": buy_turnover, "sellTurnover": sell_turnover,
        "totalTurnover": money(buy_turnover + sell_turnover),
        "listedCommission": listed, "paidCommission": paid,
        "commissionRebate": money(max(listed - paid, ZERO)), "commissionDiscount": discount,
    }


def _discount_label(discount: Decimal) -> str:
    tenths = (discount * Decimal("10")).normalize()
    return f"{tenths}折"


def _has_backtest_amounts(row: Mapping[str, object]) -> bool:
    return (
        "totalTurnover" in row or "total_turnover" in row
        or all(key in row for key in ("entryPrice", "exitPrice", "quantity"))
    )


def performance(trades: Iterable[Mapping[str, object]], initial_capital: object = "3000000") -> dict[str, object]:
    rows = list(trades)
    transaction_amounts = [_backtest_amounts(row) for row in rows]
    transaction_metrics_available = all(_has_backtest_amounts(row) for row in rows)
    pnls = [dec(row.get("netPnl", row.get("net_pnl", 0))) for row in rows]
    costs = [
        dec(row.get("cost", row.get("total", 0)))
        if "cost" in row or "total" in row
        else sum((
            dec(row.get("buyFee", row.get("buy_fee", 0))),
            dec(row.get("sellFee", row.get("sell_fee", 0))),
            dec(row.get("transactionTax", row.get("transaction_tax", 0))),
            dec(row.get("slippage", 0)),
            dec(row.get("otherCost", row.get("other_cost", 0))),
        ), ZERO)
        for row in rows
    ]
    gross_pnls = [
        dec(row.get("grossPnl", row.get("gross_pnl", pnls[index] + costs[index])))
        for index, row in enumerate(rows)
    ]
    winners = [pnl for pnl in pnls if pnl > 0]
    losers = [pnl for pnl in pnls if pnl < 0]
    gross_profit = sum(winners, ZERO)
    gross_loss = sum(losers, ZERO)
    total = len(pnls)
    win_rate = (Decimal(len(winners)) / total * 100).quantize(Decimal("0.1")) if total else ZERO
    profit_factor = (gross_profit / abs(gross_loss)).quantize(Decimal("0.01")) if gross_loss else None
    avg_win = money(gross_profit / len(winners)) if winners else ZERO
    avg_loss = money(gross_loss / len(losers)) if losers else ZERO
    payoff = (avg_win / abs(avg_loss)).quantize(Decimal("0.01")) if avg_loss else None
    net = money(sum(pnls, ZERO))
    initial = dec(initial_capital)
    discount = transaction_amounts[0]["commissionDiscount"] if transaction_amounts else Decimal("0.2")
    return {
        "initialCapital": str(money(initial)), "endingCapital": str(money(initial + net)),
        "netPnl": str(net), "netReturnPct": str((net / initial * 100).quantize(Decimal("0.01")) if initial else ZERO),
        "grossPnl": str(money(sum(gross_pnls, ZERO))), "totalCost": str(money(sum(costs, ZERO))),
        "buyTurnover": str(money(sum((item["buyTurnover"] for item in transaction_amounts), ZERO))),
        "sellTurnover": str(money(sum((item["sellTurnover"] for item in transaction_amounts), ZERO))),
        "totalTurnover": str(money(sum((item["totalTurnover"] for item in transaction_amounts), ZERO))),
        "listedCommission": str(money(sum((item["listedCommission"] for item in transaction_amounts), ZERO))),
        "paidCommission": str(money(sum((item["paidCommission"] for item in transaction_amounts), ZERO))),
        "commissionRebate": str(money(sum((item["commissionRebate"] for item in transaction_amounts), ZERO))),
        "commissionDiscount": str(discount), "commissionDiscountLabel": _discount_label(discount),
        "transactionMetricsAvailable": transaction_metrics_available,
        "totalProfit": str(money(gross_profit)), "totalLoss": str(money(abs(gross_loss))),
        "tradeCount": total, "winCount": len(winners), "lossCount": len(losers),
        "flatCount": total - len(winners) - len(losers),
        "winRate": str(win_rate), "sampleSufficient": total >= 10,
        "averageWin": str(avg_win), "averageLoss": str(avg_loss),
        "payoffRatio": str(payoff) if payoff is not None else None,
        "profitFactor": str(profit_factor) if profit_factor is not None else None,
        "maxWin": str(money(max(pnls, default=ZERO))), "maxLoss": str(money(min(pnls, default=ZERO))),
    }


def performance_by_strategy(
    trades: Iterable[Mapping[str, object]], strategy_ids: Iterable[str],
    initial_capital: object = "3000000",
) -> dict[str, dict[str, object]]:
    rows = list(trades)
    return {
        strategy_id: performance(
            [row for row in rows if str(row.get("strategyId", row.get("strategy_id", ""))) == strategy_id],
            initial_capital,
        )
        for strategy_id in strategy_ids
    }


def risk_status(realized_pnl: object, config: Mapping[str, object] | None = None) -> str:
    cfg = merged_config(config)
    loss = max(-dec(realized_pnl), ZERO)
    if loss >= dec(cfg["dailyStopLoss"]):
        return "HALTED"
    if loss >= dec(cfg["dailyReduceLoss"]):
        return "REDUCED"
    return "NORMAL"


def apply_execution_report(state: Mapping[str, object], *, execution_id: str, quantity: int, price: object) -> dict[str, object]:
    """Idempotently apply a broker fill to an order state."""
    seen = set(state.get("executionIds", []))
    if execution_id in seen:
        return dict(state)
    ordered = int(state.get("orderQuantity", 0))
    already = int(state.get("filledQuantity", 0))
    accepted = max(0, min(quantity, ordered - already))
    total_quantity = already + accepted
    previous_notional = dec(state.get("averagePrice", 0)) * already
    average = (previous_notional + dec(price) * accepted) / total_quantity if total_quantity else ZERO
    seen.add(execution_id)
    result = dict(state)
    result.update({
        "filledQuantity": total_quantity,
        "unfilledQuantity": max(ordered - total_quantity, 0),
        "averagePrice": str(average.quantize(Decimal("0.0001"))),
        "executionIds": sorted(seen),
        "status": "FILLED" if total_quantity >= ordered else "PARTIALLY_FILLED",
    })
    return result


def exit_action(
    *, current_price: object, stop_price: object, first_target_price: object,
    trailing_stop_price: object, first_target_filled: bool = False,
) -> dict[str, object] | None:
    current = dec(current_price)
    if current <= dec(stop_price):
        return {"reason": "停損", "percentage": 100}
    if trailing_stop_price and current <= dec(trailing_stop_price):
        return {"reason": "移動停利", "percentage": 100}
    if not first_target_filled and current >= dec(first_target_price):
        return {"reason": "第一段停利", "percentage": 50}
    return None


def market_gate_reasons(
    *, now: datetime, market_crashing: bool, quote_reliable: bool, volume: int,
    turnover: object, spread_pct: object, vwap_deviation_pct: object,
    blocked: bool, connection_ok: bool, available_capital: object,
    open_positions: int, sector_positions: int, realized_pnl: object,
    config: Mapping[str, object] | None = None,
) -> list[str]:
    cfg = merged_config(config)
    reasons: list[str] = []
    local = now.astimezone(TAIPEI) if now.tzinfo else now.replace(tzinfo=TAIPEI)
    cutoff = time.fromisoformat(str(cfg["latestEntryTime"]))
    checks = (
        (market_crashing, "大盤急跌"), (not quote_reliable, "即時行情不可靠"),
        (volume < int(cfg["minimumVolume"]), "成交量不足"),
        (dec(turnover) < dec(cfg["minimumTurnover"]), "成交金額不足"),
        (dec(spread_pct) > dec(cfg["maximumSpreadPct"]), "買賣價差過大"),
        (abs(dec(vwap_deviation_pct)) > dec(cfg["maximumVwapDeviationPct"]), "偏離VWAP過遠"),
        (blocked, "股票位於禁止名單"), (not connection_ok, "系統或券商連線異常"),
        (dec(available_capital) <= 0, "可用資金不足"),
        (open_positions >= int(cfg["maxOpenPositions"]), "超過同時持倉上限"),
        (sector_positions >= int(cfg["maxSectorPositions"]), "超過產業集中限制"),
        (risk_status(realized_pnl, cfg) == "HALTED", "已達單日虧損上限"),
        (local.time() >= cutoff, "超過允許建立新部位時間"),
    )
    return [reason for failed, reason in checks if failed]


@dataclass(frozen=True)
class MinuteBar:
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


@dataclass(frozen=True)
class StrategySignal:
    strategy_id: str
    confidence: Decimal
    entry_price: Decimal
    stop_price: Decimal
    target_price: Decimal
    reasons: tuple[str, ...]


def _vwap(bars: Sequence[MinuteBar]) -> Decimal:
    volume = sum(bar.volume for bar in bars)
    if not volume:
        return bars[-1].close
    return sum((((bar.high + bar.low + bar.close) / 3) * bar.volume for bar in bars), ZERO) / volume


def evaluate_strategies(
    bars: Sequence[MinuteBar], *, previous_high: object | None = None,
    previous_low: object | None = None,
    strategy_parameters: Mapping[str, Mapping[str, object]] | None = None,
    enabled_strategies: set[str] | None = None,
) -> list[StrategySignal]:
    """Evaluate completed bars only. The last bar is the decision bar."""
    if len(bars) < 16:
        return []
    current = bars[-1]
    local_time = current.timestamp.astimezone(TAIPEI).time() if current.timestamp.tzinfo else current.timestamp.time()
    history = bars[:-1]
    current_vwap = _vwap(bars)
    previous_vwap = _vwap(history)
    avg_volume = Decimal(sum(bar.volume for bar in history[-15:])) / min(15, len(history))
    volume_ratio = Decimal(current.volume) / avg_volume if avg_volume else ZERO
    signals: list[StrategySignal] = []

    parameters = {key: dict(value) for key, value in DEFAULT_STRATEGY_PARAMETERS.items()}
    for key, values in (strategy_parameters or {}).items():
        if key in parameters:
            parameters[key].update(values)

    def param(strategy_id: str, key: str) -> object:
        return parameters[strategy_id][key]

    def add(strategy_id: str, stop: Decimal, reasons: tuple[str, ...]) -> None:
        entry = current.close
        if stop >= entry:
            return
        confidence = min(Decimal("100"), dec(param(strategy_id, "baseScore")) + min(volume_ratio * 5, Decimal("15")))
        target = entry + (entry - stop) * dec(param(strategy_id, "targetRiskReward"))
        signals.append(StrategySignal(strategy_id, confidence.quantize(Decimal("0.1")), entry, stop, target, reasons))

    enabled = enabled_strategies or set(parameters)
    opening = [bar for bar in bars if time(9, 0) <= (bar.timestamp.astimezone(TAIPEI).time() if bar.timestamp.tzinfo else bar.timestamp.time()) < time(9, 15)]
    if "OPENING_RANGE_BREAKOUT" in enabled and opening and time(9, 15) <= local_time <= time(11, 0):
        opening_high = max(bar.high for bar in opening)
        if current.close > opening_high and current.close > current_vwap and volume_ratio >= dec(param("OPENING_RANGE_BREAKOUT", "volumeMultiplier")):
            add("OPENING_RANGE_BREAKOUT", opening_high, ("突破開盤15分鐘區間", "站在VWAP之上", "突破量能放大"))

    if "VWAP_TREND_PULLBACK" in enabled:
        pullback = dec(param("VWAP_TREND_PULLBACK", "pullbackRangePct")) / 100
        if current.close >= current_vwap and current_vwap > previous_vwap and current.low <= current_vwap * (Decimal("1") + pullback) and current.close > current.open and volume_ratio >= dec(param("VWAP_TREND_PULLBACK", "volumeMultiplier")):
            add("VWAP_TREND_PULLBACK", min(current.low, current_vwap * (Decimal("1") - pullback)), ("VWAP斜率向上", "回踩VWAP未破", "買盤重新出現"))

    if "VOLUME_HIGH_BREAKOUT" in enabled:
        volume_lookback = int(param("VOLUME_HIGH_BREAKOUT", "lookbackBars"))
        resistance = max([bar.high for bar in history[-volume_lookback:]] + ([dec(previous_high)] if previous_high else []))
        if current.close > resistance and volume_ratio >= dec(param("VOLUME_HIGH_BREAKOUT", "volumeMultiplier")):
            add("VOLUME_HIGH_BREAKOUT", resistance, ("爆量突破前高", "相對成交量明顯放大", "價格守住突破平台"))

    if "FALSE_BREAKDOWN_REVERSAL" in enabled:
        false_lookback = int(param("FALSE_BREAKDOWN_REVERSAL", "lookbackBars"))
        support = min([bar.low for bar in history[-false_lookback:]] + ([dec(previous_low)] if previous_low else []))
        recent = bars[-int(param("FALSE_BREAKDOWN_REVERSAL", "confirmationBars")):]
        if min(bar.low for bar in recent) < support and current.close > support and current.close > current.open and volume_ratio >= dec(param("FALSE_BREAKDOWN_REVERSAL", "volumeMultiplier")):
            add("FALSE_BREAKDOWN_REVERSAL", min(bar.low for bar in recent), ("短暫跌破支撐", "限定時間內站回", "反轉K棒確認"))

    if "AFTERNOON_STRENGTH_BREAKOUT" in enabled and time(12, 30) <= local_time < time(13, 20) and current.close > current_vwap:
        afternoon = [bar for bar in history[-int(param("AFTERNOON_STRENGTH_BREAKOUT", "lookbackBars")):] if (bar.timestamp.astimezone(TAIPEI).time() if bar.timestamp.tzinfo else bar.timestamp.time()) >= time(12, 0)]
        if afternoon and current.close > max(bar.high for bar in afternoon) and volume_ratio >= dec(param("AFTERNOON_STRENGTH_BREAKOUT", "volumeMultiplier")):
            add("AFTERNOON_STRENGTH_BREAKOUT", max(current_vwap, min(bar.low for bar in afternoon[-10:])), ("午後維持VWAP之上", "突破午後整理區間", "量價結構偏多"))
    return signals


def resolve_duplicate_signals(signals: Iterable[StrategySignal]) -> tuple[StrategySignal | None, list[StrategySignal]]:
    ranked = sorted(signals, key=lambda item: item.confidence, reverse=True)
    return (ranked[0], ranked[1:]) if ranked else (None, [])


class BrokerAdapter(Protocol):
    name: str
    live: bool

    def connected(self) -> bool: ...
    def submit(self, order: Mapping[str, object]) -> Mapping[str, object]: ...
    def cancel_all(self) -> Sequence[Mapping[str, object]]: ...
    def synchronize(self) -> Mapping[str, object]: ...


class LiveTradingUnavailable(RuntimeError):
    pass


class DisabledLiveBrokerAdapter:
    name = "尚未設定券商"
    live = True

    def connected(self) -> bool:
        return False

    def submit(self, order: Mapping[str, object]) -> Mapping[str, object]:
        raise LiveTradingUnavailable("真實交易已鎖定：尚未設定並驗證券商API")

    def cancel_all(self) -> Sequence[Mapping[str, object]]:
        raise LiveTradingUnavailable("真實交易已鎖定：尚未設定並驗證券商API")

    def synchronize(self) -> Mapping[str, object]:
        return {"connected": False, "positions": [], "orders": [], "reason": "尚未設定券商API"}


def _run_backtest_legacy(
    datasets: Mapping[str, Sequence[MinuteBar]], *, strategy_id: str = "ALL",
    config: Mapping[str, object] | None = None, portfolio: bool = True,
    strategy_parameters: Mapping[str, Mapping[str, object]] | None = None,
    market_regime_by_time: Mapping[datetime, str] | None = None,
    sector_by_symbol: Mapping[str, str] | None = None,
    controller_filter: bool | None = None,
) -> dict[str, object]:
    """Minute backtest with next-bar fills and shared capital.

    Signals are calculated from completed bars through index ``i`` and filled
    at bar ``i + 1`` open. This makes future-bar access explicit and testable.
    """
    from .day_trading_v2_controller import REGIME_MILD, REGIME_UNKNOWN

    cfg = merged_config(config)
    initial = dec(cfg["initialCapital"])
    cash = initial
    trades: list[dict[str, object]] = []
    candidates: list[tuple[datetime, str, StrategySignal, MinuteBar, Sequence[MinuteBar]]] = []
    enabled_strategies = {item[0] for item in STRATEGIES} if strategy_id == "ALL" else {strategy_id}
    for symbol, bars in datasets.items():
        for index in range(15, len(bars) - 1):
            decision_bars = bars[: index + 1]
            next_bar = bars[index + 1]
            evaluation_filter = None if strategy_id == "ALL" else {strategy_id}
            for signal in evaluate_strategies(
                decision_bars, strategy_parameters=strategy_parameters, enabled_strategies=evaluation_filter,
            ):
                if signal.strategy_id in enabled_strategies:
                    candidates.append((signal_time := decision_bars[-1].timestamp, symbol, signal, next_bar, bars[index + 1 :]))
    candidates.sort(key=lambda row: (row[0], -row[2].confidence, row[1]))
    if (portfolio if controller_filter is None else controller_filter) and candidates:
        # Portfolio backtests use the same unified ranking rule as the live
        # controller. When no index series is supplied, the disclosed fallback
        # is a mild-bull regime; qualified optimization datasets should provide it.
        from .day_trading_v2_controller import ControllerCandidateInput, rank_candidates, score_candidate
        selected_keys: set[tuple[datetime, str, str]] = set()
        grouped: dict[datetime, list[tuple[tuple[datetime, str, StrategySignal, MinuteBar, Sequence[MinuteBar]], object]]] = {}
        for row in candidates:
            signal_time, symbol, signal, _fill, _future = row
            controller_input = ControllerCandidateInput(
                key=f"backtest:{signal_time.isoformat()}:{symbol}:{signal.strategy_id}",
                symbol=symbol, stock_name=symbol,
                sector=(sector_by_symbol or {}).get(symbol, "回測未分類"),
                strategy_id=signal.strategy_id, strategy_version="BACKTEST",
                signal_time=signal_time, raw_score=signal.confidence,
                entry_price=signal.entry_price, stop_price=signal.stop_price,
                target_price=signal.target_price,
                risk_reward=(signal.target_price - signal.entry_price) / (signal.entry_price - signal.stop_price),
                sector_strength=Decimal("50"), liquidity_score=Decimal("100"),
            )
            local_time = signal_time.astimezone(TAIPEI).time() if signal_time.tzinfo else signal_time.time()
            scored = score_candidate(
                controller_input, regime=(market_regime_by_time or {}).get(signal_time, REGIME_MILD),
                local_time=local_time, config=cfg,
            )
            grouped.setdefault(signal_time, []).append((row, scored))
        for signal_time, rows in grouped.items():
            ranked = rank_candidates(item[1] for item in rows)
            winner = next((item for item in ranked if item.allowed), None)
            if winner:
                selected_keys.add((signal_time, winner.candidate.symbol, winner.candidate.strategy_id))
        candidates = [row for row in candidates if (row[0], row[1], row[2].strategy_id) in selected_keys]
    occupied: list[tuple[datetime, str]] = []
    traded_keys: set[tuple[str, datetime.date]] = set()
    for signal_time, symbol, signal, fill_bar, future in candidates:
        day_key = (symbol, signal_time.date())
        if day_key in traded_keys or signal.confidence < dec(cfg["minimumConfidence"]):
            continue
        occupied = [(end, held_symbol) for end, held_symbol in occupied if end > signal_time]
        if portfolio and len(occupied) >= int(cfg["maxOpenPositions"]):
            continue
        allocation = next((amount for sid, _, amount in STRATEGIES if sid == signal.strategy_id), initial)
        capital_limit = min(cash, allocation if portfolio else initial)
        entry = fill_bar.open * (Decimal("1") + dec(cfg["slippageBps"]) / Decimal("10000"))
        risk_budget = dec(cfg["maxRiskPerTrade"])
        if risk_status(sum((dec(row["netPnl"]) for row in trades if row["exitTime"].date() == signal_time.date()), ZERO), cfg) == "REDUCED":
            risk_budget /= 2
        quantity = calculate_position_size(
            price=entry, stop_price=signal.stop_price, risk_budget=risk_budget,
            capital_limit=capital_limit, lot_size=int(cfg["boardLotSize"]),
            allow_odd_lots=bool(cfg["allowOddLots"]),
        )
        if quantity <= 0:
            continue
        exit_bar = future[-1]
        exit_reason = "收盤前強制平倉"
        for bar in future:
            if bar.low <= signal.stop_price:
                exit_bar, exit_reason = bar, "停損"
                break
            if bar.high >= signal.target_price:
                exit_bar, exit_reason = bar, "第一段停利"
                break
        raw_exit = signal.stop_price if exit_reason == "停損" else signal.target_price if exit_reason == "第一段停利" else exit_bar.close
        exit_price = raw_exit * (Decimal("1") - dec(cfg["slippageBps"]) / Decimal("10000"))
        result = calculate_trade_result(
            entry_price=entry, exit_price=exit_price, quantity=quantity,
            commission_rate=cfg["commissionRate"], commission_discount=cfg["commissionDiscount"],
            minimum_commission=cfg["minimumCommission"], tax_rate=cfg["dayTradeTaxRate"],
            slippage_bps=0, other_cost=cfg["otherCost"],
        )
        cash += result["netPnl"]
        trades.append({
            "symbol": symbol, "strategyId": signal.strategy_id, "signalTime": signal_time,
            "marketRegime": (market_regime_by_time or {}).get(signal_time, REGIME_UNKNOWN),
            "marketRegimeVerified": signal_time in (market_regime_by_time or {}),
            "entryTime": fill_bar.timestamp, "entryPrice": str(money(entry)), "quantity": quantity,
            "exitTime": exit_bar.timestamp, "exitPrice": str(money(exit_price)), "exitReason": exit_reason,
            "grossPnl": str(result["grossPnl"]), "cost": str(result["total"]), "netPnl": str(result["netPnl"]),
            "buyTurnover": str(result["buyTurnover"]), "sellTurnover": str(result["sellTurnover"]),
            "totalTurnover": str(result["totalTurnover"]),
            "listedCommission": str(result["listedCommission"]), "paidCommission": str(result["paidCommission"]),
            "commissionRebate": str(result["commissionRebate"]),
            "commissionRate": str(cfg["commissionRate"]), "commissionDiscount": str(cfg["commissionDiscount"]),
            "minimumCommission": str(cfg["minimumCommission"]),
        })
        occupied.append((exit_bar.timestamp, symbol))
        traded_keys.add(day_key)
    result: dict[str, object] = {
        "summary": performance(trades, initial), "trades": trades, "endingCapital": str(money(cash)),
    }
    if strategy_id == "ALL":
        result["strategySummaries"] = performance_by_strategy(
            trades, [item[0] for item in STRATEGIES], initial,
        )
    return result
