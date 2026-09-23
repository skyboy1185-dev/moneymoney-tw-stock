from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from datetime import UTC, datetime
import hashlib
import hmac
from pathlib import Path
import threading
import time
import httpx
from zoneinfo import ZoneInfo
from pydantic import BaseModel, Field

from ..services.official_market_data import (
    OfficialStockQuote,
    StockQuoteRequest,
    official_market_data_provider,
)
from ..services.day_trading import day_trading_engine


router = APIRouter(prefix="/market-data", tags=["market-data"])

RELAY_DIGEST_PATH = Path("/app/data/quote-relay.sha256")
INDUSTRY_UNIVERSE_TTL_SECONDS = 6 * 60 * 60
_industry_universe_cache: tuple[float, list[StockQuoteRequest]] = (0.0, [])
_industry_universe_lock = threading.Lock()


def _text(row: dict, *keys: str) -> str:
    for key in keys:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _industry_universe() -> list[StockQuoteRequest]:
    global _industry_universe_cache
    now = time.monotonic()
    if _industry_universe_cache[0] > now:
        return _industry_universe_cache[1]
    with _industry_universe_lock:
        if _industry_universe_cache[0] > time.monotonic():
            return _industry_universe_cache[1]
        try:
            with httpx.Client(timeout=15) as client:
                listed = client.get("https://openapi.twse.com.tw/v1/opendata/t187ap03_L").json()
                otc = client.get("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O").json()
            rows: list[StockQuoteRequest] = []
            for row in listed:
                symbol = _text(row, "公司代號")
                industry = _text(row, "產業別")
                if symbol.isdigit() and len(symbol) == 4 and industry:
                    rows.append(StockQuoteRequest(symbol, _text(row, "公司簡稱") or symbol, "上市"))
            for row in otc:
                symbol = _text(row, "SecuritiesCompanyCode")
                industry = _text(row, "SecuritiesIndustryCode")
                if symbol.isdigit() and len(symbol) == 4 and industry:
                    rows.append(StockQuoteRequest(symbol, _text(row, "CompanyAbbreviation") or symbol, "上櫃"))
            if rows:
                _industry_universe_cache = (time.monotonic() + INDUSTRY_UNIVERSE_TTL_SECONDS, rows)
        except (httpx.HTTPError, ValueError, TypeError):
            pass
        return _industry_universe_cache[1]


def relay_authorized(authorization: str = Header(default="")) -> None:
    try:
        expected = RELAY_DIGEST_PATH.read_text().strip()
    except OSError:
        raise HTTPException(status_code=503, detail="Quote relay is not configured")
    token = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
    if not token or not hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), expected):
        raise HTTPException(status_code=401, detail="Invalid relay credential")


class RelayBatch(BaseModel):
    rows: list[dict[str, object]] = Field(min_length=1, max_length=60)


def _relay_held_requests():
    from sqlalchemy import select
    from ..database import SessionLocal
    from ..models import LongTermPosition
    with SessionLocal() as db:
        positions = list(db.scalars(select(LongTermPosition).where(LongTermPosition.status == "open")))
        held = [StockQuoteRequest(p.stock_code, p.stock_name, p.market_type) for p in positions]
    return held


def _relay_requests():
    from ..services.day_trading_quote_pump import day_trading_quote_pump
    return list({r.symbol: r for r in [*_industry_universe(), *_relay_held_requests(), *day_trading_quote_pump.relay_targets()]}.values())


@router.get("/relay/targets", dependencies=[Depends(relay_authorized)])
def relay_targets():
    from ..services.day_trading_quote_pump import day_trading_quote_pump
    held = _relay_held_requests()
    priority = day_trading_quote_pump.priority_symbols() | {r.symbol for r in held}
    targets = {r.symbol: r for r in [*held, *day_trading_quote_pump.relay_targets()]}
    return {"priorityRefreshSeconds": 5, "baselineBatchSize": 200,
            "items": [{"symbol": r.symbol, "name": r.name, "market": r.market,
                       "priority": r.symbol in priority}
                      for r in targets.values()]}


@router.post("/relay/quotes", dependencies=[Depends(relay_authorized)])
def relay_quotes(body: RelayBatch):
    from ..services.day_trading_quote_pump import day_trading_quote_pump
    from ..services.official_market_data import parse_mis_quote
    from ..services.quote_quality import trusted_quote
    targets = {r.symbol: r for r in _relay_requests()}
    previous = day_trading_engine.official_quotes_snapshot()
    now = datetime.now(UTC)
    quotes = {}
    books = {}
    for raw in body.rows:
        symbol = str(raw.get("c") or "")
        if not (symbol.isdigit() and len(symbol) == 4):
            continue
        fallback = targets.get(symbol) or StockQuoteRequest(
            symbol,
            str(raw.get("n") or symbol),
            "上市" if str(raw.get("ex") or "").lower() == "tse" else "上櫃",
        )
        quote = parse_mis_quote(raw, fallback, previous.get(symbol), now=now)
        if quote is not None:
            books[symbol] = quote
        if trusted_quote(quote, now=now, max_age_seconds=15):
            quotes[symbol] = quote
    official_market_data_provider.ingest_order_books(books)
    official_market_data_provider.ingest_market_snapshots(books)
    official_market_data_provider.ingest_verified_quotes(quotes)
    day_trading_quote_pump.ingest_mis_relay(quotes)
    accepted = len(quotes)
    return {"accepted": accepted, "rejected": len(body.rows) - accepted, "receivedAt": now.isoformat()}


class OfficialQuoteRequestItem(BaseModel):
    symbol: str = Field(pattern=r"^\d{4,6}$")
    name: str = Field(min_length=1, max_length=80)
    market: Literal["上市", "上櫃"]


class OfficialQuoteBatchRequest(BaseModel):
    items: list[OfficialQuoteRequestItem] = Field(min_length=1, max_length=60)


class OfficialSnapshotRequest(BaseModel):
    items: list[OfficialQuoteRequestItem] = Field(min_length=1, max_length=2500)


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


@router.post("/quotes/snapshot")
def get_official_quote_snapshot(body: OfficialSnapshotRequest) -> dict[str, object]:
    """Return the relay's current shared snapshot without issuing new MIS requests."""
    requests = [StockQuoteRequest(item.symbol, item.name, item.market) for item in body.items]
    quotes = official_market_data_provider.cached_market_snapshots(requests)
    now = datetime.now(UTC)
    taipei_now = now.astimezone(ZoneInfo("Asia/Taipei"))
    cash_session = taipei_now.weekday() < 5 and (9, 0) <= (taipei_now.hour, taipei_now.minute) <= (13, 30)
    fresh: dict[str, OfficialStockQuote] = {}
    for symbol, quote in quotes.items():
        try:
            received = datetime.fromisoformat(quote.received_at or quote.quote_timestamp)
            age = (now - received.astimezone(UTC)).total_seconds()
        except ValueError:
            continue
        if quote.quote_timestamp[:10] == taipei_now.date().isoformat() and (not cash_session or 0 <= age <= 180):
            fresh[symbol] = quote
    return {"items": [_quote_payload(fresh[item.symbol]) for item in body.items if item.symbol in fresh]}


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
