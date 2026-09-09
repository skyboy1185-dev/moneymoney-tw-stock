from datetime import UTC, datetime, timedelta
from dataclasses import replace

from app.services.long_term_selection import _PortfolioQuote, _valuation_metadata
from app.services.official_market_data import OfficialStockQuote, StockQuoteRequest, TwseMisMarketDataProvider


def quote(now):
    return OfficialStockQuote("3532", "test", 406, 410, 410, 411, 400, 1000, -4, -1,
                             now.isoformat(), "TWSE MIS", True)


def test_only_recent_verified_exchange_price_is_realtime():
    now = datetime(2026, 9, 9, 1, 30, tzinfo=UTC)
    live = quote(now)
    assert _valuation_metadata(live, now)["valuationIsRealtime"]
    old = replace(live, quote_timestamp=(now-timedelta(minutes=20)).isoformat())
    metadata = _valuation_metadata(old, now)
    assert metadata["quoteStatus"] == "delayed"
    assert metadata["quoteAgeSeconds"] == 1200
    assert not metadata["valuationIsRealtime"]
    yahoo = _PortfolioQuote(406, "Yahoo Finance 備援", now.isoformat())
    assert not _valuation_metadata(yahoo, now)["valuationIsRealtime"]
    assert _valuation_metadata(None, now)["quoteStatus"] == "stored"
    assert _valuation_metadata(_PortfolioQuote(406, "TWSE 日收盤價"), now)["quoteStatus"] == "close"


def test_relay_shares_prices_with_portfolio_without_refreshing_timestamp(monkeypatch):
    from app.services import official_market_data as module
    from types import SimpleNamespace
    now = datetime(2026, 9, 9, 1, 30, tzinfo=UTC)
    monkeypatch.setattr(module, "datetime", SimpleNamespace(now=lambda tz: now, fromtimestamp=datetime.fromtimestamp))
    provider = TwseMisMarketDataProvider()
    live = quote(now)
    provider.ingest_verified_quotes({"3532": live})
    provider.ingest_verified_quotes({"3532": replace(live, price=400, quote_timestamp=(now-timedelta(seconds=5)).isoformat())})
    cached = provider.cached_quotes([StockQuoteRequest("3532", "test", "上市")])["3532"]
    assert cached.price == 406
    assert cached.quote_timestamp == now.isoformat()
