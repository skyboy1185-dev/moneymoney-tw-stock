"""Best-effort international market snapshot with explicit source timestamps.

Yahoo Finance is a free secondary source. Missing or stale instruments remain absent;
the analysis service never substitutes synthetic prices.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

INSTRUMENTS = {
    "dow": ("^DJI", "美國道瓊指數"),
    "sp500": ("^GSPC", "S&P 500 指數"),
    "nasdaq": ("^IXIC", "NASDAQ 指數"),
    "sox": ("^SOX", "費城半導體指數"),
    "tsm": ("TSM", "台積電 ADR"),
    "nvda": ("NVDA", "NVIDIA"),
    "amd": ("AMD", "AMD"),
    "es_future": ("ES=F", "S&P 500 期貨"),
    "nq_future": ("NQ=F", "NASDAQ 期貨"),
    "ym_future": ("YM=F", "道瓊期貨"),
    "dxy": ("DX-Y.NYB", "美元指數"),
    "us10y": ("^TNX", "美國10年期公債殖利率"),
    "oil": ("CL=F", "WTI 原油"),
    "gold": ("GC=F", "黃金"),
}


class InternationalMarketProvider:
    def __init__(self) -> None:
        self._snapshot: dict[str, Any] = {"quotes": {}, "errors": {}, "updatedAt": None}
        self._refreshed_at: datetime | None = None
        self._lock = asyncio.Lock()

    def snapshot(self) -> dict[str, Any]:
        return self._snapshot

    async def refresh(self, now: datetime | None = None, *, force: bool = False) -> dict[str, Any]:
        current = now or datetime.now(UTC)
        if not force and self._refreshed_at and current - self._refreshed_at < timedelta(minutes=3):
            return self._snapshot
        async with self._lock:
            if not force and self._refreshed_at and current - self._refreshed_at < timedelta(minutes=3):
                return self._snapshot
            async with httpx.AsyncClient(timeout=8, headers={"User-Agent": "Mozilla/5.0 TWSE market analysis"}) as client:
                results = await asyncio.gather(
                    *(self._fetch(client, key, ticker, name) for key, (ticker, name) in INSTRUMENTS.items()),
                    return_exceptions=True,
                )
            quotes: dict[str, Any] = {}
            errors: dict[str, str] = {}
            for key, result in zip(INSTRUMENTS, results, strict=True):
                if isinstance(result, Exception):
                    errors[key] = str(result)[:180]
                else:
                    quotes[key] = result
            self._snapshot = {"quotes": quotes, "errors": errors, "updatedAt": current.isoformat()}
            self._refreshed_at = current
            return self._snapshot

    async def _fetch(self, client: httpx.AsyncClient, key: str, ticker: str, name: str) -> dict[str, Any]:
        last_error = "unknown error"
        for host in ("query1", "query2"):
            try:
                response = await client.get(
                    f"https://{host}.finance.yahoo.com/v8/finance/chart/{ticker}",
                    params={"interval": "5m", "range": "5d", "events": "div,splits"},
                )
                response.raise_for_status()
                result = response.json()["chart"]["result"][0]
                meta = result["meta"]
                closes = result.get("indicators", {}).get("quote", [{}])[0].get("close", [])
                stamps = result.get("timestamp", [])
                valid = [(stamp, close) for stamp, close in zip(stamps, closes, strict=False) if close is not None]
                if not valid:
                    raise ValueError("沒有有效成交價格")
                stamp, price = valid[-1]
                previous = meta.get("chartPreviousClose") or meta.get("previousClose")
                if not previous or float(previous) <= 0:
                    raise ValueError("缺少前收價格")
                change = (float(price) / float(previous) - 1) * 100
                return {
                    "key": key, "symbol": ticker, "name": name, "price": round(float(price), 4),
                    "previousClose": round(float(previous), 4), "changePct": round(change, 2),
                    "dataTime": datetime.fromtimestamp(stamp, UTC).isoformat(), "source": "Yahoo Finance chart",
                    "exchangeTimezone": meta.get("exchangeTimezoneName"),
                }
            except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
                last_error = str(exc)
        raise ValueError(last_error)


international_market_provider = InternationalMarketProvider()
