from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import httpx


TAIPEI = ZoneInfo("Asia/Taipei")
logger = logging.getLogger(__name__)
LIVE_QUOTE_CACHE_SECONDS = 5
MIS_ENDPOINT = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
MIS_BATCH_SIZE = 10
MIS_FALLBACK_BATCH_SIZE = 3
MIS_REQUEST_ATTEMPTS = 3
MIS_REQUEST_TIMEOUT_SECONDS = 3.0
MIS_TOTAL_REFRESH_TIMEOUT_SECONDS = 12.0


@dataclass(frozen=True)
class StockQuoteRequest:
    symbol: str
    name: str
    market: str


@dataclass(frozen=True)
class OfficialStockQuote:
    symbol: str
    name: str
    price: float
    previous_close: float
    open: float
    high: float
    low: float
    volume: int
    change: float
    change_percent: float
    quote_timestamp: str
    source: str
    is_realtime: bool
    best_bid: float | None = None
    best_ask: float | None = None
    bid_prices: tuple[float, ...] = ()
    bid_volumes: tuple[int, ...] = ()
    ask_prices: tuple[float, ...] = ()
    ask_volumes: tuple[int, ...] = ()


class MarketDataProvider(Protocol):
    async def get_quotes(
        self,
        stocks: list[StockQuoteRequest],
        *,
        force_refresh: bool = False,
    ) -> dict[str, OfficialStockQuote]: ...


def _number(value: Any) -> float | None:
    try:
        parsed = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return parsed


def _first_order_price(value: Any) -> float | None:
    first = str(value or "").split("_", 1)[0]
    parsed = _number(first)
    return parsed if parsed is not None and parsed > 0 else None


def _order_prices(value: Any) -> tuple[float, ...]:
    prices: list[float] = []
    for raw in str(value or "").split("_"):
        parsed = _number(raw)
        if parsed is not None and parsed > 0:
            prices.append(parsed)
    return tuple(prices[:5])


def _order_volumes(value: Any) -> tuple[int, ...]:
    volumes: list[int] = []
    for raw in str(value or "").split("_"):
        parsed = _number(raw)
        if parsed is not None and parsed > 0:
            volumes.append(round(parsed * 1000))
    return tuple(volumes[:5])


def _iso_date(value: Any) -> str:
    text = str(value or "")
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return datetime.now(UTC).astimezone(TAIPEI).date().isoformat()


def _is_realtime_quote(date_value: str, time_value: str, now: datetime | None = None) -> bool:
    local_now = (now or datetime.now(UTC)).astimezone(TAIPEI)
    minutes = local_now.hour * 60 + local_now.minute
    try:
        quote_time = datetime.fromisoformat(f"{date_value}T{time_value}+08:00")
        delay_seconds = abs((local_now - quote_time).total_seconds())
    except ValueError:
        return False
    return (
        local_now.weekday() < 5
        and 540 <= minutes <= 810
        and date_value == local_now.date().isoformat()
        and bool(time_value)
        and delay_seconds <= 120
    )


def parse_mis_quote(
    row: dict[str, Any],
    fallback: StockQuoteRequest,
    previous_trade: OfficialStockQuote | None = None,
    *,
    now: datetime | None = None,
) -> OfficialStockQuote | None:
    previous_close = _number(row.get("y"))
    last_trade = _number(row.get("z"))
    date_value = _iso_date(row.get("d"))
    has_last_trade = last_trade is not None and last_trade > 0
    best_bid = _first_order_price(row.get("b"))
    best_ask = _first_order_price(row.get("a"))
    bid_prices = _order_prices(row.get("b"))
    ask_prices = _order_prices(row.get("a"))
    bid_volumes = _order_volumes(row.get("g"))
    ask_volumes = _order_volumes(row.get("f"))
    order_book_price = best_ask or best_bid
    cached_trade = (
        previous_trade
        if previous_trade is not None
        and previous_trade.source == "TWSE MIS"
        and previous_trade.quote_timestamp[:10] == date_value
        and _is_realtime_quote(
            previous_trade.quote_timestamp[:10],
            previous_trade.quote_timestamp[11:19],
            now,
        )
        else None
    )
    if previous_close is None or previous_close <= 0:
        return None
    snapshot_time = str(row.get("t") or row.get("ot") or "")
    if has_last_trade and last_trade is not None:
        price = last_trade
        time_value = snapshot_time
        source = "TWSE MIS"
    elif cached_trade is not None:
        price = cached_trade.price
        time_value = cached_trade.quote_timestamp[11:19]
        source = "TWSE MIS"
    elif order_book_price is not None:
        price = order_book_price
        time_value = snapshot_time
        source = "TWSE MIS 五檔參考價"
    else:
        return None
    if not time_value:
        return None
    open_price = _number(row.get("o")) or price
    high = _number(row.get("h")) or price
    low = _number(row.get("l")) or price
    volume_lots = _number(row.get("v"))
    volume = (
        round(volume_lots * 1000)
        if volume_lots is not None
        else cached_trade.volume if cached_trade is not None else 0
    )
    change = price - previous_close
    return OfficialStockQuote(
        symbol=str(row.get("c") or fallback.symbol),
        name=str(row.get("n") or fallback.name),
        price=price,
        previous_close=previous_close,
        open=open_price,
        high=high,
        low=low,
        volume=volume,
        change=change,
        change_percent=(change / previous_close) * 100,
        quote_timestamp=f"{date_value}T{time_value}+08:00",
        source=source,
        is_realtime=_is_realtime_quote(date_value, time_value, now),
        best_bid=best_bid,
        best_ask=best_ask,
        bid_prices=bid_prices,
        bid_volumes=bid_volumes,
        ask_prices=ask_prices,
        ask_volumes=ask_volumes,
    )


