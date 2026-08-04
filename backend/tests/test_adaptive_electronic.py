from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.adaptive_schemas import (
    AdaptiveBacktestPrice,
    AdaptiveBacktestRequest,
    AdaptiveScanPayload,
    AdaptiveMarketMetrics,
    AdaptiveStockInput,
)
from app.services.adaptive_backtest_service import run_backtest
from app.services.adaptive_electronic_service import _display_candidate_status, _selection_strategy
from app.services.adaptive_entry_window import adaptive_entry_window_open
from app.services.adaptive_parameters import DEFAULT_PARAMETERS
from app.services.adaptive_performance_service import estimated_trade_result, win_rate_from_profits
from app.services.adaptive_strategies import BreakoutStrategy, RangeTradingStrategy
from app.services.electronic_stock_universe_service import common_filter_failures
from app.services.market_regime_service import evaluate_market_regime
from app.services.risk_management_service import position_size_shares


TAIPEI = ZoneInfo("Asia/Taipei")
PARAMETERS = {f"{group}.{name}": value for (group, name), (value, _) in DEFAULT_PARAMETERS.items()}


def market(**changes):
    values = {
        "trade_date": date(2026, 7, 31),
        "updated_at": datetime(2026, 7, 31, 13, 30, tzinfo=TAIPEI),
        "official_data": True,
        "missing_fields": [],
    }
    values.update(changes)
    return AdaptiveMarketMetrics(**values)


def stock(**changes):
    values = {
        "stock_code": "2330", "stock_name": "台積電", "market_type": "上市",
        "industry_code": "24", "main_industry": "半導體", "sub_industry": "晶圓代工",
        "listing_date": date(1994, 9, 5), "is_electronic": True,
        "quote_source": "TWSE MIS", "quote_timestamp": datetime(2026, 7, 31, 10, 0, tzinfo=TAIPEI),
        "price": 100, "open": 98, "high": 101, "low": 98,
        "volume_shares": 2_000_000, "average_volume_20d_shares": 1_000_000,
        "average_turnover_20d": 100_000_000, "return_5d": 5, "return_20d": 10,
        "relative_strength_market": 5, "relative_strength_electronic": 4,
        "ma5": 98, "ma10": 97, "ma20": 95, "ma60": 90,
        "ma20_slope": 1, "ma60_slope": 0.2, "atr14": 2, "rsi14": 62,
        "range_low": 88, "range_high": 98, "range_amplitude": 11,
        "range_position": 1, "breakout_20d": True, "breakout_60d": True,
        "breakout_percent": 2.04, "distance_to_high_percent": 0,
        "volume_ratio_20d": 1.8, "close_location": .85, "upper_shadow_ratio": .2,
        "volume_contracting": True, "down_volume_less_than_up": True,
        "foreign_net_5d": 1000, "trust_net_5d": 500, "holder_400_change": .5,
        "holder_1000_change": .3, "industry_strength_score": 85,
        "industry_rank_percentile": .1, "same_industry_strong_count": 3,
        "revenue_yoy": 10, "latest_eps": 5, "trailing_eps": 20,
    }
    values.update(changes)
    return AdaptiveStockInput(**values)


def test_immediate_crash_overrides_debounce_and_disables_entry() -> None:
    result = evaluate_market_regime(
        market(taiex_return_1d=-4.2, taiex_above_ma60=False), PARAMETERS,
        previous_regime="BREAKOUT",
    )
    assert result.regime == "CRASH"
    assert result.immediate_crash is True
    assert result.exposure_min == 0
    assert result.exposure_max == 20


def test_recovery_requires_two_confirmed_trading_days() -> None:
    metrics = market(
        taiex_new_low=False, electronic_new_low=False, higher_low=True,
        taiex_above_ma5=True, ma5_slope=.1, advance_ratio_2d=60,
    )
    first = evaluate_market_regime(metrics, PARAMETERS, previous_regime="CRASH")
    second = evaluate_market_regime(
        metrics, PARAMETERS, previous_regime="CRASH",
        previous_provisional="RECOVERY", previous_confirmation_days=1,
    )
    assert first.regime == "CRASH"
    assert second.regime == "RECOVERY"


def test_uncertain_market_keeps_recovery_observation_scanner_running() -> None:
    metrics = market(electronic_return_20d=-10, electronic_above_ma60=False)
    evaluation = evaluate_market_regime(metrics, PARAMETERS, previous_regime="UNCERTAIN")
    payload = AdaptiveScanPayload(market=metrics, industries=[], stocks=[])
    assert evaluation.regime == "UNCERTAIN"
    assert _selection_strategy(evaluation, payload) == "RECOVERY"
    assert PARAMETERS["recovery.observation_score"] == 60


