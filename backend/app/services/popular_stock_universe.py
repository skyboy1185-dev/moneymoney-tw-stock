from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from itertools import zip_longest
from typing import Any

import httpx

from .theme_stock_universe import ELECTRONIC_ALERT_STOCKS, ThemeStock


POPULAR_THEME = "熱門股"
TWSE_VOLUME_RANK_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_VOLUME_RANK_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
MOMENTUM_UNIVERSE_SIZE = 300
POPULAR_STOCKS_PER_MARKET = 300

# 讓服務剛啟動或官方排行暫時無法連線時，仍有一組高流動性熱門股可監測。
POPULAR_ALERT_FALLBACK_STOCKS = (
    ThemeStock("2303", "聯電", "上市", "半導體", (POPULAR_THEME,)),
    ThemeStock("2354", "鴻準", "上市", "電子零組件", (POPULAR_THEME,)),
    ThemeStock("2409", "友達", "上市", "光電", (POPULAR_THEME,)),
    ThemeStock("3231", "緯創", "上市", "電腦及週邊", (POPULAR_THEME,)),
    ThemeStock("3481", "群創", "上市", "光電", (POPULAR_THEME,)),
    ThemeStock("6116", "彩晶", "上市", "光電", (POPULAR_THEME,)),
    ThemeStock("6770", "力積電", "上市", "半導體", (POPULAR_THEME,)),
    ThemeStock("3105", "穩懋", "上櫃", "半導體", (POPULAR_THEME,)),
    ThemeStock("3260", "威剛", "上櫃", "半導體", (POPULAR_THEME,)),
    ThemeStock("5351", "鈺創", "上櫃", "半導體", (POPULAR_THEME,)),
    ThemeStock("5483", "中美晶", "上櫃", "半導體", (POPULAR_THEME,)),
    ThemeStock("8358", "金居", "上櫃", "電子零組件", (POPULAR_THEME,)),
)


def _is_common_stock_symbol(value: object) -> bool:
    return bool(re.fullmatch(r"[1-9]\d{3}", str(value or "").strip()))


def _is_financial_stock(symbol: str, name: str, industry: str = "") -> bool:
    """Exclude banks, financial holdings, insurers and brokers from this radar."""
    if "金融" in industry or "保險" in industry:
        return True
    if name.endswith(("金", "銀", "票", "證", "期", "產險", "保", "壽")):
        return True
    # Covers financial companies whose abbreviated exchange name is ambiguous.
    return symbol in {
        "2801", "2809", "2812", "2820", "2832", "2834", "2836", "2838",
        "2845", "2849", "2850", "2851", "2852", "2855", "2867", "2880",
        "2881", "2882", "2883", "2884", "2885", "2886", "2887", "2888",
        "2889", "2890", "2891", "2892", "2897", "5876", "5880", "6005",
        "6015", "6016", "6020", "6021", "6023", "6024", "6026", "6027",
    }


def _amount(value: object) -> int:
    try:
        return int(float(str(value or "0").replace(",", "").strip()))
    except ValueError:
        return 0


def parse_twse_volume_rank(payload: object, limit: int = POPULAR_STOCKS_PER_MARKET) -> tuple[ThemeStock, ...]:
    if isinstance(payload, list):
        ranked = sorted(
            (item for item in payload if isinstance(item, dict)),
            key=lambda item: _amount(item.get("TradeValue")),
            reverse=True,
        )
        rows: list[tuple[str, str]] = [
            (str(item.get("Code") or "").strip(), str(item.get("Name") or "").strip())
            for item in ranked
        ]
    elif isinstance(payload, dict) and isinstance(payload.get("data"), list):
        rows = [
            (str(row[1]).strip(), str(row[2]).strip())
            for row in payload["data"]
            if isinstance(row, list) and len(row) >= 3
        ]
    else:
        return ()
    stocks: list[ThemeStock] = []
    for symbol, name in rows:
        if not _is_common_stock_symbol(symbol) or not name or _is_financial_stock(symbol, name):
            continue
        stocks.append(ThemeStock(symbol, name, "上市", "市場熱門", (POPULAR_THEME,)))
        if len(stocks) >= limit:
            break
    return tuple(stocks)


def parse_tpex_volume_rank(payload: object, limit: int = POPULAR_STOCKS_PER_MARKET) -> tuple[ThemeStock, ...]:
    if not isinstance(payload, list):
        return ()
    dictionary_rows = [row for row in payload if isinstance(row, dict)]
    dated_rows = [row for row in dictionary_rows if str(row.get("Date") or "").strip()]
    if dated_rows:
        latest_date = max(str(row.get("Date") or "").strip() for row in dated_rows)
        dictionary_rows = [
            row for row in dated_rows if str(row.get("Date") or "").strip() == latest_date
        ]
    dictionary_rows.sort(
        key=lambda item: _amount(item.get("TransactionAmount")),
        reverse=True,
    )
    stocks: list[ThemeStock] = []
    for row in dictionary_rows:
        symbol = str(row.get("SecuritiesCompanyCode", "")).strip()
        name = str(row.get("CompanyName", "")).strip()
        if not _is_common_stock_symbol(symbol) or not name or _is_financial_stock(symbol, name):
            continue
        stocks.append(ThemeStock(symbol, name, "上櫃", "市場熱門", (POPULAR_THEME,)))
        if len(stocks) >= limit:
            break
    return tuple(stocks)


def merge_momentum_stocks(popular: Sequence[ThemeStock]) -> tuple[tuple[ThemeStock, ...], frozenset[str]]:
    popular_candidates = tuple(
        stock for stock in (*POPULAR_ALERT_FALLBACK_STOCKS, *popular)
        if not _is_financial_stock(stock.symbol, stock.name, stock.industry)
    )
    merged: dict[str, ThemeStock] = {stock.symbol: stock for stock in ELECTRONIC_ALERT_STOCKS}
    for stock in popular_candidates:
        merged.setdefault(stock.symbol, stock)
        if len(merged) >= MOMENTUM_UNIVERSE_SIZE:
            break
    popular_symbols = frozenset(
        stock.symbol for stock in popular_candidates if stock.symbol in merged
    )
    return tuple(merged.values()), popular_symbols


class OfficialPopularStockProvider:
    async def fetch(self) -> tuple[ThemeStock, ...]:
        timeout = httpx.Timeout(8.0, connect=4.0)
        headers = {"User-Agent": "MoneyMoney-TWSE/1.0"}
        async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
            responses = await asyncio.gather(
                client.get(TWSE_VOLUME_RANK_URL),
                client.get(TPEX_VOLUME_RANK_URL),
                return_exceptions=True,
            )

        stocks: list[ThemeStock] = []
        twse_response, tpex_response = responses
        if isinstance(twse_response, httpx.Response) and twse_response.is_success:
            twse_stocks = parse_twse_volume_rank(twse_response.json())
        else:
            twse_stocks = ()
        if isinstance(tpex_response, httpx.Response) and tpex_response.is_success:
            tpex_stocks = parse_tpex_volume_rank(tpex_response.json())
        else:
            tpex_stocks = ()
        # 上市、上櫃成交熱門股交錯加入，避免擴充名額被單一市場占滿。
        for listed, otc in zip_longest(twse_stocks, tpex_stocks):
            if listed is not None:
                stocks.append(listed)
            if otc is not None:
                stocks.append(otc)
        return tuple(stocks)
