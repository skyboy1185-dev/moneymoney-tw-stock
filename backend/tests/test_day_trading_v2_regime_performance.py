from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.day_trading_v2_models import DayTradeV2Trade
from app.services.day_trading_v2_controller import REGIME_RANGE, REGIME_STRONG
from app.services.day_trading_v2_regime_performance import aggregate_regime_performance


def trade(strategy: str, regime: str, pnl: int, index: int, cost: int = 10) -> dict[str, object]:
    return {
        "id": f"{strategy}-{regime}-{index}", "symbol": "2330", "strategyId": strategy,
        "marketRegime": regime, "entryTime": datetime(2026, 8, 1, tzinfo=UTC) + timedelta(minutes=index),
        "exitTime": datetime(2026, 8, 1, tzinfo=UTC) + timedelta(minutes=index + 1),
        "grossPnl": pnl + cost, "cost": cost, "netPnl": pnl,
    }


def test_regime_matrix_separates_profit_loss_cost_and_best_fit():
    records = []
    records.extend(trade("OPENING_RANGE_BREAKOUT", REGIME_STRONG, 200, index) for index in range(15))
    records.extend(trade("OPENING_RANGE_BREAKOUT", REGIME_STRONG, -100, index + 15) for index in range(5))
    records.extend(trade("VWAP_TREND_PULLBACK", REGIME_STRONG, 100, index) for index in range(18))
    records.extend(trade("VWAP_TREND_PULLBACK", REGIME_STRONG, -100, index + 18) for index in range(2))
    records.extend(trade("FALSE_BREAKDOWN_REVERSAL", REGIME_RANGE, -150, index) for index in range(20))

    result = aggregate_regime_performance(records, minimum_sample=20)
    opening = next(row for row in result["rows"] if row["strategyId"] == "OPENING_RANGE_BREAKOUT" and row["marketRegime"] == REGIME_STRONG)
    assert opening["tradeCount"] == 20
    assert opening["totalProfit"] == "3000.00"
    assert opening["totalLoss"] == "500.00"
    assert opening["totalCost"] == "200.00"
    assert opening["netPnl"] == "2500.00"
    assert opening["expectancy"] == "125.00"
    assert next(item for item in result["bestByRegime"] if item["marketRegime"] == REGIME_STRONG)["best"]["strategyId"] == "OPENING_RANGE_BREAKOUT"
    assert result["mostProfitable"]["strategyId"] == "OPENING_RANGE_BREAKOUT"
    assert result["largestLoss"]["strategyId"] == "FALSE_BREAKDOWN_REVERSAL"


def test_regime_best_fit_is_not_declared_when_sample_is_insufficient():
    result = aggregate_regime_performance(
        [trade("VWAP_TREND_PULLBACK", REGIME_RANGE, 500, index) for index in range(19)],
        minimum_sample=20,
    )
    row = next(item for item in result["rows"] if item["strategyId"] == "VWAP_TREND_PULLBACK" and item["marketRegime"] == REGIME_RANGE)
    assert row["sampleSufficient"] is False
    assert row["suitability"] == "INSUFFICIENT"
    assert next(item for item in result["bestByRegime"] if item["marketRegime"] == REGIME_RANGE)["best"] is None


def test_unknown_regime_is_excluded_and_coverage_is_disclosed():
    result = aggregate_regime_performance([
        trade("OPENING_RANGE_BREAKOUT", REGIME_STRONG, 100, 1),
        trade("OPENING_RANGE_BREAKOUT", "UNKNOWN", 99999, 2),
    ])
    assert result["coverage"] == {"totalTrades": 2, "classifiedTrades": 1, "unknownTrades": 1, "classifiedPct": "50.0"}
    assert result["mostProfitable"]["netPnl"] == "100.00"


def test_paper_trade_keeps_entry_regime_through_close(monkeypatch):
    from app.routers import day_trading_v2 as router

    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    entry_at = datetime(2026, 9, 7, 1, 30, tzinfo=UTC)
    monkeypatch.setattr(router, "_now", lambda: entry_at)
    with sessions() as db:
        position = router.paper_entry(router.PaperEntryBody(
            strategy_id="OPENING_RANGE_BREAKOUT", symbol="2330", stock_name="台積電",
            signal_price=Decimal("100"), fill_price=Decimal("100"), stop_price=Decimal("98"),
            target_price=Decimal("104"), confidence=Decimal("90"), signal_time=entry_at,
            reasons=["突破確認"], sector="半導體", market_regime=REGIME_STRONG,
            market_context={"marketRegimeConfidence": "88"},
        ), "regime-user", db)
        router.close_position(str(position["id"]), router.CloseBody(fill_price=Decimal("104"), reason="停利"), "regime-user", db)
        saved = db.scalar(select(DayTradeV2Trade))
        assert saved.entry_market_regime == REGIME_STRONG
        assert REGIME_STRONG in saved.market_context_json
        assert '"marketRegimeConfidence": "88"' in saved.market_context_json
        response = router.performance_by_market_regime("PAPER", "ALL", "", "CHALLENGER", "regime-user", db)
        assert response["coverage"]["classifiedTrades"] == 1
        cell = next(row for row in response["rows"] if row["strategyId"] == "OPENING_RANGE_BREAKOUT" and row["marketRegime"] == REGIME_STRONG)
        assert cell["tradeCount"] == 1
        assert cell["trades"][0]["symbol"] == "2330"
