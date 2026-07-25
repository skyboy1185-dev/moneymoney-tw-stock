import asyncio
import base64
import hashlib
import hmac
from typing import Any

import httpx

from app.services.line_messaging import (
    LineMessagingClient,
    LineNotificationDispatcher,
    LineNotificationEvent,
    format_position_message,
    format_signal_message,
    mask_group_id,
    verify_line_signature,
)


def test_line_signature_uses_raw_body_hmac_sha256() -> None:
    body = b'{"events":[{"message":{"text":"\\u7d81\\u5b9a"}}]}'
    secret = "test-channel-secret"
    signature = base64.b64encode(
        hmac.new(secret.encode(), body, hashlib.sha256).digest(),
    ).decode()
    assert verify_line_signature(body, signature, secret)
    assert not verify_line_signature(body + b" ", signature, secret)
    assert not verify_line_signature(body, "invalid", secret)


def test_group_id_is_masked() -> None:
    masked = mask_group_id("C1234567890abcdefghijkl")
    assert masked.startswith("C12345")
    assert masked.endswith("ijkl")
    assert "7890abcdef" not in masked


def test_signal_and_emergency_messages_follow_required_format() -> None:
    signal: dict[str, Any] = {
        "symbol": "2330", "stockName": "台積電", "price": 1000,
        "action": "突破買進", "entryMin": 995, "entryMax": 1000,
        "stopLoss": 980, "target1": 1020, "target2": 1040,
        "confidenceScore": 88, "healthScore": 82, "riskRewardRatio": 2.1,
        "expiresAt": "2026-07-27T01:10:00+00:00",
        "generatedAt": "2026-07-27T01:05:00+00:00",
        "direction": "long", "reasons": ["站上 VWAP"], "warnings": ["禁止追價"],
    }
    message = format_signal_message(signal)
    assert "【AI當沖機器人｜做多訊號】" in message
    assert "股票：2330 台積電" in message
    assert "推薦原因：\n- 站上 VWAP" in message
    assert "僅供研究參考，不構成投資建議。" in message

    demo_message = format_signal_message({
        **signal,
        "dataMode": "demo",
        "dataSource": "mock_opening_simulation",
    })
    assert demo_message.startswith("【展示模式，非即時行情】")

    official_message = format_signal_message({
        **signal,
        "dataMode": "official_quote_demo_strategy",
        "dataSource": "TWSE MIS",
        "quoteStatus": "最近有效行情／收盤",
        "quoteTimestamp": "2026-07-24T13:30:00+08:00",
    })
    assert official_message.startswith("【官方市場報價｜策略展示模式】")
    assert "行情來源：TWSE MIS" in official_message
    assert "報價狀態：最近有效行情／收盤" in official_message
    assert "行情時間：2026-07-24 13:30:00" in official_message

    emergency = format_position_message({
        "level": "emergency", "action": "立即全部回補", "price": 101,
        "reason": "突破停損價",
        "position": {
            "symbol": "2317", "stockName": "鴻海", "direction": "short", "stopLoss": 100,
        },
    })
    assert "【AI當沖機器人｜緊急回補】" in emergency
    assert "指令：立即全部回補" in emergency


def test_push_retries_at_most_three_times(monkeypatch: Any) -> None:
    calls = 0
    retry_keys: list[str] = []

    async def fake_post(*_: Any, **kwargs: Any) -> httpx.Response:
        nonlocal calls
        calls += 1
        retry_keys.append(kwargs["headers"]["X-Line-Retry-Key"])
        return httpx.Response(503)

    async def no_sleep(_: float) -> None:
        return None

    client = LineMessagingClient()
    monkeypatch.setattr(client._settings, "line_channel_access_token", "test-token")
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    result = asyncio.run(client.push_text("C-test-group", "test"))
    assert not result.success
    assert result.attempts == 3
    assert calls == 3
    assert len(set(retry_keys)) == 1


def test_line_retry_key_treats_accepted_409_as_success(monkeypatch: Any) -> None:
    statuses = iter([503, 409])

    async def fake_post(*_: Any, **__: Any) -> httpx.Response:
        return httpx.Response(next(statuses))

    async def no_sleep(_: float) -> None:
        return None

    client = LineMessagingClient()
    monkeypatch.setattr(client._settings, "line_channel_access_token", "test-token")
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    result = asyncio.run(client.push_text("C-test-group", "test"))
    assert result.success
    assert result.attempts == 2
    assert result.response_status == 409


def test_queue_sends_emergency_before_entry(monkeypatch: Any) -> None:
    order: list[str] = []
    dispatcher = LineNotificationDispatcher()

    async def fake_dispatch(event: LineNotificationEvent) -> int:
        order.append(event.event_type)
        return 1

    monkeypatch.setattr(dispatcher, "_dispatch", fake_dispatch)

    async def run() -> None:
        await dispatcher.start()
        entry = LineNotificationEvent("long_entry", "突破買進", "entry", "entry-1", 6)
        emergency = LineNotificationEvent("stop_loss", "立即停損", "exit", "exit-1", 0)
        first = asyncio.create_task(dispatcher.dispatch_many([entry]))
        await asyncio.sleep(0)
        second = asyncio.create_task(dispatcher.dispatch_many([emergency]))
        await asyncio.gather(first, second)
        await dispatcher.stop()

    asyncio.run(run())
    assert order == ["stop_loss", "long_entry"]


def test_line_recommendations_are_capped_at_five_per_batch(monkeypatch: Any) -> None:
    dispatcher = LineNotificationDispatcher()
    captured: list[LineNotificationEvent] = []

    async def fake_dispatch_many(events: list[LineNotificationEvent]) -> int:
        captured.extend(events)
        return len(events)

    monkeypatch.setattr(dispatcher, "dispatch_many", fake_dispatch_many)
    recommendations = [
        {
            "id": f"signal-{index}",
            "symbol": f"23{index:02d}",
            "stockName": f"測試股{index}",
            "direction": "long",
            "action": "突破買進",
            "price": 100,
            "entryMin": 99,
            "entryMax": 100,
            "stopLoss": 97,
            "target1": 104,
            "target2": 108,
            "confidenceScore": 90,
            "healthScore": 85,
            "riskRewardRatio": 2,
            "generatedAt": "2026-07-24T09:03:00+08:00",
            "expiresAt": "2026-07-24T09:08:00+08:00",
            "reasons": ["站上 VWAP"],
            "warnings": [],
            "isOfficialRecommendation": True,
        }
        for index in range(6)
    ]
    sent = asyncio.run(dispatcher.send_recommendations(recommendations))
    assert sent == 5
    assert len(captured) == 5
