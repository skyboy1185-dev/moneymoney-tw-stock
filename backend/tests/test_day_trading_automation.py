import asyncio
from types import SimpleNamespace
from typing import Any

import app.services.day_trading_automation as automation_module


class FakeSession:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def __enter__(self) -> "FakeSession":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def commit(self) -> None:
        self.events.append("commit")


def test_web_entry_is_persisted_when_line_delivery_fails(monkeypatch: Any) -> None:
    events: list[str] = []
    recommendation = {
        "id": "2330-long",
        "symbol": "2330",
        "direction": "long",
        "status": "confirmed",
        "action": "buy",
        "expiresAt": (automation_module.datetime.now(automation_module.UTC) + automation_module.timedelta(minutes=5)).isoformat(),
        "isOfficialRecommendation": True,
        "momentumUniverseMember": True,
        "dataStatus": "normal",
        "dataMode": "official",
        "quoteIsRealtime": True,
        "confidenceScore": 90,
        "confirmationScore": 90,
        "healthScore": 90,
        "riskRewardRatio": 2,
        "spreadPercentage": 0.1,
        "volume": automation_module.TradingScheduleConfig().minimum_volume,
        "turnover": automation_module.TradingScheduleConfig().minimum_turnover,
        "stopDistancePercent": 1,
        "largeOrderDataAvailable": True,
        "largeOrderContinuousBuy": True,
        "largeOrderContinuousSell": False,
        "tradingEligible": True,
        "marketAlignment": 80,
    }
    monkeypatch.setattr(automation_module, "SessionLocal", lambda: FakeSession(events))
    monkeypatch.setattr(
        automation_module.day_trading_restrictions,
        "filter_candidates",
        lambda items: items,
    )
    monkeypatch.setattr(
        automation_module,
        "record_official_recommendations",
        lambda _db, _items, **_kwargs: events.append("web-entry"),
    )
    monkeypatch.setattr(
        automation_module,
        "ensure_positions_for_official_recommendations",
        lambda _db, _items, **_kwargs: [],
    )

    async def fail_line(_items: list[dict[str, Any]]) -> int:
        events.append("line-entry")
        raise RuntimeError("LINE unavailable")

    monkeypatch.setattr(
        automation_module.line_notification_dispatcher,
        "send_recommendations",
        fail_line,
    )

    sent = asyncio.run(
        automation_module.DayTradingAutomationSupervisor()._send_recommendations_and_track(
            [recommendation],
            automation_module.TradingScheduleConfig(),
            {"formalSignalsAllowed": True, "formalLongSignalsAllowed": True, "phase": "scanning", "statusMessage": ""},
            automation_module.datetime.now(automation_module.UTC),
        )
    )

    assert sent == 0
    assert events == ["web-entry", "commit", "line-entry"]


def test_web_exit_is_persisted_before_line_delivery(monkeypatch: Any) -> None:
    events: list[str] = []
    event = {
        "type": "emergency_exit",
        "level": "emergency",
        "action": "立即全部回補",
        "reason": "突破停損價",
        "price": 101.0,
        "position": {"symbol": "2317"},
        "_positionId": 7,
        "_terminal": True,
    }
    monkeypatch.setattr(automation_module, "SessionLocal", lambda: FakeSession(events))
    monkeypatch.setattr(
        automation_module,
        "pending_automatic_position_events",
        lambda *_args, **_kwargs: [event],
    )
    monkeypatch.setattr(
        automation_module,
        "finalize_automatic_position_event",
        lambda _db, _event: events.append("web-exit"),
    )

    async def fail_line(_event: dict[str, Any]) -> int:
        events.append("line-exit")
        raise RuntimeError("LINE unavailable")

    monkeypatch.setattr(
        automation_module.line_notification_dispatcher,
        "send_position_event",
        fail_line,
    )

    evaluated, sent = asyncio.run(
        automation_module.DayTradingAutomationSupervisor()._monitor_automatic_positions(
            data_status="normal",
            phase="scanning",
        )
    )

    assert (evaluated, sent) == (1, 0)
    assert events == ["commit", "web-exit", "commit", "line-exit"]


def test_quote_requests_are_deduped_before_refresh(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        automation_module.day_trading_restrictions,
        "is_disposed",
        lambda _symbol: False,
    )
    monkeypatch.setattr(
        automation_module.day_trading_restrictions,
        "market_restrictions_available",
        lambda _market: True,
    )
    stocks = [
        SimpleNamespace(symbol="2330", name="台積電", market="上市"),
        SimpleNamespace(symbol="2330", name="台積電 duplicate", market="上市"),
        SimpleNamespace(symbol="2354", name="鴻準", market="上市"),
    ]

    requests = automation_module._quote_requests_for_stocks(stocks)

    assert [request.symbol for request in requests] == ["2330", "2354"]


def test_baseline_quote_slice_rotates_without_refreshing_full_universe() -> None:
    supervisor = automation_module.DayTradingAutomationSupervisor()
    stocks = tuple(
        SimpleNamespace(symbol=str(1000 + index), name=f"Stock {index}", market="上市")
        for index in range(automation_module.BASELINE_QUOTE_BATCH_SIZE + 5)
    )

    first = supervisor._baseline_quote_slice(stocks)
    second = supervisor._baseline_quote_slice(stocks)

    assert len(first) == automation_module.BASELINE_QUOTE_BATCH_SIZE
    assert len(second) == automation_module.BASELINE_QUOTE_BATCH_SIZE
    assert first[0].symbol == "1000"
    assert second[0].symbol == str(1000 + automation_module.BASELINE_QUOTE_BATCH_SIZE)
