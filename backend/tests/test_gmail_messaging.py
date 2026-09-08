import asyncio
from typing import Any

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.models import GmailDeliveryLog
import app.services.gmail_messaging as gmail_module
from app.services.gmail_messaging import GmailNotificationDispatcher


@pytest.fixture
def delivery_session(monkeypatch: Any):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    GmailDeliveryLog.__table__.create(engine)
    test_session = sessionmaker(bind=engine, expire_on_commit=False)
    settings = gmail_module.get_settings()
    monkeypatch.setattr(settings, "gmail_notifications_enabled", True)
    monkeypatch.setattr(settings, "gmail_sender_email", "sender@gmail.com")
    monkeypatch.setattr(settings, "gmail_app_password", "test-app-password")
    monkeypatch.setattr(settings, "gmail_recipient_emails", "receiver@gmail.com")
    monkeypatch.setattr(settings, "gmail_apps_script_url", "")
    monkeypatch.setattr(settings, "gmail_apps_script_secret", "")
    monkeypatch.setattr(gmail_module, "SessionLocal", test_session)
    yield test_session
    engine.dispose()


def test_gmail_delivery_is_sent_once_per_recipient_and_event(monkeypatch: Any, delivery_session) -> None:

    sent: list[tuple[str, str, str]] = []

    def fake_send(recipient: str, subject: str, body: str) -> None:
        sent.append((recipient, subject, body))

    dispatcher = GmailNotificationDispatcher()
    monkeypatch.setattr(dispatcher, "_send_sync", fake_send)
    event = {
        "event_type": "long_entry",
        "action": "突破買進",
        "message": "2330 台積電模擬買進",
        "dedupe_key": "formal-entry:2026-08-10:2330:long",
        "signal_id": "signal-2330",
        "symbol": "2330",
    }

    assert asyncio.run(dispatcher.dispatch(**event)) == 1
    assert asyncio.run(dispatcher.dispatch(**event)) == 0
    assert len(sent) == 1
    assert dispatcher.delivery_complete(event["dedupe_key"])
    with delivery_session() as db:
        assert db.scalar(select(func.count()).select_from(GmailDeliveryLog)) == 1
        log = db.scalar(select(GmailDeliveryLog))
        assert log is not None
        assert log.status == "sent"
        assert log.attempts == 1


def test_failed_delivery_recovers_without_resending_successful_recipients(monkeypatch: Any, delivery_session) -> None:
    settings = gmail_module.get_settings()
    monkeypatch.setattr(settings, "gmail_recipient_emails", "first@gmail.com,second@gmail.com")
    dispatcher = GmailNotificationDispatcher()
    outage = True
    calls: list[str] = []

    def fake_send(recipient: str, subject: str, body: str) -> None:
        calls.append(recipient)
        if outage and recipient == "second@gmail.com":
            raise OSError("temporary outage")

    async def no_sleep(delay):
        pass

    monkeypatch.setattr(dispatcher, "_send_sync", fake_send)
    monkeypatch.setattr(gmail_module.asyncio, "sleep", no_sleep)
    event = dict(event_type="long_entry", action="BUY", message="test", dedupe_key="recover")

    assert asyncio.run(dispatcher.dispatch(**event)) == 1
    assert not dispatcher.delivery_complete("recover")
    outage = False
    assert asyncio.run(dispatcher.dispatch(**event)) == 1
    assert dispatcher.delivery_complete("recover")
    assert asyncio.run(dispatcher.dispatch(**event)) == 0
    assert calls.count("first@gmail.com") == 1
    assert calls.count("second@gmail.com") == 4
    with delivery_session() as db:
        logs = list(db.scalars(select(GmailDeliveryLog).order_by(GmailDeliveryLog.recipient)))
        assert len(logs) == 2
        assert [log.status for log in logs] == ["sent", "sent"]
        assert [log.attempts for log in logs] == [1, 4]
        assert all(log.error_message is None for log in logs)


def test_only_one_worker_retries_a_failed_recipient(monkeypatch: Any, delivery_session) -> None:
    settings = gmail_module.get_settings()
    monkeypatch.setattr(settings, "gmail_apps_script_url", "https://example.invalid/test")
    monkeypatch.setattr(settings, "gmail_apps_script_secret", "test-only")
    dispatcher = GmailNotificationDispatcher()
    event = dict(event_type="long_entry", action="BUY", message="test", dedupe_key="concurrent-retry")

    async def fail_send(*args):
        raise OSError("temporary outage")

    async def no_sleep(delay):
        pass

    monkeypatch.setattr(dispatcher, "_send_apps_script", fail_send)
    monkeypatch.setattr(gmail_module.asyncio, "sleep", no_sleep)
    assert asyncio.run(dispatcher.dispatch(**event)) == 0

    async def run_workers():
        started = asyncio.Event()
        release = asyncio.Event()
        sent: list[str] = []

        async def send(recipient, subject, body):
            sent.append(recipient)
            started.set()
            await release.wait()

        monkeypatch.setattr(dispatcher, "_send_apps_script", send)
        first = asyncio.create_task(dispatcher.dispatch(**event))
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            assert await dispatcher.dispatch(**event) == 0
            assert not dispatcher.delivery_complete(event["dedupe_key"])
        finally:
            release.set()
        assert await first == 1
        assert sent == ["receiver@gmail.com"]

    asyncio.run(run_workers())
    assert dispatcher.delivery_complete(event["dedupe_key"])


def test_delivery_completion_requires_current_recipients(monkeypatch: Any, delivery_session) -> None:
    dispatcher = GmailNotificationDispatcher()
    assert not dispatcher.delivery_complete("missing")
    monkeypatch.setattr(dispatcher, "_send_sync", lambda *args: None)
    event = dict(event_type="long_entry", action="BUY", message="test", dedupe_key="recipients")
    assert asyncio.run(dispatcher.dispatch(**event)) == 1
    settings = gmail_module.get_settings()
    monkeypatch.setattr(settings, "gmail_recipient_emails", "receiver@gmail.com,new@gmail.com")
    assert not dispatcher.delivery_complete(event["dedupe_key"])
    assert asyncio.run(dispatcher.dispatch(**event)) == 1
    assert dispatcher.delivery_complete(event["dedupe_key"])
    monkeypatch.setattr(settings, "gmail_recipient_emails", "")
    assert not dispatcher.delivery_complete(event["dedupe_key"])
