# pyright: reportArgumentType=false

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.database import get_db
from app.routers.strong_stock import router as strong_stock_router
from app.strong_stock_models import StrongStockAccount, StrongStockOrder, StrongStockPosition, StrongStockRanking
from app.services.strong_stock import (
    DEFAULT_CONFIG, calculate_position_quantity, classify_market, commission,
    ensure_defaults, fill_pending_orders, industry_score, merged_config,
    percentile_scores, queue_paper_orders, score_stock,
)


def market(**changes):
    values = dict(
        official_data=True, taiex_above_ma20=True, taiex_above_ma60=True,
        ma20_slope=1.0, ma60_slope=.5, advance_ratio=62,
        new_high_20d_ratio=15,
    )
    values.update(changes)
    return SimpleNamespace(**values)


def stock(index: int = 0, **changes):
    price = 100 + index
    values = dict(
        stock_code=f"{2300 + index}", stock_name=f"測試{index}", market_type="上市",
        sub_industry="半導體", price=price, data_completeness=1,
        return_1d=1, return_5d=5 + index, return_20d=12 + index,
        average_turnover_20d=200_000_000, has_recent_trade=True,
        is_full_delivery=False, is_alternate_trading=False, is_disposed=False,
        is_suspended=False, is_delisted=False, abnormal_trading=False,
        ma20=95, ma60=90, ma20_slope=1, ma60_slope=.5, atr14=3,
        atr20_ratio=3, breakout_20d=True, breakout_60d=False,
        range_high=100, volume_ratio_20d=1.8, volume_contracting=False,
        bottom_reversal_candle=False, higher_low=True, upper_shadow_ratio=.1,
        foreign_net_5d=100, trust_net_5d=50, revenue_yoy=15,
        revenue_3m_yoy=12, trailing_eps=5, gross_margin_change=1,
        operating_margin_change=1, fundamental_risk=False,
        distance_to_high_percent=5,
    )
    values.update(changes)
    return SimpleNamespace(**values)


def test_market_regimes_cover_bull_range_and_weak() -> None:
    config = merged_config()
    assert classify_market(market(), config).regime == "STRONG_BULL"
    assert classify_market(market(taiex_above_ma20=False, ma20_slope=0, advance_ratio=50), config).regime == "MILD_BULL"
    assert classify_market(market(taiex_above_ma60=False, ma20_slope=-1, ma60_slope=-.5, advance_ratio=35), config).regime == "WEAK"
    assert classify_market(market(taiex_above_ma60=False, ma20_slope=.1, ma60_slope=-.1, advance_ratio=50), config).regime == "RANGE"


def test_relative_strength_is_market_percentile_not_positive_return_flag() -> None:
    rows = [stock(index) for index in range(5)]
    scores = percentile_scores(rows)
    assert scores[rows[-1].stock_code] == Decimal("100.0")
    assert scores[rows[0].stock_code] == Decimal("0.0")


def test_strong_candidate_requires_entry_point_and_complete_fundamentals() -> None:
    decision = classify_market(market(), merged_config())
    result = score_stock(stock(), Decimal("95"), Decimal("90"), decision, merged_config(), True)
    assert result.entry_type == "BREAKOUT"
    assert result.status == "ENTRY_READY"
    incomplete = score_stock(stock(trailing_eps=None, revenue_yoy=None, revenue_3m_yoy=None), Decimal("95"), Decimal("90"), decision, merged_config(), True)
    assert incomplete.status == "DATA_INSUFFICIENT"
    assert "基本面或歷史資料完整度不足" in incomplete.blocked_reasons


def test_chasing_and_down_averaging_are_not_entry_signals() -> None:
    decision = classify_market(market(), merged_config())
    chased = score_stock(stock(return_1d=7, price=115), Decimal("95"), Decimal("90"), decision, merged_config(), True)
    assert chased.status != "ENTRY_READY"
    assert "單日漲幅超過禁止追高門檻" in chased.blocked_reasons
    falling = score_stock(stock(breakout_20d=False, higher_low=False, volume_contracting=False), Decimal("95"), Decimal("90"), decision, merged_config(), True)
    assert falling.entry_type == "WATCH"


def test_position_size_obeys_risk_capital_and_board_lot() -> None:
    assert calculate_position_quantity(Decimal("300000"), Decimal("100"), Decimal("95"), Decimal("15000")) == 3000
    assert calculate_position_quantity(Decimal("300000"), Decimal("100"), Decimal("95"), Decimal("10000")) == 2000
    assert calculate_position_quantity(Decimal("90000"), Decimal("123"), Decimal("118"), Decimal("15000"), allow_odd_lots=True) == 731
    assert calculate_position_quantity(Decimal("90000"), Decimal("123"), Decimal("118"), Decimal("15000"), allow_odd_lots=False) == 0


