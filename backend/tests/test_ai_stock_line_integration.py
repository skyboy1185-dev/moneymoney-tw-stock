import base64
import hashlib
import hmac
import json
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import AIStockLineGroup
from app.routers import ai_stock_line_integration
from app.services.ai_stock_line_messaging import ai_stock_line_dispatcher
from app.services.line_messaging import LineApiResult, LineMessagingClient


def _signature(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def test_ai_stock_line_uses_independent_credentials(monkeypatch: Any) -> None:
    client = LineMessagingClient(
        access_token_setting="ai_stock_line_channel_access_token",
        channel_secret_setting="ai_stock_line_channel_secret",
        enabled_setting="ai_stock_line_notifications_enabled",
    )
    monkeypatch.setattr(client._settings, "line_channel_access_token", "day-trading-token")
    monkeypatch.setattr(client._settings, "line_channel_secret", "day-trading-secret")
    monkeypatch.setattr(client._settings, "ai_stock_line_notifications_enabled", True)
    monkeypatch.setattr(client._settings, "ai_stock_line_channel_access_token", "")
    monkeypatch.setattr(client._settings, "ai_stock_line_channel_secret", "")
    assert not client.configured

    monkeypatch.setattr(client._settings, "ai_stock_line_channel_access_token", "ai-stock-token")
    monkeypatch.setattr(client._settings, "ai_stock_line_channel_secret", "ai-stock-secret")
    assert client.configured


def test_ai_stock_line_webhook_binds_group_with_valid_signature(monkeypatch: Any) -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    def session_dependency():
        with Session(engine) as session:
            yield session

    async def fake_reply(_: str, __: str) -> LineApiResult:
        return LineApiResult(True, 1, 200, None)

    secret = "ai-stock-channel-secret"
    monkeypatch.setattr(
        ai_stock_line_integration.settings,
        "ai_stock_line_channel_secret",
        secret,
    )
    monkeypatch.setattr(ai_stock_line_dispatcher.client, "reply_text", fake_reply)

    app = FastAPI()
    app.include_router(ai_stock_line_integration.webhook_router)
    app.dependency_overrides[get_db] = session_dependency
    payload = {
        "events": [{
            "webhookEventId": "ai-stock-webhook-1",
            "type": "message",
            "replyToken": "reply-token",
            "source": {"type": "group", "groupId": "C-ai-stock-group"},
            "message": {"type": "text", "text": "綁定AI選股機器人"},
        }],
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    with TestClient(app) as client:
        invalid = client.post(
            "/api/integrations/ai-stock-line/webhook",
            content=raw,
            headers={"X-Line-Signature": "invalid", "Content-Type": "application/json"},
        )
        assert invalid.status_code == 401

        response = client.post(
            "/api/integrations/ai-stock-line/webhook",
            content=raw,
            headers={"X-Line-Signature": _signature(raw, secret), "Content-Type": "application/json"},
        )
        assert response.status_code == 200
        assert response.json()["handledEvents"] == 1

    with Session(engine) as db:
        group = db.scalar(select(AIStockLineGroup))
        assert group is not None
        assert group.group_id == "C-ai-stock-group"
        assert group.active
