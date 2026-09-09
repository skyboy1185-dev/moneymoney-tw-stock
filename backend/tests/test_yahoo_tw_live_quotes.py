from datetime import UTC, datetime, timedelta
from dataclasses import replace
from types import SimpleNamespace

from app.services.yahoo_tw_live_quotes import parse_quote
from app.services.official_market_data import StockQuoteRequest, TwseMisMarketDataProvider
from app.services.quote_quality import trusted_quote
from app.services.day_trading_quote_pump import DayTradingQuotePump

NOW = datetime(2026, 9, 9, 2, 10, tzinfo=UTC)
REQUEST = StockQuoteRequest("3532", "test", "上市")


def row(**changes):
    return {"symbol": "3532.TW", "marketStatus": "open", "exchangeDataDelayedBy": 0,
        "regularMarketTime": NOW.isoformat(), "price": {"raw": "420"},
        "regularMarketPreviousClose": {"raw": "398"}, "regularMarketOpen": {"raw": "412"},
        "regularMarketDayHigh": {"raw": "425"}, "regularMarketDayLow": {"raw": "401"},
        "volume": "100000", **changes}


def test_quote_requires_symbol_session_delay_and_valid_exchange_time():
    q = parse_quote(row(), REQUEST, NOW)
    assert q.price == 420 and q.volume == 100000
    assert trusted_quote(q, NOW, 15)
    assert not trusted_quote(q, NOW, 15, require_book=True)
    for changes in ({"symbol": "3693.TWO"}, {"exchangeDataDelayedBy": 20}, {"marketStatus": "closed"},
                    {"regularMarketTime": (NOW+timedelta(seconds=1)).isoformat()}, {"price": {"raw": "NaN"}},
                    {"volume": "-1"}, {"price": {"raw": "999"}}):
        assert parse_quote(row(**changes), REQUEST, NOW) is None


def test_old_trade_stays_old_even_when_received_again():
    q = parse_quote(row(regularMarketTime=(NOW-timedelta(minutes=2)).isoformat()), REQUEST, NOW)
    assert q is not None
    assert not trusted_quote(q, NOW, 15)


def test_only_independently_timed_fresh_book_can_enable_entry(monkeypatch):
    from app.services import official_market_data as module
    monkeypatch.setattr(module, "datetime", SimpleNamespace(now=lambda tz: NOW, fromisoformat=datetime.fromisoformat))
    provider = TwseMisMarketDataProvider()
    q = parse_quote(row(), REQUEST, NOW)
    book = replace(q, source="TWSE MIS 五檔參考價", book_timestamp=(NOW-timedelta(seconds=5)).isoformat(), best_bid=419.5, best_ask=420)
    provider.ingest_order_books({q.symbol: book})
    combined = provider.attach_order_book(q, NOW)
    assert combined.quote_timestamp == q.quote_timestamp
    assert combined.book_timestamp == book.book_timestamp
    assert trusted_quote(combined, NOW, 15, require_book=True)
    assert provider.attach_order_book(q, NOW+timedelta(seconds=20)).book_timestamp is None


def test_primary_yahoo_source_is_selected_without_fugle_entitlement():
    published = []
    pump = DayTradingQuotePump(provider=object(), publisher=published.append, utcnow=lambda: NOW)
    pump.update_targets([REQUEST], [])
    pump._cache_source("YAHOO_TW", {REQUEST.symbol: parse_quote(row(), REQUEST, NOW)})
    pump._publish_selected()
    pump._publish_selected()
    assert len(published) == 1
    assert pump.state["activeSource"] == "YAHOO_TW"
    assert pump.state["ready"] and pump.state["providerMode"] == "free_quotes"
