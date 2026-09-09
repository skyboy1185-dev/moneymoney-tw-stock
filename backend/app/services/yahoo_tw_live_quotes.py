"""Bounded Taiwan Yahoo quote batches; no synthetic trade or book timestamps."""
from datetime import UTC, datetime
import math

from .official_market_data import OfficialStockQuote, StockQuoteRequest

SOURCE = "Yahoo 台灣股市"
ENDPOINT = "https://tw.stock.yahoo.com/_td-stock/api/resource/StockServices.stockList;symbols="


def ticker(request):
    if request.symbol == "t00":
        return "^TWII"
    return request.symbol + (".TWO" if request.market in {"上櫃", "TPEX", "OTC"} else ".TW")


def parse_quote(row, request: StockQuoteRequest, now: datetime):
    try:
        if row.get("symbol") != ticker(request) or row.get("exchangeDataDelayedBy") != 0 or row.get("marketStatus") != "open":
            return None
        stamp = datetime.fromisoformat(row["regularMarketTime"].replace("Z", "+00:00"))
        if stamp.tzinfo is None or stamp > now:
            return None
        values = [float(row[key]["raw"]) for key in ("price", "regularMarketPreviousClose", "regularMarketOpen", "regularMarketDayHigh", "regularMarketDayLow")]
        if not all(math.isfinite(x) and x > 0 for x in values):
            return None
        price, previous, opening, high, low = values
        volume = int(row.get("volume") or 0)
        if volume < 0 or high < low or not low <= price <= high:
            return None
        quote = OfficialStockQuote(request.symbol, request.name, price, previous, opening, high, low,
            volume, price-previous, (price/previous-1)*100, stamp.isoformat(), SOURCE, True,
            quote_kind="index" if request.symbol == "t00" else "trade", received_at=now.isoformat())
        from .quote_quality import trusted_quote
        return quote if trusted_quote(quote, now) else None
    except (KeyError, ValueError, TypeError, OverflowError, AttributeError):
        return None


async def fetch_batch(client, requests):
    if not requests or len(requests) > 50:
        return {}
    lookup = {ticker(r): r for r in requests}
    response = await client.get(ENDPOINT + ",".join(lookup),
        params={"_": str(int(datetime.now(UTC).timestamp()*1000))})
    response.raise_for_status()
    rows = response.json()
    if not isinstance(rows, list):
        raise ValueError("Yahoo quote batch is not a list")
    now = datetime.now(UTC)
    return {q.symbol: q for row in rows if isinstance(row, dict) and row.get("symbol") in lookup
            if (q := parse_quote(row, lookup[row["symbol"]], now)) is not None}
