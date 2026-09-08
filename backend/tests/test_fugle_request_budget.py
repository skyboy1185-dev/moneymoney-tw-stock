import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.services.fugle_request_budget import FugleBudgetUnavailable, FugleRequestBudget


@pytest.mark.parametrize("runtime,environment,blocked", [("railway", "development", True), ("local", "production", True), ("local", "development", False)])
def test_missing_shared_storage_fails_closed_except_local_development(monkeypatch, runtime, environment, blocked):
    from app import config
    from app.services import fugle_request_budget as module
    monkeypatch.setattr(module, "_budgets", {})
    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(
        redis_url=None, runtime_mode=runtime, app_env=environment, fugle_rest_requests_per_minute=600))
    item = module.get_fugle_request_budget("local-test-only")
    if blocked:
        with pytest.raises(FugleBudgetUnavailable):
            asyncio.run(item.acquire())
        assert item.diagnostics()["storage"] == "redis-unavailable"
    else:
        asyncio.run(item.acquire())
        assert item.diagnostics()["storage"] == "local"


class Clock:
    tick = 1_800_000_000.0
    async def sleep(self, seconds):
        self.tick += max(.000001, seconds)
        await asyncio.sleep(0)


def budget(clock, **kwargs):
    return FugleRequestBudget("fake-private-key", clock=lambda: clock.tick, sleep=clock.sleep, **kwargs)


def test_unknown_entitlement_is_conservative_and_actual_headers_control_ninety_percent():
    clock = Clock()
    item = budget(clock)
    assert item.diagnostics()["effectivePerMinute"] == 54
    assert item.diagnostics()["entitlementKnown"] is False
    asyncio.run(item.observe_response(200, {"X-RateLimit-Limit": "600", "X-RateLimit-Remaining": "599"}))
    assert item.diagnostics()["effectivePerMinute"] == 540
    assert item.diagnostics()["limitPerMinute"] == 600
    asyncio.run(item.observe_response(200, {"x-ratelimit-limit": "60"}))
    assert item.diagnostics()["effectivePerMinute"] == 54


def test_priority_reservation_does_not_allow_metadata_to_use_the_full_budget():
    clock, times = Clock(), []
    item = budget(clock)
    async def scenario():
        for _ in range(27):
            await item.acquire(priority="metadata")
            times.append(clock.tick)
    asyncio.run(scenario())
    clock.tick += 60 / 54
    assert item._try_local(.5) > 0
    assert item._try_local(1) == 0  # Reserved priority capacity remains available.
    assert all(b - a >= 1 for a, b in zip(times, times[1:]))


def test_retry_after_is_applied_before_next_consumer_request():
    clock = Clock()
    item = budget(clock)
    start = clock.tick
    async def scenario():
        await item.observe_response(429, {"retry-after": "20", "x-ratelimit-limit": "60"})
        await item.acquire(priority="priority")
    asyncio.run(scenario())
    assert clock.tick >= start + 20


def test_configured_redis_failure_fails_closed_without_local_fallback():
    class BrokenRedis:
        def eval(self, *_args):
            raise ConnectionError("unavailable")
    item = budget(Clock(), redis_client=BrokenRedis())
    with pytest.raises(FugleBudgetUnavailable):
        asyncio.run(item.acquire(priority="priority"))
    assert item.diagnostics()["storage"] == "redis-unavailable"


def test_redis_atomic_sliding_window_is_shared_across_instances():
    fakeredis = pytest.importorskip("fakeredis")
    clock = Clock()
    shared = fakeredis.FakeRedis(decode_responses=True)
    first, second = budget(clock, redis_client=shared), budget(clock, redis_client=shared)
    times = []
    async def scenario():
        await first.observe_response(200, {"x-ratelimit-limit": "60"})
        for index in range(60):
            await (first if index % 2 else second).acquire(priority="priority")
            times.append(clock.tick)
    asyncio.run(scenario())
    assert second.diagnostics()["limitPerMinute"] == 60
    assert times[-1] - times[0] >= 60
    assert all(sum(0 <= time - start < 60 for time in times) <= 54 for start in times)
    assert all("fake-private-key" not in key for key in shared.keys())


def test_redis_header_cooldown_propagates_to_a_different_instance():
    fakeredis = pytest.importorskip("fakeredis")
    clock = Clock()
    shared = fakeredis.FakeRedis(decode_responses=True)
    first, second = budget(clock, redis_client=shared), budget(clock, redis_client=shared)
    start = clock.tick
    async def scenario():
        await first.observe_response(429, {"retry-after": "30", "x-ratelimit-limit": "600"})
        await second.acquire(priority="priority")
    asyncio.run(scenario())
    assert clock.tick >= start + 30
    assert second.diagnostics()["effectivePerMinute"] == 540


def test_chip_flow_uses_injected_shared_budget_for_every_response():
    import httpx
    from app.services.chip_flow_provider import FugleRealtimeTradeProvider
    events = []
    class SharedBudget:
        async def acquire(self, *, priority):
            events.append(("acquire", priority))
        async def observe_response(self, status, headers):
            events.append(("observe", status, headers.get("x-ratelimit-limit")))
    provider = FugleRealtimeTradeProvider("fake-private-key", budget=SharedBudget(),
        transport=httpx.MockTransport(lambda _req: httpx.Response(429, headers={"retry-after": "30", "x-ratelimit-limit": "60"})))
    async def scenario():
        async with httpx.AsyncClient(transport=provider._transport, base_url="https://fixture.local") as client:
            response = await provider._paced_get(client, "/quote", params={})
            assert response.status_code == 429
    asyncio.run(scenario())
    assert events == [("acquire", "normal"), ("observe", 429, "60")]
