from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import httpx


MarketSnapshot = dict[str, tuple[float, int]]
_snapshot_cache: dict[date, MarketSnapshot] = {}


def _number(value: Any) -> float | None:
    try:
        parsed = float(str(value or "").replace(",", "").replace("+", "").strip())
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _parse_tables(payload: dict[str, Any], symbol_field: str, close_field: str, volume_field: str) -> MarketSnapshot:
    result: MarketSnapshot = {}
    for table in payload.get("tables", []) if isinstance(payload.get("tables"), list) else []:
        fields = table.get("fields", []) if isinstance(table, dict) else []
        rows = table.get("data", []) if isinstance(table, dict) else []
        if not all(field in fields for field in (symbol_field, close_field, volume_field)):
            continue
        symbol_index = fields.index(symbol_field)
        close_index = fields.index(close_field)
        volume_index = fields.index(volume_field)
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, list) or max(symbol_index, close_index, volume_index) >= len(row):
                continue
            symbol = str(row[symbol_index] or "").strip()
            close = _number(row[close_index])
            volume = _number(row[volume_index]) or 0
            if len(symbol) == 4 and symbol.isdigit() and not symbol.startswith("00") and close is not None:
                result[symbol] = (close, round(volume))
    return result


async def _fetch_snapshot(report_date: date) -> MarketSnapshot:
    cached = _snapshot_cache.get(report_date)
    if cached is not None:
        return cached
    compact = report_date.strftime("%Y%m%d")
    slash = report_date.strftime("%Y/%m/%d")
    urls = (
        f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={compact}&type=ALLBUT0999&response=json",
        f"https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date={slash}&id=&response=json",
    )
    headers = {"Accept": "application/json", "User-Agent": "Moneymoney-TWSE-Dashboard"}
    async with httpx.AsyncClient(timeout=20.0, headers=headers) as client:
        responses = await asyncio.gather(*(client.get(url) for url in urls), return_exceptions=True)
    result: MarketSnapshot = {}
    for index, response in enumerate(responses):
        if isinstance(response, Exception) or response.status_code >= 400:
            continue
        try:
            payload = response.json()
        except ValueError:
            continue
        result.update(
            _parse_tables(payload, "證券代號", "收盤價", "成交股數")
            if index == 0 else _parse_tables(payload, "代號", "收盤", "成交股數")
        )
    if result:
        _snapshot_cache[report_date] = result
    return result


async def fetch_whale_period_market_data(
    report_dates: list[date],
) -> dict[str, dict[date, tuple[float, int]]]:
    unique_dates = sorted(set(report_dates))
    snapshots = await asyncio.gather(*(_fetch_snapshot(value) for value in unique_dates))
    by_stock: dict[str, dict[date, tuple[float, int]]] = {}
    for report_date, snapshot in zip(unique_dates, snapshots, strict=True):
        for stock_code, metrics in snapshot.items():
            by_stock.setdefault(stock_code, {})[report_date] = metrics
    return by_stock
