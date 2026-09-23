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


def book_row():
    return row(bid={"raw": "419.5"}, ask={"raw": "420"}, orderbook=[
        {"bid": "419.5", "ask": "420", "bidVol": 6000, "askVol": 10000},
        {"bid": "419", "ask": "420.5", "bidVol": 5000, "askVol": 2000},
    ])


def test_yahoo_snapshot_book_uses_source_time_and_share_units():
    from app.services.day_trading_v2_quotes import entry_spread
    q = parse_quote(book_row(), REQUEST, NOW)
    assert q.bid_volumes == (6000, 5000)
    assert q.ask_prices == (420, 420.5)
    assert q.book_timestamp == NOW.isoformat()
    assert trusted_quote(q, NOW, 15, require_book=True)
    assert 0 < entry_spread(q, NOW, 15) < .5
    old = parse_quote(book_row(), REQUEST, NOW + timedelta(seconds=16))
    assert old.book_timestamp == q.book_timestamp
    assert entry_spread(old, NOW + timedelta(seconds=16), 15) is None
    # A valid native book must not be overwritten by an older MIS cache.
    assert TwseMisMarketDataProvider().attach_order_book(q, NOW) is q


def test_invalid_yahoo_books_keep_trade_but_cannot_authorize_entry():
    import copy
    bad_rows = []
    for key, value in [("bid", "421"), ("ask", "NaN"), ("bidVol", -1), ("askVol", 1.5), ("bidVol", True)]:
        data = book_row()
        data["orderbook"][0][key] = value
        bad_rows.append(data)
    mismatch = book_row()
    mismatch["bid"]["raw"] = "418"
    bad_rows.extend([mismatch, row(orderbook=[]), row(orderbook="invalid")])
    for data in bad_rows:
        q = parse_quote(copy.deepcopy(data), REQUEST, NOW)
        assert q is not None and q.price == 420
        assert not trusted_quote(q, NOW, 15, require_book=True)


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


def test_yahoo_slow_batch_does_not_block_second_batch_and_concurrency_is_bounded(monkeypatch):
    import asyncio
    from contextlib import suppress
    from app.services import yahoo_tw_live_quotes as yahoo

    async def scenario():
        pump = DayTradingQuotePump(provider=object(), publisher=lambda quotes: None)
        targets = [StockQuoteRequest(str(1000+i), "test", "??") for i in range(150)]
        pump.update_targets(targets[:30], targets[30:])
        started = []
        second_finished = asyncio.Event()
        release = asyncio.Event()

        async def fetch(_client, batch):
            started.append(batch)
            if len(started) == 1:
                await release.wait()
            else:
                second_finished.set()
            return {}

        monkeypatch.setattr(yahoo, "fetch_batch", fetch)
        task = asyncio.create_task(pump._run_yahoo())
        try:
            await asyncio.wait_for(second_finished.wait(), 2)
            assert len(started) == 2
            assert all(len(batch) <= 50 for batch in started)
            assert not release.is_set()
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    asyncio.run(scenario())
