import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.services import fugle_live_quotes as module
from app.services.fugle_live_quotes import FugleLiveQuotes, parse_fugle_quote
from app.services.official_market_data import StockQuoteRequest


NOW = datetime(2026, 9, 8, 1, 30, tzinfo=UTC)


def row(symbol="2330"):
    stamp = int(NOW.timestamp() * 1_000_000)
    return dict(symbol=symbol, name="test", date="2026-09-08", type="EQUITY", market="TSE",
                previousClose=100, openPrice=101, highPrice=105, lowPrice=99,
                closePrice=102, closeTime=stamp, lastPrice=999, change=899,
                lastTrade=dict(price=102, time=stamp), total=dict(tradeVolume=123),
                bids=[dict(price=101, size=3)], asks=[dict(price=103, size=4)], lastUpdated=stamp)


class Budget:
    def __init__(self, limit=None):
        self.limit = limit
        self.calls = []

    def diagnostics(self):
        return {"limitPerMinute": self.limit}

    async def acquire(self, *, priority):
        self.calls.append(("acquire", priority))

    async def observe_response(self, status_code, headers):
        self.calls.append(("observe", status_code))
        self.limit = int(headers.get("x-ratelimit-limit", self.limit or 60))


class Lease:
    def __init__(self):
        self.releases = 0

    async def acquire(self):
        return True

    async def renew(self):
        return True

    async def release(self):
        self.releases += 1


def adapter(**kwargs):
    published = []
    result = FugleLiveQuotes("test-key", "https://api.fugle.tw/marketdata/v1.0",
                            lambda quotes, *, source: published.append((quotes, source)),
                            kwargs.pop("budget", Budget()), lease=kwargs.pop("lease", Lease()),
                            utcnow=lambda: NOW, **kwargs)
    return result, published


def test_real_trade_microseconds_units_and_change_ignore_trial_last_price():
    quote = parse_fugle_quote(row(), now=NOW)
    assert quote.price == 102 and quote.change == 2 and quote.change_percent == 2
    assert quote.volume == 123000 and quote.bid_volumes == (3000,) and quote.ask_volumes == (4000,)
    assert quote.quote_timestamp == NOW.isoformat()
    assert quote.source == "FUGLE" and quote.session == "regular" and quote.volume_unit == "shares"
    assert quote.is_realtime and quote.book_timestamp == NOW.isoformat()


def test_index_uses_close_timestamp_without_trade_and_does_not_scale_volume():
    data = row("IX0001")
    data.update(type="INDEX", total={"tradeVolume": 4633124, "tradeValue": 451714616800})
    for key in ("lastTrade", "bids", "asks"):
        data.pop(key)
    quote = parse_fugle_quote(data, now=NOW)
    assert quote.symbol == "t00" and quote.price == 102 and quote.volume == 4633124
    assert quote.quote_kind == "index" and quote.volume_unit == "index" and quote.is_realtime


@pytest.mark.parametrize("change,error", [
    ({"lastTrade": None}, "missing_actual_trade"),
    ({"lastTrade": {"price": float("nan"), "time": 1}}, "invalid_number"),
    ({"lastTrade": {"price": 102, "time": int(NOW.timestamp())}}, "timestamp_not_microseconds"),
    ({"lastTrade": {"price": 102, "time": int((NOW + timedelta(seconds=1)).timestamp() * 1e6)}}, "future_trade"),
    ({"date": "2026-09-07"}, "wrong_trading_day"),
    ({"market": "ESB"}, "unsupported_session"),
    ({"intradayOddLot": True}, "unsupported_session"),
    ({"total": {}}, "invalid_number"),
    ({"total": {"tradeVolume": 1e308}}, "invalid_volume"),
    ({"highPrice": 101}, "inconsistent_ohlc"),
    ({"bids": [{"price": 104, "size": 1}]}, "crossed_book"),
])
def test_rejects_unusable_payloads_without_receipt_timestamp_fallback(change, error):
    data = row()
    data.update(change)
    with pytest.raises(ValueError, match=error):
        parse_fugle_quote(data, now=NOW)


@pytest.mark.parametrize("flag", ["isTrial", "isLimitDownHalt", "isLimitUpHalt", "isDelayedOpen", "isDelayedClose"])
def test_unsafe_status_is_published_explicitly_to_invalidate_preceding_quote(flag):
    data = row()
    data[flag] = True
    quote = parse_fugle_quote(data, now=NOW)
    assert not quote.is_realtime
    assert quote.is_trial if flag == "isTrial" else quote.is_halted


