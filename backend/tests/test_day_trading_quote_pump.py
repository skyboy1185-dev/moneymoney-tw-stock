import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app import config as config_module
from app.services import day_trading_quote_pump as pump_module
from app.services import fugle_live_quotes as fugle_module
from app.services import official_market_data as market
from app.services.day_trading_quote_pump import DayTradingQuotePump
from app.services.official_market_data import StockQuoteRequest, TwseMisMarketDataProvider


def stocks(count, start=1000):
    return [StockQuoteRequest(str(start + i), "test", "上市") for i in range(count)]


class Clock:
    value = 0.0

    def monotonic(self):
        return self.value

    def utcnow(self):
        return datetime(2026, 9, 8, 1, tzinfo=UTC) + timedelta(seconds=self.value)


class Provider:
    def __init__(self, *, fail=False):
        self.calls = []
        self.fail = fail

    async def fetch_intraday_batch(self, batch):
        self.calls.append([row.symbol for row in batch])
        if self.fail:
            raise TimeoutError("test")
        return {row.symbol: SimpleNamespace(quote_timestamp="2026-09-08T09:00:00+08:00") for row in batch}


def make_pump(provider=None):
    clock, published = Clock(), []
    provider = provider or Provider()
    pump = DayTradingQuotePump(provider=provider, publisher=published.append,
                               monotonic=clock.monotonic, utcnow=clock.utcnow)
    return pump, provider, clock, published


def test_rate_limit_incremental_publish_and_two_priority_one_baseline():
    pump, provider, clock, published = make_pump()
    pump.update_targets(stocks(30), stocks(100, 2000))

    async def scenario():
        assert await pump.run_once()
        assert len(published) == 1
        assert not await pump.run_once()
        clock.value = .99
        assert not await pump.run_once()
        for at in (1, 2, 3):
            clock.value = at
            assert await pump.run_once()

    asyncio.run(scenario())
    assert provider.calls == [[str(1000 + i) for i in range(10)],
                              [str(1010 + i) for i in range(10)],
                              [str(2000 + i) for i in range(10)],
                              [str(1020 + i) for i in range(10)]]
    assert len(published) == 4
    assert pump.state["lastReceivedAt"] != pump.state["latestQuoteAt"]


def test_failed_symbols_wait_five_seconds_and_do_not_starve_baseline():
    pump, provider, clock, published = make_pump(Provider(fail=True))
    pump.update_targets(stocks(10), stocks(10, 2000), mandatory_symbols=[row.symbol for row in stocks(10)])

    async def scenario():
        assert await pump.run_once()
        clock.value = 1
        assert await pump.run_once()  # A failed holding cannot monopolize polling.
        clock.value = 4.99
        assert not await pump.run_once()
        clock.value = 5
        assert await pump.run_once()

    asyncio.run(scenario())
    assert len(provider.calls) == 3
    assert provider.calls[0] == provider.calls[2]
    assert not published
    assert pump.state["failureCount"] == 3
    assert pump.state["lastReceivedAt"] is None


def test_baseline_rotates_eighty_every_thirty_seconds():
    pump, provider, clock, _ = make_pump()
    pump.update_targets([], stocks(100))

    async def scenario():
        for at in range(8):
            clock.value = at
            assert await pump.run_once()
        clock.value = 29.99
        assert not await pump.run_once()
        clock.value = 30
        assert await pump.run_once()

    asyncio.run(scenario())
    assert len({symbol for batch in provider.calls[:8] for symbol in batch}) == 80
    assert provider.calls[-1] == [str(1080 + i) for i in range(10)]


def test_new_priority_interrupts_pending_baseline_next_batch_and_disabled_does_not_fetch():
    pump, provider, clock, _ = make_pump()
    pump.update_targets([], stocks(100))

    async def scenario():
        await pump.run_once()
        pump.update_targets(stocks(1, 2000), stocks(100))
        clock.value = 1
        await pump.run_once()
        pump.update_targets(stocks(1), stocks(100), enabled=False)
        clock.value = 20
        assert not await pump.run_once()

    asyncio.run(scenario())
    assert provider.calls[1] == ["2000"]


