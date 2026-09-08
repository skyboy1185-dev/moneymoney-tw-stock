import asyncio
from uuid import uuid4

from app.services.fugle_stream_lease import FugleStreamLease


def test_one_owner_and_expired_owner_cannot_release_successor(monkeypatch):
    monkeypatch.setattr("app.services.fugle_stream_lease.get_settings", lambda: type("Settings", (), {
        "runtime_mode": "local", "app_env": "development", "redis_url": None,
    })())
    clock = [0.0]
    key = str(uuid4())
    first = FugleStreamLease(key, monotonic=lambda: clock[0])
    successor = FugleStreamLease(key, monotonic=lambda: clock[0])

    async def scenario():
        assert await first.acquire()
        assert not await successor.acquire()
        clock[0] = 10
        assert await first.renew()
        clock[0] = 24
        assert not await successor.acquire()
        clock[0] = 26
        assert await successor.acquire()
        assert not await first.renew()
        await first.release()
        assert await successor.renew()
        await successor.release()
        assert await first.acquire()
        await first.release()

    asyncio.run(scenario())


def test_production_cannot_fall_back_to_process_local_lease(monkeypatch):
    monkeypatch.setattr("app.services.fugle_stream_lease.get_settings", lambda: type("Settings", (), {
        "runtime_mode": "railway", "app_env": "production", "redis_url": None,
    })())
    lease = FugleStreamLease(str(uuid4()))
    assert not asyncio.run(lease.acquire())
    assert lease.last_error == "SharedLeaseUnavailable"


def test_redis_outage_does_not_create_a_second_stream_owner():
    class FailedRedis:
        def set(self, *_args, **_kwargs):
            raise TimeoutError("connection unavailable")

        def eval(self, *_args):
            raise TimeoutError("connection unavailable")

    lease = FugleStreamLease(str(uuid4()), redis_client=FailedRedis(), allow_local=True)
    assert not asyncio.run(lease.acquire())
    assert not asyncio.run(lease.renew())
    assert lease.last_error == "TimeoutError"
