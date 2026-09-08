from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, create_engine
from sqlalchemy.orm import Session

from app.day_trading_v2_models import DayTradeV2CandidateState, DayTradeV2Position, DayTradeV2RuntimeState, DayTradeV2Setting
from app.models import DayTradingPosition
from app.services.day_trading_quote_targets import PriorityCandidate, QuoteTargetInputs, QuoteTargetSelector, read_quote_target_inputs
from app.services.theme_stock_universe import ThemeStock

NOW = datetime(2026, 9, 8, 2, tzinfo=UTC)
TODAY = date(2026, 9, 8)


def stock(symbol: str, market: str = "上市") -> ThemeStock:
    return ThemeStock(symbol, f"Stock {symbol}", market, "test", ())


def seed(db: Session, model, **overrides):
    values = {}
    for column in model.__table__.columns:
        if column.nullable or column.default is not None or column.server_default is not None or column.primary_key and isinstance(column.type, Integer):
            continue
        kind = column.type
        values[column.name] = False if isinstance(kind, Boolean) else NOW if isinstance(kind, DateTime) else TODAY if isinstance(kind, Date) else 1 if isinstance(kind, (Integer, Numeric)) else ""
    values.update(overrides)
    db.execute(model.__table__.insert().values(**values))


def test_database_filters_active_paper_today_recent_and_top_ten_per_user_without_writes():
    engine = create_engine("sqlite://")
    tables = (DayTradingPosition, DayTradeV2Position, DayTradeV2Setting, DayTradeV2RuntimeState, DayTradeV2CandidateState)
    for model in tables:
        model.__table__.create(engine)
    with Session(engine) as db:
        for user, mode, status in (("a", "PAPER", "RUNNING"), ("b", "PAPER", "PAUSED"), ("stopped", "PAPER", "STOPPED"), ("backtest", "BACKTEST", "RUNNING")):
            seed(db, DayTradeV2Setting, user_id=user, trade_mode=mode)
            seed(db, DayTradeV2RuntimeState, user_id=user, mode="PAPER", trading_date=TODAY, status=status)
        for index in range(11):
            seed(db, DayTradeV2CandidateState, user_id="a", mode="PAPER", symbol=str(9000 + index), confidence=100 - index, trading_date=TODAY, scanned_at=NOW)
        for user, symbol, mode, day, scanned_at in (
            ("b", "9100", "PAPER", TODAY, NOW - timedelta(seconds=120)),
            ("a", "9101", "PAPER", TODAY, NOW - timedelta(seconds=121)),
            ("a", "9102", "PAPER", TODAY, NOW + timedelta(seconds=1)),
            ("a", "9103", "BACKTEST", TODAY, NOW),
            ("a", "9104", "PAPER", TODAY - timedelta(days=1), NOW),
            ("stopped", "9105", "PAPER", TODAY, NOW),
            ("backtest", "9106", "PAPER", TODAY, NOW),
        ):
            seed(db, DayTradeV2CandidateState, user_id=user, mode=mode, symbol=symbol, confidence=200, trading_date=day, scanned_at=scanned_at)
        seed(db, DayTradingPosition, user_id="old-auto", symbol="9200", status="open")
        seed(db, DayTradingPosition, user_id="old-auto", symbol="9201", status="closed")
        seed(db, DayTradeV2Position, id="paper-held", user_id="stopped", symbol="9202", mode="PAPER", status="OPEN")
        seed(db, DayTradeV2Position, id="bt-held", user_id="backtest", symbol="9203", mode="BACKTEST", status="OPEN")
        seed(db, DayTradeV2Position, id="closed", user_id="a", symbol="9204", mode="PAPER", status="CLOSED")
        db.commit()
        before = db.connection().exec_driver_sql("SELECT total_changes()").scalar()
        inputs = read_quote_target_inputs(db, NOW, ("old-auto",))
        assert {row.symbol for row in inputs.candidates} == {*map(str, range(9000, 9010)), "9100"}
        assert inputs.held_symbols == {"9200", "9202"}
        assert inputs.legacy_open_count == inputs.automatic_open_count == 1
        assert db.connection().exec_driver_sql("SELECT total_changes()").scalar() == before
    engine.dispose()


def test_held_metadata_survives_universe_removal_and_unknown_exchange_is_not_guessed():
    selector = QuoteTargetSelector()
    inputs = QuoteTargetInputs(frozenset({"9901", "9902"}), ())
    selector.select((stock("9901", "上櫃"),), inputs, ())
    targets = selector.select((), inputs, ())
    assert [(item.symbol, item.market) for item in targets.priority] == [("t00", "上市"), ("9901", "上櫃")]
    assert targets.unresolved_symbols == ("9902",)
    assert {"9901", "9902"} <= targets.tracking_symbols
    assert {"t00", "9901", "9902"} == targets.mandatory_symbols


def test_held_channels_are_never_removed_by_capacity_or_candidate_score():
    held = frozenset(map(str, range(9000, 9031)))
    universe = tuple(stock(symbol) for symbol in (*sorted(held), "9990"))
    targets = QuoteTargetSelector().select(universe, QuoteTargetInputs(held, (PriorityCandidate("9990", Decimal(100)),)), ())
    assert {item.symbol for item in targets.priority} == {"t00", *held}
    assert [item.symbol for item in targets.baseline] == ["9990"]
    assert targets.candidate_overflow_count == 1


def test_existing_high_frequency_has_priority_and_overflow_candidates_remain_baseline():
    existing = tuple(map(str, range(9000, 9029)))
    candidates = (PriorityCandidate("9901", Decimal(50)), PriorityCandidate("9902", Decimal(80)))
    universe = tuple(stock(symbol) for symbol in (*existing, "9901", "9902"))
    targets = QuoteTargetSelector().select(universe, QuoteTargetInputs(frozenset(), candidates), existing)
    assert len(targets.priority) == 30
    assert {item.symbol for item in targets.priority} == {"t00", *existing}
    assert set(targets.overflow_symbols) == {"9901", "9902"}
    assert {item.symbol for item in targets.baseline} == {"9901", "9902"}
    assert targets.candidate_overflow_count == 2


def test_candidates_dedupe_highest_score_with_deterministic_order():
    candidates = (PriorityCandidate("9901", Decimal(20)), PriorityCandidate("9902", Decimal(70)), PriorityCandidate("9901", Decimal(90)))
    targets = QuoteTargetSelector().select((stock("9901"), stock("9902")), QuoteTargetInputs(frozenset(), candidates), ())
    assert [item.symbol for item in targets.priority] == ["t00", "9901", "9902"]


def test_existing_high_frequency_over_capacity_is_preserved_with_held_and_index():
    existing = tuple(map(str, range(9000, 9035)))
    inputs = QuoteTargetInputs(frozenset({"9900"}), (PriorityCandidate("9901", Decimal(100)),))
    targets = QuoteTargetSelector().select(tuple(stock(symbol) for symbol in (*existing, "9900", "9901")), inputs, existing)
    assert {item.symbol for item in targets.priority} == {"t00", "9900", *existing}
    assert targets.mandatory_symbols == {"t00", "9900", *existing}
    assert [item.symbol for item in targets.baseline] == ["9901"]
