import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from app.services.day_trading_quote_pump import DayTradingQuotePump
from app.services.official_market_data import OfficialStockQuote, StockQuoteRequest


class Clock:
    tick = 0
    def now(self):
        return datetime(2026, 9, 8, 1, tzinfo=UTC) + timedelta(seconds=self.tick)


def quote(clock, source="FUGLE", symbol="2330"):
    return OfficialStockQuote(symbol, "test", 100, 99, 99, 101, 98, 1000, 1, 1,
                              clock.now().isoformat(), source, True,
                              quote_kind="index" if symbol == "t00" else "trade")


class Adapter:
    def __init__(self, *, ready=True):
        self.info = {"entitlementReady": ready, "wsSubscriptionLimit": 300 if ready else 5,
                     "wsConnected": True, "acknowledgedCount": 1, "desiredCount": 1,
                     "subscriptionSymbols": ["2330"], "overCapacity": False}
    def diagnostics(self):
        return dict(self.info)
    def update_targets(self, *args, **kwargs):
        self.enabled = kwargs.get("enabled", True)


def test_halt_blocks_older_cross_source_quotes_until_new_trade():
    for flag in ("is_halted", "is_trial"):
        pump, clock, _adapter, _mis, published = fixture()
        original = quote(clock)
        pump.ingest_fugle({"2330": original})
        pump.drain_quotes()
        pump.ingest_fugle({"2330": original}, source="FUGLE_REST")
        pump.drain_quotes()
        clock.tick = 5
        negative = replace(original, **{flag: True}, received_at=clock.now().isoformat())
        pump.ingest_fugle({"2330": negative})
        assert getattr(published[-1]["2330"], flag)
        assert pump.state["activeSource"] == "NONE"
        assert pump.state["quoteCoverageCount"] == 0
        clock.tick = 6
        pump.ingest_fugle({"2330": replace(original, received_at=clock.now().isoformat())}, source="FUGLE_REST")
        pump.drain_quotes()
        assert getattr(published[-1]["2330"], flag)
        # Only a truly newer exchange trade can release the halt marker.
        pump.ingest_fugle({"2330": quote(clock)}, source="FUGLE_REST")
        pump.drain_quotes()
        assert not getattr(published[-1]["2330"], flag)
        assert pump.state["quoteCoverageCount"] == 1


def test_shadow_halt_never_invalidates_canonical_mis():
    pump, clock, _adapter, _mis, published = fixture(publish=False)
    asyncio.run(pump.run_once())
    count, last_success = len(published), pump.state["lastSuccessAt"]
    clock.tick = 1
    pump.ingest_fugle({"2330": replace(quote(clock), is_halted=True)})
    assert len(published) == count
    assert pump.state["lastSuccessAt"] == last_success
    assert published[-1]["2330"].source == "TWSE MIS"


def test_repeated_negative_observation_extends_canonical_block():
    pump, clock, _adapter, _mis, published = fixture()
    original = quote(clock)
    pump.ingest_fugle({"2330": original})
    pump.drain_quotes()
    for moment in (3, 5):
        clock.tick = moment
        pump.ingest_fugle({"2330": replace(original, is_halted=True, received_at=clock.now().isoformat())})
    assert len(published) == 3
    assert published[-1]["2330"].received_at == clock.now().isoformat()
    between = replace(original, quote_timestamp=(clock.now() - timedelta(seconds=1)).isoformat())
    pump.ingest_fugle({"2330": between}, source="FUGLE_REST")
    pump.drain_quotes()
    assert published[-1]["2330"].is_halted


def test_source_cache_rejects_reordered_trade_and_quality_fingerprint_republishes():
    pump, clock, _adapter, _mis, published = fixture()
    original = quote(clock)
    clock.tick = 2
    current = quote(clock)
    pump.ingest_fugle({"2330": current})
    pump.drain_quotes()
    count = len(published)
    pump.ingest_fugle({"2330": original})
    pump.drain_quotes()
    assert len(published) == count
    assert pump._source_quotes["FUGLE_WS"]["2330"] == current
    pump.ingest_fugle({"2330": replace(current, continuity="new-session")})
    pump.drain_quotes()
    assert len(published) == count + 1
    assert published[-1]["2330"].continuity == "new-session"


def test_disabled_targets_stop_adapter_and_ignore_late_publication():
    pump, clock, adapter, _mis, published = fixture()
    pump.update_targets([StockQuoteRequest("2330", "test", "listed")], [], enabled=False)
    assert adapter.enabled is False
    pump.ingest_fugle({"2330": quote(clock)})
    pump.drain_quotes()
    assert not published
    assert not pump.state["ready"]


def test_shadow_warmup_tracks_consecutive_minutes_without_canonical_writes():
    pump, clock, _adapter, _mis, published = fixture(publish=False)
    for minute in range(17):
        clock.tick = minute * 60
        pump.ingest_fugle({"2330": quote(clock)})
        pump.drain_quotes()
    warmup = pump.state["shadowWarmup"]
    assert warmup["readyCount"] == 1
    assert warmup["symbols"][0]["consecutiveMinutes"] == 16
    assert not published and pump.state["lastSuccessAt"] is None
    assert pump.state["latestQuoteAt"] is None and pump.state["lastReceivedAt"] is None
    clock.tick += 60
    pump.ingest_fugle({"2330": replace(quote(clock), continuity="new")})
    pump.drain_quotes()
    assert pump.state["shadowWarmup"]["readyCount"] == 0


