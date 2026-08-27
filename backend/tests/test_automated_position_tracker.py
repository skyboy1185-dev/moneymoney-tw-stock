from datetime import UTC, datetime
import json
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import (
    DayTradingAlert,
    DayTradingCandidateSnapshot,
    DayTradingPosition,
    DayTradingRecommendationHistory,
    DayTradingTrade,
    LineDeliveryLog,
)
from app.services.automated_position_tracker import (
    AUTOMATION_DAILY_CAPITAL,
    AUTOMATION_FIXED_MAX_POSITION_CAPITAL,
    AUTOMATION_FIXED_REPEAT_STOP_LIMIT,
    AUTOMATION_USER_ID,
    DYNAMIC_AUTOMATION_USER_ID,
    DYNAMIC_STRATEGY_KEY,
    FIXED_STRATEGY_KEY,
    automation_capital_state,
    ensure_positions_for_delivered_entries,
    finalize_automatic_position_event,
    pending_automatic_position_events,
    record_official_recommendations,
)
from app.services.day_trading_candidate_snapshots import replay_candidate_snapshots, save_candidate_snapshots
from app.services.day_trading_schedule import TradingScheduleConfig, trading_session_state


TAIPEI = ZoneInfo("Asia/Taipei")


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
        "price": 100.0,
        "stopLoss": 99.0,
        "target1": 102.0,
        "target2": 103.0,
        "healthScore": 90,
        "generatedAt": "2026-07-30T09:41:52+08:00",
        "isOfficialRecommendation": True,
    }


def _qualified_signal(**overrides: object) -> dict[str, object]:
    value = {
        **_signal(),
        "status": "confirmed",
        "dataMode": "official",
        "quoteIsRealtime": True,
        "confidenceScore": 85,
        "healthScore": 85,
        "riskRewardRatio": 2.0,
        "volume": 1_000_000,
        "turnover": 100_000_000,
        "spreadPercentage": 0.1,
        "tradingEligible": True,
        "shortAvailabilityKnown": True,
        "shortEligible": True,
        "nearLimitDown": False,
        "excessiveNegativeDeviation": False,
        "chaseBlocked": False,
        "stopDistancePercent": 1.0,
        "marketAlignment": 80,
        "confirmationScore": 70,
        "volumeScore": 80,
        "activeForce": 80,
        "largeOrderForce": 70,
        "largeOrderDataAvailable": True,
        "largeOrderContinuousBuy": True,
        "largeOrderContinuousSell": True,
        "industryScore": 80,
        "liquidityScore": 80,
        "momentumUniverseMember": True,
        "expiresAt": "2026-07-30T10:00:00+08:00",
    }
    value.update(overrides)
    return value


def _formal_session() -> tuple[TradingScheduleConfig, dict[str, object], datetime]:
    now = datetime(2026, 7, 30, 9, 41, 52, tzinfo=TAIPEI)
    config = TradingScheduleConfig()
    session = trading_session_state(config, now, quote_samples=10, infrastructure_ok=True)
    return config, session, now


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


