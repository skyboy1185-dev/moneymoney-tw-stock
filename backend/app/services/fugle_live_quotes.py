"""Independent Fugle aggregates stream and budgeted index/backup REST quotes.

Transport liveness never replaces exchange timestamps.  Publication eligibility and
source failover belong to the quote pump; this adapter also operates in shadow mode.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import math
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from websockets.asyncio.client import connect

from .official_market_data import OfficialStockQuote, StockQuoteRequest

TAIPEI = ZoneInfo("Asia/Taipei")
RECONNECT_DELAYS = (1, 2, 4, 8, 15, 30)
_OWNERS: dict[str, object] = {}
_OWNER_LOCK = threading.Lock()


def _number(value: Any, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError("invalid_number")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("invalid_number") from exc
    if not math.isfinite(result) or (result <= 0 if positive else result < 0):
        raise ValueError("invalid_number")
    return result


def _timestamp(value: Any) -> datetime:
    # Fugle stock v1 timestamps are microseconds.  Never guess receipt or seconds.
    micros = _number(value, positive=True)
    if micros < 100_000_000_000_000:
        raise ValueError("timestamp_not_microseconds")
    try:
        return datetime.fromtimestamp(micros / 1_000_000, UTC)
    except (ValueError, OverflowError, OSError) as exc:
        raise ValueError("invalid_timestamp") from exc


def _quantity(value: Any, multiplier: int, *, positive: bool = False) -> int:
    scaled = _number(value, positive=positive) * multiplier
    if not math.isfinite(scaled) or scaled > 2**63 - 1:
        raise ValueError("invalid_volume")
    return round(scaled)


def parse_fugle_quote(
    row: dict[str, Any], *, now: datetime | None = None,
    continuity: str | None = None,
) -> OfficialStockQuote:
    """Decode a complete regular-session snapshot, retaining unsafe quality flags."""
    current = now or datetime.now(UTC)
    current = current.replace(tzinfo=UTC) if current.tzinfo is None else current.astimezone(UTC)
    local = current.astimezone(TAIPEI)
    symbol = str(row.get("symbol", ""))
    is_index = row.get("type") == "INDEX"
    if is_index:
        if symbol != "IX0001":
            raise ValueError("unsupported_index")
        price = _number(row.get("closePrice"), positive=True)
        stamp = _timestamp(row.get("closeTime"))
    else:
        if not symbol or row.get("market") not in {"TSE", "OTC"} or row.get("intradayOddLot"):
            raise ValueError("unsupported_session")
        trade = row.get("lastTrade")
        if not isinstance(trade, dict):
            raise ValueError("missing_actual_trade")
        price = _number(trade.get("price"), positive=True)
        stamp = _timestamp(trade.get("time"))
    if str(row.get("date", "")) != local.date().isoformat() or stamp.astimezone(TAIPEI).date() != local.date():
        raise ValueError("wrong_trading_day")
    if stamp > current:
        raise ValueError("future_trade")
    stamp_local = stamp.astimezone(TAIPEI)
    if not (540 <= stamp_local.hour * 60 + stamp_local.minute <= 810):
        raise ValueError("outside_regular_session")
    previous = _number(row.get("previousClose"), positive=True)
    opening = _number(row.get("openPrice"), positive=True)
    high = _number(row.get("highPrice"), positive=True)
    low = _number(row.get("lowPrice"), positive=True)
    if low > min(opening, price) or high < max(opening, price):
        raise ValueError("inconsistent_ohlc")
    total = row.get("total")
    if not isinstance(total, dict):
        raise ValueError("missing_total")
    multiplier = 1 if is_index else 1000
    volume = _quantity(total.get("tradeVolume"), multiplier)
    change = price - previous
    change_percent = change / previous * 100
    if not math.isfinite(change_percent):
        raise ValueError("invalid_change")

    def book(side: str) -> tuple[tuple[float, ...], tuple[int, ...]]:
        levels = row.get(side, [])
        if not isinstance(levels, list):
            raise ValueError("invalid_book")
        prices, sizes = [], []
        for level in levels[:5]:
            if not isinstance(level, dict):
                raise ValueError("invalid_book")
            prices.append(_number(level.get("price"), positive=True))
            sizes.append(_quantity(level.get("size"), multiplier, positive=True))
        return tuple(prices), tuple(sizes)

    bids, bid_sizes = book("bids")
    asks, ask_sizes = book("asks")
    if bids and asks and bids[0] > asks[0]:
        raise ValueError("crossed_book")
    book_stamp = None
    if bids and asks and row.get("lastUpdated") is not None:
        updated = _timestamp(row["lastUpdated"])
        if updated > current or updated.astimezone(TAIPEI).date() != local.date():
            raise ValueError("invalid_book_time")
        book_stamp = updated.isoformat()
    trial = bool(row.get("isTrial"))
    halted = any(bool(row.get(flag)) for flag in ("isHalted", "isLimitDownHalt", "isLimitUpHalt", "isDelayedOpen", "isDelayedClose"))
    age = (current - stamp).total_seconds()
    live = (local.weekday() < 5 and 540 <= local.hour * 60 + local.minute <= 810
            and age <= 120 and not trial and not halted and (is_index or bool(bids and asks and book_stamp)))
    return OfficialStockQuote(
        symbol="t00" if is_index else symbol, name=str(row.get("name") or symbol),
        price=price, previous_close=previous, open=opening, high=high, low=low,
        volume=volume, change=change, change_percent=change_percent,
        quote_timestamp=stamp.isoformat(), source="FUGLE", is_realtime=live,
        best_bid=bids[0] if bids else None, best_ask=asks[0] if asks else None,
        bid_prices=bids, bid_volumes=bid_sizes, ask_prices=asks, ask_volumes=ask_sizes,
        quote_kind="index" if is_index else "trade", session="regular",
        volume_unit="index" if is_index else "shares", continuity=continuity,
        received_at=current.isoformat(), book_timestamp=book_stamp, is_trial=trial, is_halted=halted,
    )


class FugleLiveQuotes:
    def __init__(
        self, api_key: str, base_url: str, publisher: Callable[..., Any], budget: Any,
        subscription_limit: int = 300, *, client: Any = None, ws_connect: Callable[..., Any] = connect,
        lease: Any = None,
        utcnow: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._ws_url = self._base_url.replace("https://", "wss://", 1) + "/stock/streaming"
        self._publisher, self._budget = publisher, budget
        self._configured_limit = max(1, min(300, int(subscription_limit)))
        self._client, self._owns_client = client, client is None
        self._connect, self._utcnow, self._monotonic = ws_connect, utcnow, monotonic
        if lease is None:
            from .fugle_stream_lease import FugleStreamLease
            lease = FugleStreamLease(api_key)
        self._lease, self._leader = lease, False
        self._enabled = True
        self._loop: asyncio.AbstractEventLoop | None = None
        self._control = asyncio.Event()
        self._lock = threading.RLock()
        self._owner_key = hashlib.sha256(api_key.encode()).hexdigest()
        self._tasks: list[asyncio.Task] = []
        self._targets: tuple[str, ...] = ()
        self._mandatory: set[str] = set()
        self._quotes: dict[str, OfficialStockQuote] = {}
        self._exchange_updates: dict[str, datetime] = {}
        self._acks: dict[str, str] = {}
        self._pending: dict[str, float] = {}
        self._removing: dict[str, float] = {}
        self._generation = 0
        self._session_id = uuid4().hex[:12]
        self._auth_failed = False
        self._rest_auth_failed = False
        self._ws_restricted = False
        self._diag: dict[str, Any] = {
            "status": "STOPPED", "wsConnected": False, "lastReceivedAt": None,
            "lastTradeAt": None, "lastWsReceivedAt": None, "lastRestReceivedAt": None,
            "requestCount": 0, "successCount": 0, "failureCount": 0,
            "decodeErrorCount": 0, "lastDecodeError": None, "lastError": None,
            "reconnectCount": 0, "missingBookCount": 0,
            "lastTransport": None,
            "outOfOrderCount": 0,
        }

    def _quota(self) -> tuple[int, bool]:
        observed = self._budget.diagnostics().get("limitPerMinute")
        ready = isinstance(observed, (int, float)) and observed >= 600 and not self._ws_restricted
        return self._configured_limit if ready else min(5, self._configured_limit), ready

    def update_targets(self, priority: Iterable[StockQuoteRequest], baseline: Iterable[StockQuoteRequest],
                       mandatory_symbols: Iterable[str] = (), *, enabled: bool = True) -> None:
        values = list(priority) + list(baseline)
        symbols = list(dict.fromkeys(str(getattr(value, "symbol", value)) for value in values))
        mandatory = {str(s) for s in mandatory_symbols if str(s) not in {"t00", "IX0001"}}
        # Mandatory subscriptions already active keep their relative place on rotations.
        with self._lock:
            retained = [s for s in self._targets if s in mandatory]
            ordered = retained + [s for s in symbols if s in mandatory and s not in retained]
            ordered += [s for s in symbols if s not in mandatory]
            self._targets = tuple(s for s in dict.fromkeys(ordered) if s not in {"t00", "IX0001"})
            self._mandatory = mandatory
            changed = self._enabled != bool(enabled)
            self._enabled = bool(enabled)
            if not self._enabled:
                self._leader = False
        if changed and self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._control.set)

    def snapshot(self) -> dict[str, OfficialStockQuote]:
        with self._lock:
            return dict(self._quotes)

    def diagnostics(self) -> dict[str, Any]:
        limit, ready = self._quota()
        now = self._utcnow()
        with self._lock:
            fresh = sum(q.is_realtime and 0 <= (now - datetime.fromisoformat(q.quote_timestamp)).total_seconds() <= 15
                        for q in self._quotes.values())
            return {**self._diag, "observedAt": now.isoformat(), "source": "FUGLE",
                    "isLeader": self._leader, "leaseError": getattr(self._lease, "last_error", None),
                    "enabled": self._enabled,
                    "wsSubscriptionLimit": limit, "entitlementReady": ready,
                    "desiredCount": len(self._targets), "acknowledgedCount": len(self._acks),
                    "pendingCount": len(self._pending), "overCapacity": len(self._targets) > limit,
                    "mandatoryOverflowCount": max(0, len(self._mandatory) - limit),
                    "unresolvedMandatoryCount": len(self._mandatory.difference(self._targets)),
                    "trackedCount": len(self._quotes), "freshCount": fresh,
                    "subscriptionSymbols": list(self._acks)}

    async def start(self) -> None:
        if self._tasks:
            return
        if not self._api_key:
            raise ValueError("Fugle API key is required")
        with _OWNER_LOCK:
            if self._owner_key in _OWNERS and _OWNERS[self._owner_key] is not self:
                raise RuntimeError("Fugle stream already owned in this process")
            _OWNERS[self._owner_key] = self
        try:
            if self._client is None:
                self._client = httpx.AsyncClient(timeout=2, limits=httpx.Limits(max_connections=2, max_keepalive_connections=2))
            self._auth_failed = False
            self._rest_auth_failed = False
            self._ws_restricted = False
            with self._lock:
                self._diag["status"] = "STARTING"
            self._loop = asyncio.get_running_loop()
            self._control = asyncio.Event()
            self._tasks = [asyncio.create_task(self._leader_loop(), name="fugle-live-leader")]
        except BaseException:
            with _OWNER_LOCK:
                if _OWNERS.get(self._owner_key) is self:
                    _OWNERS.pop(self._owner_key)
            raise

    async def stop(self) -> None:
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None
        with self._lock:
            self._diag.update(status="STOPPED", wsConnected=False)
            self._acks.clear()
            self._pending.clear()
            self._removing.clear()
        with _OWNER_LOCK:
            if _OWNERS.get(self._owner_key) is self:
                _OWNERS.pop(self._owner_key)

    async def _leader_loop(self) -> None:
        while True:
            if not self._enabled:
                with self._lock:
                    self._diag.update(status="DISABLED", wsConnected=False)
                await self._wait_control(5)
                continue
            children: list[asyncio.Task] = []
            try:
                self._leader = bool(await self._lease.acquire()) and self._enabled
                if self._leader:
                    children = [asyncio.create_task(self._index_loop(), name="fugle-index-rest"),
                                asyncio.create_task(self._ws_loop(), name="fugle-aggregates")]
                    while self._leader:
                        await self._wait_control(5)
                        self._leader = self._enabled and bool(await self._lease.renew())
                        if any(task.done() and not task.cancelled() and task.exception() is not None for task in children):
                            self._leader = False
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                with self._lock:
                    self._diag["lastError"] = type(exc).__name__
            finally:
                self._leader = False
                for task in children:
                    task.cancel()
                if children:
                    await asyncio.gather(*children, return_exceptions=True)
                await self._lease.release()
                with self._lock:
                    self._diag.update(wsConnected=False, status="STANDBY")
            await self._wait_control(5)

    async def _wait_control(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(self._control.wait(), timeout)
        except TimeoutError:
            pass
        self._control.clear()

    async def _publish(self, row: dict[str, Any], transport: str) -> OfficialStockQuote | None:
        if not self._enabled:
            return None
        now = self._utcnow()
        symbol = "t00" if row.get("symbol") == "IX0001" else str(row.get("symbol", ""))
        trial = bool(row.get("isTrial"))
        halted = any(bool(row.get(flag)) for flag in ("isHalted", "isLimitDownHalt", "isLimitUpHalt", "isDelayedOpen", "isDelayedClose"))
        unsafe = trial or halted
        try:
            updated = _timestamp(row["lastUpdated"]) if row.get("lastUpdated") is not None else None
            if updated is not None and (updated > now or updated.astimezone(TAIPEI).date() != now.astimezone(TAIPEI).date()):
                raise ValueError("invalid_update_time")
            with self._lock:
                old = self._quotes.get(symbol)
                watermark = self._exchange_updates.get(symbol)
            if updated is not None and watermark is not None and updated < watermark:
                with self._lock:
                    self._diag["outOfOrderCount"] += 1
                return None
            continuity = f"{transport}:{self._session_id + ':' + str(self._generation) if transport == 'FUGLE_WS' else 'rest'}:{now.astimezone(TAIPEI).date()}:regular"
            try:
                quote = parse_fugle_quote(row, now=now, continuity=continuity)
            except (ValueError, TypeError, KeyError, OverflowError):
                if not unsafe or old is None or (row.get("date") is not None and row["date"] != now.astimezone(TAIPEI).date().isoformat()):
                    raise
                # A status-only message may invalidate a known snapshot. Do not
                # merge partial prices/books or invent a new exchange timestamp.
                quote = replace(old, is_realtime=False, is_trial=trial, is_halted=halted,
                                received_at=now.isoformat(), continuity=continuity)
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            with self._lock:
                self._diag["decodeErrorCount"] += 1
                self._diag["lastDecodeError"] = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            return None
        with self._lock:
            old = self._quotes.get(quote.symbol)
            if (old is not None and (old.is_trial or old.is_halted) and not unsafe
                    and quote.quote_timestamp <= old.quote_timestamp
                    and (updated is None or (watermark is not None and updated <= watermark))):
                # Equal-time REST/WS duplicates must not clear a newer unsafe
                # status. Require a later exchange update or actual trade.
                self._diag["outOfOrderCount"] += 1
                return None
            if old is not None and quote.quote_timestamp < old.quote_timestamp and not unsafe:
                self._diag["outOfOrderCount"] += 1
                return None
            if old is not None and quote.quote_timestamp < old.quote_timestamp and unsafe:
                # Invalidate the latest known price without rolling its trade
                # history backward to the lastTrade embedded in a halt event.
                quote = replace(old, is_realtime=False, is_trial=trial, is_halted=halted,
                                received_at=now.isoformat(), continuity=quote.continuity)
            self._quotes[quote.symbol] = quote
            if updated is not None:
                self._exchange_updates[quote.symbol] = updated
            self._diag["lastDecodeError"] = None
            self._diag["lastTransport"] = transport
            self._diag["lastTradeAt"] = max(self._diag["lastTradeAt"] or quote.quote_timestamp, quote.quote_timestamp)
            if quote.quote_kind == "trade" and (not quote.bid_prices or not quote.ask_prices):
                self._diag["missingBookCount"] += 1
        result = self._publisher({quote.symbol: quote}, source=transport)
        if inspect.isawaitable(result):
            await result
        return quote

    async def fetch_quote(self, symbol: str) -> OfficialStockQuote | None:
        if self._client is None:
            raise RuntimeError("Fugle adapter is not started")
        if not self._enabled or not self._leader or self._rest_auth_failed:
            return None
        external = "IX0001" if symbol in {"t00", "IX0001"} else symbol
        if not external.isalnum():
            raise ValueError("invalid_symbol")
        await self._budget.acquire(priority="priority" if external == "IX0001" or symbol in self._mandatory else "normal")
        if not self._enabled or not self._leader:
            return None
        with self._lock:
            self._diag["requestCount"] += 1
        try:
            response = await asyncio.wait_for(self._client.get(
                f"{self._base_url}/stock/intraday/quote/{external}", headers={"X-API-KEY": self._api_key}), timeout=2)
            await self._budget.observe_response(response.status_code, response.headers)
            if response.status_code in {401, 403}:
                self._rest_auth_failed = True
            response.raise_for_status()
            if not self._enabled or not self._leader:
                return None
            row = response.json()
            if not isinstance(row, dict) or row.get("symbol") != external:
                raise ValueError("unexpected_quote_symbol")
            now = self._utcnow().isoformat()
            with self._lock:
                self._diag.update(lastReceivedAt=now, lastRestReceivedAt=now, lastError=None)
                self._diag["successCount"] += 1
            return await self._publish(row, "FUGLE_REST")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Exception messages/URLs may include credentials. Diagnostics expose only class/code.
            with self._lock:
                self._diag["failureCount"] += 1
                self._diag["lastError"] = type(exc).__name__
            return None

    async def _index_loop(self) -> None:
        while True:
            started = self._monotonic()
            await self.fetch_quote("t00")
            await asyncio.sleep(max(.1, 5 - (self._monotonic() - started)))

    async def _sync_subscriptions(self, ws: Any) -> None:
        limit, _ = self._quota()
        now = self._monotonic()
        with self._lock:
            desired = self._targets[:limit]
            if any(now - at > 10 for at in (*self._pending.values(), *self._removing.values())):
                raise TimeoutError("subscription_ack_timeout")
            removed = {s: ident for s, ident in self._acks.items() if s not in desired and ident not in self._removing}
            # Don't oversubscribe while an unsubscribe acknowledgement is outstanding.
            available = max(0, limit - len(self._acks) - len(self._pending))
            added = [s for s in desired if s not in self._acks and s not in self._pending][:available]
            for ident in removed.values():
                self._removing[ident] = now
            for symbol in added:
                self._pending[symbol] = now
        if removed:
            await ws.send(json.dumps({"event": "unsubscribe", "data": {"ids": list(removed.values())}}))
        if added:
            await ws.send(json.dumps({"event": "subscribe", "data": {"channel": "aggregates", "symbols": added}}))

    async def _handle_message(self, message: dict[str, Any]) -> str:
        event, data = message.get("event"), message.get("data")
        if event in {"subscribed", "unsubscribed"}:
            items = data if isinstance(data, list) else [data]
            with self._lock:
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    symbol, ident = item.get("symbol"), item.get("id")
                    if event == "subscribed" and item.get("channel") == "aggregates" and symbol in self._pending and ident:
                        self._acks[symbol] = str(ident)
                        self._pending.pop(symbol, None)
                    elif event == "unsubscribed" and ident:
                        self._removing.pop(str(ident), None)
                        self._acks = {s: i for s, i in self._acks.items() if i != str(ident)}
        elif event in {"snapshot", "data"} and message.get("channel") == "aggregates" and isinstance(data, dict):
            with self._lock:
                acknowledged = self._acks.get(str(data.get("symbol")))
            if acknowledged and message.get("id") == acknowledged:
                now = self._utcnow().isoformat()
                with self._lock:
                    self._diag.update(lastReceivedAt=now, lastWsReceivedAt=now)
                await self._publish(data, "FUGLE_WS")
        elif event == "error":
            # A REST entitlement does not prove a WS subscription entitlement.
            # Stay at the free cap after any subscription rejection until restart.
            self._ws_restricted = True
            raise RuntimeError("websocket_server_error")
        return str(event)

    async def _ws_session(self, ws: Any) -> None:
        await ws.send(json.dumps({"event": "auth", "data": {"apikey": self._api_key}}))
        auth = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        if auth.get("event") != "authenticated":
            self._auth_failed = auth.get("event") == "error"
            raise RuntimeError("websocket_auth_failed")
        with self._lock:
            self._generation += 1
            self._diag.update(wsConnected=True, status="CONNECTED", lastError=None)
        next_ping, pong_deadline = self._monotonic() + 10, None
        await self._sync_subscriptions(ws)
        while True:
            now = self._monotonic()
            if pong_deadline is not None and now >= pong_deadline:
                raise TimeoutError("websocket_pong_timeout")
            if now >= next_ping:
                await ws.send(json.dumps({"event": "ping"}))
                pong_deadline, next_ping = now + 5, now + 10
            await self._sync_subscriptions(ws)
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=.5)
            except TimeoutError:
                continue
            message = json.loads(raw)
            if not isinstance(message, dict):
                raise ValueError("invalid_websocket_message")
            if await self._handle_message(message) == "pong":
                pong_deadline = None

    async def _ws_loop(self) -> None:
        failures = 0
        while True:
            started = self._monotonic()
            try:
                async with self._connect(self._ws_url, open_timeout=5, close_timeout=2,
                                         ping_interval=None, max_size=2**20) as ws:
                    await self._ws_session(ws)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                with self._lock:
                    self._diag.update(lastError=type(exc).__name__, status="AUTH_FAILED" if self._auth_failed else "RECONNECTING")
                    self._diag["reconnectCount"] += 1
            finally:
                with self._lock:
                    self._diag["wsConnected"] = False
                    self._acks.clear()
                    self._pending.clear()
                    self._removing.clear()
            if self._auth_failed:
                return
            if self._monotonic() - started >= 30:
                failures = 0
            delay = RECONNECT_DELAYS[min(failures, len(RECONNECT_DELAYS) - 1)]
            failures += 1
            await asyncio.sleep(delay)