def test_capacity_preserves_all_mandatory_and_routes_overflow_to_baseline():
    pump, _, _, _ = make_pump()
    rows = stocks(40)
    pump.update_targets(rows, [], mandatory_symbols=[row.symbol for row in rows[:35]])
    assert pump.state["priorityCount"] == 35
    assert pump.state["overCapacity"] is True
    assert pump.state["priorityOverflowCount"] == 5
    assert pump.state["baselineCount"] == 5
    pump.update_targets(rows, [], mandatory_symbols=["1000"])
    assert [row.symbol for row in pump._priority] == [row.symbol for row in rows[:30]]
    assert pump.state["overCapacity"] is True
    assert pump.state["mandatoryOverCapacity"] is False
    snapshot = pump.diagnostics()
    snapshot["priorityCount"] = 999
    assert pump.state["priorityCount"] == 30


def test_blocking_next_batch_does_not_hide_previous_publication_and_stop_cancels():
    async def scenario():
        entered, cancelled = asyncio.Event(), asyncio.Event()

        class BlockingSecond(Provider):
            async def fetch_intraday_batch(self, batch):
                if self.calls:
                    entered.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        cancelled.set()
                return await super().fetch_intraday_batch(batch)

        pump, _, clock, published = make_pump(BlockingSecond())
        pump.update_targets(stocks(20), [])
        await pump.run_once()
        assert len(published) == 1
        clock.value = 1
        await pump.start()
        await entered.wait()
        assert len(published) == 1
        await pump.stop()
        assert cancelled.is_set()
        assert pump.state["status"] == "stopped"

    asyncio.run(scenario())


def test_hard_batch_deadline_has_no_inline_retry(monkeypatch):
    monkeypatch.setattr(pump_module, "REQUEST_TIMEOUT_SECONDS", .01)

    class Blocked(Provider):
        async def fetch_intraday_batch(self, batch):
            self.calls.append(batch)
            await asyncio.Event().wait()

    pump, provider, _, published = make_pump(Blocked())
    pump.update_targets(stocks(1), [])
    asyncio.run(pump.run_once())
    assert len(provider.calls) == 1
    assert pump.state["failureCount"] == 1
    assert not published


def test_dedicated_provider_ignores_shared_lock_and_keeps_exchange_timestamp(monkeypatch):
    calls = []
    quote_time = datetime.now(UTC).astimezone(market.TAIPEI).replace(microsecond=0)

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"msgArray": [{"c": "2330", "y": "100", "z": "101", "v": "1000",
                                   "d": quote_time.strftime("%Y%m%d"), "t": quote_time.strftime("%H:%M:%S")}]}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, *args, **kwargs):
            calls.append(kwargs)
            return Response()

    monkeypatch.setattr(market.httpx, "AsyncClient", lambda **kwargs: Client())

    async def scenario():
        provider = TwseMisMarketDataProvider()
        # Neither this instance's compatibility lock nor the global provider
        # lock is part of the dedicated, exclusively owned batch method.
        async with market.official_market_data_provider._lock:
            async with provider._lock:
                return await provider.fetch_intraday_batch([StockQuoteRequest("2330", "test", "上市")])

    result = asyncio.run(scenario())
    assert len(calls) == 1
    assert result["2330"].quote_timestamp == quote_time.isoformat()


def test_intraday_provider_rejects_oversized_batch_without_network():
    with pytest.raises(ValueError, match="at most 10"):
        asyncio.run(TwseMisMarketDataProvider().fetch_intraday_batch(stocks(11)))


def test_removing_then_restoring_a_target_does_not_bypass_retry_cooldown():
    pump, provider, clock, _ = make_pump()

    async def scenario():
        pump.update_targets(stocks(1), [])
        await pump.run_once()
        pump.update_targets([], [])
        clock.value = 1
        pump.update_targets(stocks(1), [])
        assert not await pump.run_once()
        clock.value = 5
        assert await pump.run_once()

    asyncio.run(scenario())
    assert len(provider.calls) == 2


