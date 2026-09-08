import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.day_trading_v2_models import DayTradeV2Notification, DayTradeV2RuntimeState, DayTradeV2Setting
from app.services import day_trading_v2_automation as automation


@pytest.fixture
def notification_queue(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    current = [datetime(2026, 9, 8, 0, 55, tzinfo=UTC)]

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return current[0].astimezone(tz) if tz else current[0].replace(tzinfo=None)

    monkeypatch.setattr(automation, "SessionLocal", sessions)
    monkeypatch.setattr(automation, "datetime", FrozenDateTime)
    with sessions() as db:
        db.add(DayTradeV2Setting(user_id="notification-user", config_json="{}"))
        db.commit()
    yield sessions, current
    engine.dispose()


def add_notification(sessions, current, event_id="ready", event_type="PREOPEN_READY", attempted_at=None):
    with sessions() as db:
        row = DayTradeV2Notification(
            user_id="notification-user", event_id=event_id, mode="PAPER", event_type=event_type,
            title="當沖機器人2", message="通知測試", created_at=current[0], email_attempted_at=attempted_at,
        )
        db.add(row)
        db.commit()
        return row.id


class FakeDispatcher:
    configured = True

    def __init__(self):
        self.calls = []
        self.completed = set()

    async def dispatch(self, **event):
        self.calls.append(event)
        return int(event["dedupe_key"] in self.completed)

    def delivery_complete(self, dedupe_key):
        return dedupe_key in self.completed


def test_failed_notification_retries_after_cooldown_and_recovers(notification_queue, monkeypatch):
    sessions, current = notification_queue
    notification_id = add_notification(sessions, current)
    dispatcher = FakeDispatcher()
    monkeypatch.setattr(automation, "gmail_notification_dispatcher", dispatcher)
    coordinator = automation.DayTradingV2Coordinator()

    assert asyncio.run(coordinator.dispatch_pending()) == 0
    assert asyncio.run(coordinator.dispatch_pending()) == 0
    assert len(dispatcher.calls) == 1
    with sessions() as db:
        assert not db.get(DayTradeV2Notification, notification_id).email_sent

    current[0] += timedelta(seconds=60)
    dispatcher.completed.add("dtv2-email:ready")
    assert asyncio.run(coordinator.dispatch_pending()) == 1
    assert asyncio.run(coordinator.dispatch_pending()) == 0
    assert len(dispatcher.calls) == 2
    with sessions() as db:
        assert db.get(DayTradeV2Notification, notification_id).email_sent


def test_more_than_twenty_failures_do_not_starve_new_notification(notification_queue, monkeypatch):
    sessions, current = notification_queue
    for index in range(25):
        add_notification(sessions, current, f"old-{index}", attempted_at=current[0] - timedelta(minutes=2))
    notification_id = add_notification(sessions, current, "new")
    dispatcher = FakeDispatcher()
    dispatcher.completed.add("dtv2-email:new")
    monkeypatch.setattr(automation, "gmail_notification_dispatcher", dispatcher)
    coordinator = automation.DayTradingV2Coordinator()

    assert asyncio.run(coordinator.dispatch_pending()) == 1
    assert dispatcher.calls[0]["dedupe_key"] == "dtv2-email:new"
    assert len(dispatcher.calls) == 20
    assert asyncio.run(coordinator.dispatch_pending()) == 0
    assert len(dispatcher.calls) == 26
    with sessions() as db:
        assert db.get(DayTradeV2Notification, notification_id).email_sent
        assert sum(row.email_sent for row in db.scalars(select(DayTradeV2Notification))) == 1


def test_partial_recipient_success_waits_for_all_recipients(notification_queue, monkeypatch):
    sessions, current = notification_queue
    notification_id = add_notification(sessions, current)

    class PartialDispatcher(FakeDispatcher):
        async def dispatch(self, **event):
            self.calls.append(event)
            if len(self.calls) == 2:
                self.completed.add(event["dedupe_key"])
            # Each pass sends one recipient; only pass two completes both.
            return 1

    dispatcher = PartialDispatcher()
    monkeypatch.setattr(automation, "gmail_notification_dispatcher", dispatcher)
    coordinator = automation.DayTradingV2Coordinator()
    assert asyncio.run(coordinator.dispatch_pending()) == 0
    with sessions() as db:
        assert not db.get(DayTradeV2Notification, notification_id).email_sent
    current[0] += timedelta(seconds=60)
    assert asyncio.run(coordinator.dispatch_pending()) == 1
    with sessions() as db:
        assert db.get(DayTradeV2Notification, notification_id).email_sent


def test_already_sent_recipient_logs_complete_event_without_new_sends(notification_queue, monkeypatch):
    sessions, current = notification_queue
    notification_id = add_notification(sessions, current)

    class AlreadySentDispatcher(FakeDispatcher):
        async def dispatch(self, **event):
            self.calls.append(event)
            return 0

    dispatcher = AlreadySentDispatcher()
    dispatcher.completed.add("dtv2-email:ready")
    monkeypatch.setattr(automation, "gmail_notification_dispatcher", dispatcher)
    assert asyncio.run(automation.DayTradingV2Coordinator().dispatch_pending()) == 1
    with sessions() as db:
        assert db.get(DayTradeV2Notification, notification_id).email_sent


def test_workers_claim_attempt_before_await_and_skip_stale_queue_rows(notification_queue, monkeypatch):
    sessions, current = notification_queue
    add_notification(sessions, current, "first")
    add_notification(sessions, current, "second")
    other_worker = automation.DayTradingV2Coordinator()
    other_deliveries = []

    class ConcurrentDispatcher(FakeDispatcher):
        async def dispatch(self, **event):
            self.calls.append(event)
            if event["dedupe_key"] == "dtv2-email:first":
                other_deliveries.append(await other_worker.dispatch_pending())
            self.completed.add(event["dedupe_key"])
            return 1

    dispatcher = ConcurrentDispatcher()
    monkeypatch.setattr(automation, "gmail_notification_dispatcher", dispatcher)
    assert asyncio.run(automation.DayTradingV2Coordinator().dispatch_pending()) == 1
    assert other_deliveries == [1]
    assert [event["dedupe_key"] for event in dispatcher.calls] == ["dtv2-email:first", "dtv2-email:second"]


def test_disabled_hourly_email_is_explicitly_handled_without_delivery(notification_queue, monkeypatch):
    sessions, current = notification_queue
    notification_id = add_notification(sessions, current, event_type="HOURLY_SUMMARY")
    dispatcher = FakeDispatcher()
    monkeypatch.setattr(automation, "gmail_notification_dispatcher", dispatcher)
    assert asyncio.run(automation.DayTradingV2Coordinator().dispatch_pending()) == 0
    assert dispatcher.calls == []
    with sessions() as db:
        row = db.get(DayTradeV2Notification, notification_id)
        assert row.email_sent
        assert json.loads(row.payload_json)["emailDeliveryStatus"] == "SKIPPED_DISABLED"
        assert row.title == "當沖機器人2"


def test_delivery_exception_does_not_block_following_notification(notification_queue, monkeypatch):
    sessions, current = notification_queue
    add_notification(sessions, current, "bad")
    add_notification(sessions, current, "good")

    class RaisingDispatcher(FakeDispatcher):
        async def dispatch(self, **event):
            self.calls.append(event)
            if event["dedupe_key"] == "dtv2-email:bad":
                raise RuntimeError("simulated dispatcher interruption")
            self.completed.add(event["dedupe_key"])
            return 1

    dispatcher = RaisingDispatcher()
    monkeypatch.setattr(automation, "gmail_notification_dispatcher", dispatcher)
    assert asyncio.run(automation.DayTradingV2Coordinator().dispatch_pending()) == 1
    assert len(dispatcher.calls) == 2


def test_heartbeat_risk_email_fits_existing_log_event_type_column(notification_queue, monkeypatch):
    sessions, current = notification_queue
    add_notification(sessions, current, "heartbeat", "SYSTEM_HEARTBEAT_INTERRUPTED")
    dispatcher = FakeDispatcher()
    dispatcher.completed.add("dtv2-email:heartbeat")
    monkeypatch.setattr(automation, "gmail_notification_dispatcher", dispatcher)
    assert asyncio.run(automation.DayTradingV2Coordinator().dispatch_pending()) == 1
    event = dispatcher.calls[0]
    assert event["event_type"] == "dtv2_system_heartbeat_interrupted"
    assert len(event["event_type"]) <= 40
    assert event["dedupe_key"] == "dtv2-email:heartbeat"


def test_september_8_no_trade_day_keeps_ready_opening_and_close_notifications(notification_queue, monkeypatch):
    sessions, current = notification_queue
    from app.routers import day_trading_v2 as router_module

    class EmptyProvider:
        def __init__(self, **_kwargs):
            pass

        async def fetch(self):
            return ()

    class SuccessfulDispatcher(FakeDispatcher):
        async def dispatch(self, **event):
            self.calls.append(event)
            self.completed.add(event["dedupe_key"])
            return 1

    with sessions() as db:
        setting = db.get(DayTradeV2Setting, "notification-user")
        # This test jumps between scheduled times instead of running every second.
        setting.config_json = json.dumps({"heartbeatTimeoutSeconds": 86400})
        db.commit()
    dispatcher = SuccessfulDispatcher()
    monkeypatch.setattr(automation, "gmail_notification_dispatcher", dispatcher)
    monkeypatch.setattr(automation, "OfficialPopularStockProvider", EmptyProvider)
    monkeypatch.setattr(router_module, "_now", lambda: current[0])
    monkeypatch.setattr(router_module, "_scan_now", lambda *args, **kwargs: None)
    coordinator = automation.DayTradingV2Coordinator()
    monkeypatch.setattr(coordinator, "_refresh_calendar", lambda *args: None)

    delivered = 0
    for hour, minute in [(0, 30), (0, 55), (1, 15), (5, 40)]:
        current[0] = datetime(2026, 9, 8, hour, minute, tzinfo=UTC)
        assert coordinator.run_cycle(current[0]) == 1
        delivered += asyncio.run(coordinator.dispatch_pending())

    assert delivered == 3
    assert [event["action"] for event in dispatcher.calls] == ["PREOPEN_READY", "OPENING_RANGE_READY", "DAILY_REPORT"]
    with sessions() as db:
        runtime = db.scalar(select(DayTradeV2RuntimeState))
        assert runtime.trading_date.isoformat() == "2026-09-08"
        assert runtime.status == "COMPLETED"
        assert runtime.order_count == runtime.completed_trade_count == 0
        rows = list(db.scalars(select(DayTradeV2Notification)))
        assert len(rows) == 6
        assert {row.event_type for row in rows} == {"PREOPEN_READY", "OPENING_RANGE_READY", "HOURLY_SUMMARY", "DAILY_REPORT"}
        assert all(row.email_sent for row in rows)
