"""Verified Taiwan index data used to complete the post-market report."""
from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx


INDEXES = {
    "taiex": ("^TWII", "加權指數"),
    "otc": ("^TWOII", "櫃買指數"),
}
TPEX_INDEX_SUMMARY_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/indexSummary"
TAIPEI = ZoneInfo("Asia/Taipei")


def _number(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def parse_tpex_index_summary(payload: dict[str, Any], report_date: date) -> dict[str, Any] | None:
    """Parse the official TPEx capitalization-weighted index close."""
    if payload.get("stat") != "ok" or payload.get("date") != report_date.strftime("%Y%m%d"):
        return None
    for table in payload.get("tables") or []:
        if "收市指數" not in (table.get("fields") or []):
            continue
        for row in table.get("data") or []:
            if not row or row[0] != "櫃買指數":
                continue
            close = _number(row[1] if len(row) > 1 else None)
            return_1d = _number(row[3] if len(row) > 3 else None)
            if close is None:
                return None
            data_time = datetime.combine(report_date, datetime.min.time(), TAIPEI).replace(hour=13, minute=30)
            return {
                "close": round(close, 2),
                "return_1d": round(return_1d, 2) if return_1d is not None else None,
                "above_ma60": None,
                "volume_ratio_20d": None,
                "support": None,
                "resistance": None,
                "data_time": data_time.isoformat(),
                "source": "TPEx 上櫃股價指數收盤行情",
            }
    return None


def parse_index_history(payload: dict[str, Any], report_date: date) -> dict[str, Any] | None:
    result = payload.get("chart", {}).get("result") or []
    if not result:
        return None
    chart = result[0]
    timestamps = chart.get("timestamp") or []
    quote = (chart.get("indicators", {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []
    rows = [
        (datetime.fromtimestamp(stamp, UTC).date(), float(close), float(volume or 0), stamp)
        for stamp, close, volume in zip(timestamps, closes, volumes, strict=False)
        if close is not None
    ]
    selected = next((index for index, row in enumerate(rows) if row[0] == report_date), None)
    if selected is None:
        return None
    _, close, volume, stamp = rows[selected]
    previous = rows[selected - 1][1] if selected else None
    window20 = rows[max(0, selected - 19):selected + 1]
    window60 = rows[max(0, selected - 59):selected + 1]
    avg_volume = sum(row[2] for row in window20) / len(window20) if window20 else 0
    return {
        "close": round(close, 2),
        "return_1d": round((close / previous - 1) * 100, 2) if previous else None,
        "above_ma60": close >= sum(row[1] for row in window60) / len(window60) if window60 else None,
        "volume_ratio_20d": round(volume / avg_volume, 2) if volume and avg_volume else None,
        "support": round(min(row[1] for row in window20), 2) if window20 else None,
        "resistance": round(max(row[1] for row in window20), 2) if window20 else None,
        "data_time": datetime.fromtimestamp(stamp, UTC).isoformat(),
        "source": "Yahoo Finance chart",
    }


async def fetch_post_market_indexes(report_date: date) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10, headers={"User-Agent": "Mozilla/5.0 TWSE post-market report"}) as client:
        yahoo_responses = await asyncio.gather(*(
            client.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
                params={"interval": "1d", "range": "3mo", "events": "div,splits"},
            )
            for ticker, _ in INDEXES.values()
        ), return_exceptions=True)
        tpex_responses = await asyncio.gather(client.get(
            TPEX_INDEX_SUMMARY_URL,
            params={"date": report_date.strftime("%Y/%m/%d"), "response": "json"},
            headers={"Accept": "application/json"},
        ), return_exceptions=True)
    indexes: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for (key, (_, name)), response in zip(INDEXES.items(), yahoo_responses, strict=True):
        if isinstance(response, Exception):
            errors[key] = str(response)[:180]
            continue
        try:
            response.raise_for_status()
            parsed = parse_index_history(response.json(), report_date)
            if parsed:
                indexes[key] = {"name": name, **parsed}
            else:
                errors[key] = f"{report_date} 沒有日線資料"
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            errors[key] = str(exc)[:180]
    official_response = tpex_responses[0]
    if isinstance(official_response, Exception):
        errors["otc_official"] = str(official_response)[:180]
    else:
        try:
            official_response.raise_for_status()
            official_otc = parse_tpex_index_summary(official_response.json(), report_date)
            if official_otc:
                yahoo_otc = indexes.get("otc", {})
                indexes["otc"] = {
                    "name": "櫃買指數",
                    **{key: value for key, value in yahoo_otc.items() if key != "name"},
                    **{key: value for key, value in official_otc.items() if value is not None},
                }
                errors.pop("otc", None)
            elif "otc" not in indexes:
                errors["otc_official"] = f"{report_date} 沒有官方收盤資料"
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            errors["otc_official"] = str(exc)[:180]
    return {"indexes": indexes, "errors": errors}
