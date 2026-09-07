from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from app.services import day_trading_v2 as dtv2

from app.services.day_trading_v2 import (
    STRATEGIES, TAIPEI, DisabledLiveBrokerAdapter, LiveTradingUnavailable, MinuteBar, StrategySignal, apply_execution_report,
    calculate_costs, calculate_position_size, calculate_trade_result,
    evaluate_strategies, exit_action, market_gate_reasons, performance, performance_by_strategy,
    resolve_duplicate_signals, risk_status, run_backtest,
)


def test_win_rate_uses_closed_trades_only():
    result = performance([{"netPnl": 100}, {"netPnl": -40}, {"netPnl": 0}])
    assert result["tradeCount"] == 3
    assert result["winCount"] == 1
    assert result["lossCount"] == 1
    assert result["flatCount"] == 1
    assert result["winRate"] == "33.3"
    assert result["sampleSufficient"] is False


def test_performance_summarizes_profit_loss_and_costs():
    result = performance([
        {"grossPnl": 120, "cost": 20, "netPnl": 100},
        {"grossPnl": -30, "cost": 10, "netPnl": -40},
    ])
    assert result["grossPnl"] == "90.00"
    assert result["totalCost"] == "30.00"
    assert result["totalProfit"] == "100.00"
    assert result["totalLoss"] == "40.00"
    assert result["netPnl"] == "60.00"
    assert Decimal(result["totalProfit"]) - Decimal(result["totalLoss"]) == Decimal(result["netPnl"])
    assert Decimal(result["grossPnl"]) - Decimal(result["totalCost"]) == Decimal(result["netPnl"])


def test_performance_without_trades_has_zero_totals_and_no_wins():
    result = performance([])
    assert result["tradeCount"] == 0
    assert result["winCount"] == 0
    assert result["lossCount"] == 0
    assert result["flatCount"] == 0
    assert result["totalProfit"] == "0.00"
    assert result["totalLoss"] == "0.00"
    assert result["totalCost"] == "0.00"
    assert result["totalTurnover"] == "0.00"
    assert result["commissionRebate"] == "0.00"
    assert result["transactionMetricsAvailable"] is True


def test_performance_can_be_split_by_strategy_and_reconciled_to_total():
    trades = [
        {"strategyId": "OPENING_RANGE_BREAKOUT", "grossPnl": 120, "cost": 20, "netPnl": 100},
        {"strategyId": "OPENING_RANGE_BREAKOUT", "grossPnl": -30, "cost": 10, "netPnl": -40},
        {"strategyId": "VWAP_TREND_PULLBACK", "grossPnl": 70, "cost": 10, "netPnl": 60},
    ]
    strategy_ids = ["OPENING_RANGE_BREAKOUT", "VWAP_TREND_PULLBACK", "VOLUME_HIGH_BREAKOUT"]
    split = performance_by_strategy(trades, strategy_ids)
    total = performance(trades)
    assert list(split) == strategy_ids
    assert split["OPENING_RANGE_BREAKOUT"]["winRate"] == "50.0"
    assert split["VWAP_TREND_PULLBACK"]["winRate"] == "100.0"
    assert split["VOLUME_HIGH_BREAKOUT"]["tradeCount"] == 0
    assert sum(Decimal(item["netPnl"]) for item in split.values()) == Decimal(total["netPnl"])
    assert sum(Decimal(item["totalProfit"]) for item in split.values()) == Decimal(total["totalProfit"])
    assert sum(Decimal(item["totalLoss"]) for item in split.values()) == Decimal(total["totalLoss"])
    assert sum(Decimal(item["totalCost"]) for item in split.values()) == Decimal(total["totalCost"])


def test_gross_and_net_pnl_include_all_costs():
    result = calculate_trade_result(
        entry_price=100, exit_price=102, quantity=1000,
        commission_rate="0.001425", commission_discount="0.2",
        minimum_commission=20, tax_rate="0.0015", slippage_bps=5, other_cost=10,
    )
    assert result["grossPnl"] == Decimal("2000.00")
    assert result["netPnl"] == result["grossPnl"] - result["total"]
    assert result["netPnl"] < result["grossPnl"]
    assert result["buyTurnover"] == Decimal("100000.00")
    assert result["sellTurnover"] == Decimal("102000.00")
    assert result["totalTurnover"] == Decimal("202000.00")
    assert result["listedCommission"] == Decimal("287.85")
    assert result["paidCommission"] == Decimal("57.57")
    assert result["commissionRebate"] == Decimal("230.28")


