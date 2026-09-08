"""Bounded intraday quote lane independent of recommendations and shared fetches."""
from __future__ import annotations

import asyncio
from collections import deque
from contextlib import suppress
from datetime import UTC, datetime
import logging
from threading import RLock
import time
from typing import Any, Callable, Iterable

from .official_market_data import OfficialStockQuote, StockQuoteRequest, TwseMisMarketDataProvider

logger = logging.getLogger(__name__)
PRIORITY_CAPACITY = 30
PRIORITY_SECONDS = 5.0
BASELINE_SECONDS = 30.0
BASELINE_SIZE = 80
BATCH_SIZE = 10
REQUEST_SECONDS = 1.0
REQUEST_TIMEOUT_SECONDS = 2.0


def _dedupe(rows: Iterable[StockQuoteRequest]) -> list[StockQuoteRequest]:
    return list({row.symbol: row for row in rows if row.symbol}.values())


def _publish(quotes: dict[str, OfficialStockQuote]) -> None:
    from .day_trading import day_trading_engine
    day_trading_engine.update_official_quotes(quotes)


class _ShadowHistory:
    """Minute coverage only; no orders, canonical engine, indicators or cache writes."""
    def __init__(self):
        self.rows: dict[tuple[str, str], tuple[OfficialStockQuote, deque[int]]] = {}

    def ingest(self, source: str, quotes: dict[str, OfficialStockQuote], now: datetime) -> None:
        from .quote_quality import quote_time, same_quote_segment, trusted_quote
        for symbol, quote in quotes.items():
            key = (source, symbol)
            if not trusted_quote(quote, now=now, max_age_seconds=15):
                if quote.is_trial or quote.is_halted:
                    self.rows.pop(key, None)
                continue
            stamp = quote_time(quote)
            if stamp is None:
                continue
            prior = self.rows.get(key)
            buckets = prior[1] if prior and same_quote_segment(prior[0], quote) else deque(maxlen=300)
            bucket = int(stamp.timestamp() // 60)
            if buckets and bucket < buckets[-1]:
                continue
            if not buckets or bucket != buckets[-1]:
                buckets.append(bucket)
            self.rows[key] = (quote, buckets)

    def diagnostics(self, now: datetime, symbols: set[str]) -> dict:
        current = int(now.timestamp() // 60)
        rows = []
        for (source, symbol), (_quote, buckets) in self.rows.items():
            if symbol not in symbols:
                continue
            completed = [minute for minute in buckets if minute < current]
            expected, consecutive = current - 1, 0
            for minute in reversed(completed):
                if minute != expected:
                    break
                consecutive += 1
                expected -= 1
            rows.append({"symbol": symbol, "source": source, "completedMinutes": len(completed),
                         "consecutiveMinutes": consecutive, "ready": consecutive >= 16})
        return {"observedAt": now.isoformat(), "requiredConsecutiveMinutes": 16,
                "readyCount": len({row["symbol"] for row in rows if row["ready"]}), "symbols": rows}


class DayTradingQuotePump:
    def __init__(self, *, provider=None, publisher: Callable = _publish,
                 monotonic: Callable = time.monotonic, utcnow: Callable = lambda: datetime.now(UTC),
                 fugle_provider=None, publish_enabled: bool | None = None,
                 failover_seconds: float = 10, recovery_seconds: float = 30) -> None:
        self._provider = provider if provider is not None else TwseMisMarketDataProvider()
        self._publisher = publisher
        self._monotonic = monotonic
        self._utcnow = utcnow
        self._lock = RLock()
        self._cycle_lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._backup_task: asyncio.Task | None = None
        self._rest_attempts: dict[str, float] = {}
        self._mandatory_symbols: set[str] = set()
        self._fugle = fugle_provider
        self._auto_fugle = provider is None
        self._publish_fugle = publish_enabled
        self._failover_seconds, self._recovery_seconds = failover_seconds, recovery_seconds
        self._source_quotes: dict[str, dict[str, OfficialStockQuote]] = {name: {} for name in ("FUGLE_WS", "FUGLE_REST", "TWSE_MIS")}
        self._active_sources: dict[str, str] = {}
        self._healthy_since: dict[str, float] = {}
        self._validated_symbols: set[str] = set()
        self._published: dict[str, tuple] = {}
        self._selection_dirty = False
        self._invalidations: dict[str, tuple[datetime, OfficialStockQuote, str]] = {}
        self._shadow_history = _ShadowHistory()
        self._mis_failures = 0
        self._mis_open_until = 0.0
        self._priority: list[StockQuoteRequest] = []
        self._baseline: list[StockQuoteRequest] = []
        self._pending: list[StockQuoteRequest] = []
        self._attempts: dict[str, float] = {}
        self._baseline_cycle: float | None = None
        self._baseline_cursor = 0
        self._next_request = 0.0
        self._priority_streak = 0
        self._enabled = False
        self._state: dict[str, Any] = {
            "status": "stopped", "enabled": False, "lastAttemptAt": None,
            "lastSuccessAt": None, "lastReceivedAt": None, "lastError": None, "latestQuoteAt": None,
            "priorityCount": 0, "mandatoryCount": 0, "baselineCount": 0,
            "priorityOverflowCount": 0, "overCapacity": False, "mandatoryOverCapacity": False,
            "missingMandatoryCount": 0,
            "baselineQuoteRefreshSeconds": BASELINE_SECONDS,
            "priorityQuoteRefreshSeconds": PRIORITY_SECONDS,
            "requestCount": 0, "successCount": 0, "failureCount": 0, "lastBatchSize": 0,
            "lastBatchLane": None, "lastBatchReceivedCount": 0, "quoteCoverageCount": 0,
            "providerMode": "mis_only", "ready": False, "entitlementReason": None,
            "activeSource": "NONE", "activeSourceCounts": {}, "sourceSwitchCount": 0,
            "lastSourceSwitchAt": None, "misCircuit": {"state": "closed", "consecutiveFailures": 0, "openedAt": None, "probeSuccesses": 0},
        }
        self._received_symbols: set[str] = set()

    @property
    def state(self) -> dict[str, object]:
        with self._lock:
            adapter = self._fugle.diagnostics() if self._fugle else None
            budget = getattr(self._fugle, "_budget", None) if self._fugle else None
            symbols = {row.symbol for row in [*self._priority, *self._baseline]}
            return {**self._state, "sourceHealth": adapter, "fugleBudget": budget.diagnostics() if budget else None,
                    "selectionPending": self._selection_dirty,
                    "shadowWarmup": self._shadow_history.diagnostics(self._utcnow(), symbols), "observedAt": self._utcnow().isoformat(),
                    "trackedCount": len(self._priority) + len(self._baseline),
                    "prioritySymbols": [row.symbol for row in self._priority]}

    def diagnostics(self) -> dict[str, object]:
        return self.state

    def update_targets(self, priority_requests: Iterable[StockQuoteRequest],
                       baseline_requests: Iterable[StockQuoteRequest], *,
                       mandatory_symbols: Iterable[str] = (), enabled: bool = True) -> None:
        priority = _dedupe(priority_requests)
        baseline = _dedupe(baseline_requests)
        mandatory = set(mandatory_symbols)
        all_requests = _dedupe([*priority, *baseline])
        required = [row for row in all_requests if row.symbol in mandatory]
        optional = [row for row in priority if row.symbol not in mandatory]
        selected = required + optional[:max(0, PRIORITY_CAPACITY - len(required))]
        selected_symbols = {row.symbol for row in selected}
        overflow = [row for row in priority if row.symbol not in selected_symbols]
        remaining = [row for row in _dedupe([*baseline, *overflow]) if row.symbol not in selected_symbols]
        with self._lock:
            self._priority, self._baseline, self._enabled = selected, remaining, enabled
            self._mandatory_symbols = mandatory
            allowed = {row.symbol for row in remaining}
            self._pending = [row for row in self._pending if row.symbol in allowed]
            live_symbols = selected_symbols | allowed
            now = self._monotonic()
            self._attempts = {symbol: at for symbol, at in self._attempts.items()
                              if symbol in live_symbols or now - at < PRIORITY_SECONDS}
            self._received_symbols.intersection_update(live_symbols)
            self._state.update(enabled=enabled, priorityCount=len(selected), mandatoryCount=len(required),
                               baselineCount=len(remaining), priorityOverflowCount=len(overflow),
                               overCapacity=bool(overflow) or len(required) > PRIORITY_CAPACITY,
                               mandatoryOverCapacity=len(required) > PRIORITY_CAPACITY,
                               missingMandatoryCount=len(mandatory - {row.symbol for row in required}))
            for cache in self._source_quotes.values():
                for symbol in set(cache) - live_symbols:
                    cache.pop(symbol, None)
            for state in (self._active_sources, self._healthy_since, self._published, self._invalidations):
                for symbol in set(state) - live_symbols:
                    state.pop(symbol, None)
            self._shadow_history.rows = {key: value for key, value in self._shadow_history.rows.items() if key[1] in live_symbols}
        if self._fugle:
            self._fugle.update_targets(selected, remaining, mandatory_symbols=mandatory, enabled=enabled)

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        if self._fugle is None and self._auto_fugle:
            from ..config import get_settings
            from .fugle_live_quotes import FugleLiveQuotes
            from .fugle_request_budget import get_fugle_request_budget
            settings = get_settings()
            if (
                not settings.free_quote_only
                and settings.fugle_live_enabled
                and settings.fugle_marketdata_api_key
            ):
                self._publish_fugle = settings.fugle_live_publish_enabled
                self._failover_seconds = settings.fugle_failover_after_seconds
                self._recovery_seconds = settings.fugle_recovery_hold_seconds
                self._fugle = FugleLiveQuotes(settings.fugle_marketdata_api_key,
                    base_url=settings.fugle_marketdata_base_url, publisher=self.ingest_fugle,
                    budget=get_fugle_request_budget(settings.fugle_marketdata_api_key),
                    subscription_limit=settings.fugle_live_subscription_limit)
        if self._fugle:
            self._fugle.update_targets(self._priority, self._baseline,
                                      mandatory_symbols=self._mandatory_symbols, enabled=self._enabled)
            await self._fugle.start()
            self._backup_task = asyncio.create_task(self._run_rest_backup(), name="fugle-stock-rest-backup")
        if opener := getattr(self._provider, "open_intraday", None):
            try:
                await opener()
            except Exception:
                logger.warning("MIS warmup unavailable; independent quote source remains enabled")
        with self._lock:
            self._state["status"] = "running"
        self._task = asyncio.create_task(self._run(), name="day-trading-quote-pump")

    async def stop(self) -> None:
        task, self._task = self._task, None
        try:
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        finally:
            if self._backup_task:
                self._backup_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._backup_task
                self._backup_task = None
            if self._fugle:
                await self._fugle.stop()
            if closer := getattr(self._provider, "close_intraday", None):
                await closer()
            with self._lock:
                self._state["status"] = "stopped"

    def _next_batch(self, now: float) -> tuple[str, list[StockQuoteRequest]]:
        # Called under _lock. Every symbol, including a failing holding, gets a
        # retry cooldown; a stale exchange timestamp cannot monopolize the lane.
        if not self._enabled or now < self._next_request or now < self._mis_open_until:
            return "", []
        if not self._pending and self._baseline and (
            self._baseline_cycle is None or now - self._baseline_cycle >= BASELINE_SECONDS
        ):
            count = min(BASELINE_SIZE, len(self._baseline))
            start = self._baseline_cursor % len(self._baseline)
            self._pending = [self._baseline[(start + i) % len(self._baseline)] for i in range(count)]
            self._baseline_cursor = (start + count) % len(self._baseline)
            self._baseline_cycle = now
        due = lambda row: now - self._attempts.get(row.symbol, float("-inf")) >= PRIORITY_SECONDS
        priority = sorted((row for row in self._priority if due(row)),
                          key=lambda row: self._attempts.get(row.symbol, float("-inf")))
        baseline = [row for row in self._pending if due(row)]
        if baseline and (not priority or self._priority_streak >= 2):
            lane, batch = "baseline", baseline[:BATCH_SIZE]
            symbols = {row.symbol for row in batch}
            self._pending = [row for row in self._pending if row.symbol not in symbols]
            self._priority_streak = 0
        elif priority:
            lane, batch = "priority", priority[:BATCH_SIZE]
            self._priority_streak += 1
        else:
            return "", []
        for row in batch:
            self._attempts[row.symbol] = now
        self._next_request = now + REQUEST_SECONDS
        return lane, batch

    def ingest_fugle(self, quotes: dict[str, OfficialStockQuote], *, source: str = "FUGLE_WS") -> None:
        if source not in {"FUGLE_WS", "FUGLE_REST"}:
            return
        with self._lock:
            accepted = self._cache_source(source, quotes)
            self._shadow_history.ingest(source, accepted, self._utcnow())
            self._selection_dirty = self._selection_dirty or bool(accepted)
            invalidate_now = any(quote.is_trial or quote.is_halted for quote in accepted.values())
        # Ordinary ticks are O(changed symbols), not O(the entire 300-stock pool).
        # Existing periodic loops coalesce them; safety observations cannot wait.
        if invalidate_now:
            self._publish_selected()

    def drain_quotes(self) -> None:
        """Publish the latest cached ticks and reevaluate time-based failover.

        Called by the existing periodic lanes even without dirty ticks, since
        stale-source detection and recovery timers must continue while quiet.
        """
        self._publish_selected()

    def _event_at(self, quote) -> datetime | None:
        from .quote_quality import negative_quote_time
        if getattr(quote, "is_halted", False) or getattr(quote, "is_trial", False):
            return negative_quote_time(quote, self._utcnow())
        stamps = []
        for field in ("book_timestamp", "received_at", "quote_timestamp"):
            try:
                stamp = datetime.fromisoformat(str(getattr(quote, field, None)))
                if stamp.tzinfo is not None and stamp <= self._utcnow():
                    stamps.append(stamp)
            except (TypeError, ValueError):
                pass
        return max(stamps, default=None)

    def _cache_source(self, source: str, quotes: dict) -> dict:
        from .quote_quality import quote_time
        accepted = {}
        for symbol, quote in quotes.items():
            previous = self._source_quotes[source].get(symbol)
            if previous is not None:
                stamp, prior_stamp = quote_time(quote), quote_time(previous)
                negative = getattr(quote, "is_trial", False) or getattr(quote, "is_halted", False)
                event, prior_event = self._event_at(quote), self._event_at(previous)
                if negative:
                    if prior_event and (event is None or event < prior_event):
                        continue
                elif stamp and prior_stamp and (stamp < prior_stamp or stamp == prior_stamp and event and prior_event and event < prior_event):
                    continue
            self._source_quotes[source][symbol] = quote
            accepted[symbol] = quote
        return accepted

    @staticmethod
    def _fingerprint(source, quote) -> tuple:
        return (source, quote.quote_timestamp, getattr(quote, "book_timestamp", None), quote.price, quote.volume,
                quote.is_realtime, getattr(quote, "quote_kind", "trade"), getattr(quote, "session", "regular"),
                getattr(quote, "volume_unit", "shares"), getattr(quote, "continuity", None),
                getattr(quote, "is_trial", False), getattr(quote, "is_halted", False),
                getattr(quote, "received_at", None) if getattr(quote, "is_trial", False) or getattr(quote, "is_halted", False) else None,
                getattr(quote, "best_bid", None), getattr(quote, "best_ask", None))

    def _publish_selected(self) -> None:
        from .quote_quality import quote_time, trusted_quote
        with self._lock:
            self._selection_dirty = False
            if not self._enabled:
                self._state.update(ready=False, activeSource="NONE", activeSourceCounts={})
                return
            now, tick = self._utcnow(), self._monotonic()
            adapter = self._fugle.diagnostics() if self._fugle else {}
            entitled = bool(adapter.get("entitlementReady") and int(adapter.get("wsSubscriptionLimit", 0)) >= 300 and not adapter.get("overCapacity"))
            subscribed = int(adapter.get("acknowledgedCount", 0)) >= int(adapter.get("desiredCount", 0))
            if entitled and subscribed and adapter.get("wsConnected"):
                self._validated_symbols = set(adapter.get("subscriptionSymbols", []))
            expected = {row.symbol for row in [*self._priority, *self._baseline]} - {"t00", "IX0001"}
            # Preserve confirmed entitlement through a disconnection so REST can
            # take over; a newly added, unacknowledged target cannot enable it.
            coverage_confirmed = expected <= self._validated_symbols
            allowed = bool(self._publish_fugle and entitled and coverage_confirmed)
            connected = bool(adapter.get("wsConnected"))
            reason = None if allowed else "Fugle 尚在確認完整訂閱" if entitled and self._publish_fugle and not coverage_confirmed else "Fugle 僅影子驗證，尚未核准正式發布" if entitled else "Fugle 需要確認每分鐘600次與300個訂閱額度"
            outgoing = {}
            counts: dict[str, int] = {}
            stock_counts: dict[str, int] = {}
            index_source = None
            symbols = {row.symbol for row in [*self._priority, *self._baseline]}
            for symbol in symbols:
                ws = self._source_quotes["FUGLE_WS"].get(symbol)
                rest = self._source_quotes["FUGLE_REST"].get(symbol)
                mis = self._source_quotes["TWSE_MIS"].get(symbol)
                eligible = [("TWSE_MIS", mis)]
                if allowed:
                    eligible.extend([("FUGLE_WS", ws), ("FUGLE_REST", rest)])
                for source, quote in eligible:
                    if quote and (quote.is_trial or quote.is_halted):
                        event = self._event_at(quote)
                        old_block = self._invalidations.get(symbol)
                        if event and (old_block is None or event > old_block[0]):
                            self._invalidations[symbol] = (event, quote, source)
                blocked = self._invalidations.get(symbol)
                if blocked:
                    replacements = [q for q in ([mis, ws, rest] if allowed else [mis])
                                    if trusted_quote(q, now=now, max_age_seconds=15)
                                    and (stamp := quote_time(q)) is not None and stamp > blocked[0]]
                    if replacements:
                        self._invalidations.pop(symbol, None)
                    else:
                        fingerprint = self._fingerprint(blocked[2], blocked[1])
                        if self._published.get(symbol) != fingerprint:
                            outgoing[symbol] = blocked[1]
                            self._published[symbol] = fingerprint
                        self._healthy_since.pop(symbol, None)
                        continue
                ws_healthy = bool(allowed and connected and symbol in adapter.get("subscriptionSymbols", []) and trusted_quote(ws, now=now, max_age_seconds=self._failover_seconds))
                if ws_healthy:
                    self._healthy_since.setdefault(symbol, tick)
                else:
                    self._healthy_since.pop(symbol, None)
                old = self._active_sources.get(symbol)
                recovered = ws_healthy and (old in {None, "FUGLE_WS"} or tick - self._healthy_since[symbol] >= self._recovery_seconds)
                source = "FUGLE_WS" if recovered else "FUGLE_REST" if allowed and trusted_quote(rest, now=now, max_age_seconds=15) else "TWSE_MIS" if trusted_quote(mis, now=now, max_age_seconds=15) else None
                if source is None:
                    continue
                selected = self._source_quotes[source][symbol]
                counts[source] = counts.get(source, 0) + 1
                if symbol in {"t00", "IX0001"}:
                    index_source = source
                else:
                    stock_counts[source] = stock_counts.get(source, 0) + 1
                fingerprint = self._fingerprint(source, selected)
                if self._published.get(symbol) == fingerprint:
                    continue
                if old and old != source:
                    self._state["sourceSwitchCount"] = int(self._state["sourceSwitchCount"]) + 1
                    self._state["lastSourceSwitchAt"] = now.isoformat()
                self._active_sources[symbol] = source
                self._published[symbol] = fingerprint
                outgoing[symbol] = selected
            shadow_symbols = set(self._source_quotes["FUGLE_WS"]) | set(self._source_quotes["FUGLE_REST"])
            shadow_fresh = sum(any(trusted_quote(self._source_quotes[source].get(symbol), now=now, max_age_seconds=15)
                                   for source in ("FUGLE_WS", "FUGLE_REST")) for symbol in shadow_symbols)
            summary_counts = stock_counts or counts
            self._state.update(ready=allowed, entitlementReady=entitled, entitlementReason=reason,
                providerMode="mis_only" if not self._fugle else "shadow" if not allowed else "degraded" if stock_counts.get("TWSE_MIS") or stock_counts.get("FUGLE_REST") else "primary",
                activeSource=next(iter(summary_counts)) if len(summary_counts) == 1 else "MIXED" if summary_counts else "NONE",
                activeSourceCounts=counts, indexSource=index_source, fugleShadowFreshCount=shadow_fresh,
                fugleShadowObservedAt=now.isoformat())
        if outgoing:
            self._publisher(outgoing)
            self._record_publication(outgoing)

    def _record_publication(self, quotes: dict) -> None:
        from .quote_quality import quote_time, trusted_quote
        with self._lock:
            for symbol, quote in quotes.items():
                if trusted_quote(quote, now=self._utcnow(), max_age_seconds=15):
                    self._received_symbols.add(symbol)
                else:
                    self._received_symbols.discard(symbol)
            stamps = [quote_time(quote) for quote in quotes.values()]
            latest = max((stamp for stamp in stamps if stamp is not None), default=None)
            self._state.update(lastSuccessAt=self._utcnow().isoformat(), lastReceivedAt=self._utcnow().isoformat(),
                               quoteCoverageCount=len(self._received_symbols), canonicalPublishCount=int(self._state.get("canonicalPublishCount", 0)) + 1)
            if latest and (not self._state["latestQuoteAt"] or latest.isoformat() > str(self._state["latestQuoteAt"])):
                self._state["latestQuoteAt"] = latest.isoformat()

    async def _run_rest_backup(self) -> None:
        # Separate task: a slow MIS batch/circuit never delays Fugle fallback.
        from .quote_quality import trusted_quote
        service = self._fugle
        if service is None:
            return
        while True:
            try:
                adapter = service.diagnostics()
                with self._lock:
                    now, tick = self._utcnow(), self._monotonic()
                    choices = [*self._priority, *self._baseline] if adapter.get("entitlementReady") else self._priority[:5]
                    # Receiving fresh WS ticks is not yet a source switch: the
                    # recovery hold can outlast a REST quote's 15-second TTL.
                    # Keep refreshing the selected fallback until WS is active.
                    recovering = {symbol for symbol, source in self._active_sources.items()
                                  if self._publish_fugle and adapter.get("entitlementReady")
                                  and source in {"FUGLE_REST", "TWSE_MIS"}}
                    due = [row for row in choices if row.symbol not in {"t00", "IX0001"}
                           and tick - self._rest_attempts.get(row.symbol, float("-inf")) >= PRIORITY_SECONDS
                           and not (row.symbol not in recovering and adapter.get("wsConnected") and trusted_quote(self._source_quotes["FUGLE_WS"].get(row.symbol), now=now, max_age_seconds=self._failover_seconds))
                           and not trusted_quote(self._source_quotes["FUGLE_REST"].get(row.symbol), now=now, max_age_seconds=self._failover_seconds)] if self._enabled else []
                    priority_symbols = {row.symbol for row in self._priority}
                    due.sort(key=lambda row: (row.symbol not in priority_symbols, self._rest_attempts.get(row.symbol, float("-inf"))))
                    selected = due[0].symbol if due else None
                    if selected:
                        self._rest_attempts[selected] = tick
                if selected:
                    await service.fetch_quote(selected)
                self.drain_quotes()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Fugle backup observation unavailable")
            await asyncio.sleep(.1)

    def _mis_failed(self) -> None:
        with self._lock:
            self._mis_failures += 1
            circuit = dict(self._state["misCircuit"])
            circuit.update(consecutiveFailures=self._mis_failures, probeSuccesses=0)
            if self._mis_failures >= 3:
                self._mis_open_until = self._monotonic() + 30
                circuit.update(state="open", openedAt=self._utcnow().isoformat())
            self._state["misCircuit"] = circuit

    async def run_once(self) -> bool:
        """Attempt at most one batch; useful for deterministic isolated tests."""
        async with self._cycle_lock:
            if self._fugle:
                self.drain_quotes()
            with self._lock:
                lane, batch = self._next_batch(self._monotonic())
                if not batch:
                    return False
                if self._mis_open_until:
                    self._state["misCircuit"] = {**self._state["misCircuit"], "state": "half_open"}
                self._state.update(lastAttemptAt=self._utcnow().isoformat(), lastBatchLane=lane,
                                   lastBatchSize=len(batch), lastBatchReceivedCount=0,
                                   requestCount=int(self._state["requestCount"]) + 1)
            try:
                quotes = await asyncio.wait_for(self._provider.fetch_intraday_batch(batch),
                                                timeout=REQUEST_TIMEOUT_SECONDS)
                received_at = self._utcnow().isoformat()
                # Publishing happens before another batch or unrelated work.
                if quotes:
                    with self._lock:
                        accepted = self._cache_source("TWSE_MIS", quotes)
                    if self._fugle:
                        self._publish_selected()
                    else:
                        if accepted and self._enabled:
                            self._publisher(accepted)
                else:
                    self._mis_failed()
                with self._lock:
                    if not self._fugle:
                        self._received_symbols.update(quotes)
                    latest = max((row.quote_timestamp for row in quotes.values()), default=None)
                    self._state.update(lastBatchReceivedCount=len(quotes),
                                       quoteCoverageCount=len(self._received_symbols), lastError=None,
                                       misLastReceivedAt=received_at)
                    if not self._fugle:
                        self._state["lastReceivedAt"] = received_at
                    if quotes:
                        self._mis_failures = 0
                        self._mis_open_until = 0
                        self._state["misCircuit"] = {"state": "closed", "consecutiveFailures": 0, "openedAt": None, "probeSuccesses": 1}
                        self._state["successCount"] = int(self._state["successCount"]) + 1
                        self._state["misLastSuccessAt"] = received_at
                        if not self._fugle:
                            self._state["lastSuccessAt"] = received_at
                            if latest and (not self._state["latestQuoteAt"] or latest > str(self._state["latestQuoteAt"])):
                                self._state["latestQuoteAt"] = latest
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._mis_failed()
                with self._lock:
                    self._state.update(lastError=f"{type(exc).__name__}: {str(exc)[:200]}",
                                       failureCount=int(self._state["failureCount"]) + 1)
                logger.warning("Intraday quote batch failed (%s, %s symbols): %s", lane, len(batch), type(exc).__name__)
            return True

    async def _run(self) -> None:
        while True:
            await self.run_once()
            await asyncio.sleep(0.1)


day_trading_quote_pump = DayTradingQuotePump()