def test_missing_book_does_not_fabricate_from_trade_or_receipt():
    data = row()
    data.pop("bids")
    data["lastTrade"].update(bid=101, ask=103)
    quote = parse_fugle_quote(data, now=NOW)
    assert quote.best_bid is None and quote.book_timestamp is None and not quote.is_realtime


class WS:
    def __init__(self):
        self.sent = []

    async def send(self, raw):
        self.sent.append(json.loads(raw))


def targets(count=12):
    return [StockQuoteRequest(str(2330 + i), "test", "上市") for i in range(count)]


def test_unknown_free_and_upgraded_quota_acknowledgements_and_rotation():
    service, _ = adapter()
    service.update_targets(targets(), [], mandatory_symbols={"2341"})
    socket = WS()

    async def scenario():
        await service._sync_subscriptions(socket)
        sent = socket.sent[-1]["data"]["symbols"]
        assert len(sent) == 5 and sent[0] == "2341"
        assert service.diagnostics()["acknowledgedCount"] == 0
        await service._handle_message({"event": "subscribed", "data": [
            {"channel": "aggregates", "symbol": s, "id": s} for s in sent]})
        assert service.diagnostics()["acknowledgedCount"] == 5
        service._budget.limit = 600
        await service._sync_subscriptions(socket)
        assert len(socket.sent[-1]["data"]["symbols"]) == 7
        assert service.diagnostics()["entitlementReady"]

    asyncio.run(scenario())


def test_unsubscribe_ack_precedes_replacement_to_keep_free_subscription_cap():
    service, _ = adapter(budget=Budget(60))
    socket = WS()

    async def scenario():
        service.update_targets(targets(5), [])
        await service._sync_subscriptions(socket)
        await service._handle_message({"event": "subscribed", "data": [
            {"channel": "aggregates", "symbol": r.symbol, "id": r.symbol} for r in targets(5)]})
        service.update_targets(targets(12)[-5:], [])
        await service._sync_subscriptions(socket)
        assert socket.sent[-1]["event"] == "unsubscribe"
        count = len(socket.sent)
        await service._sync_subscriptions(socket)
        assert len(socket.sent) == count
        await service._handle_message({"event": "unsubscribed", "data": [{"id": r.symbol} for r in targets(5)]})
        await service._sync_subscriptions(socket)
        assert socket.sent[-1]["event"] == "subscribe"
        assert len(socket.sent[-1]["data"]["symbols"]) == 5

    asyncio.run(scenario())


def test_ack_required_and_heartbeat_never_refreshes_trade_timestamp():
    service, published = adapter()
    data = {"event": "data", "channel": "aggregates", "id": "one", "data": row()}

    async def scenario():
        await service._handle_message(data)
        assert not published
        service._pending["2330"] = 0
        await service._handle_message({"event": "subscribed", "data": {"id": "one", "symbol": "2330", "channel": "aggregates"}})
        await service._handle_message(data)
        assert published[0][1] == "FUGLE_WS"
        before = service.diagnostics()
        await service._handle_message({"event": "heartbeat", "data": {"time": 999}})
        after = service.diagnostics()
        assert after["lastTradeAt"] == before["lastTradeAt"]
        assert after["lastReceivedAt"] == before["lastReceivedAt"]
        assert "price" not in json.dumps(after).lower()

    asyncio.run(scenario())


def test_rest_shared_budget_headers_index_mapping_and_transport():
    calls = []
    data = row("IX0001")
    data["type"] = "INDEX"

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json=data, headers={"x-ratelimit-limit": "600"})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service, published = adapter(client=client)
            assert await service.fetch_quote("t00") is None  # Non-leader cannot spend quota.
            service._leader = True
            quote = await service.fetch_quote("t00")
            assert quote.symbol == "t00" and published[0][1] == "FUGLE_REST"
            assert service._budget.calls == [("acquire", "priority"), ("observe", 200)]
            assert service.diagnostics()["entitlementReady"]
    asyncio.run(scenario())
    assert calls == ["/marketdata/v1.0/stock/intraday/quote/IX0001"]


def test_rest_auth_failure_is_not_retried_and_never_discloses_credentials():
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(403))) as client:
            service, published = adapter(client=client)
            service._leader = True
            assert await service.fetch_quote("2330") is None
            assert await service.fetch_quote("2330") is None
            assert service.diagnostics()["requestCount"] == 1
            assert "test-key" not in json.dumps(service.diagnostics()) and not published
    asyncio.run(scenario())