def test_regular_stock_cost_uses_discount_and_point_three_percent_tax() -> None:
    config = merged_config()
    assert commission(Decimal("100000"), config) == Decimal("28.50")
    assert Decimal(str(config["taxRate"])) == Decimal("0.003")


def test_industry_strength_rewards_relative_performance_and_breadth() -> None:
    weak = SimpleNamespace(return_5d=-5, return_20d=-10, relative_taiex=-8, advance_ratio=30, new_high_ratio=0, volume_growth=0, continuation_days=0)
    strong = SimpleNamespace(return_5d=8, return_20d=15, relative_taiex=12, advance_ratio=70, new_high_ratio=20, volume_growth=1.5, continuation_days=4)
    assert industry_score(strong)[0] > industry_score(weak)[0]


def test_pending_or_open_symbol_cannot_consume_cash_twice() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    signal_date = date(2026, 9, 7)
    with Session(engine, expire_on_commit=False) as db:
        _setting, account, _config = ensure_defaults(db, "strong-test-user")
        ranking = StrongStockRanking(
            trade_date=signal_date, symbol="2330", name="台積電", market="上市", industry="半導體",
            rank=1, total_score=Decimal("90"), relative_strength_score=Decimal("24"),
            trend_score=Decimal("20"), industry_score=Decimal("14"), volume_chip_score=Decimal("12"),
            fundamental_score=Decimal("12"), valuation_risk_score=Decimal("8"), data_completeness=Decimal("1"),
            close_price=Decimal("100"), entry_low=Decimal("99"), entry_high=Decimal("101"),
            stop_price=Decimal("95"), add_price=Decimal("105"), risk_reward=Decimal("2"),
            suggested_capital=Decimal("300000"), status="ENTRY_READY", entry_type="BREAKOUT",
            reasons_json='["測試訊號"]', blocked_reasons_json="[]", score_details_json="{}",
            source_snapshot_json="{}", strategy_version="1.0.0",
        )
        db.add(ranking); db.commit()
        assert queue_paper_orders(db, "strong-test-user", signal_date) == 1
        assert queue_paper_orders(db, "strong-test-user", signal_date) == 0
        pending = db.scalar(select(StrongStockOrder).where(StrongStockOrder.user_id == "strong-test-user"))
        assert pending is not None
        position = StrongStockPosition(
            id=str(uuid4()), user_id="strong-test-user", symbol="2330", name="台積電", industry="半導體",
            quantity=100, average_cost=Decimal("100"), current_price=Decimal("100"),
            initial_stop=Decimal("95"), trailing_stop=Decimal("95"), next_add_price=Decimal("105"),
            invested_capital=Decimal("10000"), initial_risk=Decimal("500"), current_score=Decimal("90"),
            entry_type="BREAKOUT", strategy_version="1.0.0", entry_at=datetime(2026, 9, 8, 1, 0, tzinfo=UTC),
        )
        db.add(position); db.commit()
        cash_before = account.cash
        result = fill_pending_orders(db, "strong-test-user", {"2330": Decimal("100")}, datetime(2026, 9, 8, 1, 1, tzinfo=UTC))
        assert result["filled"] == 0
        assert pending.status == "REJECTED_DUPLICATE_POSITION"
        assert db.get(StrongStockAccount, "strong-test-user").cash == cash_before


def test_dashboard_uses_independent_paper_ledger_and_backtest_refuses_missing_point_in_time_data() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    app = FastAPI()
    app.include_router(strong_stock_router, prefix="/api/v1")

    def session_dependency():
        with Session(engine, expire_on_commit=False) as db:
            yield db

    app.dependency_overrides[get_db] = session_dependency
    client = TestClient(app)
    headers = {"X-User-Id": "strong-api-user"}
    dashboard = client.get("/api/v1/strong-stocks/dashboard", headers=headers)
    assert dashboard.status_code == 200
    assert dashboard.json()["mode"] == "PAPER"
    assert dashboard.json()["liveTradingAvailable"] is False
    assert Decimal(dashboard.json()["performance"]["initialCapital"]) == Decimal("3000000")
    backtest = client.post("/api/v1/strong-stocks/backtests", headers=headers, json={
        "start_date": "2025-01-01", "end_date": "2026-01-01", "benchmark": "0050",
    })
    assert backtest.status_code == 200
    assert backtest.json()["status"] == "DATA_INSUFFICIENT"
    invalid = client.patch("/api/v1/strong-stocks/settings", headers=headers, json={
        "paper_enabled": True, "config": {"minimumEntryScore": "65", "minimumWatchScore": "70"},
    })
    assert invalid.status_code == 422
