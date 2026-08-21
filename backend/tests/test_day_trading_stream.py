import asyncio
from typing import Any

from app.routers import day_trading


def test_stream_releases_database_connection_before_yield(monkeypatch: Any) -> None:
    connection_active = False

    class ScalarResult:
        @staticmethod
        def all() -> list[object]:
            return []

    class TrackingSession:
        def __enter__(self) -> "TrackingSession":
            nonlocal connection_active
            connection_active = True
            return self

        def __exit__(self, *_: object) -> None:
            nonlocal connection_active
            connection_active = False

        @staticmethod
        def scalars(_: object) -> ScalarResult:
            return ScalarResult()

        @staticmethod
        def commit() -> None:
            return None

    class ConnectedRequest:
        @staticmethod
        async def is_disconnected() -> bool:
            return False

    selection = {
        "recommended": [],
        "candidates": [],
        "totalRecommended": 0,
        "maximumRecommendations": 5,
        "summary": {},
        "session": {"phase": "scanning"},
        "infrastructure": {},
        "regime": {"dataStatus": "normal", "mode": "live"},
    }
    monkeypatch.setattr(day_trading, "SessionLocal", TrackingSession)
    monkeypatch.setattr(day_trading, "_selection", lambda *_args, **_kwargs: selection)
    monkeypatch.setattr(day_trading.day_trading_engine, "market_regime", lambda: selection["regime"])
    monkeypatch.setattr(day_trading.day_trading_engine, "consume_scenario", lambda: None)
    monkeypatch.setattr(day_trading.day_trading_engine, "signals", lambda: [])
    monkeypatch.setattr(day_trading.day_trading_cache, "put", lambda *_args: None)
    monkeypatch.setattr(day_trading.day_trading_cache, "publish", lambda *_args: None)

    async def consume_first_event() -> None:
        stream = day_trading._stream_events(ConnectedRequest(), "test-user")
        assert await anext(stream) == "retry: 2000\n\n"
        event = await anext(stream)
        assert "event:" in event
        assert connection_active is False
        await stream.aclose()

    asyncio.run(consume_first_event())
