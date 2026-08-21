from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
import math
from typing import Any, Iterable
from urllib.parse import quote

import httpx


YAHOO_CHART_URLS = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
    "https://query2.finance.yahoo.com/v8/finance/chart/{ticker}",
)
DIVIDEND_CACHE_TTL = timedelta(hours=6)
DIVIDEND_FAILURE_CACHE_TTL = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class DividendEvent:
    ex_date: date
    amount_per_share: float


@dataclass(frozen=True, slots=True)
class DividendHistory:
    symbol: str
    events: tuple[DividendEvent, ...]
    available: bool
    fetched_at: datetime


def parse_yahoo_dividends(payload: dict[str, Any]) -> tuple[DividendEvent, ...]:
    results = payload.get("chart", {}).get("result") or []
    if not results:
        return ()
    raw_events = results[0].get("events", {}).get("dividends") or {}
    by_date: dict[date, float] = {}
    for raw in raw_events.values():
        if not isinstance(raw, dict):
            continue
        raw_date = raw.get("date")
        raw_amount = raw.get("amount")
        if not isinstance(raw_date, (int, float, str)) or not isinstance(
            raw_amount, (int, float, str),
        ):
            continue
        try:
            ex_date = datetime.fromtimestamp(int(raw_date), UTC).date()
            amount = float(raw_amount)
        except (TypeError, ValueError, OSError):
            continue
        if amount <= 0 or not math.isfinite(amount):
            continue
        by_date[ex_date] = by_date.get(ex_date, 0.0) + amount
    return tuple(
        DividendEvent(ex_date=ex_date, amount_per_share=round(amount, 6))
        for ex_date, amount in sorted(by_date.items())
    )


def dividends_per_share(
    events: Iterable[DividendEvent],
    entry_date: date,
    end_date: date,
) -> float:
    """Cash dividends earned after entry and on or before the exit/as-of date.

    A position opened on the ex-dividend date is not entitled to that payout,
    hence the intentionally strict lower bound.
    """
    return round(sum(
        event.amount_per_share
        for event in events
        if entry_date < event.ex_date <= end_date
    ), 6)


class LongTermDividendProvider:
    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], DividendHistory] = {}

    async def _fetch_one(
        self,
        client: httpx.AsyncClient,
        semaphore: asyncio.Semaphore,
        symbol: str,
        market: str,
    ) -> DividendHistory:
        suffix = "TWO" if market == "上櫃" else "TW"
        ticker = quote(f"{symbol}.{suffix}", safe="")
        fetched_at = datetime.now(UTC)
        for attempt, template in enumerate(YAHOO_CHART_URLS):
            try:
                async with semaphore:
                    response = await client.get(
                        template.format(ticker=ticker),
                        params={"interval": "1d", "range": "2y", "events": "dividends"},
                    )
                response.raise_for_status()
                return DividendHistory(
                    symbol=symbol,
                    events=parse_yahoo_dividends(response.json()),
                    available=True,
                    fetched_at=fetched_at,
                )
            except (httpx.HTTPError, ValueError, TypeError):
                if attempt + 1 < len(YAHOO_CHART_URLS):
                    await asyncio.sleep(0.15)
        return DividendHistory(symbol=symbol, events=(), available=False, fetched_at=fetched_at)

    async def get_histories(
        self,
        requests: Iterable[tuple[str, str]],
    ) -> dict[str, DividendHistory]:
        unique = {
            (str(symbol), str(market or "上市"))
            for symbol, market in requests
            if str(symbol)
        }
        now = datetime.now(UTC)
        result: dict[str, DividendHistory] = {}
        missing: list[tuple[str, str]] = []
        for key in unique:
            cached = self._cache.get(key)
            ttl = DIVIDEND_CACHE_TTL if cached and cached.available else DIVIDEND_FAILURE_CACHE_TTL
            if cached is not None and now - cached.fetched_at < ttl:
                result[key[0]] = cached
            else:
                missing.append(key)
        if missing:
            limits = httpx.Limits(max_connections=12, max_keepalive_connections=8)
            headers = {"Accept": "application/json", "User-Agent": "Moneymoney-TWSE-Dashboard"}
            async with httpx.AsyncClient(timeout=12.0, limits=limits, headers=headers) as client:
                semaphore = asyncio.Semaphore(8)
                try:
                    fetched = await asyncio.wait_for(asyncio.gather(*(
                        self._fetch_one(client, semaphore, symbol, market)
                        for symbol, market in missing
                    )), timeout=20.0)
                except TimeoutError:
                    failed_at = datetime.now(UTC)
                    fetched = [
                        DividendHistory(symbol, (), False, failed_at)
                        for symbol, _ in missing
                    ]
            for key, history in zip(missing, fetched, strict=True):
                self._cache[key] = history
                result[key[0]] = history
        return result


long_term_dividend_provider = LongTermDividendProvider()