def test_commission_rebate_respects_minimum_fee_and_does_not_change_net_pnl():
    result = calculate_trade_result(
        entry_price=10, exit_price=10, quantity=1,
        commission_rate="0.001425", commission_discount="0.2",
        minimum_commission=20, tax_rate=0, slippage_bps=0,
    )
    assert result["listedCommission"] == Decimal("40.00")
    assert result["paidCommission"] == Decimal("40.00")
    assert result["commissionRebate"] == Decimal("0.00")
    assert result["netPnl"] == Decimal("-40.00")


def test_performance_reconstructs_turnover_and_two_tenths_rebate_for_stored_backtest_trades():
    result = performance([{
        "entryPrice": "100", "exitPrice": "102", "quantity": 1000,
        "grossPnl": "2000", "cost": "210.57", "netPnl": "1789.43",
    }])
    assert result["totalTurnover"] == "202000.00"
    assert result["listedCommission"] == "287.85"
    assert result["paidCommission"] == "57.57"
    assert result["commissionRebate"] == "230.28"
    assert result["commissionDiscountLabel"] == "2折"
    assert result["transactionMetricsAvailable"] is True
    assert result["netPnl"] == "1789.43"


def test_performance_marks_transaction_metrics_unavailable_without_fill_details():
    result = performance([{"grossPnl": "120", "cost": "20", "netPnl": "100"}])
    assert result["transactionMetricsAvailable"] is False


def test_commission_uses_two_tenths_discount_and_minimum():
    large = calculate_costs(entry_price=100, exit_price=100, quantity=1000)
    small = calculate_costs(entry_price=10, exit_price=10, quantity=1)
    assert large.buy_fee == Decimal("28.50")
    assert small.buy_fee == Decimal("20.00")


def test_day_trade_tax_default_is_point_one_five_percent():
    costs = calculate_costs(entry_price=100, exit_price=100, quantity=1000, slippage_bps=0)
    assert costs.transaction_tax == Decimal("150.00")


def test_slippage_calculation_uses_both_sides_notional():
    costs = calculate_costs(entry_price=100, exit_price=100, quantity=1000, slippage_bps=5)
    assert costs.slippage == Decimal("100.00")


def test_position_size_uses_smaller_of_risk_and_capital_then_board_lot():
    assert calculate_position_size(price=100, stop_price=98, risk_budget=6000, capital_limit=250000) == 2000
    assert calculate_position_size(price=100, stop_price=98, risk_budget=6000, capital_limit=99000) == 0


def test_position_size_supports_odd_lots_when_enabled():
    assert calculate_position_size(price=100, stop_price=98, risk_budget=6000, capital_limit=99000, allow_odd_lots=True) == 990


def test_invalid_stop_or_insufficient_funds_produces_zero_quantity():
    assert calculate_position_size(price=100, stop_price=100, risk_budget=6000, capital_limit=300000) == 0
    assert calculate_position_size(price=100, stop_price=98, risk_budget=6000, capital_limit=0) == 0


def test_duplicate_signal_selects_highest_confidence():
    low = StrategySignal("A", Decimal("81"), Decimal("10"), Decimal("9"), Decimal("12"), ())
    high = StrategySignal("B", Decimal("92"), Decimal("10"), Decimal("9"), Decimal("12"), ())
    winner, rejected = resolve_duplicate_signals([low, high])
    assert winner == high
    assert rejected == [low]


def test_partial_fill_only_adds_actual_quantity():
    state = apply_execution_report({"orderQuantity": 1000, "filledQuantity": 0}, execution_id="f1", quantity=400, price=50)
    assert state["filledQuantity"] == 400
    assert state["unfilledQuantity"] == 600
    assert state["status"] == "PARTIALLY_FILLED"


def test_duplicate_execution_report_is_idempotent():
    first = apply_execution_report({"orderQuantity": 1000, "filledQuantity": 0}, execution_id="f1", quantity=400, price=50)
    duplicate = apply_execution_report(first, execution_id="f1", quantity=400, price=50)
    assert duplicate == first


def test_complete_fill_updates_weighted_average_and_status():
    first = apply_execution_report({"orderQuantity": 1000, "filledQuantity": 0}, execution_id="f1", quantity=400, price=50)
    final = apply_execution_report(first, execution_id="f2", quantity=600, price=51)
    assert final["filledQuantity"] == 1000
    assert final["averagePrice"] == "50.6000"
    assert final["status"] == "FILLED"


def test_stop_loss_precedes_profit_actions():
    action = exit_action(current_price=98, stop_price=98, first_target_price=103, trailing_stop_price=99)
    assert action == {"reason": "停損", "percentage": 100}


