import asyncio
from datetime import UTC, datetime, timedelta
from dataclasses import replace
from decimal import Decimal

import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.services.official_market_data import StockQuoteRequest, OfficialStockQuote
from app.services.strong_stock_quotes import load_quotes
from app.services.strong_stock import ensure_defaults, queue_paper_orders, fill_pending_orders, monitor_positions
from app.strong_stock_models import StrongStockPosition, StrongStockRanking

NOW = datetime(2026, 9, 9, 2, 30, tzinfo=UTC)
REQUEST = StockQuoteRequest("2330", "test", "上市")


def quote(price=100, age=0):
    return OfficialStockQuote("2330", "test", price, 99, 99, 105, 90, 1000, price-99, 1,
                             (NOW-timedelta(seconds=age)).isoformat(), "Yahoo 台灣股市", True)


class Provider:
    def __init__(self, quotes=None):
        self.quotes = quotes or {}
    def cached_quotes(self, requests):
        return dict(self.quotes)
    def ingest_verified_quotes(self, quotes):
        self.quotes.update(quotes)


def test_shared_fresh_quotes_need_no_network_and_stale_prices_are_not_usable():
    async def fail(*args):
        raise AssertionError("Unexpected fetch")
    quotes, health = asyncio.run(load_quotes([REQUEST], provider=Provider({"2330": quote()}), fetcher=fail, now=lambda: NOW))
    assert quotes["2330"].price == 100 and health["status"] == "current"
    async def timeout(*args):
        raise httpx.ReadTimeout("offline")
    quotes, health = asyncio.run(load_quotes([REQUEST], provider=Provider({"2330": quote(age=16)}), fetcher=timeout, now=lambda: NOW))
    assert quotes == {} and health["status"] == "unavailable"
    assert health["errors"] == ["ReadTimeout"]


def test_future_and_unknown_symbols_are_rejected_at_handoff():
    async def fetch(*args):
        return {"2330": quote(age=-1), "9999": replace(quote(), symbol="9999")}
    quotes, health = asyncio.run(load_quotes([REQUEST], provider=Provider(), fetcher=fetch, now=lambda: NOW))
    assert quotes == {} and health["freshCount"] == 0


def test_new_source_fills_and_marks_paper_position_but_old_quote_cannot_stop_it():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        ensure_defaults(db, "quote-test")
        db.add(StrongStockRanking(trade_date=NOW.date()-timedelta(days=1), symbol="2330", name="test", market="上市", industry="test",
            rank=1, total_score=90, relative_strength_score=24, trend_score=20, industry_score=14, volume_chip_score=12,
            fundamental_score=12, valuation_risk_score=8, data_completeness=1, close_price=100, entry_low=99, entry_high=101,
            stop_price=95, add_price=105, risk_reward=2, suggested_capital=300000, status="ENTRY_READY", entry_type="BREAKOUT",
            reasons_json="[]", blocked_reasons_json="[]", score_details_json="{}", source_snapshot_json="{}", strategy_version="1.0.0"))
        db.commit()
        assert queue_paper_orders(db, "quote-test", NOW.date()-timedelta(days=1)) == 1
        def prices(q):
            async def fetch(*args): return {"2330": q}
            quotes, _ = asyncio.run(load_quotes([REQUEST], provider=Provider(), fetcher=fetch, now=lambda: NOW))
            return {s: Decimal(str(r.price)) for s, r in quotes.items()}
        assert fill_pending_orders(db, "quote-test", prices(quote(age=30)), NOW)["filled"] == 0
        assert fill_pending_orders(db, "quote-test", prices(quote()), NOW)["filled"] == 1
        monitor_positions(db, "quote-test", prices(quote(price=104)), NOW)
        position = db.scalar(select(StrongStockPosition))
        assert position.current_price == 104 and position.status == "OPEN"
        monitor_positions(db, "quote-test", prices(quote(price=90, age=30)), NOW)
        assert position.current_price == 104 and position.status == "OPEN"
