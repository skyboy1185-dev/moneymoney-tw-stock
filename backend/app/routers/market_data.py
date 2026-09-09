from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from datetime import UTC, datetime
import hashlib
import hmac
from pathlib import Path
from pydantic import BaseModel, Field

from ..services.official_market_data import (
    OfficialStockQuote,
    StockQuoteRequest,
    official_market_data_provider,
)
from ..services.day_trading import day_trading_engine


router = APIRouter(prefix="/market-data", tags=["market-data"])

RELAY_DIGEST_PATH = Path("/app/data/quote-relay.sha256")


def relay_authorized(authorization: str = Header(default="")) -> None:
    try:
        expected = RELAY_DIGEST_PATH.read_text().strip()
    except OSError:
        raise HTTPException(status_code=503, detail="Quote relay is not configured")
    token = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
    if not token or not hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), expected):
        raise HTTPException(status_code=401, detail="Invalid relay credential")


class RelayBatch(BaseModel):
    rows: list[dict[str, str]] = Field(min_length=1, max_length=60)


@router.get("/relay/targets", dependencies=[Depends(relay_authorized)])
def relay_targets():
    from ..services.day_trading_quote_pump import day_trading_quote_pump
    return {"items": [{"symbol": r.symbol, "name": r.name, "market": r.market}
                      for r in day_trading_quote_pump.relay_targets()]}


@router.post("/relay/quotes", dependencies=[Depends(relay_authorized)])
def relay_quotes(body: RelayBatch):
    from ..services.day_trading_quote_pump import day_trading_quote_pump
    from ..services.official_market_data import parse_mis_quote
    from ..services.quote_quality import trusted_quote
    targets = {r.symbol: r for r in day_trading_quote_pump.relay_targets()}
    previous = day_trading_engine.official_quotes_snapshot()
    now = datetime.now(UTC)
    quotes = {}
    for raw in body.rows:
        symbol = raw.get("c", "")
        if symbol not in targets:
            continue
        quote = parse_mis_quote(raw, targets[symbol], previous.get(symbol), now=now)
        if trusted_quote(quote, now=now, max_age_seconds=15):
            quotes[symbol] = quote
    accepted = day_trading_quote_pump.ingest_mis_relay(quotes)
    return {"accepted": accepted, "rejected": len(body.rows) - accepted, "receivedAt": now.isoformat()}


class OfficialQuoteRequestItem(BaseModel):
    symbol: str = Field(pattern=r"^\d{4,6}$")
    name: str = Field(min_length=1, max_length=80)
    market: Literal["上市", "上櫃"]


class OfficialQuoteBatchRequest(BaseModel):
    items: list[OfficialQuoteRequestItem] = Field(min_length=1, max_length=60)


def _quote_payload(quote: OfficialStockQuote) -> dict[str, object]:
    return {
        "symbol": quote.symbol,
        "name": quote.name,
        "price": quote.price,
        "previousClose": quote.previous_close,
        "open": quote.open,
        "high": quote.high,
        "low": quote.low,
        "volume": quote.volume,
        "change": quote.change,
        "changePercent": quote.change_percent,
        "quoteTimestamp": quote.quote_timestamp,
        "source": quote.source,
        "isRealtime": quote.is_realtime,
        "bestBid": quote.best_bid,
        "bestAsk": quote.best_ask,
    }


@router.post("/quotes")
async def get_official_quotes(body: OfficialQuoteBatchRequest) -> dict[str, object]:
    requests = [
        StockQuoteRequest(item.symbol, item.name, item.market)
        for item in body.items
    ]
    quotes = await official_market_data_provider.get_quotes(requests)
    return {
        "items": [
            _quote_payload(quotes[item.symbol])
            for item in body.items
            if item.symbol in quotes
        ],
    }


@router.post("/quote-history")
async def get_official_quote_history(body: OfficialQuoteBatchRequest) -> dict[str, object]:
    """Serve the shared in-memory intraday samples without extra MIS requests."""
    return {
        "items": [
            {
                "symbol": item.symbol,
                "points": day_trading_engine.quote_history_for(item.symbol),
            }
            for item in body.items
        ],
    }