def test_provider_timeout_cancels_single_request_without_retry(monkeypatch):
    calls, cancelled = [], []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, *_args, **_kwargs):
            calls.append(1)
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(True)

    monkeypatch.setattr(market.httpx, "AsyncClient", lambda **kwargs: Client())
    with pytest.raises(TimeoutError):
        asyncio.run(TwseMisMarketDataProvider().fetch_intraday_batch(stocks(1), timeout_seconds=.01))
    assert calls == [1]
    assert cancelled == [True]


def test_provider_does_not_retimestamp_missing_trades_or_overwrite_newer_quote(monkeypatch):
    provider = TwseMisMarketDataProvider()
    now = datetime.now(UTC).astimezone(market.TAIPEI).replace(microsecond=0)
    when = now - timedelta(seconds=10)
    original = market.parse_mis_quote({"c": "2330", "z": "101", "y": "100", "v": "1000",
                                      "d": when.strftime("%Y%m%d"), "t": when.strftime("%H:%M:%S")},
                                     StockQuoteRequest("2330", "test", "上市"), now=now)
    # Freeze quote validation during a trading session even when tests run at night.
    monkeypatch.setattr(market, "_is_realtime_quote", lambda *_args, **_kwargs: True)
    provider._last_trades["2330"] = original
    provider._cache["2330"] = (original, datetime.now(UTC))
    row = {"c": "2330", "z": "-", "y": "100", "v": "1001",
           "d": now.strftime("%Y%m%d"), "t": now.strftime("%H:%M:%S")}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, *_args, **_kwargs):
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"msgArray": [row]})

    monkeypatch.setattr(market.httpx, "AsyncClient", lambda **kwargs: Client())
    result = asyncio.run(provider.fetch_intraday_batch([StockQuoteRequest("2330", "test", "上市")]))
    assert result["2330"].quote_timestamp == original.quote_timestamp
    row.update(z="99", t=(when - timedelta(seconds=5)).strftime("%H:%M:%S"))
    assert asyncio.run(provider.fetch_intraday_batch([StockQuoteRequest("2330", "test", "上市")])) == {}
    assert provider._cache["2330"][0].quote_timestamp == original.quote_timestamp


def test_dedicated_client_reuses_connection_and_closes_with_pump_lifecycle(monkeypatch):
    clients = []

    class Client:
        def __init__(self):
            self.calls, self.closed = 0, False
            clients.append(self)

        async def get(self, *_args, **_kwargs):
            self.calls += 1
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"msgArray": []})

        async def aclose(self):
            self.closed = True

    monkeypatch.setattr(market.httpx, "AsyncClient", lambda **kwargs: Client())

    async def scenario():
        pump, _, clock, _ = make_pump(TwseMisMarketDataProvider())
        await pump.start()
        await pump.start()  # Idempotent: no extra task/client.
        pump.update_targets(stocks(20), [])
        await pump.run_once()
        clock.value = 1
        await pump.run_once()
        assert len(clients) == 1
        assert clients[0].calls == 2
        await pump.stop()
        assert clients[0].closed
        await pump.start()
        assert len(clients) == 2
        await pump.stop()
        assert clients[1].closed

    asyncio.run(scenario())


def test_free_quote_mode_never_initializes_paid_fugle_adapter(monkeypatch):
    monkeypatch.setattr(config_module, "get_settings", lambda: SimpleNamespace(free_quote_only=True))

    class PaidAdapterMustNotStart:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("free quote mode must not initialize Fugle")

    monkeypatch.setattr(fugle_module, "FugleLiveQuotes", PaidAdapterMustNotStart)

    async def scenario():
        pump = DayTradingQuotePump()
        pump._provider = Provider()
        await pump.start()
        assert pump._fugle is None
        await pump.stop()

    asyncio.run(scenario())
