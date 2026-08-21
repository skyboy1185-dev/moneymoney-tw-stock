from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import httpx


TAIPEI = ZoneInfo("Asia/Taipei")
logger = logging.getLogger(__name__)
LIVE_QUOTE_CACHE_SECONDS = 5
MIS_ENDPOINT = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
MIS_BATCH_SIZE = 35
MIS_REQUEST_ATTEMPTS = 3


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
    )


class TwseMisMarketDataProvider:
    def __init__(self) -> None:
        self._cache: dict[str, tuple[OfficialStockQuote, datetime]] = {}
        self._last_trades: dict[str, OfficialStockQuote] = {}
        self._lock = asyncio.Lock()

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
            async with httpx.AsyncClient(timeout=8.0) as client:
                for batch_start in range(0, len(missing), MIS_BATCH_SIZE):
                    batch = missing[batch_start:batch_start + MIS_BATCH_SIZE]
                    channels = "|".join(
                        f"{'tse' if stock.market == '上市' else 'otc'}_{stock.symbol}.tw"
                        for stock in batch
                    )
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
                        except (httpx.HTTPError, ValueError, TypeError) as error:
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