def test_first_target_is_partial_exit():
    action = exit_action(current_price=103, stop_price=98, first_target_price=103, trailing_stop_price=99)
    assert action == {"reason": "第一段停利", "percentage": 50}


def test_trailing_stop_closes_remaining_position():
    action = exit_action(current_price=101, stop_price=98, first_target_price=103, trailing_stop_price=101, first_target_filled=True)
    assert action == {"reason": "移動停利", "percentage": 100}


def test_daily_loss_reduces_then_halts():
    assert risk_status(-11999) == "NORMAL"
    assert risk_status(-12000) == "REDUCED"
    assert risk_status(-24000) == "HALTED"


def test_common_market_filter_records_every_skip_reason():
    reasons = market_gate_reasons(
        now=datetime(2026, 9, 7, 5, 21, tzinfo=UTC), market_crashing=True,
        quote_reliable=False, volume=0, turnover=0, spread_pct=1,
        vwap_deviation_pct=2, blocked=True, connection_ok=False,
        available_capital=0, open_positions=3, sector_positions=2, realized_pnl=-24000,
    )
    assert "大盤急跌" in reasons
    assert "即時行情不可靠" in reasons
    assert "可用資金不足" in reasons
    assert "已達單日虧損上限" in reasons
    assert "超過允許建立新部位時間" in reasons


def _opening_breakout_bars() -> list[MinuteBar]:
    start = datetime(2026, 9, 7, 1, 0, tzinfo=UTC)  # 09:00 Asia/Taipei
    rows = [MinuteBar(start + timedelta(minutes=i), Decimal("99.5"), Decimal("100"), Decimal("99"), Decimal("99.8"), 1000) for i in range(15)]
    rows.append(MinuteBar(start + timedelta(minutes=15), Decimal("100"), Decimal("102.5"), Decimal("100"), Decimal("102"), 2500))
    rows.append(MinuteBar(start + timedelta(minutes=16), Decimal("101.9"), Decimal("103"), Decimal("101.8"), Decimal("102.5"), 2500))
    rows.append(MinuteBar(start + timedelta(minutes=17), Decimal("106"), Decimal("107"), Decimal("105"), Decimal("106"), 1500))
    rows.append(MinuteBar(start.replace(hour=5, minute=25), Decimal("106"), Decimal("106"), Decimal("105.5"), Decimal("105.8"), 1200))
    return rows


def test_opening_breakout_strategy_is_long_only():
    signals = evaluate_strategies(_opening_breakout_bars()[:16])
    assert any(signal.strategy_id == "OPENING_RANGE_BREAKOUT" for signal in signals)
    assert all(signal.stop_price < signal.entry_price for signal in signals)


def test_backtest_fills_on_next_bar_not_signal_price():
    result = run_backtest({"2330": _opening_breakout_bars()}, strategy_id="OPENING_RANGE_BREAKOUT", config={"minimumConfidence": "0", "allowOddLots": True, "slippageBps": "0"})
    trade = result["trades"][0]
    assert trade["entryPrice"] == "101.90"
    assert trade["signalTime"] < trade["entryTime"]
    assert trade["marketRegime"] == "UNKNOWN"
    assert trade["marketRegimeVerified"] is False
    assert Decimal(trade["totalTurnover"]) == (
        Decimal(trade["entryPrice"]) + Decimal(trade["exitPrice"])
    ) * trade["quantity"]
    assert Decimal(trade["commissionRebate"]) >= 0
    assert result["summary"]["totalTurnover"] == trade["totalTurnover"]
    assert result["summary"]["commissionRebate"] == trade["commissionRebate"]


def test_backtest_reports_slippage_as_an_explicit_cost_without_moving_fill_price():
    result = run_backtest(
        {"2330": _opening_breakout_bars()}, strategy_id="OPENING_RANGE_BREAKOUT",
        config={"minimumConfidence": "0", "allowOddLots": True, "slippageBps": "5"},
    )
    trade = result["trades"][0]
    assert trade["entryPrice"] == "101.90"
    assert Decimal(trade["slippage"]) > 0
    assert Decimal(trade["cost"]) == (
        Decimal(trade["buyFee"]) + Decimal(trade["sellFee"])
        + Decimal(trade["transactionTax"]) + Decimal(trade["slippage"])
        + Decimal(trade["otherCost"])
    )


