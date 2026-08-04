from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import AIStockMonitor, AIStockPosition, PortfolioSettings
from app.services.ai_stock_service import (
    calculate_position_allocation,
    confirm_entry,
    evaluate_position_action,
    monitor_entry_failures,
    position_risk_failures,
    quote_is_fresh,
    sync_recommendations,
    update_position_quote,
)
from app.schemas import AIRecommendationSyncItem
from app.services.official_market_data import OfficialStockQuote


TAIPEI = ZoneInfo("Asia/Taipei")


def test_decimal_allocation_respects_risk_and_position_limits() -> None:
    settings = PortfolioSettings(
        user_id="test-user",
        total_capital=Decimal("1000000"),
        max_total_exposure=Decimal("85"),
        max_position_percentage=Decimal("20"),
        max_industry_percentage=Decimal("35"),
        max_risk_per_trade=Decimal("0.5"),
        initial_entry_ratio=Decimal("40"),
        first_add_on_ratio=Decimal("30"),
        second_add_on_ratio=Decimal("30"),
        updated_at=datetime.now(TAIPEI),
    )
    result = calculate_position_allocation(
        settings, entry_price=Decimal("100"), stop_loss=Decimal("95"),
        score=Decimal("90"), strategy_fit=Decimal("85"), health_score=Decimal("80"),
    )
    assert result["quantity"] == 1000
    assert result["initial_quantity"] == 400
    assert result["estimated_risk"] == Decimal("2000.00")
    assert isinstance(result["initial_amount"], Decimal)


def test_quote_must_be_current_taipei_trading_session() -> None:
    now = datetime(2026, 7, 27, 10, 0, tzinfo=TAIPEI)
    assert quote_is_fresh(datetime(2026, 7, 27, 9, 59, 30, tzinfo=TAIPEI), now)
    assert not quote_is_fresh(datetime(2026, 7, 24, 13, 30, tzinfo=TAIPEI), now)
    assert not quote_is_fresh(datetime(2026, 7, 27, 10, 0, tzinfo=TAIPEI), datetime(2026, 7, 27, 15, 0, tzinfo=TAIPEI))


def _monitor(status: str = "buy_confirmed") -> AIStockMonitor:
    created_at = datetime(2026, 7, 27, 9, 55, tzinfo=TAIPEI)
    return AIStockMonitor(
        user_id="test-user",
        symbol="2330",
        stock_name="台積電",
        market="上市",
        industry="半導體",
        strategy_name="波段起漲 Bot",
        secondary_strategies_json='["多頭回檔 Bot"]',
        signal_id="test-signal-2330",
        monitor_status=status,
        total_score=Decimal("88"),
        strategy_fit=Decimal("84"),
        market_fit=Decimal("76"),
        health_score=Decimal("82"),
        current_price=Decimal("100"),
        entry_min=Decimal("99"),
        entry_max=Decimal("101"),
        stop_loss=Decimal("95"),
        target_1=Decimal("110"),
        target_2=Decimal("118"),
        risk_reward_ratio=Decimal("2"),
        target_allocation_percentage=Decimal("20"),
        initial_allocation_percentage=Decimal("8"),
        first_add_on_percentage=Decimal("6"),
        second_add_on_percentage=Decimal("6"),
        suggested_initial_amount=Decimal("80000"),
        suggested_initial_quantity=800,
        estimated_risk_amount=Decimal("4000"),
        reasons_json='["站上 MA20", "MACD 翻紅", "成交量增加"]',
        warnings_json="[]",
        quote_source="TWSE MIS",
        quote_timestamp=created_at,
        created_at=created_at,
        updated_at=created_at,
        expired_at=datetime(2026, 7, 27, 10, 10, tzinfo=TAIPEI),
    )


def test_entry_gate_requires_fresh_quote_spread_and_entry_zone() -> None:
    now = datetime(2026, 7, 27, 10, 0, tzinfo=TAIPEI)
    quote = OfficialStockQuote(
        symbol="2330",
        name="台積電",
        price=100,
        previous_close=98,
        open=99,
        high=101,
        low=98,
        volume=2_000_000,
        change=2,
        change_percent=2.04,
        quote_timestamp=now.isoformat(),
        source="TWSE MIS",
        is_realtime=True,
        best_bid=99.9,
        best_ask=100,
    )
    assert monitor_entry_failures(_monitor(), quote, now) == []

    stale_and_overpriced = OfficialStockQuote(
        **{
            **quote.__dict__,
            "price": 103,
            "quote_timestamp": datetime(2026, 7, 24, 13, 30, tzinfo=TAIPEI).isoformat(),
            "is_realtime": False,
            "best_bid": None,
            "best_ask": None,
        }
    )
    failures = monitor_entry_failures(_monitor(), stale_and_overpriced, now)
    assert any("行情時間過期" in item for item in failures)
    assert any("缺少即時買賣價差" in item for item in failures)
    assert any("禁止追價" in item for item in failures)


def test_confirm_entry_cannot_bypass_buy_confirmation() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(PortfolioSettings(user_id="test-user", updated_at=datetime.now(TAIPEI)))
        db.add(_monitor(status="monitoring"))
        db.commit()
        monitor = db.query(AIStockMonitor).filter_by(user_id="test-user").one()
        with pytest.raises(ValueError, match="尚未形成買進確認"):
            confirm_entry(
                db,
                "test-user",
                monitor.id,
                entry_price=Decimal("100"),
                quantity=100,
                entry_time=datetime.now(TAIPEI),
                custom_stop_loss=None,
                line_exit_notifications=True,
                add_on_enabled=True,
            )


