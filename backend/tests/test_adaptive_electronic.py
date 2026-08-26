from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.adaptive_schemas import (
    AdaptiveBacktestPrice,
    AdaptiveBacktestRequest,
    AdaptiveScanPayload,
    AdaptiveMarketMetrics,
    AdaptiveStockInput,
)
from app.database import Base
from app.models import AdaptivePaperTrade, AdaptiveSignal, AdaptiveStockCandidate, SuperAIDaytradeNotification
from app.services.adaptive_backtest_service import run_backtest
from app.services.adaptive_electronic_service import _active_trading_strategy, _display_candidate_status, _selection_strategy
from app.services.adaptive_electronic_automation import _normalize_scan_payload, _signal_has_super_ai_trade_record
from app.services.adaptive_entry_window import adaptive_entry_window_open
from app.services.adaptive_parameters import DEFAULT_PARAMETERS
from app.services.adaptive_performance_service import (
    _exit_reason,
    _release_reserved_capital,
    estimated_trade_result,
    update_adaptive_paper_trades,
    update_open_trade_from_market_price,
    win_rate_from_profits,
)
from app.services.adaptive_strategies import BreakoutStrategy, RangeTradingStrategy
from app.services.electronic_stock_universe_service import common_filter_failures
from app.services.market_regime_service import evaluate_market_regime, intraday_regime_override
from app.services.risk_management_service import position_size_shares
from app.services.super_ai_daytrade_service import ensure_settings as ensure_super_ai_settings, market_state, trading_gate


TAIPEI = ZoneInfo("Asia/Taipei")
PARAMETERS = {f"{group}.{name}": value for (group, name), (value, _) in DEFAULT_PARAMETERS.items()}


def test_scan_payload_normalizes_missing_industry_code_without_rejecting_batch() -> None:
    raw = {"stocks": [{"stock_code": "2330", "industry_code": ""}, {"stock_code": "2454", "industry_code": "24"}]}
    normalized = _normalize_scan_payload(raw)
    assert normalized["stocks"][0]["industry_code"] == "00"
    assert normalized["stocks"][1]["industry_code"] == "24"


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


def test_strong_rebound_from_uncertain_is_provisional_recovery() -> None:
    metrics = market(
        taiex_new_low=False, electronic_new_low=False, higher_low=True,
        taiex_above_ma5=True, ma5_slope=1.4, advance_ratio_2d=74,
        up_volume_expanding=True, sector_continuation_days=3,
        new_high_20d_ratio=11,
    )
    result = evaluate_market_regime(metrics, PARAMETERS, previous_regime="UNCERTAIN")
    assert result.provisional_regime == "RECOVERY"
    assert result.regime == "UNCERTAIN"


def test_intraday_bull_squeeze_overrides_slow_regime_for_day_trading() -> None:
    metrics = market(
        taiex_return_1d=1.1,
        electronic_return_1d=1.4,
        advance_ratio=68,
        taiex_above_ma5=True,
        market_open=True,
    )
    override = intraday_regime_override(
        metrics,
        "RANGE",
    )
    assert override == "BREAKOUT"
    evaluation = evaluate_market_regime(metrics, PARAMETERS, previous_regime="UNCERTAIN")
    payload = AdaptiveScanPayload(market=metrics, industries=[], stocks=[])
    assert _active_trading_strategy(evaluation, payload, override) == "BREAKOUT"


def test_intraday_bull_squeeze_override_value() -> None:
    assert intraday_regime_override(
        market(
            taiex_return_1d=1.1,
            electronic_return_1d=1.4,
            advance_ratio=68,
            taiex_above_ma5=True,
        ),
        "RANGE",
    ) == "BREAKOUT"


def test_intraday_selloff_overrides_recovery_for_day_trading() -> None:
    assert intraday_regime_override(
        market(
            taiex_return_1d=-1.0,
            electronic_return_1d=-1.3,
            advance_ratio=28,
            taiex_new_low=True,
        ),
        "RECOVERY",
    ) == "CRASH"


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


def test_yahoo_fallback_quote_remains_observable() -> None:
    failures = common_filter_failures(
        stock(quote_source="Yahoo Finance fallback"), PARAMETERS, date(2026, 7, 31),
    )
    assert failures == []


