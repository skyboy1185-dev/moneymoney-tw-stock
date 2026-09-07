from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.services.day_trading_v2 import (
    DisabledLiveBrokerAdapter, LiveTradingUnavailable, MinuteBar, StrategySignal, apply_execution_report,
    calculate_costs, calculate_position_size, calculate_trade_result,
    evaluate_strategies, exit_action, market_gate_reasons, performance,
    resolve_duplicate_signals, risk_status, run_backtest,
)


def test_win_rate_uses_closed_trades_only():
    result = performance([{"netPnl": 100}, {"netPnl": -40}, {"netPnl": 0}])
    assert result["tradeCount"] == 3
    assert result["winCount"] == 1
    assert result["lossCount"] == 1
    assert result["winRate"] == "33.3"
    assert result["sampleSufficient"] is False


def test_gross_and_net_pnl_include_all_costs():
    result = calculate_trade_result(
        entry_price=100, exit_price=102, quantity=1000,
        commission_rate="0.001425", commission_discount="0.2",
        minimum_commission=20, tax_rate="0.0015", slippage_bps=5, other_cost=10,
    )
    assert result["grossPnl"] == Decimal("2000.00")
    assert result["netPnl"] == result["grossPnl"] - result["total"]
    assert result["netPnl"] < result["grossPnl"]


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
    rows.append(MinuteBar(start + timedelta(minutes=16), Decimal("110"), Decimal("111"), Decimal("109"), Decimal("110"), 2500))
    rows.append(MinuteBar(start + timedelta(minutes=17), Decimal("106"), Decimal("107"), Decimal("105"), Decimal("106"), 1500))
    return rows


def test_opening_breakout_strategy_is_long_only():
    signals = evaluate_strategies(_opening_breakout_bars()[:16])
    assert any(signal.strategy_id == "OPENING_RANGE_BREAKOUT" for signal in signals)
    assert all(signal.stop_price < signal.entry_price for signal in signals)


def test_backtest_fills_on_next_bar_not_signal_price():
    result = run_backtest({"2330": _opening_breakout_bars()}, strategy_id="OPENING_RANGE_BREAKOUT", config={"minimumConfidence": "0", "allowOddLots": True, "slippageBps": "0"})
    trade = result["trades"][0]
    assert trade["entryPrice"] == "110.00"
    assert trade["signalTime"] < trade["entryTime"]


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
