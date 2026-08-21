from types import SimpleNamespace
from typing import Any

import app.services.day_trading_cache as cache_module


def settings(app_env: str) -> SimpleNamespace:
    return SimpleNamespace(app_env=app_env, redis_url=None)


def test_development_memory_cache_allows_formal_signals(monkeypatch: Any) -> None:
    monkeypatch.setattr(cache_module, "get_settings", lambda: settings("development"))
    cache = cache_module.DayTradingCache()

    assert cache.mode == "memory"
    assert cache.healthy is False
    assert cache.ready_for_formal_signals is True
    assert cache.status == "memory_fallback"


def test_production_memory_cache_still_blocks_formal_signals(monkeypatch: Any) -> None:
    monkeypatch.setattr(cache_module, "get_settings", lambda: settings("production"))
    cache = cache_module.DayTradingCache()

    assert cache.mode == "memory"
    assert cache.healthy is False
    assert cache.ready_for_formal_signals is False
    assert cache.status == "unavailable"