def test_new_entry_window_closes_exactly_at_noon() -> None:
    assert adaptive_entry_window_open(
        datetime(2026, 7, 31, 11, 59, 59, tzinfo=TAIPEI), True, date(2026, 7, 31),
    ) is True
    assert adaptive_entry_window_open(
        datetime(2026, 7, 31, 12, 0, tzinfo=TAIPEI), True, date(2026, 7, 31),
    ) is False
    assert adaptive_entry_window_open(
        datetime(2026, 7, 31, 13, 10, tzinfo=TAIPEI), False, date(2026, 7, 31),
    ) is False
    assert _display_candidate_status(
        "can_enter", date(2026, 7, 31),
        datetime(2026, 7, 31, 12, 0, tzinfo=TAIPEI),
    ) == "next_day_watch"


def test_super_ai_daytrade_forces_exit_after_1325() -> None:
    class Trade:
        side = "LONG"
        stop_loss_price = Decimal("90")
        target_price_2 = Decimal("120")

    assert _exit_reason(
        Trade(), Decimal("100"), "RANGE", None,
        datetime(2026, 7, 31, 13, 25, tzinfo=TAIPEI),
    ) == "DAY_TRADE_CLOSE"


def test_super_ai_long_uses_hard_stop_loss() -> None:
    class Trade:
        side = "LONG"
        stop_loss_price = Decimal("100")
        target_price_2 = Decimal("120")

    assert _exit_reason(
        Trade(), Decimal("100.01"), "RANGE", None,
        datetime(2026, 7, 31, 10, 30, tzinfo=TAIPEI),
    ) is None
    assert _exit_reason(
        Trade(), Decimal("100"), "RANGE", None,
        datetime(2026, 7, 31, 10, 30, tzinfo=TAIPEI),
    ) == "STOP_LOSS"


def test_super_ai_short_uses_hard_stop_loss() -> None:
    class Trade:
        side = "SHORT"
        stop_loss_price = Decimal("100")
        target_price_2 = Decimal("80")

    assert _exit_reason(
        Trade(), Decimal("99.99"), "RANGE", None,
        datetime(2026, 7, 31, 10, 30, tzinfo=TAIPEI),
    ) is None
    assert _exit_reason(
        Trade(), Decimal("100"), "RANGE", None,
        datetime(2026, 7, 31, 10, 30, tzinfo=TAIPEI),
    ) == "STOP_LOSS"


def test_super_ai_realtime_price_update_closes_trade_and_records_exit() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    now = datetime(2026, 7, 31, 10, 0, tzinfo=TAIPEI)
    trade = AdaptivePaperTrade(
        stock_code="2330",
        stock_name="TSMC",
        strategy_type="BREAKOUT",
        entry_signal_key="entry-2330",
        side="LONG",
        trade_mode="PAPER",
        quantity_shares=1000,
        entry_price=Decimal("100"),
        entry_time=now,
        entry_reason="test",
        stop_loss_price=Decimal("98"),
        target_price_1=Decimal("103"),
        target_price_2=Decimal("106"),
        last_price=Decimal("100"),
        ai_score=Decimal("88"),
        market_regime="BREAKOUT",
        sector_status="AI",
        initial_capital=Decimal("1000000"),
        risk_amount=Decimal("2000"),
        initial_r=Decimal("2"),
        entry_reasons_json="[]",
        exit_reasons_json="[]",
        status="open",
        created_at=now,
        updated_at=now,
    )

    with Session(engine) as db:
        settings = ensure_super_ai_settings(db, now)
        settings.max_capital = Decimal("1000000")
        settings.available_capital = Decimal("900000")
        db.add(trade)
        db.commit()
        trade_id = trade.id

        no_exit_signal = update_open_trade_from_market_price(
            db,
            trade_id=trade_id,
            price=Decimal("101"),
            at=now + timedelta(seconds=1),
            regime="RANGE",
        )
        db.commit()
        open_trade = db.get(AdaptivePaperTrade, trade_id)
        assert no_exit_signal is None
        assert open_trade is not None
        assert open_trade.status == "open"
        assert open_trade.last_price == Decimal("101")
        assert open_trade.unrealized_profit > 0
        assert open_trade.return_percentage > 0
        assert db.scalar(select(func.count(AdaptiveSignal.id))) == 0

        signal_key = update_open_trade_from_market_price(
            db,
            trade_id=trade_id,
            price=Decimal("98"),
            at=now + timedelta(seconds=5),
            regime="RANGE",
        )
        db.commit()

        closed_trade = db.get(AdaptivePaperTrade, trade_id)
        assert signal_key is not None
        assert closed_trade is not None
        assert closed_trade.status == "closed"
        assert closed_trade.exit_reason == "STOP_LOSS"
        assert closed_trade.exit_price == Decimal("98")
        assert closed_trade.unrealized_profit == Decimal("0")
        assert closed_trade.net_profit < 0
        assert db.scalar(select(func.count(AdaptiveSignal.id))) == 1
        assert db.scalar(select(func.count(SuperAIDaytradeNotification.id))) == 1