def test_backtest_trade_keeps_verified_entry_market_regime():
    bars = _opening_breakout_bars()
    result = run_backtest(
        {"2330": bars}, strategy_id="OPENING_RANGE_BREAKOUT",
        config={"minimumConfidence": "0", "allowOddLots": True, "slippageBps": "0"},
        market_regime_by_time={bars[15].timestamp: "A_STRONG_TREND"},
    )
    assert result["trades"][0]["marketRegime"] == "A_STRONG_TREND"
    assert result["trades"][0]["marketRegimeVerified"] is True


def test_all_strategy_backtest_includes_five_strategy_summaries():
    result = run_backtest(
        {"2330": _opening_breakout_bars()}, strategy_id="ALL",
        config={"minimumConfidence": "0", "allowOddLots": True, "slippageBps": "0"},
    )
    assert list(result["strategySummaries"]) == [item[0] for item in STRATEGIES]
    assert sum(item["tradeCount"] for item in result["strategySummaries"].values()) == result["summary"]["tradeCount"]
    assert sum(Decimal(item["totalTurnover"]) for item in result["strategySummaries"].values()) == Decimal(result["summary"]["totalTurnover"])
    assert sum(Decimal(item["commissionRebate"]) for item in result["strategySummaries"].values()) == Decimal(result["summary"]["commissionRebate"])


def test_shared_portfolio_never_exceeds_three_million():
    bars = _opening_breakout_bars()
    datasets = {str(2300 + index): bars for index in range(20)}
    result = run_backtest(datasets, strategy_id="OPENING_RANGE_BREAKOUT", config={"minimumConfidence": "0", "allowOddLots": True, "slippageBps": "0"}, portfolio=True)
    total_entry = sum(Decimal(row["entryPrice"]) * row["quantity"] for row in result["trades"][:3])
    assert total_entry <= Decimal("3000000")
    entry_counts = {}
    for trade in result["trades"]:
        entry_counts[trade["entryTime"]] = entry_counts.get(trade["entryTime"], 0) + 1
    assert max(entry_counts.values()) <= 3


def test_portfolio_backtest_uses_controller_and_opens_at_most_one_position_per_decision_time():
    bars = _opening_breakout_bars()
    result = run_backtest(
        {"2330": bars, "2454": bars, "2382": bars}, strategy_id="OPENING_RANGE_BREAKOUT",
        config={"minimumConfidence": "0", "allowOddLots": True, "slippageBps": "0"}, portfolio=True,
        sector_by_symbol={"2330": "半導體", "2454": "半導體", "2382": "電腦"},
    )
    counts = {}
    for trade in result["trades"]:
        counts[trade["entryTime"]] = counts.get(trade["entryTime"], 0) + 1
    assert max(counts.values(), default=0) <= 1


def test_backtest_resets_opening_range_and_vwap_each_trading_day():
    first = _opening_breakout_bars()
    first = [MinuteBar(bar.timestamp, Decimal("199"), Decimal("200"), Decimal("198"), Decimal("199"), bar.volume) for bar in first]
    second = [MinuteBar(bar.timestamp + timedelta(days=1), bar.open, bar.high, bar.low, bar.close, bar.volume) for bar in _opening_breakout_bars()]
    result = run_backtest(
        {"2330": [*first, *second]}, strategy_id="OPENING_RANGE_BREAKOUT",
        config={"minimumConfidence": "0", "allowOddLots": True, "slippageBps": "0"},
    )
    assert len(result["trades"]) == 1
    assert result["trades"][0]["signalTime"].date() == second[15].timestamp.date()
    assert result["validationChecks"]["dailyIndicatorReset"] is True


def test_backtest_never_uses_a_later_day_as_forced_close():
    bars = _opening_breakout_bars()
    next_day = MinuteBar(
        bars[-1].timestamp + timedelta(days=1), Decimal("120"), Decimal("120"),
        Decimal("120"), Decimal("120"), 1000,
    )
    result = run_backtest(
        {"2330": [*bars, next_day]}, strategy_id="OPENING_RANGE_BREAKOUT",
        config={"minimumConfidence": "0", "allowOddLots": True, "slippageBps": "0"},
    )
    assert result["trades"]
    assert all(trade["entryTime"].date() == trade["exitTime"].date() for trade in result["trades"])


def test_backtest_rejects_an_untradeable_next_bar_gap():
    bars = _opening_breakout_bars()
    bars[16] = MinuteBar(
        bars[16].timestamp, Decimal("110"), Decimal("111"), Decimal("109"), Decimal("110"), 2500,
    )
    result = run_backtest(
        {"2330": bars}, strategy_id="OPENING_RANGE_BREAKOUT",
        config={"minimumConfidence": "0", "allowOddLots": True, "slippageBps": "0"},
    )
    assert any(row["reason"] == "NEXT_BAR_CHASE_TOO_LARGE" for row in result["skipReasons"])
    assert all(trade["signalTime"] != bars[15].timestamp for trade in result["trades"])