def test_fugle_canonical_publication_updates_persistence_despite_mis_outage():
    pump, clock, _adapter, mis, _published = fixture()
    mis.failed = True
    asyncio.run(pump.run_once())
    assert pump.state["lastSuccessAt"] is None
    pump.ingest_fugle({"2330": quote(clock)})
    pump.drain_quotes()
    assert pump.state["lastSuccessAt"] == clock.now().isoformat()
    assert pump.state["lastReceivedAt"] == clock.now().isoformat()
    assert pump.state["latestQuoteAt"] == clock.now().isoformat()
    assert pump.state["quoteCoverageCount"] == 1


class Mis:
    failed = False
    calls = 0
    def __init__(self, clock):
        self.clock = clock
    async def fetch_intraday_batch(self, batch):
        self.calls += 1
        if self.failed:
            raise TimeoutError("MIS unavailable")
        return {row.symbol: quote(self.clock, "TWSE MIS", row.symbol) for row in batch}


def fixture(*, ready=True, publish=True):
    clock, adapter, published = Clock(), Adapter(ready=ready), []
    mis = Mis(clock)
    pump = DayTradingQuotePump(provider=mis, fugle_provider=adapter, publish_enabled=publish,
                               publisher=published.append, monotonic=lambda: clock.tick, utcnow=clock.now)
    pump.update_targets([StockQuoteRequest("2330", "test", "上市")], [])
    return pump, clock, adapter, mis, published


def test_low_quota_and_unapproved_shadow_never_replace_mis():
    for ready, publish in ((False, True), (True, False)):
        pump, clock, _adapter, _mis, published = fixture(ready=ready, publish=publish)
        asyncio.run(pump.run_once())
        pump.ingest_fugle({"2330": quote(clock)})
        pump.drain_quotes()
        assert published[-1]["2330"].source == "TWSE MIS"
        assert pump.state["providerMode"] == "shadow" and not pump.state["ready"]
        assert pump.state["fugleShadowFreshCount"] == 1


def test_ws_stale_rest_takeover_and_thirty_seconds_of_recovery():
    pump, clock, _adapter, _mis, published = fixture()
    pump.ingest_fugle({"2330": quote(clock)})
    pump.drain_quotes()
    assert pump.state["activeSource"] == "FUGLE_WS"
    clock.tick = 11
    pump.ingest_fugle({"2330": quote(clock)}, source="FUGLE_REST")
    pump.drain_quotes()
    assert pump.state["activeSource"] == "FUGLE_REST"
    for moment in range(12, 43, 5):
        clock.tick = moment
        pump.ingest_fugle({"2330": quote(clock)}, source="FUGLE_REST")
        pump.drain_quotes()
        pump.ingest_fugle({"2330": quote(clock)})
        pump.drain_quotes()
        if moment < 42:
            assert pump.state["activeSource"] == "FUGLE_REST"
    assert pump.state["activeSource"] == "FUGLE_WS"
    assert pump.state["sourceSwitchCount"] == 2
    assert published[-1]["2330"].quote_timestamp == clock.now().isoformat()


def test_ws_disconnect_keeps_confirmed_entitlement_for_rest_fallback():
    pump, clock, adapter, _mis, _published = fixture()
    pump.ingest_fugle({"2330": quote(clock)})
    pump.drain_quotes()
    adapter.info.update(wsConnected=False, acknowledgedCount=0, subscriptionSymbols=[])
    clock.tick = 1
    pump.ingest_fugle({"2330": quote(clock)}, source="FUGLE_REST")
    pump.drain_quotes()
    assert pump.state["activeSource"] == "FUGLE_REST" and pump.state["ready"]


def test_unacknowledged_targets_cannot_promote_full_pool():
    pump, clock, adapter, _mis, published = fixture()
    adapter.info.update(acknowledgedCount=0, subscriptionSymbols=[])
    pump.ingest_fugle({"2330": quote(clock)})
    pump.drain_quotes()
    assert not published and not pump.state["ready"]


def test_mis_fallback_keeps_original_exchange_timestamp():
    pump, clock, _adapter, _mis, published = fixture()
    pump.ingest_fugle({"2330": quote(clock)})
    pump.drain_quotes()
    clock.tick = 11
    asyncio.run(pump.run_once())
    assert pump.state["activeSource"] == "TWSE_MIS"
    assert published[-1]["2330"].source == "TWSE MIS"
    clock.tick = 40
    pump._publish_selected()
    assert pump.state["activeSource"] == "NONE"
    assert published[-1]["2330"].quote_timestamp != clock.now().isoformat()


