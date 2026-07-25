from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import AIStockPosition, PortfolioSettings
from app.services.ai_stock_service import (
    calculate_position_allocation,
    evaluate_position_action,
    quote_is_fresh,
)


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


def test_position_schema_can_persist_across_sessions() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(PortfolioSettings(user_id="persist-user", updated_at=datetime.now(TAIPEI)))
        db.commit()
    with Session(engine) as restored:
        item = restored.query(PortfolioSettings).filter_by(user_id="persist-user").one()
        assert item.total_capital == Decimal("1000000.00")