def test_position_risk_limits_are_hard_failures() -> None:
    settings = PortfolioSettings(
        user_id="risk-user",
        total_capital=Decimal("1000000"),
        max_total_exposure=Decimal("80"),
        max_position_percentage=Decimal("20"),
        max_industry_percentage=Decimal("35"),
        max_risk_per_trade=Decimal("0.5"),
        max_portfolio_risk=Decimal("3"),
        updated_at=datetime.now(TAIPEI),
    )
    failures = position_risk_failures(
        settings,
        invested_amount=Decimal("250000"),
        estimated_risk=Decimal("6000"),
        total_invested_after=Decimal("850000"),
        industry_invested_after=Decimal("400000"),
        total_risk_after=Decimal("35000"),
    )
    assert len(failures) == 5
    assert "超過單檔最大資金占比" in failures
    assert "超過整體持倉最大風險" in failures


def _position() -> AIStockPosition:
    return AIStockPosition(
        user_id="test-user", monitor_id=1, symbol="2330", stock_name="台積電",
        industry="半導體", direction="long", strategy_name="波段起漲 Bot",
        entry_price=Decimal("100"), average_cost=Decimal("100"),
        original_quantity=1000, remaining_quantity=1000,
        entry_time=datetime.now(TAIPEI), stop_loss=Decimal("95"),
        target_1=Decimal("110"), target_2=Decimal("120"),
        current_price=Decimal("100"), highest_price=Decimal("100"),
        lowest_price=Decimal("100"), health_score=Decimal("80"),
        created_at=datetime.now(TAIPEI), updated_at=datetime.now(TAIPEI),
    )


def test_stop_loss_has_priority_and_invalid_quote_does_not_fake_sell() -> None:
    position = _position()
    assert evaluate_position_action(position, Decimal("94"), quote_valid=True)[0] == "立即停損"
    assert evaluate_position_action(position, Decimal("94"), quote_valid=False)[0] == "資料異常"
    assert evaluate_position_action(position, Decimal("110"), quote_valid=True)[0] == "建議減碼 50%"
    assert evaluate_position_action(position, Decimal("120"), quote_valid=True)[0] == "建議全部賣出"


def test_overnight_position_returns_to_active_monitoring_with_fresh_quote() -> None:
    position = _position()
    position.overnight_status = False
    position.position_status = "overnight"
    action, _ = update_position_quote(position, Decimal("102"), quote_valid=True)
    assert action == "續抱"
    assert position.position_status == "continue_holding"


def test_position_schema_can_persist_across_sessions() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(PortfolioSettings(user_id="persist-user", updated_at=datetime.now(TAIPEI)))
        db.commit()
    with Session(engine) as restored:
        item = restored.query(PortfolioSettings).filter_by(user_id="persist-user").one()
        assert item.total_capital == Decimal("1000000.00")


def _recommendation(signal_id: str, symbol: str) -> AIRecommendationSyncItem:
    return AIRecommendationSyncItem(
        signal_id=signal_id,
        symbol=symbol,
        stock_name=f"測試{symbol}",
        market="上市",
        industry="半導體",
        strategy_name="波段起漲 Bot",
        secondary_strategies=["多頭回檔 Bot"],
        total_score=Decimal("88"),
        strategy_fit=Decimal("84"),
        market_fit=Decimal("76"),
        health_score=Decimal("82"),
        current_price=Decimal("100"),
        entry_min=Decimal("99"),
        entry_max=Decimal("101"),
        stop_loss=Decimal("95"),
        target_1=Decimal("110"),
        target_2=Decimal("118"),
        risk_reward_ratio=Decimal("2"),
        reasons=["站上 MA20", "MACD 翻紅", "成交量增加"],
        warnings=[],
        quote_source="TWSE MIS",
        quote_timestamp=datetime(2026, 7, 27, 10, 0, tzinfo=TAIPEI),
        expired_at=datetime(2026, 7, 27, 10, 20, tzinfo=TAIPEI),
    )


def test_recommendation_replacement_expires_old_waiting_item_immediately() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime(2026, 7, 27, 10, 0, 30, tzinfo=TAIPEI)
    with Session(engine) as db:
        sync_recommendations(
            db,
            "replace-user",
            [_recommendation("signal-old-2330", "2330")],
            now,
        )
        active = sync_recommendations(
            db,
            "replace-user",
            [_recommendation("signal-new-2317", "2317")],
            now,
        )
        assert [item.symbol for item in active] == ["2317"]
        old = db.query(AIStockMonitor).filter_by(signal_id="signal-old-2330").one()
        assert old.monitor_status == "expired"


def test_ai_monitor_sync_rejects_non_theme_symbols() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime(2026, 7, 27, 10, 0, 30, tzinfo=TAIPEI)
    with Session(engine) as db:
        active = sync_recommendations(
            db,
            "theme-user",
            [_recommendation("signal-unrelated-2603", "2603")],
            now,
        )
        assert active == []
        assert db.query(AIStockMonitor).count() == 0
