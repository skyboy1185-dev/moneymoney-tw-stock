from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx


TWSE_DAILY_ENDPOINT = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_DAILY_ENDPOINT = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
THREE_GATE_CACHE_TTL = timedelta(hours=6)


@dataclass(frozen=True)
class ThreeGatePrice:
    source_date: str
    upper: float
    middle: float
    lower: float


@dataclass(frozen=True)
class ThreeGateDecision:
    direction: str
    level: str
    position: str
    crossed: bool
    status: str


def stock_tick_size(price: float) -> float:
    if price < 10:
        return 0.01
    if price < 50:
        return 0.05
    if price < 100:
        return 0.1
    if price < 500:
        return 0.5
    if price < 1_000:
        return 1
    return 5


def _round_to_stock_tick(price: float) -> float:
    tick = stock_tick_size(price)
    rounded = math.floor(price / tick + 0.5) * tick
    digits = 2 if tick < 0.1 else 1 if tick < 1 else 0
    return round(rounded, digits)


def calculate_three_gate_price(source_date: str, high: float, low: float) -> ThreeGatePrice | None:
    if high <= 0 or low <= 0 or high < low:
        return None
    price_range = high - low
    return ThreeGatePrice(
        source_date=source_date,
        upper=_round_to_stock_tick(high + price_range * 0.382),
        middle=_round_to_stock_tick((high + low) / 2),
        lower=_round_to_stock_tick(low - price_range * 0.382),
    )


def evaluate_three_gate_direction(
    current_price: float,
    previous_price: float | None,
    three_gate: ThreeGatePrice,
) -> ThreeGateDecision:
    if current_price >= three_gate.middle:
        level = "upper" if current_price >= three_gate.upper else "middle"
        crossed = previous_price is not None and previous_price < three_gate.middle <= current_price
        status = "突破上關價，三關價偏多" if level == "upper" else "站上中關價，三關價偏多"
        return ThreeGateDecision("long", level, "above", crossed, status)
    level = "lower" if current_price < three_gate.lower else "middle"
    crossed = previous_price is not None and previous_price >= three_gate.middle > current_price
    status = "跌破下關價，三關價偏空" if level == "lower" else "跌破中關價，三關價偏空"
    return ThreeGateDecision("short", level, "below", crossed, status)


def _number(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _roc_date(value: Any) -> str:
    text = str(value or "").replace("/", "").replace("-", "").strip()
    if len(text) == 7 and text.isdigit():
        return f"{int(text[:3]) + 1911:04d}-{text[3:5]}-{text[5:7]}"
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return str(value or "")


def parse_twse_daily_rows(rows: list[dict[str, Any]]) -> dict[str, ThreeGatePrice]:
    result: dict[str, ThreeGatePrice] = {}
    for row in rows:
        symbol = str(row.get("Code") or "").strip()
        high = _number(row.get("HighestPrice"))
        low = _number(row.get("LowestPrice"))
        if symbol and high is not None and low is not None:
            gate = calculate_three_gate_price(_roc_date(row.get("Date")), high, low)
            if gate is not None:
                result[symbol] = gate
    return result


def parse_tpex_daily_rows(rows: list[dict[str, Any]]) -> dict[str, ThreeGatePrice]:
    result: dict[str, ThreeGatePrice] = {}
    for row in rows:
        symbol = str(row.get("SecuritiesCompanyCode") or "").strip()
        high = _number(row.get("High"))
        low = _number(row.get("Low"))
        if symbol and high is not None and low is not None:
            gate = calculate_three_gate_price(_roc_date(row.get("Date")), high, low)
            if gate is not None:
                result[symbol] = gate
    return result


class OfficialThreeGatePriceProvider:
    def __init__(self) -> None:
        self._cache: dict[str, ThreeGatePrice] = {}
        self._cached_at: datetime | None = None
        self._lock = asyncio.Lock()

    async def get_levels(self, symbols: tuple[str, ...]) -> dict[str, ThreeGatePrice]:
        async with self._lock:
            now = datetime.now(UTC)
            if self._cached_at is None or now - self._cached_at >= THREE_GATE_CACHE_TTL:
                self._cache = await self._fetch_all()
                self._cached_at = now
            return {symbol: self._cache[symbol] for symbol in symbols if symbol in self._cache}

    @staticmethod
    async def _fetch_all() -> dict[str, ThreeGatePrice]:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            responses = await asyncio.gather(
                client.get(TWSE_DAILY_ENDPOINT),
                client.get(TPEX_DAILY_ENDPOINT),
                return_exceptions=True,
            )
        result: dict[str, ThreeGatePrice] = {}
        successful_sources = 0
        for response, parser in zip(responses, (parse_twse_daily_rows, parse_tpex_daily_rows), strict=True):
            if isinstance(response, Exception):
                continue
            try:
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError):
                continue
            if isinstance(payload, list):
                result.update(parser(payload))
                successful_sources += 1
        if successful_sources == 0:
            raise RuntimeError("TWSE and TPEx three-gate daily data are unavailable")
        return result


official_three_gate_price_provider = OfficialThreeGatePriceProvider()