def test_portfolio_ranks_only_candidates_that_can_close_same_day():
    complete = _opening_breakout_bars()
    result = run_backtest(
        {"2330": complete[:-1], "2454": complete}, strategy_id="OPENING_RANGE_BREAKOUT",
        config={"minimumConfidence": "0", "allowOddLots": True, "slippageBps": "0"},
        sector_by_symbol={"2330": "A", "2454": "B"},
    )
    assert result["trades"]
    assert result["trades"][0]["symbol"] == "2454"
    assert any(row["reason"] == "FORCED_CLOSE_MINUTE_MISSING" for row in result["skipReasons"])


def test_forced_close_uses_first_available_trade_after_1325():
    bars = _opening_breakout_bars()
    bars[-1] = MinuteBar(
        bars[-1].timestamp + timedelta(minutes=1), bars[-1].open, bars[-1].high,
        bars[-1].low, bars[-1].close, bars[-1].volume,
    )
    # Avoid hitting the target before the forced-close flow is exercised.
    bars[17] = MinuteBar(
        bars[17].timestamp, Decimal("102.5"), Decimal("103"), Decimal("101"), Decimal("102"), 1500,
    )
    result = run_backtest(
        {"2330": bars}, strategy_id="OPENING_RANGE_BREAKOUT",
        config={"minimumConfidence": "0", "allowOddLots": True, "slippageBps": "0"},
    )
    assert result["trades"]
    assert result["trades"][0]["exitReason"] == "FORCED_CLOSE"
    assert result["trades"][0]["exitTime"].astimezone(TAIPEI).time().isoformat() == "13:26:00"


def test_individual_backtest_also_applies_controller_time_window(monkeypatch):
    start = datetime(2026, 9, 7, 3, 30, tzinfo=UTC)  # 11:30 Taipei, after opening strategy window
    bars = [MinuteBar(start + timedelta(minutes=i), Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100"), 1000) for i in range(18)]
    bars.append(MinuteBar(start.replace(hour=5, minute=25), Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100"), 1000))

    def fake_evaluate(rows, **_kwargs):
        return [StrategySignal("OPENING_RANGE_BREAKOUT", Decimal("95"), Decimal("100"), Decimal("99"), Decimal("103"), ("test",))]

    monkeypatch.setattr(dtv2, "evaluate_strategies", fake_evaluate)
    result = run_backtest(
        {"2330": bars}, strategy_id="OPENING_RANGE_BREAKOUT", portfolio=False,
        config={"minimumConfidence": "0", "allowOddLots": True, "slippageBps": "0"},
    )
    assert result["trades"] == []
    assert result["validationChecks"]["controllerApplied"] is True
    assert any(row["reason"].startswith("CONTROLLER:") for row in result["skipReasons"])


def test_individual_backtest_reserves_the_shared_three_million(monkeypatch):
    bars = _opening_breakout_bars()

    def fake_evaluate(rows, **_kwargs):
        if len(rows) == 16:
            return [StrategySignal("OPENING_RANGE_BREAKOUT", Decimal("95"), Decimal("100"), Decimal("90"), Decimal("130"), ("test",))]
        return []

    monkeypatch.setattr(dtv2, "evaluate_strategies", fake_evaluate)
    result = run_backtest(
        {str(2300 + index): bars for index in range(4)}, strategy_id="OPENING_RANGE_BREAKOUT",
        portfolio=False, controller_filter=False,
        config={
            "minimumConfidence": "0", "allowOddLots": True, "slippageBps": "0",
            "maxRiskPerTrade": "3000000",
        },
    )
    simultaneous = [trade for trade in result["trades"] if trade["entryTime"] == bars[16].timestamp]
    assert sum(Decimal(trade["entryPrice"]) * trade["quantity"] for trade in simultaneous) <= Decimal("3000000")


def test_restart_broker_sync_fails_closed_without_credentials():
    broker = DisabledLiveBrokerAdapter()
    state = broker.synchronize()
    assert state["connected"] is False
    assert state["positions"] == []
    assert broker.connected() is False


def test_live_order_failure_is_explicit_and_cannot_create_position():
    broker = DisabledLiveBrokerAdapter()
    with pytest.raises(LiveTradingUnavailable, match="真實交易已鎖定"):
        broker.submit({"symbol": "2330", "side": "BUY", "quantity": 1000})