def _automatic_position(db: Session, user_id: str = AUTOMATION_USER_ID) -> DayTradingPosition:
    position = DayTradingPosition(
        user_id=user_id,
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


def test_formal_entry_creates_only_original_fixed_lot_position() -> None:
    with _session() as db:
        signal = _signal()
        _delivered_entry(db, str(signal["id"]))

        first = ensure_positions_for_delivered_entries(db, [signal])
        db.commit()
        second = ensure_positions_for_delivered_entries(db, [signal])
        db.commit()

        assert len(first) == 1
        assert second == []
        positions = {
            position.user_id: position
            for position in db.scalars(select(DayTradingPosition)).all()
        }
        assert set(positions) == {AUTOMATION_USER_ID}
        assert positions[AUTOMATION_USER_ID].quantity == 2
        assert positions[AUTOMATION_USER_ID].symbol == "2330"


def test_entry_without_successful_line_delivery_is_still_auto_tracked() -> None:
    with _session() as db:
        created = ensure_positions_for_delivered_entries(db, [_signal()])
        db.commit()

        assert len(created) == 1
        assert db.scalar(select(func.count()).select_from(DayTradingPosition)) == 1


def test_repeated_same_symbol_signal_does_not_add_another_position() -> None:
    with _session() as db:
        first = _signal()
        repeated = {**first, "id": f"{first['id']}-repeat"}

        assert len(ensure_positions_for_delivered_entries(db, [first])) == 1
        db.commit()
        assert ensure_positions_for_delivered_entries(db, [repeated]) == []
        assert db.scalar(select(func.count()).select_from(DayTradingPosition)) == 1
        position = db.scalar(select(DayTradingPosition))
        assert position is not None and position.quantity == 2


def test_paused_dynamic_strategy_creates_no_new_positions() -> None:
    with _session() as db:
        created: list[DayTradingPosition] = []
        for index in range(5):
            signal = {
                **_signal(),
                "id": f"dynamic-{index}",
                "symbol": f"99{index:02d}",
                "price": 100.0,
                "stopLoss": 99.0,
                "target1": 102.0,
                "target2": 103.0,
            }
            created.extend(ensure_positions_for_delivered_entries(db, [signal]))
        db.commit()

        dynamic_created = [
            position for position in created
            if position.user_id == DYNAMIC_AUTOMATION_USER_ID
        ]
        fixed_created = [
            position for position in created
            if position.user_id == AUTOMATION_USER_ID
        ]
        assert dynamic_created == []
        assert [position.quantity for position in fixed_created] == [2.0] * 5
        capital = automation_capital_state(db, datetime(2026, 7, 30, 3, 0, tzinfo=UTC))
        assert capital["usedCapital"] == 0
        assert capital["availableCapital"] == AUTOMATION_DAILY_CAPITAL


def test_official_recommendation_history_is_deduplicated() -> None:
    with _session() as db:
        signal = _signal()
        ensure_positions_for_delivered_entries(db, [signal])
        first = record_official_recommendations(db, [signal])
        db.commit()
        second = record_official_recommendations(db, [signal])
        db.commit()

        assert first == 1
        assert second == 0
        row = db.scalar(select(DayTradingRecommendationHistory))
        assert row is not None
        assert row.signal_id == signal["id"]
        assert row.trading_date.isoformat() == "2026-07-30"
        payload = json.loads(row.payload_json)
        assert payload["recommendedQuantityLots"] == 2
        assert payload["strategyAllocations"][FIXED_STRATEGY_KEY]["quantityLots"] == 2
        assert DYNAMIC_STRATEGY_KEY not in payload["strategyAllocations"]


def test_low_confidence_official_recommendation_does_not_open_or_record() -> None:
    config, session, now = _formal_session()
    signal = _qualified_signal(confidenceScore=79)

    with _session() as db:
        created = ensure_positions_for_delivered_entries(
            db,
            [signal],
            config=config,
            session=session,
            now=now,
        )
        recorded = record_official_recommendations(
            db,
            [signal],
            config=config,
            session=session,
            now=now,
        )
        db.commit()

        assert created == []
        assert recorded == 0
        assert db.scalar(select(func.count()).select_from(DayTradingPosition)) == 0
        assert db.scalar(select(func.count()).select_from(DayTradingRecommendationHistory)) == 0


def test_low_confirmation_official_recommendation_does_not_open_or_record() -> None:
    config, session, now = _formal_session()
    signal = _qualified_signal(confirmationScore=44)

    with _session() as db:
        created = ensure_positions_for_delivered_entries(
            db,
            [signal],
            config=config,
            session=session,
            now=now,
        )
        recorded = record_official_recommendations(
            db,
            [signal],
            config=config,
            session=session,
            now=now,
        )
        db.commit()

        assert created == []
        assert recorded == 0
        assert db.scalar(select(func.count()).select_from(DayTradingPosition)) == 0
        assert db.scalar(select(func.count()).select_from(DayTradingRecommendationHistory)) == 0


def test_fixed_two_lot_strategy_skips_excessive_stop_risk() -> None:
    with _session() as db:
        signal = {
            **_signal(),
            "price": 4_060.0,
            "stopLoss": 4_025.0,
            "target1": 4_110.0,
            "target2": 4_150.0,
        }

        created = ensure_positions_for_delivered_entries(db, [signal])

        assert created == []
        allocation = signal["strategyAllocations"][FIXED_STRATEGY_KEY]
        assert allocation["quantityLots"] == 0
        assert "超過單筆上限 50,000 元" in allocation["status"]


def test_fixed_two_lot_strategy_skips_oversized_high_price_position() -> None:
    with _session() as db:
        signal = {
            **_signal(),
            "id": "3034-long-20260826T105814",
            "symbol": "3034",
            "stockName": "聯詠",
            "price": 561.0,
            "stopLoss": 556.51,
            "target1": 567.73,
            "target2": 572.22,
        }

        created = ensure_positions_for_delivered_entries(db, [signal])

        assert created == []
        allocation = signal["strategyAllocations"][FIXED_STRATEGY_KEY]
        assert allocation["quantityLots"] == 0
        assert f"{AUTOMATION_FIXED_MAX_POSITION_CAPITAL:,.0f}" in allocation["status"]


def test_fixed_two_lot_strategy_pauses_symbol_after_repeated_stop_losses() -> None:
    with _session() as db:
        now = datetime(2026, 8, 26, 3, 0, tzinfo=UTC)
        for _index in range(AUTOMATION_FIXED_REPEAT_STOP_LIMIT):
            db.add(DayTradingTrade(
                user_id=AUTOMATION_USER_ID,
                symbol="3034",
                stock_name="聯詠",
                direction="long",
                entry_time=now,
                entry_price=100,
                exit_time=now,
                exit_price=95,
                quantity=2,
                fee=0,
                tax=0,
                slippage=0,
                profit=-10_000,
                return_percentage=-5,
                max_profit=0,
                max_loss=-10_000,
                entry_reason="test",
                exit_reason="跌破停損價",
                followed_signal=True,
            ))
        db.commit()
        signal = {
            **_signal(),
            "id": "3034-long-repeat-stop",
            "symbol": "3034",
            "stockName": "聯詠",
        }

        created = ensure_positions_for_delivered_entries(db, [signal], now=now)

        assert created == []
        allocation = signal["strategyAllocations"][FIXED_STRATEGY_KEY]
        assert allocation["quantityLots"] == 0
        assert "同股已停損" in allocation["status"]


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


def test_dynamic_strategy_exit_stays_in_dynamic_ledger() -> None:
    with _session() as db:
        position = _automatic_position(db, DYNAMIC_AUTOMATION_USER_ID)
        event = pending_automatic_position_events(
            db,
            lambda symbol: 2205 if symbol == "2330" else None,
            data_status="normal",
        )[0]

        finalize_automatic_position_event(db, event)
        db.commit()

        alert = db.scalar(select(DayTradingAlert))
        trade = db.scalar(select(DayTradingTrade))
        assert alert is not None and alert.user_id == DYNAMIC_AUTOMATION_USER_ID
        assert trade is not None and trade.user_id == DYNAMIC_AUTOMATION_USER_ID
        assert "新版 500 萬動態配置" in alert.title
        assert "新版 500 萬動態配置" in trade.strategy_name
        assert position.status == "closed"


def test_partial_target_is_not_repeated_and_position_stays_open() -> None:
    with _session() as db:
        position = _automatic_position(db)
        position.quantity = 2
        db.commit()
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
        assert position.quantity == 1
        trade = db.scalar(select(DayTradingTrade))
        assert trade is not None
        assert trade.exit_price == 2282.06
        assert trade.quantity == 1
        assert repeated == []


def test_second_target_after_partial_closes_only_remaining_quantity() -> None:
    with _session() as db:
        position = _automatic_position(db)
        position.quantity = 2
        db.commit()

        first = pending_automatic_position_events(
            db, lambda _: 2282.06, data_status="normal",
        )[0]
        finalize_automatic_position_event(db, first)
        db.commit()

        second = pending_automatic_position_events(
            db, lambda _: 2300.10, data_status="normal",
        )[0]
        finalize_automatic_position_event(db, second)
        db.commit()

        db.refresh(position)
        trades = db.scalars(select(DayTradingTrade).order_by(DayTradingTrade.exit_time)).all()
        assert position.status == "closed"
        assert position.quantity == 1
        assert position.realized_profit == round(sum(item.profit for item in trades), 2)
        assert len(trades) == 2
        assert [item.quantity for item in trades] == [1, 1]


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


def test_high_confidence_long_transitions_to_one_night_position() -> None:
    with _session() as db:
        position = _automatic_position(db)
        position.entry_confidence = 90
        position.strategy_confidence = 88
        db.commit()
        current = datetime(2026, 7, 30, 5, 25, tzinfo=UTC)

        event = pending_automatic_position_events(
            db,
            lambda _: 2260,
            data_status="normal",
            force_close=True,
            now=current,
        )[0]

        assert event["action"] == "轉為隔日多單"
        assert event["_transitionOnly"] is True
        assert event["_terminal"] is False
        finalize_automatic_position_event(db, event, now=current)
        db.commit()
        db.refresh(position)
        assert position.status == "open"
        assert position.quantity == 1
        assert position.holding_period == "overnight_long"
        assert db.scalar(select(func.count()).select_from(DayTradingTrade)) == 0

        repeated = pending_automatic_position_events(
            db,
            lambda _: 2260,
            data_status="normal",
            force_close=True,
            now=current,
        )
        assert repeated == []


def test_overnight_long_is_closed_by_next_trading_day_close() -> None:
    with _session() as db:
        position = _automatic_position(db)
        position.holding_period = "overnight_long"
        position.entry_confidence = 90
        position.strategy_confidence = 90
        db.commit()

        event = pending_automatic_position_events(
            db,
            lambda _: 2270,
            data_status="normal",
            force_close=True,
            now=datetime(2026, 7, 31, 5, 25, tzinfo=UTC),
        )[0]

        assert event["action"] == "隔日多單到期，全部賣出"
        assert event["_terminal"] is True


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


def test_candidate_snapshots_can_be_replayed_with_latest_rules() -> None:
    with _session() as db:
        config, _, now = _formal_session()
        qualified = _qualified_signal(id="2330-long-qualified", rank=1)
        weak = _qualified_signal(
            id="2454-long-weak-confirmation",
            symbol="2454",
            stockName="weak",
            rank=2,
            confirmationScore=20,
        )

        saved = save_candidate_snapshots(
            db,
            [qualified, weak],
            config=config,
            snapshot_at=now,
        )
        db.commit()

        assert saved == 2
        assert db.scalar(select(func.count()).select_from(DayTradingCandidateSnapshot)) == 2

        replayed = replay_candidate_snapshots(
            db,
            config,
            trading_date=now.date(),
        )
        by_id = {item["id"]: item for item in replayed}

        assert by_id["2330-long-qualified"]["wouldBeOfficialRecommendation"] is True
        assert by_id["2454-long-weak-confirmation"]["wouldBeOfficialRecommendation"] is False
        assert by_id["2454-long-weak-confirmation"]["replayFailures"]


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
