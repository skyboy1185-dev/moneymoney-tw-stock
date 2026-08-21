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
    recommendation = {"id": "2330-long", "symbol": "2330"}
    monkeypatch.setattr(automation_module, "SessionLocal", lambda: FakeSession(events))
    monkeypatch.setattr(
        automation_module.day_trading_restrictions,
        "filter_candidates",
        lambda items: items,
    )
    monkeypatch.setattr(
        automation_module,
        "record_official_recommendations",
        lambda _db, _items: events.append("web-entry"),
    )
    monkeypatch.setattr(
        automation_module,
        "ensure_positions_for_official_recommendations",
        lambda _db, _items: [],
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
