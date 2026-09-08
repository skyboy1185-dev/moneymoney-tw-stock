from datetime import datetime, timedelta
from decimal import Decimal

from app.services.day_trading_v2 import MinuteBar, TAIPEI, evaluate_strategies, performance, run_backtest
from app.services.day_trading_v2_controller import REGIME_MILD
from app.services.day_trading_v2_research import (
    PreparedSignalEvaluator, run_regime_research, research_candidates, validate_routes,
)
from app.services.day_trading_v2_optimization import frozen_validation_ranges, walk_forward_splits


def bars(count=50):
    start = datetime(2026, 1, 1, 9, tzinfo=TAIPEI)
    return [MinuteBar(start+timedelta(minutes=i), Decimal(100), Decimal(101+i%5),
                      Decimal(99), Decimal(100+i%3), 1000+i*50) for i in range(count)]


def test_prepared_indicators_match_original_for_every_prefix_and_candidate():
    rows = bars()
    prepared = PreparedSignalEvaluator({"2330": rows})
    for candidate in research_candidates():
        options = {"strategy_parameters": {candidate["strategyId"]: candidate["parameters"]},
                   "enabled_strategies": {candidate["strategyId"]}}
        for length in range(16, len(rows)+1):
            assert prepared(rows[:length], **options) == evaluate_strategies(rows[:length], **options)


def test_empty_policy_means_cash_and_unknown_regime_cannot_enable_a_route():
    rows = bars()
    for policy in [{}, {REGIME_MILD: {"OPENING_RANGE_BREAKOUT": {}}}]:
        result = run_backtest({"2330": rows}, strategy_policy=policy, market_regime_by_time={})
        assert result["trades"] == []


def test_rejected_validation_route_is_cash_not_an_alternative_candidate():
    metric = {"tradeCount": 50, "activeDays": 10, "netPnl": "-1", "profitFactor": "0.99", "lossCount": 1}
    assert validate_routes({REGIME_MILD: {"id": "winner"}}, {REGIME_MILD: metric}) == {}


def test_holdout_losses_cannot_change_frozen_selection():
    start = datetime(2025, 1, 1, 9, tzinfo=TAIPEI)
    rows = [MinuteBar(start+timedelta(days=i), *([Decimal(100)]*4), 1000) for i in range(160)]
    def experiment(oos_pnl):
        def runner(data, **kwargs):
            first = data["2330"][0].timestamp
            held_out = first >= start+timedelta(days=140)
            pnl = oos_pnl if held_out else 100
            trades = [{"strategyId": "VWAP_TREND_PULLBACK", "marketRegime": REGIME_MILD,
                       "entryTime": first+timedelta(days=i//3), "exitTime": first+timedelta(days=i//3, minutes=1),
                       "netPnl": str(pnl), "grossPnl": str(pnl+10), "cost": "10"} for i in range(30)]
            return {"trades": trades, "summary": performance(trades)}
        return run_regime_research({"2330": rows}, {}, {}, runner=runner)
    positive, negative = experiment(100), experiment(-100)
    a, b = positive["folds"][0], negative["folds"][0]
    assert a["frozenRoutes"]
    assert a["frozenRoutes"] == b["frozenRoutes"]
    assert a["outOfSample"]["summary"]["netPnl"] != b["outOfSample"]["summary"]["netPnl"]
    assert positive["automaticallyActivated"] is False


def test_single_version_optimizer_cannot_pool_future_validation_windows():
    days = [(datetime(2025, 1, 1)+timedelta(days=i)).date() for i in range(240)]
    folds = walk_forward_splits(days)
    selected = frozen_validation_ranges(folds)
    assert selected == [folds[0]["validation"]]
    assert all(end < fold["oos"][0] for _, end in selected for fold in folds)
