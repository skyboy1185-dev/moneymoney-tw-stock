"""Current, observed financial inputs for the strong-stock daily scan only."""
import asyncio
from datetime import UTC, date, datetime, timedelta
import math
import json

import httpx

_cache: dict[tuple[str, str, date], list[dict]] = {}


def financial_features(revenues: list[dict], statements: list[dict], today: date) -> dict:
    values = {}
    monthly = {}
    for row in revenues:
        try:
            published = str(row.get("create_time") or row["date"])[:10]
            if date.fromisoformat(published) > today:
                continue
            key = int(row["revenue_year"]) * 12 + int(row["revenue_month"]) - 1
            amount = float(row["revenue"])
            if math.isfinite(amount) and amount >= 0:
                monthly[key] = amount
        except (KeyError, TypeError, ValueError):
            continue
    if monthly:
        latest = max(monthly)
        # Do not count an old series as current coverage.
        if today.year * 12 + today.month - 1 - latest <= 2:
            for field, count in [("revenue_yoy", 1), ("revenue_3m_yoy", 3)]:
                keys = [latest - n for n in range(count)]
                if all(k in monthly and k - 12 in monthly for k in keys):
                    previous = sum(monthly[k - 12] for k in keys)
                    if previous > 0:
                        values[field] = (sum(monthly[k] for k in keys) / previous - 1) * 100
    quarters = {}
    for row in statements:
        try:
            period = date.fromisoformat(row["date"])
            value = float(row["value"])
            if period <= today and math.isfinite(value):
                quarters.setdefault(period, {})[row["type"]] = value
        except (KeyError, ValueError, TypeError):
            continue
    if quarters:
        latest = max(quarters)
        prior = date(latest.year - 1, latest.month, latest.day)
        if (today - latest).days <= 200 and prior in quarters:
            current, previous = quarters[latest], quarters[prior]
            for source, field in [("GrossProfit", "gross_margin_change"), ("OperatingIncome", "operating_margin_change")]:
                if all(source in q and q.get("Revenue", 0) > 0 for q in [current, previous]):
                    values[field] = (current[source] / current["Revenue"] - previous[source] / previous["Revenue"]) * 100
            if "EPS" in current:
                values["latest_eps"] = current["EPS"]
    return values


async def enrich_strong_stock_fundamentals(payload):
    today = datetime.now(UTC).date()
    # Never inject newly fetched financials into a previous day's archive.
    if payload.market.trade_date != today:
        return {"status": "skipped_historical_payload"}
    semaphore = asyncio.Semaphore(2)
    completed = failed = 0
    limited = False
    # Reuse only financial fields observed today, including across restarts.
    from sqlalchemy import select
    from ..database import SessionLocal
    from ..strong_stock_models import StrongStockScanArchive
    names = ["revenue_yoy", "revenue_3m_yoy", "gross_margin_change", "operating_margin_change", "latest_eps"]
    saved = {}
    with SessionLocal() as db:
        archives = db.scalars(select(StrongStockScanArchive).where(StrongStockScanArchive.trade_date == today)).all()
        for archive in archives:
            for row in json.loads(archive.payload_json).get("stocks", []):
                saved.setdefault(row["stock_code"], {}).update({k: row[k] for k in names if row.get(k) is not None})
    async with httpx.AsyncClient(timeout=15) as client:
        async def fetch(symbol, dataset):
            nonlocal limited
            key = (symbol, dataset, today)
            if key not in _cache:
                response = await client.get("https://api.finmindtrade.com/api/v4/data", params={
                    "dataset": dataset, "data_id": symbol,
                    "start_date": (today - timedelta(days=800)).isoformat(), "end_date": today.isoformat(),
                })
                if response.status_code in {402, 429}:
                    limited = True
                response.raise_for_status()
                result = response.json()
                if result.get("status") != 200 or not isinstance(result.get("data"), list):
                    raise ValueError("Financial source unavailable")
                _cache[key] = result["data"]
            return _cache[key]

        async def enrich(stock):
            nonlocal completed, failed
            async with semaphore:
                previous = saved.get(stock.stock_code, {})
                for name, value in previous.items():
                    setattr(stock, name, value)
                if all(name in previous for name in names[:4]):
                    completed += 1
                    return
                if limited:
                    failed += 1
                    return
                try:
                    revenue = await fetch(stock.stock_code, "TaiwanStockMonthRevenue")
                    statements = await fetch(stock.stock_code, "TaiwanStockFinancialStatements")
                    fields = financial_features(revenue, statements, today)
                    for name, value in fields.items():
                        setattr(stock, name, value)
                    completed += bool(fields)
                except (httpx.HTTPError, ValueError):
                    failed += 1
        await asyncio.gather(*(enrich(stock) for stock in payload.stocks))
    payload.data_sources.append("FinMind monthly revenue and quarterly financial statements (observed today)")
    payload.market.source_status["strong_stock_fundamentals"] = f"enriched={completed}; failed={failed}; rateLimited={limited}; observedAt={datetime.now(UTC).isoformat()}"
    return {"enriched": completed, "failed": failed, "rateLimited": limited, "total": len(payload.stocks)}