class TwseMisMarketDataProvider:
    def __init__(self) -> None:
        self._cache: dict[str, tuple[OfficialStockQuote, datetime]] = {}
        self._last_trades: dict[str, OfficialStockQuote] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _channel(stock: StockQuoteRequest) -> str:
        return f"{'tse' if stock.market in {'上市', '銝?'} else 'otc'}_{stock.symbol}.tw"

    async def _fetch_batch(
        self,
        client: httpx.AsyncClient,
        batch: list[StockQuoteRequest],
        *,
        deadline: float,
    ) -> tuple[list[dict[str, Any]] | None, Exception | None]:
        channels = "|".join(self._channel(stock) for stock in batch)
        request_params = {
            "ex_ch": channels,
            "json": "1",
            "delay": "0",
            "_": str(round(datetime.now(UTC).timestamp() * 1000)),
        }
        request_headers = {
            "Accept": "application/json",
            "Referer": "https://mis.twse.com.tw/stock/fibest.jsp",
            "User-Agent": "Mozilla/5.0 Moneymoney-TWSE-Dashboard",
            "Cache-Control": "no-cache",
        }
        last_error: Exception | None = None
        for attempt in range(MIS_REQUEST_ATTEMPTS):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None, TimeoutError("TWSE MIS quote refresh deadline exceeded")
            try:
                request_params["_"] = str(round(datetime.now(UTC).timestamp() * 1000))
                response = await asyncio.wait_for(
                    client.get(
                        MIS_ENDPOINT,
                        params=request_params,
                        headers=request_headers,
                    ),
                    timeout=min(MIS_REQUEST_TIMEOUT_SECONDS, remaining),
                )
                response.raise_for_status()
                payload = response.json()
                parsed_rows = payload.get("msgArray", [])
                if not isinstance(parsed_rows, list):
                    raise TypeError("TWSE MIS msgArray is not a list")
                return parsed_rows, None
            except (asyncio.TimeoutError, httpx.HTTPError, ValueError, TypeError) as error:
                last_error = error
                if attempt + 1 < MIS_REQUEST_ATTEMPTS:
                    await asyncio.sleep(0.25 * (attempt + 1))
        return None, last_error

    async def _fetch_batch_with_fallback(
        self,
        client: httpx.AsyncClient,
        batch: list[StockQuoteRequest],
        *,
        deadline: float,
    ) -> list[dict[str, Any]]:
        rows, error = await self._fetch_batch(client, batch, deadline=deadline)
        if rows is not None:
            return rows
        if len(batch) <= 1 or deadline - time.monotonic() <= 0:
            logger.warning(
                "TWSE MIS quote batch 1-%s failed after %s attempts; retaining verified cache: %s",
                len(batch),
                MIS_REQUEST_ATTEMPTS,
                error,
            )
            return []

        recovered: list[dict[str, Any]] = []
        failed_symbols: list[str] = []
        chunk_size = MIS_FALLBACK_BATCH_SIZE if len(batch) > MIS_FALLBACK_BATCH_SIZE else 1
        for chunk_start in range(0, len(batch), chunk_size):
            if deadline - time.monotonic() <= 0:
                failed_symbols.extend(stock.symbol for stock in batch[chunk_start:])
                break
            chunk = batch[chunk_start:chunk_start + chunk_size]
            chunk_rows, chunk_error = await self._fetch_batch(client, chunk, deadline=deadline)
            if chunk_rows is not None:
                recovered.extend(chunk_rows)
                continue
            if len(chunk) == 1:
                failed_symbols.extend(stock.symbol for stock in chunk)
                error = chunk_error
                continue
            for stock in chunk:
                if deadline - time.monotonic() <= 0:
                    failed_symbols.append(stock.symbol)
                    continue
                single_rows, single_error = await self._fetch_batch(client, [stock], deadline=deadline)
                if single_rows is None:
                    failed_symbols.append(stock.symbol)
                    error = single_error
                else:
                    recovered.extend(single_rows)
        if failed_symbols:
            logger.warning(
                "TWSE MIS quote fallback missed %s/%s symbols after batch split; retaining verified cache for: %s. Last error: %s",
                len(failed_symbols),
                len(batch),
                ",".join(failed_symbols[:12]),
                error,
            )
        return recovered

    async def get_quotes(
        self,
        stocks: list[StockQuoteRequest],
        *,
        force_refresh: bool = False,
    ) -> dict[str, OfficialStockQuote]:
        if not stocks:
            return {}
        now = datetime.now(UTC)
        # Keep the last verified quote as a fallback even when callers request a
        # forced refresh. Its timestamp still drives the stale-data safety gate,
        # so a transient MIS failure cannot erase market state or enable trading.
        verified_cache = {
            stock.symbol: entry[0]
            for stock in stocks
            if (entry := self._cache.get(stock.symbol)) is not None
        }
        cached: dict[str, OfficialStockQuote] = {}
        for stock in stocks:
            entry = self._cache.get(stock.symbol)
            if not force_refresh and entry and entry[1] > now:
                cached[stock.symbol] = entry[0]
        missing = [stock for stock in stocks if stock.symbol not in cached]
        if not missing:
            return cached
        if self._lock.locked() and verified_cache:
            return {**verified_cache, **cached}
        async with self._lock:
            now = datetime.now(UTC)
            for stock in missing:
                entry = self._cache.get(stock.symbol)
                if not force_refresh and entry and entry[1] > now:
                    cached[stock.symbol] = entry[0]
            missing = [stock for stock in missing if stock.symbol not in cached]
            if not missing:
                return cached
            rows: list[dict[str, Any]] = []
            deadline = time.monotonic() + MIS_TOTAL_REFRESH_TIMEOUT_SECONDS
            async with httpx.AsyncClient(timeout=MIS_REQUEST_TIMEOUT_SECONDS) as client:
                for batch_start in range(0, len(missing), MIS_BATCH_SIZE):
                    if deadline - time.monotonic() <= 0:
                        logger.warning(
                            "TWSE MIS quote refresh deadline reached after %s/%s requested symbols; retaining verified cache for the rest",
                            batch_start,
                            len(missing),
                        )
                        break
                    batch = missing[batch_start:batch_start + MIS_BATCH_SIZE]
                    channels = "|".join(self._channel(stock) for stock in batch)
                    request_params = {
                        "ex_ch": channels,
                        "json": "1",
                        "delay": "0",
                        "_": str(round(datetime.now(UTC).timestamp() * 1000)),
                    }
                    request_headers = {
                        "Accept": "application/json",
                        "Referer": "https://mis.twse.com.tw/stock/fibest.jsp",
                        "User-Agent": "Mozilla/5.0 Moneymoney-TWSE-Dashboard",
                        "Cache-Control": "no-cache",
                    }
                    batch_rows: list[dict[str, Any]] | None = None
                    last_error: Exception | None = None
                    for attempt in range(MIS_REQUEST_ATTEMPTS):
                        try:
                            request_params["_"] = str(round(datetime.now(UTC).timestamp() * 1000))
                            response = await client.get(
                                MIS_ENDPOINT,
                                params=request_params,
                                headers=request_headers,
                            )
                            response.raise_for_status()
                            payload = response.json()
                            parsed_rows = payload.get("msgArray", [])
                            if not isinstance(parsed_rows, list):
                                raise TypeError("TWSE MIS msgArray is not a list")
                            batch_rows = parsed_rows
                            break
                        except (asyncio.TimeoutError, httpx.HTTPError, ValueError, TypeError) as error:
                            last_error = error
                            if attempt + 1 < MIS_REQUEST_ATTEMPTS:
                                await asyncio.sleep(0.25 * (attempt + 1))
                    if batch_rows is None:
                        logger.warning(
                            "TWSE MIS quote batch %s-%s failed after %s attempts; retaining verified cache: %s",
                            batch_start + 1,
                            batch_start + len(batch),
                            MIS_REQUEST_ATTEMPTS,
                            last_error,
                        )
                        batch_rows = await self._fetch_batch_with_fallback(
                            client,
                            batch,
                            deadline=deadline,
                        )
                        if batch_rows:
                            rows.extend(batch_rows)
                    else:
                        rows.extend(batch_rows)
                    if batch_start + MIS_BATCH_SIZE < len(missing):
                        await asyncio.sleep(0.1)
            requests = {stock.symbol: stock for stock in missing}
            for row in rows:
                symbol = str(row.get("c") or "")
                fallback = requests.get(symbol)
                if fallback is None:
                    continue
                quote = parse_mis_quote(
                    row,
                    fallback,
                    self._last_trades.get(symbol),
                    now=now,
                )
                if quote is None:
                    continue
                last_trade = _number(row.get("z"))
                if last_trade is not None and last_trade > 0:
                    self._last_trades[symbol] = quote
                ttl_seconds = LIVE_QUOTE_CACHE_SECONDS if quote.is_realtime else 15
                self._cache[symbol] = (
                    quote,
                    datetime.fromtimestamp(now.timestamp() + ttl_seconds, UTC),
                )
                cached[symbol] = quote
            return {**verified_cache, **cached}


official_market_data_provider = TwseMisMarketDataProvider()
