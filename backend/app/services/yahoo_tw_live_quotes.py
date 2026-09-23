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


def parse_order_book(row, stamp):
    """Use the source snapshot time, never receipt time, for its attached book."""
    try:
        levels = row.get("orderbook")
        if not isinstance(levels, list) or not 1 <= len(levels) <= 5:
            return {}
        if any(isinstance(level[key], bool) for level in levels for key in ("bid", "ask", "bidVol", "askVol")):
            return {}
        bids = tuple(float(level["bid"]) for level in levels)
        asks = tuple(float(level["ask"]) for level in levels)
        bid_sizes = tuple(float(level["bidVol"]) for level in levels)
        ask_sizes = tuple(float(level["askVol"]) for level in levels)
        if not all(math.isfinite(x) and x > 0 for x in (*bids, *asks, *bid_sizes, *ask_sizes)):
            return {}
        if any(not x.is_integer() for x in (*bid_sizes, *ask_sizes)):
            return {}
        if bids[0] > asks[0] or any(a <= b for a, b in zip(bids, bids[1:])) or any(a >= b for a, b in zip(asks, asks[1:])):
            return {}
        if float(row["bid"]["raw"]) != bids[0] or float(row["ask"]["raw"]) != asks[0]:
            return {}
        return {"best_bid": bids[0], "best_ask": asks[0], "bid_prices": bids,
                "ask_prices": asks, "bid_volumes": tuple(int(x) for x in bid_sizes),
                "ask_volumes": tuple(int(x) for x in ask_sizes), "book_timestamp": stamp.isoformat()}
    except (KeyError, TypeError, ValueError, OverflowError):
        return {}


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
            quote_kind="index" if request.symbol == "t00" else "trade", received_at=now.isoformat(),
            **(parse_order_book(row, stamp) if request.symbol != "t00" else {}))
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
