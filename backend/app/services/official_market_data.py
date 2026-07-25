from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import httpx


TAIPEI = ZoneInfo("Asia/Taipei")
MIS_ENDPOINT = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"


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


class MarketDataProvider(Protocol):
    async def get_quotes(
        self,
        stocks: list[StockQuoteRequest],
    ) -> dict[str, OfficialStockQuote]: ...


def _number(value: Any) -> float | None:
    try:
        parsed = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return parsed


def _iso_date(value: Any) -> str:
    text = str(value or "")
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return datetime.now(UTC).astimezone(TAIPEI).date().isoformat()


def _is_realtime_quote(date_value: str, time_value: str, now: datetime | None = None) -> bool:
    local_now = (now or datetime.now(UTC)).astimezone(TAIPEI)
    minutes = local_now.hour * 60 + local_now.minute
    return (
        local_now.weekday() < 5
        and 540 <= minutes <= 810
        and date_value == local_now.date().isoformat()
        and bool(time_value)
    )


def parse_mis_quote(row: dict[str, Any], fallback: StockQuoteRequest) -> OfficialStockQuote | None:
    previous_close = _number(row.get("y"))
    last_trade = _number(row.get("z"))
    price = last_trade if last_trade is not None and last_trade > 0 else previous_close
    if price is None or previous_close is None or price <= 0 or previous_close <= 0:
        return None
    open_price = _number(row.get("o")) or price
    high = _number(row.get("h")) or price
    low = _number(row.get("l")) or price
    volume_lots = _number(row.get("v")) or 0
    date_value = _iso_date(row.get("d"))
    time_value = str(row.get("t") or row.get("ot") or "13:30:00")
    change = price - previous_close
    return OfficialStockQuote(
        symbol=str(row.get("c") or fallback.symbol),
        name=str(row.get("n") or fallback.name),
        price=price,
        previous_close=previous_close,
        open=open_price,
        high=high,
        low=low,
        volume=round(volume_lots * 1000),
        change=change,
        change_percent=(change / previous_close) * 100,
        quote_timestamp=f"{date_value}T{time_value}+08:00",
        source="TWSE MIS",
        is_realtime=_is_realtime_quote(date_value, time_value),
    )


class TwseMisMarketDataProvider:
    def __init__(self) -> None:
        self._cache: dict[str, tuple[OfficialStockQuote, datetime]] = {}
        self._lock = asyncio.Lock()

    async def get_quotes(
        self,
        stocks: list[StockQuoteRequest],
    ) -> dict[str, OfficialStockQuote]:
        if not stocks:
            return {}
        now = datetime.now(UTC)
        cached: dict[str, OfficialStockQuote] = {}
        for stock in stocks:
            entry = self._cache.get(stock.symbol)
            if entry and entry[1] > now:
                cached[stock.symbol] = entry[0]
        missing = [stock for stock in stocks if stock.symbol not in cached]
        if not missing:
            return cached
        async with self._lock:
            now = datetime.now(UTC)
            for stock in missing:
                entry = self._cache.get(stock.symbol)
                if entry and entry[1] > now:
                    cached[stock.symbol] = entry[0]
            missing = [stock for stock in missing if stock.symbol not in cached]
            if not missing:
                return cached
            channels = "|".join(
                f"{'tse' if stock.market == '上市' else 'otc'}_{stock.symbol}.tw"
                for stock in missing
            )
            try:
                async with httpx.AsyncClient(timeout=8.0) as client:
                    response = await client.get(
                        MIS_ENDPOINT,
                        params={"ex_ch": channels, "json": "1", "delay": "0"},
                        headers={
                            "Accept": "application/json",
                            "Referer": "https://mis.twse.com.tw/stock/fibest.jsp",
                            "User-Agent": "Mozilla/5.0 Moneymoney-TWSE-Dashboard",
                        },
                    )
                    response.raise_for_status()
                rows = response.json().get("msgArray", [])
            except (httpx.HTTPError, ValueError, TypeError):
                return cached
            requests = {stock.symbol: stock for stock in missing}
            for row in rows:
                symbol = str(row.get("c") or "")
                fallback = requests.get(symbol)
                if fallback is None:
                    continue
                quote = parse_mis_quote(row, fallback)
                if quote is None:
                    continue
                ttl_seconds = 8 if quote.is_realtime else 60
                self._cache[symbol] = (
                    quote,
                    datetime.fromtimestamp(now.timestamp() + ttl_seconds, UTC),
                )
                cached[symbol] = quote
            return cached


official_market_data_provider = TwseMisMarketDataProvider()