def test_non_electronic_industry_is_rejected_even_if_name_looks_technical() -> None:
    item = stock(industry_code="15", main_industry="航運", sub_industry="AI 航運", is_electronic=False)
    failures = common_filter_failures(item, PARAMETERS, date(2026, 7, 31))
    assert "非官方電子產業分類或指定題材股" in failures


def test_cross_industry_fiberglass_stock_is_allowed() -> None:
    item = stock(
        stock_code="1303", stock_name="南亞", industry_code="03",
        main_industry="塑膠", sub_industry="玻纖布", is_electronic=False,
    )
    failures = common_filter_failures(item, PARAMETERS, date(2026, 7, 31))
    assert "非官方電子產業分類或指定題材股" not in failures


def test_twse_mis_five_level_reference_price_remains_observable() -> None:
    item = stock(quote_source="TWSE MIS 五檔參考價")
    failures = common_filter_failures(item, PARAMETERS, date(2026, 7, 31))
    assert "行情來源非官方市場資訊" not in failures


def test_new_entry_window_closes_exactly_at_1320() -> None:
    assert adaptive_entry_window_open(
        datetime(2026, 7, 31, 13, 19, 59, tzinfo=TAIPEI), True, date(2026, 7, 31),
    ) is True
    assert adaptive_entry_window_open(
        datetime(2026, 7, 31, 13, 20, tzinfo=TAIPEI), True, date(2026, 7, 31),
    ) is False
    assert adaptive_entry_window_open(
        datetime(2026, 7, 31, 13, 10, tzinfo=TAIPEI), False, date(2026, 7, 31),
    ) is False
    assert _display_candidate_status(
        "can_enter", date(2026, 7, 31),
        datetime(2026, 7, 31, 13, 20, tzinfo=TAIPEI),
    ) == "next_day_watch"


def test_breakout_strategy_is_traceable_and_meets_direct_entry_score() -> None:
    result = BreakoutStrategy().evaluate(stock(), PARAMETERS)
    assert result.total >= 85
    assert result.status == "可以進場"
    assert result.components["突破量價"] > 0
    assert "收盤突破近 20 日或 60 日高點" in result.reasons


def test_missing_chip_and_fundamental_fields_do_not_receive_points() -> None:
    missing = stock(
        foreign_net_5d=None, trust_net_5d=None,
        holder_400_change=None, holder_1000_change=None,
        retail_holder_change=None, margin_change=None,
        revenue_yoy=None, latest_eps=None, trailing_eps=None,
        industry_strength_score=0,
    )
    range_result = RangeTradingStrategy().evaluate(missing, PARAMETERS)
    breakout_result = BreakoutStrategy().evaluate(missing, PARAMETERS)
    assert range_result.components["籌碼穩定度"] == 0
    assert range_result.components["基本面與產業題材"] == 0
    assert breakout_result.components["法人與大戶籌碼"] == 0
    assert breakout_result.components["基本面與營收"] == 0


def test_position_size_reports_odd_lot_when_below_one_lot() -> None:
    result = position_size_shares(100_000, 100, 95, .5)
    assert result["shares"] == 100
    assert result["lots"] == 0
    assert result["oddLotShares"] == 100


def test_paper_trade_profit_deducts_commission_and_tax() -> None:
    result = estimated_trade_result(Decimal("100"), Decimal("110"), 1000)
    assert result["grossProfit"] == Decimal("10000.00")
    assert result["tradingCost"] > Decimal("0")
    assert result["netProfit"] < result["grossProfit"]
    assert result["netProfit"] > Decimal("0")


def test_paper_trade_win_rate_uses_closed_net_profit_only() -> None:
    assert win_rate_from_profits([
        Decimal("1000"), Decimal("-500"), Decimal("0"),
    ]) == 33.33
    assert win_rate_from_profits([]) == 0


def test_backtest_enters_on_next_day_and_includes_costs() -> None:
    start = date(2025, 1, 1)
    prices = []
    for index in range(120):
        close = 100 + index * .1
        if index == 70:
            close = 120
        prices.append(AdaptiveBacktestPrice(
            date=start + timedelta(days=index), open=close,
            high=close * 1.03, low=close * .99, close=close,
            volume=3_000_000 if index == 70 else 1_000_000,
        ))
    result = run_backtest(AdaptiveBacktestRequest(
        stock_code="2330", stock_name="台積電", strategy_type="BREAKOUT",
        years=1, prices=prices,
    ))
    assert result["methodology"].startswith("訊號使用當日收盤以前資料")
    assert result["costs"]["taxRate"] == .003
    if result["trades"]:
        assert result["trades"][0]["entry_date"] > prices[70].date.isoformat()
