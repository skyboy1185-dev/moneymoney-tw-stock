import asyncio
from typing import Any

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.models import GmailDeliveryLog
import app.services.gmail_messaging as gmail_module
from app.services.gmail_messaging import GmailNotificationDispatcher


def test_gmail_delivery_is_sent_once_per_recipient_and_event(monkeypatch: Any) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    GmailDeliveryLog.__table__.create(engine)
    test_session = sessionmaker(bind=engine, expire_on_commit=False)
    settings = gmail_module.get_settings()
    monkeypatch.setattr(settings, "gmail_notifications_enabled", True)
    monkeypatch.setattr(settings, "gmail_sender_email", "sender@gmail.com")
    monkeypatch.setattr(settings, "gmail_app_password", "test-app-password")
    monkeypatch.setattr(settings, "gmail_recipient_emails", "receiver@gmail.com")
    monkeypatch.setattr(gmail_module, "SessionLocal", test_session)

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
    with test_session() as db:
        assert db.scalar(select(func.count()).select_from(GmailDeliveryLog)) == 1
        log = db.scalar(select(GmailDeliveryLog))
        assert log is not None
        assert log.status == "sent"
        assert log.attempts == 1
