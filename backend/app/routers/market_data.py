from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..services.official_market_data import (
    OfficialStockQuote,
    StockQuoteRequest,
    official_market_data_provider,
)


router = APIRouter(prefix="/market-data", tags=["market-data"])


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
