from datetime import UTC, datetime, timedelta
from typing import Any

from app.routers import day_trading


def _patch_trading_date(monkeypatch: Any) -> None:
    start = datetime(2026, 8, 27, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(
        day_trading,
        "_daily_period",
        lambda: ("2026-08-27", start, start + timedelta(days=1)),
    )


def test_cached_rankings_filter_direction_without_live_recalculation(monkeypatch: Any) -> None:
    _patch_trading_date(monkeypatch)
    monkeypatch.setattr(
        day_trading.day_trading_cache,
        "get",
        lambda key: {
            "tradingDate": "2026-08-27",
            "items": [
                {"id": "long", "direction": "long", "rank": 9},
                {"id": "short", "direction": "short", "rank": 8},
            ],
            "recommendedTotal": 1,
            "maximumRecommendations": 10,
            "summary": "cached",
            "updatedAt": "2026-08-27T01:00:00+00:00",
            "source": "automation_cache",
        },
    )

    payload = day_trading._automation_cached_rankings("long")

    assert payload is not None
    assert payload["rankingSource"] == "automation_cache"
    assert payload["total"] == 1
    assert payload["items"] == [{"id": "long", "direction": "long", "rank": 1}]


def test_cached_rankings_ignore_previous_trading_date(monkeypatch: Any) -> None:
    _patch_trading_date(monkeypatch)
    monkeypatch.setattr(
        day_trading.day_trading_cache,
        "get",
        lambda key: {"tradingDate": "2026-08-26", "items": [{"id": "old"}]},
    )

    assert day_trading._automation_cached_rankings("all") is None


def test_cached_selection_filters_user_open_signal(monkeypatch: Any) -> None:
    _patch_trading_date(monkeypatch)
    monkeypatch.setattr(
        day_trading.day_trading_cache,
        "get",
        lambda key: {
            "tradingDate": "2026-08-27",
            "recommended": [
                {"id": "already-open", "direction": "long"},
                {"id": "fresh", "direction": "long"},
            ],
            "candidates": [{"id": "candidate", "direction": "long"}],
            "maximumRecommendations": 10,
            "summary": "cached",
            "session": {"phase": "scanning"},
            "regime": {"dataStatus": "normal", "mode": "official"},
            "updatedAt": "2026-08-27T01:00:00+00:00",
            "source": "automation_cache",
        },
    )

    class ScalarResult:
        @staticmethod
        def all() -> list[str]:
            return ["already-open"]

    class FakeDb:
        @staticmethod
        def scalars(_: object) -> ScalarResult:
            return ScalarResult()

    selection = day_trading._automation_cached_selection(FakeDb(), "test-user")

    assert selection is not None
    assert selection["selectionSource"] == "automation_cache"
    assert selection["recommended"] == [{"id": "fresh", "direction": "long"}]
    assert selection["candidates"] == [{"id": "candidate", "direction": "long"}]
