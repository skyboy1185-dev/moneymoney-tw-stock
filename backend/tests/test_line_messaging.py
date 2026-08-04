import asyncio
import base64
import hashlib
import hmac
from typing import Any

import httpx

from app.services.line_messaging import (
    LINE_GROUP_DISCLAIMER,
    PERSONAL_STRATEGY_SIMULATION_NOTE,
    LineMessagingClient,
    LineNotificationDispatcher,
    LineNotificationEvent,
    format_personal_strategy_simulation,
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


def test_group_disclaimer_contains_required_non_advisory_warning() -> None:
    assert LINE_GROUP_DISCLAIMER.startswith("⚠️ 免責聲明：")
    assert "自動化數據產出" in LINE_GROUP_DISCLAIMER
    assert "絕不構成" in LINE_GROUP_DISCLAIMER
    assert "請勿依此進行真實市場跟單" in LINE_GROUP_DISCLAIMER
    assert "自行判斷並自負盈虧" in LINE_GROUP_DISCLAIMER


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
    assert message.startswith("【個人策略模擬測試】")
    assert "標的：台積電 2330" in message
    assert "模擬進場點：995.00～1,000.00" in message
    assert "模擬停損/停利：980.00 / 1,020.00、1,040.00" in message
    assert message.endswith(PERSONAL_STRATEGY_SIMULATION_NOTE)

    demo_message = format_signal_message({
        **signal,
        "dataMode": "demo",
        "dataSource": "mock_opening_simulation",
    })
    assert demo_message.startswith("【個人策略模擬測試】")

    official_message = format_signal_message({
        **signal,
        "dataMode": "official_quote_demo_strategy",
        "dataSource": "TWSE MIS",
        "quoteStatus": "最近有效行情／收盤",
        "quoteTimestamp": "2026-07-24T13:30:00+08:00",
    })
    assert official_message.startswith("【個人策略模擬測試】")
    assert "行情來源" not in official_message

    emergency = format_position_message({
        "level": "emergency", "action": "立即全部回補", "price": 101,
        "reason": "突破停損價",
        "position": {
            "symbol": "2317", "stockName": "鴻海", "direction": "short", "stopLoss": 100,
        },
    })
    assert "【AI當沖機器人｜緊急回補】" in emergency
    assert "指令：立即全部回補" in emergency
    assert PERSONAL_STRATEGY_SIMULATION_NOTE in emergency


def test_personal_strategy_template_keeps_name_before_symbol() -> None:
    message = format_personal_strategy_simulation(
        stock_name="台積電",
        symbol="2330",
        entry_min=1000,
        entry_max=1000,
        stop_loss=980,
        target_1=1040,
    )
    assert message.splitlines() == [
        "【個人策略模擬測試】",
        "標的：台積電 2330",
        "模擬進場點：1,000.00",
        "模擬停損/停利：980.00 / 1,040.00",
        f"說明：{PERSONAL_STRATEGY_SIMULATION_NOTE}",
    ]


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


def test_same_stock_direction_uses_one_daily_formal_entry(monkeypatch: Any) -> None:
    dispatcher = LineNotificationDispatcher()
    captured: list[LineNotificationEvent] = []

    async def fake_dispatch_many(events: list[LineNotificationEvent]) -> int:
        captured.extend(events)
        return len(events)

    monkeypatch.setattr(dispatcher, "dispatch_many", fake_dispatch_many)
    base = {
        "symbol": "2317", "stockName": "鴻海", "direction": "long",
        "action": "突破買進", "price": 250, "entryMin": 249, "entryMax": 251,
        "stopLoss": 247, "target1": 255, "target2": 260,
        "confidenceScore": 85, "healthScore": 82, "riskRewardRatio": 2,
        "generatedAt": "2026-08-03T10:00:00+08:00",
        "quoteTimestamp": "2026-08-03T10:00:00+08:00",
        "expiresAt": "2026-08-03T10:05:00+08:00",
        "reasons": ["正式突破"], "warnings": [],
        "isOfficialRecommendation": True,
    }

    sent = asyncio.run(dispatcher.send_recommendations([
        {**base, "id": "2317-window-1"},
        {**base, "id": "2317-window-2", "quoteTimestamp": "2026-08-03T10:05:00+08:00"},
    ]))

    assert sent == 1
    assert len(captured) == 1
    assert captured[0].dedupe_key == "formal-entry:2026-08-03:2317:long"


def test_non_formal_confidence_candidate_does_not_send_line(
    monkeypatch: Any,
) -> None:
    dispatcher = LineNotificationDispatcher()
    captured: list[LineNotificationEvent] = []

    async def fake_dispatch_many(events: list[LineNotificationEvent]) -> int:
        captured.extend(events)
        return len(events)

    monkeypatch.setattr(dispatcher, "dispatch_many", fake_dispatch_many)
    candidate = {
        "id": "candidate-1802",
        "symbol": "1802",
        "stockName": "台玻",
        "direction": "short",
        "action": "放空資格待確認",
        "price": 48.6,
        "confidenceScore": 75,
        "healthScore": 90,
        "dataMode": "warming_up",
        "dataStatus": "normal",
        "quoteIsRealtime": True,
        "status": "temporary",
        "dataSource": "TWSE MIS",
        "quoteTimestamp": "2026-07-28T10:41:15+08:00",
        "qualificationFailures": ["放空資格待確認"],
        "warnings": ["請先向券商確認券源"],
    }

    sent = asyncio.run(dispatcher.send_confidence_candidates([
        candidate,
        {**candidate, "id": "candidate-low", "symbol": "2330", "confidenceScore": 74},
        {
            **candidate,
            "id": "candidate-reference",
            "symbol": "5340",
            "dataSource": "TWSE MIS 五檔參考價",
        },
        {**candidate, "id": "candidate-stale", "symbol": "2408", "quoteIsRealtime": False},
        {
            **candidate,
            "id": "candidate-chase-blocked",
            "symbol": "2330",
            "direction": "long",
            "confidenceScore": 95,
            "chaseBlocked": True,
        },
        {
            **candidate,
            "id": "formal-signal",
            "symbol": "2317",
            "confidenceScore": 90,
            "isOfficialRecommendation": True,
        },
    ]))

    assert sent == 0
    assert captured == []


def test_confidence_candidate_notifications_stay_disabled_for_many_rows(monkeypatch: Any) -> None:
    dispatcher = LineNotificationDispatcher()
    captured: list[LineNotificationEvent] = []

    async def fake_dispatch_many(events: list[LineNotificationEvent]) -> int:
        captured.extend(events)
        return len(events)

    monkeypatch.setattr(dispatcher, "dispatch_many", fake_dispatch_many)
    candidates = [
        {
            "id": f"candidate-{index}",
            "symbol": f"23{index:02d}",
            "stockName": f"候選{index}",
            "direction": "long",
            "action": "突破觀察",
            "price": 100,
            "confidenceScore": 75 + index,
            "healthScore": 80,
            "dataMode": "official",
            "dataStatus": "normal",
            "quoteIsRealtime": True,
            "status": "confirmed",
            "dataSource": "TWSE MIS",
            "quoteTimestamp": "2026-07-28T10:41:15+08:00",
            "qualificationFailures": [],
            "warnings": [],
        }
        for index in range(7)
    ]

    sent = asyncio.run(dispatcher.send_confidence_candidates(candidates))

    assert sent == 0
    assert captured == []
