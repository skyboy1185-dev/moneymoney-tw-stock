from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.day_trading_v2_models import DayTradeV2Notification, DayTradeV2RuntimeState, DayTradeV2Setting
from app.services.day_trading_v2 import merged_config, signal_level
from app.services import day_trading_v2_automation as automation
from app.services.popular_stock_universe import parse_twse_volume_rank
from app.services.day_trading_v2_schedule import (
    at_time,
    due_event_types,
    event_schedule,
    is_trading_day,
    simulated_day,
    stale_reasons,
    trading_phase,
)


CONFIG = merged_config()
DAY = date(2026, 9, 7)


def taipei_time(value: str) -> datetime:
    return at_time(DAY, value)


def test_0830_initialization_and_0900_scan_schedule_are_present():
    schedule = {event.event_type: scheduled for event, scheduled in event_schedule(DAY, CONFIG)}
    assert schedule["DAILY_RESET"] == taipei_time("08:30:00")
    assert schedule["MARKET_SCAN_STARTED"] == taipei_time("09:00:00")
    assert schedule["DAILY_REPORT"] == taipei_time("13:40:00")


def test_0855_ready_notification_becomes_due_once():
    now = taipei_time("08:55:01")
    due = due_event_types(now, CONFIG, {"DAILY_RESET", "UNIVERSE_LOADED", "HISTORY_LOADED", "HEALTH_CHECKED", "CANDIDATE_POOL_READY"})
    assert due == ["PREOPEN_READY"]
    assert due_event_types(now, CONFIG, {"PREOPEN_READY", "DAILY_RESET", "UNIVERSE_LOADED", "HISTORY_LOADED", "HEALTH_CHECKED", "CANDIDATE_POOL_READY"}) == []


def test_late_restart_does_not_replay_expired_scheduled_notifications():
    now = taipei_time("10:05:00")
    due = due_event_types(now, CONFIG, set(), started_at=now)
    assert "PREOPEN_READY" not in due
    assert "OPENING_RANGE_READY" not in due
    assert "MARKET_SCAN_STARTED" in due


def test_trading_phase_stops_entries_at_1320_and_forces_close_at_1325():
    assert trading_phase(taipei_time("13:20:00"), CONFIG) == "ENTRY_CLOSED"
    assert trading_phase(taipei_time("13:25:00"), CONFIG) == "FORCED_CLOSING"
    assert trading_phase(taipei_time("13:40:00"), CONFIG) == "COMPLETED"


def test_weekend_and_holiday_are_not_trading_days():
    assert is_trading_day(DAY)
    assert not is_trading_day(date(2026, 9, 6))
    assert not is_trading_day(DAY, {DAY})


def test_twse_calendar_accepts_official_chinese_date_format():
    assert automation._twse_holiday_date("9月25日", 2026) == date(2026, 9, 25)
    assert automation._twse_holiday_date("2026-10-10", 2026) == date(2026, 10, 10)


def test_v2_market_universe_can_include_financial_common_stocks():
    payload = [{"Code": "2881", "Name": "富邦金", "TradeValue": "100000000"}]
    assert parse_twse_volume_rank(payload) == ()
    assert parse_twse_volume_rank(payload, exclude_financial=False)[0].symbol == "2881"


def test_heartbeat_and_quote_timeout_fail_closed():
    now = datetime(2026, 9, 7, 2, 0, tzinfo=UTC)
    reasons = stale_reasons(
        now=now, heartbeat_at=now - timedelta(seconds=46), quote_at=now - timedelta(seconds=16),
        config=CONFIG, market_hours=True,
    )
    assert reasons == ["系統心跳逾時", "行情中斷或延遲"]


@pytest.mark.parametrize(
    ("score", "expected"),
    [(0, "GENERAL"), (59, "GENERAL"), (60, "WATCH"), (70, "NEAR_ENTRY"), (80, "RISK_GATE")],
)
def test_signal_tiers(score: int, expected: str):
    assert signal_level(score, CONFIG) == expected


@pytest.mark.parametrize(
    ("scenario", "expected_orders", "notification", "skip_reason"),
    [
        ("NORMAL_SIGNAL", 1, "DAILY_REPORT", None),
        ("NO_TRADE", 0, "DAILY_REPORT", "信心分數不足"),
        ("QUOTE_INTERRUPTED", 0, "MARKET_DATA_INTERRUPTED", None),
        ("BROKER_FAILED", 0, "BROKER_DISCONNECTED", "券商連線異常"),
        ("DUPLICATE_SIGNAL", 1, "DAILY_REPORT", "重複訊號，未執行"),
    ],
)
def test_required_simulated_market_days(scenario: str, expected_orders: int, notification: str, skip_reason: str | None):
    result = simulated_day(scenario)
    assert result["orders"] == expected_orders
    assert notification in result["notifications"]
    assert "13:40收盤報告" in result["records"]
    if skip_reason:
        assert skip_reason in result["skipReasons"]


def test_duplicate_strategy_day_creates_only_one_order():
    result = simulated_day("DUPLICATE_SIGNAL")
    assert result["orders"] == 1
    assert result["skipReasons"].count("重複訊號，未執行") == 4


def test_no_trade_day_keeps_status_records_and_close_report():
    result = simulated_day("NO_TRADE")
    assert "09:00開始掃描" in result["records"]
    assert result["orders"] == 0
    assert result["notifications"][-1] == "DAILY_REPORT"


def test_coordinator_persists_heartbeat_schedule_notifications_and_starts_scanner(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with sessions() as db:
        db.add(DayTradeV2Setting(user_id="scheduler-user", trade_mode="PAPER", live_enabled=False, config_json="{}"))
        db.commit()

    class EmptyProvider:
        def __init__(self, **_kwargs):
            pass

        async def fetch(self):
            return ()

    scan_calls: list[datetime] = []
    from app.routers import day_trading_v2 as router_module

    monkeypatch.setattr(automation, "SessionLocal", sessions)
    monkeypatch.setattr(automation, "OfficialPopularStockProvider", EmptyProvider)
    monkeypatch.setattr(router_module, "_scan_now", lambda user_id, db, coordinator_now=None: scan_calls.append(coordinator_now))
    coordinator = automation.DayTradingV2Coordinator()

    coordinator.run_cycle(taipei_time("08:30:00"))
    coordinator.run_cycle(taipei_time("08:55:00"))
    coordinator.run_cycle(taipei_time("09:00:00"))

    with sessions() as db:
        runtime = db.scalar(select(DayTradeV2RuntimeState).where(DayTradeV2RuntimeState.user_id == "scheduler-user"))
        event_types = set(db.scalars(select(DayTradeV2Notification.event_type)).all())
        assert runtime is not None
        assert runtime.initialized is True
        assert runtime.status == "RUNNING"
        assert runtime.heartbeat_at is not None
        assert "PREOPEN_READY" in event_types
        assert scan_calls == [taipei_time("09:00:00")]