def test_subscription_rejection_reduces_cap_despite_paid_rest_header():
    service, _ = adapter(budget=Budget(600))
    with pytest.raises(RuntimeError):
        asyncio.run(service._handle_message({"event": "error", "data": {"message": "subscription quota"}}))
    assert not service.diagnostics()["entitlementReady"]
    assert service.diagnostics()["wsSubscriptionLimit"] == 5


def test_backoff_uses_bounded_sequence_without_inline_connection_retry(monkeypatch):
    delays = []

    @asynccontextmanager
    async def failing_connect(*args, **kwargs):
        raise OSError("test")
        yield

    async def sleep(delay):
        delays.append(delay)
        if len(delays) == 7:
            raise asyncio.CancelledError

    service, _ = adapter(ws_connect=failing_connect, monotonic=lambda: 0)
    monkeypatch.setattr(module.asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service._ws_loop())
    assert delays == [1, 2, 4, 8, 15, 30, 30]


def test_application_ping_requires_pong_within_five_seconds():
    clock = [0]
    service, _ = adapter(monotonic=lambda: clock[0])

    class NoPong(WS):
        async def recv(self):
            if not clock[0]:
                clock[0] = .01
                return json.dumps({"event": "authenticated"})
            clock[0] += 5
            return json.dumps({"event": "heartbeat"})

    socket = NoPong()
    with pytest.raises(TimeoutError, match="pong_timeout"):
        asyncio.run(service._ws_session(socket))
    assert [message["event"] for message in socket.sent] == ["auth", "ping"]


def test_single_process_owner_idempotent_start_stop_and_restart(monkeypatch):
    service, _ = adapter()
    other, _ = adapter()

    async def inert():
        await asyncio.Event().wait()

    monkeypatch.setattr(service, "_leader_loop", inert)
    monkeypatch.setattr(other, "_leader_loop", inert)

    async def scenario():
        await service.start()
        original = service._client
        await service.start()
        assert len(service._tasks) == 1
        with pytest.raises(RuntimeError, match="already owned"):
            await other.start()
        await service.stop()
        assert original.is_closed
        await other.start()
        await other.stop()
        await service.start()
        assert service._client is not original
        await service.stop()

    asyncio.run(scenario())


def test_lease_loss_cancels_both_transports_before_reacquire(monkeypatch):
    events = []
    release_done = asyncio.Event()

    class LostLease(Lease):
        async def renew(self):
            events.append("lost")
            return False

        async def release(self):
            events.append("release")
            release_done.set()

    service, _ = adapter(lease=LostLease())
    real_sleep = asyncio.sleep

    async def child(name):
        events.append(name + "-start")
        try:
            await asyncio.Event().wait()
        finally:
            events.append(name + "-stop")

    async def sleep(delay):
        if "release" in events:
            await asyncio.Event().wait()
        await real_sleep(0)

    monkeypatch.setattr(service, "_index_loop", lambda: child("rest"))
    monkeypatch.setattr(service, "_ws_loop", lambda: child("ws"))
    monkeypatch.setattr(service, "_wait_control", sleep)

    async def scenario():
        task = asyncio.create_task(service._leader_loop())
        await release_done.wait()
        assert not service._leader
        assert events.index("rest-stop") < events.index("release")
        assert events.index("ws-stop") < events.index("release")
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_lost_leader_discards_inflight_rest_result():
    service = None

    def handler(request):
        service._leader = False
        return httpx.Response(200, json=row(), headers={"x-ratelimit-limit": "600"})

    async def scenario():
        nonlocal service
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service, published = adapter(client=client)
            service._leader = True
            assert await service.fetch_quote("2330") is None
            assert not published and not service.snapshot()

    asyncio.run(scenario())


def test_disabled_adapter_does_not_acquire_lease_and_stops_both_transports_promptly(monkeypatch):
    class TrackingLease(Lease):
        acquisitions = 0

        async def acquire(self):
            self.acquisitions += 1
            return True

    lease = TrackingLease()
    service, _ = adapter(lease=lease)
    service.update_targets(targets(5), [], enabled=False)
    started = {"ws": asyncio.Event(), "rest": asyncio.Event()}
    stopped = {"ws": asyncio.Event(), "rest": asyncio.Event()}

    async def child(name):
        started[name].set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped[name].set()

    monkeypatch.setattr(service, "_ws_loop", lambda: child("ws"))
    monkeypatch.setattr(service, "_index_loop", lambda: child("rest"))

    async def scenario():
        await service.start()
        try:
            await asyncio.sleep(0)
            assert lease.acquisitions == 0
            service.update_targets(targets(5), [], enabled=True)
            await asyncio.wait_for(asyncio.gather(*(e.wait() for e in started.values())), .5)
            assert lease.acquisitions == 1
            service.update_targets(targets(5), [], enabled=False)
            assert await service.fetch_quote("2330") is None
            await asyncio.wait_for(asyncio.gather(*(e.wait() for e in stopped.values())), .5)
            assert not service.diagnostics()["isLeader"]
        finally:
            await service.stop()
        assert lease.releases >= 1

    asyncio.run(scenario())