def test_super_ai_short_exits_when_market_recovers() -> None:
    class Trade:
        side = "SHORT"
        stop_loss_price = Decimal("110")
        target_price_2 = Decimal("90")

    assert _exit_reason(
        Trade(), Decimal("101"), "RECOVERY", None,
        datetime(2026, 7, 31, 10, 30, tzinfo=TAIPEI),
    ) == "MARKET_RISK"


def test_recovery_market_shows_no_short_weight() -> None:
    state = market_state("RECOVERY")
    assert state["longWeight"] == 100
    assert state["shortWeight"] == 0


def test_super_ai_breakout_uses_tactical_intraday_stop_when_structural_stop_is_too_wide() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    now = datetime(2026, 7, 31, 10, 0, tzinfo=TAIPEI)
    candidate = AdaptiveStockCandidate(
        trade_date=date(2026, 7, 31),
        stock_code="2330",
        stock_name="TSMC",
        market_type="TWSE",
        main_industry="Electronic",
        sub_industry="AI",
        strategy_type="BREAKOUT",
        total_score=Decimal("95"),
        technical_score=Decimal("35"),
        chip_score=Decimal("10"),
        fundamental_score=Decimal("5"),
        industry_score=Decimal("10"),
        market_score=Decimal("10"),
        health_score=Decimal("90"),
        previous_health_score=Decimal("88"),
        current_price=Decimal("100"),
        entry_price_low=Decimal("100"),
        entry_price_high=Decimal("101"),
        breakout_price=Decimal("100"),
        stop_loss_price=Decimal("92"),
        target_price_1=Decimal("105"),
        target_price_2=Decimal("110"),
        allocation_percent=Decimal("20"),
        relative_strength=Decimal("8"),
        volume_status="ok",
        industry_strength=Decimal("90"),
        false_breakout_risk=Decimal("0"),
        candidate_status="can_enter",
        rank=1,
        score_breakdown_json="{}",
        selected_reasons="[]",
        risk_reasons="[]",
        missing_data_json="[]",
        quote_source="TWSE MIS",
        quote_timestamp=now,
        created_at=now,
        updated_at=now,
    )

    with Session(engine) as db:
        settings = ensure_super_ai_settings(db, now)
        settings.max_capital = Decimal("3000000")
        settings.available_capital = Decimal("3000000")
        gate = trading_gate(db, settings, candidate, "BREAKOUT", now)

        assert gate["stopDistancePct"] <= gate["maxStopDistancePct"]
        assert gate["maxStopDistancePct"] == Decimal("1.0")
        assert gate["stop"] == Decimal("99.00")
        assert "stop_distance_too_wide" not in gate["failures"]
        assert "stop_distance_capped_to_1.00%" in gate["reasons"]

        candidate.stop_loss_price = Decimal("99.50")
        gate = trading_gate(db, settings, candidate, "BREAKOUT", now)
        assert gate["stop"] == Decimal("99.50")
        assert gate["stopDistancePct"] == Decimal("0.5000")
        assert "stop_distance_capped_to_1.00%" not in gate["reasons"]

        candidate.strategy_type = "CRASH"
        candidate.relative_strength = Decimal("-8")
        candidate.industry_strength = Decimal("20")
        candidate.candidate_status = "market_risk_high"
        candidate.stop_loss_price = Decimal("92")
        gate = trading_gate(db, settings, candidate, "CRASH", now)
        assert gate["side"] == "SHORT"
        assert gate["stop"] == Decimal("101.00")
        assert gate["stopDistancePct"] == Decimal("1.0000")
        assert "stop_distance_capped_to_1.00%" in gate["reasons"]


