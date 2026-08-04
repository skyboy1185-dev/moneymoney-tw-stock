from datetime import UTC, datetime

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import (
    DayTradingAlert,
    DayTradingPosition,
    DayTradingTrade,
    LineDeliveryLog,
)
from app.services.automated_position_tracker import (
    AUTOMATION_USER_ID,
    ensure_positions_for_delivered_entries,
    finalize_automatic_position_event,
    pending_automatic_position_events,
)


def _session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _signal() -> dict[str, object]:
    return {
        "id": "2330-long-20260730T094152",
        "symbol": "2330",
        "stockName": "台積電",
        "direction": "long",
        "action": "突破買進",
        "price": 2255.0,
        "stopLoss": 2236.96,
        "target1": 2282.06,
        "target2": 2300.10,
        "healthScore": 90,
        "generatedAt": "2026-07-30T09:41:52+08:00",
        "isOfficialRecommendation": True,
    }


def _delivered_entry(db: Session, signal_id: str) -> None:
    now = datetime(2026, 7, 30, 1, 42, 10, tzinfo=UTC)
    db.add(LineDeliveryLog(
        group_id="test-group",
        event_type="long_entry",
        signal_id=signal_id,
        symbol="2330",
        action="突破買進",
        priority=6,
        dedupe_key=f"signal:{signal_id}:突破買進",
        status="sent",
        attempts=1,
        response_status=200,
        message_preview="test",
        created_at=now,
        sent_at=now,
    ))
    db.commit()


def _automatic_position(db: Session) -> DayTradingPosition:
    position = DayTradingPosition(
        user_id=AUTOMATION_USER_ID,
        signal_id="2330-long-20260730T094152",
        symbol="2330",
        stock_name="台積電",
        direction="long",
        entry_price=2255,
        quantity=1,
        opened_at=datetime(2026, 7, 30, 1, 42, 10, tzinfo=UTC),
        stop_loss=2236.96,
        target_1=2282.06,
        target_2=2300.10,
        current_price=2255,
        unrealized_profit=0,
        health_score=90,
        latest_action="自動追蹤多單",
        status="open",
    )
    db.add(position)
    db.commit()
    db.refresh(position)
    return position


def test_delivered_formal_entry_creates_one_persisted_virtual_position() -> None:
    with _session() as db:
        signal = _signal()
        _delivered_entry(db, str(signal["id"]))

        first = ensure_positions_for_delivered_entries(db, [signal])
        db.commit()
        second = ensure_positions_for_delivered_entries(db, [signal])
        db.commit()

        assert len(first) == 1
        assert second == []
        position = db.scalar(select(DayTradingPosition))
        assert position is not None
        assert position.user_id == AUTOMATION_USER_ID
        assert position.symbol == "2330"
        assert position.entry_price == 2255
        assert position.stop_loss == 2236.96
        assert position.quantity == 1


def test_entry_without_successful_line_delivery_is_not_auto_tracked() -> None:
    with _session() as db:
        created = ensure_positions_for_delivered_entries(db, [_signal()])
        db.commit()

        assert created == []
        assert db.scalar(select(func.count()).select_from(DayTradingPosition)) == 0


def test_background_stop_event_closes_position_and_records_trade() -> None:
    with _session() as db:
        position = _automatic_position(db)

        events = pending_automatic_position_events(
            db,
            lambda symbol: 2205 if symbol == "2330" else None,
            data_status="normal",
        )
        db.commit()

        assert len(events) == 1
        event = events[0]
        assert event["action"] == "立即全部賣出"
        assert event["reason"] == "跌破停損價"
        assert event["_terminal"] is True

        finalized = finalize_automatic_position_event(db, event)
        db.commit()
        assert finalized is not None
        assert finalized.id == position.id
        assert finalized.status == "closed"
        assert finalized.exit_price == 2205
        assert db.scalar(select(func.count()).select_from(DayTradingAlert)) == 1
        assert db.scalar(select(func.count()).select_from(DayTradingTrade)) == 1


def test_partial_target_is_not_repeated_and_position_stays_open() -> None:
    with _session() as db:
        position = _automatic_position(db)
        events = pending_automatic_position_events(
            db,
            lambda _: 2282.06,
            data_status="normal",
        )
        assert len(events) == 1
        assert events[0]["action"] == "減碼 50%"
        assert events[0]["_terminal"] is False

        finalize_automatic_position_event(db, events[0])
        db.commit()
        repeated = pending_automatic_position_events(
            db,
            lambda _: 2282.06,
            data_status="normal",
        )

        db.refresh(position)
        assert position.status == "open"
        assert repeated == []


def test_closing_phase_forces_intraday_position_exit() -> None:
    with _session() as db:
        _automatic_position(db)

        events = pending_automatic_position_events(
            db,
            lambda _: 2260,
            data_status="source_error",
            force_close=True,
        )

        assert len(events) == 1
        assert events[0]["action"] == "收盤前全部賣出"
        assert events[0]["reason"] == "當沖策略於收盤前強制平倉"
        assert events[0]["_terminal"] is True


def test_force_close_uses_last_known_price_when_quote_is_unavailable() -> None:
    with _session() as db:
        _automatic_position(db)

        events = pending_automatic_position_events(
            db,
            lambda _: None,
            data_status="source_error",
            force_close=True,
        )

        assert len(events) == 1
        assert events[0]["price"] == 2255
        assert events[0]["action"] == "收盤前全部賣出"


def test_bearish_five_minute_structure_closes_long_position() -> None:
    with _session() as db:
        _automatic_position(db)

        events = pending_automatic_position_events(
            db,
            lambda _: 2248,
            data_status="normal",
            risk_for=lambda _: {
                "level": "important",
                "action": "5 分 K 轉弱，全部賣出",
                "reason": "跌破開盤價且 5 分 K 均線向下",
            },
        )

        assert len(events) == 1
        assert events[0]["action"] == "5 分 K 轉弱，全部賣出"
        assert events[0]["_terminal"] is True