def test_three_mis_failures_open_circuit_probe_recovers_without_blocking_fugle():
    pump, clock, _adapter, mis, published = fixture()
    mis.failed = True
    for tick in (0, 5, 10):
        clock.tick = tick
        asyncio.run(pump.run_once())
    assert mis.calls == 3 and pump.state["misCircuit"]["state"] == "open"
    clock.tick = 20
    pump.ingest_fugle({"2330": quote(clock)})
    pump.drain_quotes()
    assert published[-1]["2330"].source == "FUGLE"
    assert not asyncio.run(pump.run_once()) and mis.calls == 3
    clock.tick = 40
    mis.failed = False
    assert asyncio.run(pump.run_once()) and mis.calls == 4
    assert pump.state["misCircuit"]["state"] == "closed"


def test_index_rest_is_normal_primary_path_not_stock_fallback():
    pump, clock, _adapter, _mis, _published = fixture()
    pump.update_targets([StockQuoteRequest("2330", "test", "上市"), StockQuoteRequest("t00", "index", "上市")], [])
    pump.ingest_fugle({"2330": quote(clock)})
    pump.drain_quotes()
    pump.ingest_fugle({"t00": quote(clock, symbol="t00")}, source="FUGLE_REST")
    pump.drain_quotes()
    assert pump.state["activeSource"] == "FUGLE_WS"
    assert pump.state["providerMode"] == "primary"
    assert pump.state["indexSource"] == "FUGLE_REST"


def test_two_hundred_ticks_coalesce_without_two_hundred_pool_scans(monkeypatch):
    pump, clock, adapter, _mis, published = fixture()
    symbols = [str(3000 + index) for index in range(289)]
    adapter.info.update(subscriptionSymbols=symbols, acknowledgedCount=289, desiredCount=289)
    requests = [StockQuoteRequest(symbol, "test", "listed") for symbol in symbols]
    pump.update_targets(requests[:30], requests[30:])
    pump.ingest_fugle({symbol: quote(clock, symbol=symbol) for symbol in symbols})
    pump.drain_quotes()
    assert len(published) == 1 and len(published[0]) == 289
    original, scans = pump._publish_selected, []
    def selected():
        scans.append(True)
        original()
    monkeypatch.setattr(pump, "_publish_selected", selected)
    for index in range(200):
        symbol = symbols[index]
        pump.ingest_fugle({symbol: replace(quote(clock, symbol=symbol), volume=2000 + index)})
    assert not scans and len(published) == 1
    assert pump.state["selectionPending"] is True
    pump.drain_quotes()
    assert len(scans) == 1 and len(published) == 2
    assert len(published[-1]) == 200
    assert published[-1][symbols[199]].volume == 2199
    assert pump.state["fugleShadowFreshCount"] == 289
    assert pump.state["selectionPending"] is False


def test_existing_periodic_cycle_drains_normal_ticks():
    pump, clock, _adapter, _mis, published = fixture()
    pump.ingest_fugle({"2330": quote(clock)})
    assert not published
    asyncio.run(pump.run_once())
    assert published[-1]["2330"].source == "FUGLE"
    assert pump.state["selectionPending"] is False


def test_rest_loop_keeps_quotes_fresh_through_thirty_second_ws_recovery(monkeypatch):
    pump, clock, adapter, _mis, published = fixture()
    pump.ingest_fugle({"2330": quote(clock)})
    pump.drain_quotes()
    clock.tick = 11
    pump.ingest_fugle({"2330": quote(clock)}, source="FUGLE_REST")
    pump.drain_quotes()
    assert pump.state["activeSource"] == "FUGLE_REST"
    clock.tick = 12
    pump.ingest_fugle({"2330": quote(clock)})
    pump.drain_quotes()
    rest_calls, observed = [], []

    async def fetch_quote(symbol):
        rest_calls.append(clock.tick)
        pump.ingest_fugle({symbol: quote(clock, symbol=symbol)}, source="FUGLE_REST")

    async def advance(_seconds):
        latest = published[-1]["2330"]
        age = (clock.now() - datetime.fromisoformat(latest.quote_timestamp)).total_seconds()
        observed.append((clock.tick, pump.state["activeSource"], age))
        if clock.tick >= 50:
            raise asyncio.CancelledError
        clock.tick += 1
        # The real receive path keeps WS fresh while its recovery timer runs.
        pump.ingest_fugle({"2330": quote(clock)})

    monkeypatch.setattr(adapter, "fetch_quote", fetch_quote, raising=False)
    monkeypatch.setattr(asyncio, "sleep", advance)

    async def run():
        try:
            await pump._run_rest_backup()
        except asyncio.CancelledError:
            pass

    asyncio.run(run())
    assert all(source == "FUGLE_REST" for tick, source, _age in observed if tick < 42)
    assert all(source == "FUGLE_WS" for tick, source, _age in observed if tick >= 42)
    assert all(0 <= age <= 15 for _tick, _source, age in observed)
    assert any(tick >= 27 for tick in rest_calls), "REST must continue beyond the initial snapshot lifetime"
    assert not any(tick > 42 for tick in rest_calls), "REST stops after the actual WS switch"