def test_super_ai_intraday_bull_breakout_bonus_allows_realtime_breakout_candidate() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    now = datetime(2026, 8, 26, 12, 15, tzinfo=TAIPEI)
    candidate = AdaptiveStockCandidate(
        trade_date=date(2026, 8, 26),
        stock_code="3504",
        stock_name="揚明光",
        market_type="TWSE",
        main_industry="Optical",
        sub_industry="光電",
        strategy_type="BREAKOUT",
        total_score=Decimal("69.66"),
        technical_score=Decimal("54"),
        chip_score=Decimal("0"),
        fundamental_score=Decimal("0"),
        industry_score=Decimal("3.66"),
        market_score=Decimal("6.67"),
        health_score=Decimal("60.49"),
        previous_health_score=None,
        current_price=Decimal("85.50"),
        entry_price_low=Decimal("83.50"),
        entry_price_high=Decimal("85.17"),
        breakout_price=Decimal("83.50"),
        stop_loss_price=Decimal("80.16"),
        target_price_1=Decimal("92.69"),
        target_price_2=Decimal("97.70"),
        allocation_percent=Decimal("0"),
        relative_strength=Decimal("27.65"),
        volume_status="量縮整理",
        industry_strength=Decimal("36.60"),
        false_breakout_risk=Decimal("0"),
        candidate_status="waiting_confirmation",
        rank=1,
        score_breakdown_json="{}",
        selected_reasons="[]",
        risk_reasons="[]",
        missing_data_json="[]",
        quote_source="TWSE MIS 五檔參考價",
        quote_timestamp=now,
        created_at=now,
        updated_at=now,
    )

    with Session(engine) as db:
        settings = ensure_super_ai_settings(db, now)
        settings.max_capital = Decimal("3000000")
        settings.available_capital = Decimal("3000000")
        settings.min_ai_score_to_trade = Decimal("80")
        gate = trading_gate(db, settings, candidate, "BREAKOUT", now)

        assert gate["allowed"], gate["failures"]
        assert gate["aiScore"] >= Decimal("80")
        assert "intraday_bull_breakout_bonus=+8" in gate["reasons"]
        assert "stop_distance_capped_to_1.00%" in gate["reasons"]


def test_super_ai_intraday_bull_breakout_bonus_requires_realtime_quote() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    now = datetime(2026, 8, 26, 12, 15, tzinfo=TAIPEI)
    candidate = AdaptiveStockCandidate(
        trade_date=date(2026, 8, 26),
        stock_code="3504",
        stock_name="揚明光",
        market_type="TWSE",
        main_industry="Optical",
        sub_industry="光電",
        strategy_type="BREAKOUT",
        total_score=Decimal("69.66"),
        technical_score=Decimal("54"),
        chip_score=Decimal("0"),
        fundamental_score=Decimal("0"),
        industry_score=Decimal("3.66"),
        market_score=Decimal("6.67"),
        health_score=Decimal("60.49"),
        previous_health_score=None,
        current_price=Decimal("85.50"),
        entry_price_low=Decimal("83.50"),
        entry_price_high=Decimal("85.17"),
        breakout_price=Decimal("83.50"),
        stop_loss_price=Decimal("80.16"),
        target_price_1=Decimal("92.69"),
        target_price_2=Decimal("97.70"),
        allocation_percent=Decimal("0"),
        relative_strength=Decimal("27.65"),
        volume_status="量縮整理",
        industry_strength=Decimal("36.60"),
        false_breakout_risk=Decimal("0"),
        candidate_status="waiting_confirmation",
        rank=1,
        score_breakdown_json="{}",
        selected_reasons="[]",
        risk_reasons="[]",
        missing_data_json="[]",
        quote_source="Yahoo Finance fallback",
        quote_timestamp=now,
        created_at=now,
        updated_at=now,
    )

    with Session(engine) as db:
        settings = ensure_super_ai_settings(db, now)
        settings.max_capital = Decimal("3000000")
        settings.available_capital = Decimal("3000000")
        settings.min_ai_score_to_trade = Decimal("80")
        gate = trading_gate(db, settings, candidate, "BREAKOUT", now)

        assert not gate["allowed"]
        assert "delayed_quote" in gate["failures"]
        assert "intraday_bull_breakout_bonus=+8" not in gate["reasons"]


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


def test_super_ai_exit_restores_reserved_capital_after_net_loss() -> None:
    class Settings:
        available_capital = Decimal("1844940")

    class Trade:
        entry_price = Decimal("100")
        quantity_shares = 1000

    _release_reserved_capital(Settings, Trade(), Decimal("-8000"))

    assert Settings.available_capital == Decimal("1936940.00")