def test_older_trade_never_reaches_publisher_or_replaces_snapshot():
    service, published = adapter()

    async def scenario():
        await service._publish(row(), "FUGLE_WS")
        older = row()
        older["lastTrade"]["time"] -= 1_000_000
        assert await service._publish(older, "FUGLE_REST") is None
        assert len(published) == 1
        assert service.snapshot()["2330"].quote_timestamp == NOW.isoformat()
        assert service.diagnostics()["outOfOrderCount"] == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("flag", ["isTrial", "isLimitDownHalt"])
def test_halt_trial_with_old_trade_invalidates_current_snapshot_without_rolling_history_back(flag):
    service, published = adapter()

    async def scenario():
        await service._publish(row(), "FUGLE_WS")
        unsafe = row()
        unsafe["lastTrade"]["time"] -= 1_000_000
        unsafe[flag] = True
        result = await service._publish(unsafe, "FUGLE_WS")
        assert len(published) == 2 and not result.is_realtime
        assert result.quote_timestamp == NOW.isoformat()
        assert result.is_trial if flag == "isTrial" else result.is_halted

    asyncio.run(scenario())


def test_partial_status_can_invalidate_but_partial_quote_does_not_merge_prices_or_books():
    service, published = adapter()

    async def scenario():
        await service._publish(row(), "FUGLE_WS")
        invalidation = await service._publish({"symbol": "2330", "isLimitUpHalt": True}, "FUGLE_WS")
        assert invalidation.is_halted and not invalidation.is_realtime
        assert invalidation.quote_timestamp == NOW.isoformat()
        assert invalidation.book_timestamp == NOW.isoformat()
        count = len(published)
        assert await service._publish({"symbol": "2330", "lastPrice": 999}, "FUGLE_WS") is None
        assert len(published) == count and service.snapshot()["2330"].price == 102

    asyncio.run(scenario())


def test_older_exchange_update_cannot_undo_current_halt_state():
    service, published = adapter()

    async def scenario():
        halted = row()
        halted["isLimitDownHalt"] = True
        await service._publish(halted, "FUGLE_WS")
        old_clear = row()
        old_clear["lastUpdated"] -= 1_000_000
        assert await service._publish(old_clear, "FUGLE_REST") is None
        assert service.snapshot()["2330"].is_halted and len(published) == 1

    asyncio.run(scenario())


def test_equal_time_duplicate_cannot_clear_partial_halt():
    service, published = adapter()

    async def scenario():
        await service._publish(row(), "FUGLE_WS")
        await service._publish({"symbol": "2330", "isLimitDownHalt": True}, "FUGLE_WS")
        assert await service._publish(row(), "FUGLE_REST") is None
        assert len(published) == 2 and service.snapshot()["2330"].is_halted

    asyncio.run(scenario())


def test_initial_snapshot_requires_matching_ack_and_uses_actual_trade_timestamp():
    service, published = adapter()
    message = {"event": "snapshot", "channel": "aggregates", "id": "snapshot-sub", "data": row()}

    async def scenario():
        await service._handle_message(message)
        assert not published
        service._pending["2330"] = 0
        await service._handle_message({"event": "subscribed", "data": {
            "channel": "aggregates", "symbol": "2330", "id": "snapshot-sub"}})
        await service._handle_message({**message, "id": "unacknowledged-sub"})
        assert not published
        await service._handle_message(message)
        assert len(published) == 1 and published[0][1] == "FUGLE_WS"
        quote = published[0][0]["2330"]
        assert quote.price == 102 and quote.quote_timestamp == NOW.isoformat()
        assert service.diagnostics()["lastWsReceivedAt"] == NOW.isoformat()
        stale = row()
        stale["lastTrade"]["time"] -= 1_000_000
        await service._handle_message({**message, "data": stale})
        assert len(published) == 1

    asyncio.run(scenario())
