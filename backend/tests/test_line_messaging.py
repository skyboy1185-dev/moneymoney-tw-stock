import asyncio
import base64
import hashlib
import hmac
from typing import Any

import httpx
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.models import LineDeliveryLog, LineNotificationGroup, LineNotificationSettings
import app.services.line_messaging as line_messaging_module
from app.services.line_messaging import (
    LINE_GROUP_DISCLAIMER,
    PERSONAL_STRATEGY_SIMULATION_NOTE,
    LineMessagingClient,
    LineApiResult,
    LineNotificationDispatcher,
    LineNotificationEvent,
    effective_daily_trade_message_limit,
    format_personal_strategy_simulation,
    format_position_message,
    format_signal_message,
    mask_group_id,
    verify_line_signature,
)


def test_daily_trade_notifications_stop_after_ten_but_system_events_continue(
    monkeypatch: Any,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    for table in (LineNotificationGroup.__table__, LineNotificationSettings.__table__, LineDeliveryLog.__table__):
        table.create(engine)
    test_session = sessionmaker(bind=engine, expire_on_commit=False)
    now = line_messaging_module.datetime.now(line_messaging_module.UTC)
    with test_session() as db:
        db.add(LineNotificationSettings(id=1, updated_at=now))
        for index in range(10):
            db.add(LineDeliveryLog(
                group_id="C-test-group",
                event_type="long_entry" if index % 2 == 0 else "long_exit",
                signal_id=f"signal-{index}",
                symbol=f"23{index:02d}",
                action="trade",
                priority=1,
                dedupe_key=f"existing-{index}",
                status="sent",
                attempts=1,
                response_status=200,
                message_preview="trade",
                created_at=now,
                sent_at=now,
            ))
        db.commit()

    settings = line_messaging_module.get_settings()
    monkeypatch.setattr(settings, "line_notifications_enabled", True)
    monkeypatch.setattr(settings, "line_target_group_id", "C-test-group")
    monkeypatch.setattr(settings, "line_daily_trade_message_limit", 10)
    monkeypatch.setattr(line_messaging_module, "SessionLocal", test_session)

    pushed: list[str] = []

    async def fake_push(_: str, message: str) -> LineApiResult:
        pushed.append(message)
        return LineApiResult(True, 1, 200, None)

    dispatcher = LineNotificationDispatcher()
    monkeypatch.setattr(dispatcher.client, "push_text", fake_push)

    async def fake_quota() -> tuple[int, int]:
        return 200, 190

    monkeypatch.setattr(dispatcher.client, "message_quota", fake_quota)

    blocked = asyncio.run(dispatcher.dispatch_many([
        LineNotificationEvent("stop_loss", "sell", "blocked trade", "trade-11", 0),
    ]))
    system_sent = asyncio.run(dispatcher.dispatch_many([
        LineNotificationEvent("opening", "open", "system message", "opening-test", 3),
    ]))

    assert blocked == 0
    assert system_sent == 1
    assert pushed == ["system message"]
    with test_session() as db:
        skipped = db.scalar(select(func.count()).select_from(LineDeliveryLog).where(
            LineDeliveryLog.status == "skipped",
            LineDeliveryLog.dedupe_key == "trade-11",
        ))
        assert skipped == 1


def test_daily_limit_expands_as_monthly_reset_approaches() -> None:
    assert effective_daily_trade_message_limit(
        monthly_limit=200,
        monthly_usage=50,
        remaining_trading_days=20,
        base_limit=10,
    ) == 8
    assert effective_daily_trade_message_limit(
        monthly_limit=200,
        monthly_usage=50,
        remaining_trading_days=15,
        base_limit=10,
    ) == 10
    assert effective_daily_trade_message_limit(
        monthly_limit=200,
        monthly_usage=50,
        remaining_trading_days=5,
        base_limit=10,
    ) == 30
    assert effective_daily_trade_message_limit(
        monthly_limit=200,
        monthly_usage=199,
        remaining_trading_days=5,
        base_limit=10,
    ) == 1
    assert effective_daily_trade_message_limit(
        monthly_limit=200,
        monthly_usage=150,
        remaining_trading_days=20,
        base_limit=200,
        minimum_daily_limit=6,
    ) == 6


def test_daily_limit_uses_fallback_only_when_line_quota_is_unavailable() -> None:
    assert effective_daily_trade_message_limit(
        monthly_limit=None,
        monthly_usage=None,
        remaining_trading_days=20,
        base_limit=200,
    ) == 200


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

    combined = format_signal_message(signal, include_session_status=True)
    assert combined.startswith("【AI當沖機器人｜今日首次進場】")
    assert "啟動：09:05 正式訊號掃描已啟動" in combined
    assert "結束：12:00 停止新進場，13:30 完成當沖部位處理" in combined
    assert "【個人策略模擬測試】" in combined

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


def test_session_status_is_attached_only_to_first_enabled_entry(
    monkeypatch: Any,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    for table in (
        LineNotificationGroup.__table__,
        LineNotificationSettings.__table__,
        LineDeliveryLog.__table__,
    ):
        table.create(engine)
    test_session = sessionmaker(bind=engine, expire_on_commit=False)
    now = line_messaging_module.datetime.now(line_messaging_module.UTC)
    with test_session() as db:
        db.add(LineNotificationSettings(id=1, updated_at=now))
        db.commit()

    settings = line_messaging_module.get_settings()
    monkeypatch.setattr(settings, "line_notifications_enabled", True)
    monkeypatch.setattr(settings, "line_target_group_id", "C-test-group")
    monkeypatch.setattr(settings, "line_daily_trade_message_limit", 200)
    monkeypatch.setattr(line_messaging_module, "SessionLocal", test_session)
    pushed: list[str] = []

    async def fake_push(_: str, message: str) -> LineApiResult:
        pushed.append(message)
        return LineApiResult(True, 1, 200, None)

    dispatcher = LineNotificationDispatcher()
    monkeypatch.setattr(dispatcher.client, "push_text", fake_push)

    async def fake_quota() -> tuple[int, int]:
        return 200, 0

    monkeypatch.setattr(dispatcher.client, "message_quota", fake_quota)

    def signal(symbol: str) -> dict[str, Any]:
        return {
            "id": f"signal-{symbol}",
            "symbol": symbol,
            "stockName": f"股票{symbol}",
            "direction": "long",
            "action": "突破買進",
            "entryMin": 100,
            "entryMax": 101,
            "stopLoss": 98,
            "target1": 103,
            "target2": 105,
            "isOfficialRecommendation": True,
            "generatedAt": "2026-08-10T09:20:00+08:00",
        }

    assert asyncio.run(dispatcher.send_recommendations([
        signal("2330"), signal("2317"),
    ])) == 2
    assert asyncio.run(dispatcher.send_recommendations([signal("2308")])) == 1
    assert len(pushed) == 3
    assert pushed[0].startswith("【AI當沖機器人｜今日首次進場】")
    assert not pushed[1].startswith("【AI當沖機器人｜今日首次進場】")
    assert not pushed[2].startswith("【AI當沖機器人｜今日首次進場】")


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


def test_line_rate_limit_429_retries_three_times(monkeypatch: Any) -> None:
    calls = 0

    async def fake_post(*_: Any, **__: Any) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, json={"message": "Too many requests"})

    async def no_sleep(_: float) -> None:
        return None

    client = LineMessagingClient()
    monkeypatch.setattr(client._settings, "line_channel_access_token", "test-token")
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    result = asyncio.run(client.push_text("C-test-group", "test"))

    assert not result.success
    assert result.attempts == 3
    assert result.response_status == 429
    assert result.error == "LINE API HTTP 429: Too many requests"
    assert calls == 3


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


def test_line_recommendations_are_capped_at_ten_per_batch(monkeypatch: Any) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    LineNotificationSettings.__table__.create(engine)
    LineDeliveryLog.__table__.create(engine)
    test_session = sessionmaker(bind=engine, expire_on_commit=False)
    with test_session() as db:
        db.add(LineNotificationSettings(
            id=1,
            updated_at=line_messaging_module.datetime.now(line_messaging_module.UTC),
        ))
        db.commit()
    monkeypatch.setattr(line_messaging_module, "SessionLocal", test_session)
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
        for index in range(11)
    ]
    recommendations[0]["action"] = "5 分 K 順勢買進"
    sent = asyncio.run(dispatcher.send_recommendations(recommendations))
    assert sent == 10
    assert len(captured) == 10


def test_same_stock_direction_uses_one_daily_formal_entry(monkeypatch: Any) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    LineNotificationSettings.__table__.create(engine)
    LineDeliveryLog.__table__.create(engine)
    test_session = sessionmaker(bind=engine, expire_on_commit=False)
    with test_session() as db:
        db.add(LineNotificationSettings(
            id=1,
            updated_at=line_messaging_module.datetime.now(line_messaging_module.UTC),
        ))
        db.commit()
    monkeypatch.setattr(line_messaging_module, "SessionLocal", test_session)
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