def test_super_ai_blocked_entry_does_not_create_watch_notification() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    now = datetime(2026, 7, 31, 10, 0, tzinfo=TAIPEI)
    payload = AdaptiveScanPayload(
        market=market(updated_at=now, market_open=True),
        industries=[],
        stocks=[],
    )
    candidate = AdaptiveStockCandidate(
        trade_date=payload.market.trade_date,
        stock_code="2330",
        stock_name="TSMC",
        market_type="TWSE",
        main_industry="Electronic",
        sub_industry="AI",
        strategy_type="BREAKOUT",
        total_score=Decimal("92"),
        technical_score=Decimal("30"),
        chip_score=Decimal("15"),
        fundamental_score=Decimal("15"),
        industry_score=Decimal("15"),
        market_score=Decimal("15"),
        health_score=Decimal("88"),
        previous_health_score=Decimal("86"),
        current_price=Decimal("100"),
        entry_price_low=Decimal("100"),
        entry_price_high=Decimal("101"),
        breakout_price=Decimal("100"),
        stop_loss_price=Decimal("98"),
        target_price_1=Decimal("103"),
        target_price_2=Decimal("105"),
        allocation_percent=Decimal("10"),
        relative_strength=Decimal("4"),
        volume_status="ok",
        industry_strength=Decimal("80"),
        false_breakout_risk=Decimal("10"),
        candidate_status="can_enter",
        rank=1,
        score_breakdown_json="{}",
        selected_reasons="[]",
        risk_reasons="[]",
        missing_data_json="[]",
        quote_source="Yahoo Finance fallback",
        quote_timestamp=now,
        created_at=now,
        updated_at=now,
    )
    signal = AdaptiveSignal(
        signal_key="blocked-watch-test",
        stock_code="2330",
        stock_name="TSMC",
        signal_type="new_top5",
        action="WATCH",
        strategy_type="BREAKOUT",
        price=Decimal("100"),
        health_score=Decimal("88"),
        reasons_json="[]",
        line_push_status="pending",
        created_at=now,
    )

    with Session(engine) as db:
        db.add_all([candidate, signal])
        db.commit()
        update_adaptive_paper_trades(db, payload, [candidate], [signal], "BREAKOUT")
        db.commit()

        assert db.scalar(select(func.count(SuperAIDaytradeNotification.id))) == 0


def test_super_ai_pending_mail_requires_matching_trade_record() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    now = datetime(2026, 7, 31, 10, 0, tzinfo=TAIPEI)
    entry_signal = AdaptiveSignal(
        signal_key="entry-with-trade",
        stock_code="2330",
        stock_name="TSMC",
        signal_type="entry_confirmed",
        action="BUY",
        strategy_type="BREAKOUT",
        price=Decimal("100"),
        health_score=Decimal("88"),
        reasons_json="[]",
        line_push_status="pending",
        created_at=now,
    )
    watch_signal = AdaptiveSignal(
        signal_key="watch-without-trade",
        stock_code="2330",
        stock_name="TSMC",
        signal_type="new_top5",
        action="WATCH",
        strategy_type="BREAKOUT",
        price=Decimal("100"),
        health_score=Decimal("88"),
        reasons_json="[]",
        line_push_status="pending",
        created_at=now,
    )
    trade = AdaptivePaperTrade(
        stock_code="2330",
        stock_name="TSMC",
        strategy_type="BREAKOUT",
        entry_signal_key="entry-with-trade",
        side="LONG",
        trade_mode="PAPER",
        quantity_shares=1000,
        entry_price=Decimal("100"),
        entry_time=now,
        entry_reason="test",
        stop_loss_price=Decimal("98"),
        target_price_1=Decimal("103"),
        target_price_2=Decimal("105"),
        last_price=Decimal("100"),
        ai_score=Decimal("88"),
        market_regime="BREAKOUT",
        sector_status="AI",
        initial_capital=Decimal("5000000"),
        risk_amount=Decimal("2000"),
        initial_r=Decimal("2"),
        entry_reasons_json="[]",
        exit_reasons_json="[]",
        status="open",
        created_at=now,
        updated_at=now,
    )

    with Session(engine) as db:
        db.add_all([entry_signal, watch_signal, trade])
        db.commit()

        assert _signal_has_super_ai_trade_record(db, entry_signal) is True
        assert _signal_has_super_ai_trade_record(db, watch_signal) is False


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
