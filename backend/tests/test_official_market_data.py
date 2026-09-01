import asyncio
from datetime import UTC, datetime, timedelta

import httpx

from app.services.day_trading import MockDayTradingEngine
from app.services import official_market_data
from app.services.official_market_data import (
    OfficialStockQuote,
    StockQuoteRequest,
    TwseMisMarketDataProvider,
    parse_mis_quote,
)


def test_force_refresh_retains_verified_quote_when_twse_mis_temporarily_fails(
    monkeypatch,
) -> None:
    provider = TwseMisMarketDataProvider()
    quote = OfficialStockQuote(
        symbol="2330",
        name="TSMC",
        price=1000,
        previous_close=995,
        open=996,
        high=1002,
        low=994,
        volume=10_000_000,
        change=5,
        change_percent=0.5,
        quote_timestamp="2026-08-11T10:20:00+08:00",
        source="TWSE MIS",
        is_realtime=True,
    )
    provider._cache[quote.symbol] = (quote, datetime.now(UTC) + timedelta(seconds=5))

    class FailingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def get(self, *args, **kwargs):
            request = httpx.Request("GET", "https://mis.twse.com.tw")
            raise httpx.ConnectError("temporary outage", request=request)

    monkeypatch.setattr(
        official_market_data.httpx,
        "AsyncClient",
        lambda **kwargs: FailingClient(),
    )

    result = asyncio.run(
        provider.get_quotes(
            [StockQuoteRequest("2330", "TSMC", "上市")],
            force_refresh=True,
        ),
    )

    assert result == {"2330": quote}


def test_locked_refresh_returns_verified_cache_without_waiting() -> None:
    provider = TwseMisMarketDataProvider()
    quote = OfficialStockQuote(
        symbol="2330",
        name="TSMC",
        price=1000,
        previous_close=995,
        open=996,
        high=1002,
        low=994,
        volume=10_000_000,
        change=5,
        change_percent=0.5,
        quote_timestamp="2026-08-11T10:20:00+08:00",
        source="TWSE MIS",
        is_realtime=True,
    )
    provider._cache[quote.symbol] = (quote, datetime.now(UTC) - timedelta(seconds=1))

    async def locked_lookup() -> dict[str, OfficialStockQuote]:
        await provider._lock.acquire()
        try:
            return await provider.get_quotes(
                [StockQuoteRequest("2330", "TSMC", "上市")],
                force_refresh=False,
            )
        finally:
            provider._lock.release()

    result = asyncio.run(locked_lookup())

    assert result == {"2330": quote}


def test_quote_history_for_returns_only_today_and_respects_limit() -> None:
    engine = MockDayTradingEngine()
    now = engine._now().astimezone(official_market_data.TAIPEI)
    yesterday = now - timedelta(days=1)
    quotes = [
        OfficialStockQuote(
            symbol="2330",
            name="台積電",
            price=price,
            previous_close=995,
            open=996,
            high=max(996, price),
            low=min(996, price),
            volume=10_000_000,
            change=price - 995,
            change_percent=(price - 995) / 995 * 100,
            quote_timestamp=timestamp.isoformat(),
            source="TWSE MIS",
            is_realtime=True,
        )
        for price, timestamp in (
            (990, yesterday),
            (1000, now - timedelta(minutes=1)),
            (1005, now),
        )
    ]
    for quote in quotes:
        engine.update_official_quotes({quote.symbol: quote})

    assert engine.quote_history_for("2330", limit=1) == [{
        "timestamp": quotes[-1].quote_timestamp,
        "price": 1005.0,
        "isRealtime": True,
    }]


def test_twse_mis_requests_large_pools_in_batches_and_retries_invalid_json(
    monkeypatch,
) -> None:
    provider = TwseMisMarketDataProvider()
    calls: list[str] = []

    class FakeResponse:
        status_code = 200

        def __init__(self, invalid: bool = False) -> None:
            self.invalid = invalid

        def raise_for_status(self) -> None:
            return None

        def json(self):
            if self.invalid:
                raise ValueError("truncated response")
            return {"msgArray": []}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def get(self, *args, **kwargs):
            calls.append(kwargs["params"]["ex_ch"])
            return FakeResponse(invalid=len(calls) == 1)

    monkeypatch.setattr(
        official_market_data.httpx,
        "AsyncClient",
        lambda **kwargs: FakeClient(),
    )
    stocks = [
        StockQuoteRequest(str(1000 + index), f"Stock {index}", "上市")
        for index in range(36)
    ]

    result = asyncio.run(provider.get_quotes(stocks, force_refresh=True))

    assert result == {}
    expected_batches = 1 + ((len(stocks) + official_market_data.MIS_BATCH_SIZE - 1) // official_market_data.MIS_BATCH_SIZE)
    assert len(calls) == expected_batches
    assert len(calls[0].split("|")) == official_market_data.MIS_BATCH_SIZE
    assert calls[1] == calls[0]
    assert len(calls[-1].split("|")) == len(stocks) % official_market_data.MIS_BATCH_SIZE


def test_twse_mis_splits_failed_batches_and_keeps_recovered_quotes(monkeypatch) -> None:
    provider = TwseMisMarketDataProvider()
    calls: list[str] = []

    class FakeResponse:
        status_code = 200

        def __init__(self, symbols: list[str]) -> None:
            self.symbols = symbols

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {
                "msgArray": [
                    {
                        "c": symbol,
                        "n": f"Stock {symbol}",
                        "z": "100.0000",
                        "y": "99.0000",
                        "o": "99.5000",
                        "h": "101.0000",
                        "l": "98.5000",
                        "v": "1000",
                        "d": "20260727",
                        "t": "11:06:40",
                    }
                    for symbol in self.symbols
                ]
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def get(self, *args, **kwargs):
            channels = kwargs["params"]["ex_ch"]
            calls.append(channels)
            symbols = [channel.split("_", 1)[1].split(".", 1)[0] for channel in channels.split("|")]
            if len(symbols) > official_market_data.MIS_FALLBACK_BATCH_SIZE:
                request = httpx.Request("GET", "https://mis.twse.com.tw")
                raise httpx.RemoteProtocolError("server disconnected", request=request)
            return FakeResponse(symbols)

    monkeypatch.setattr(
        official_market_data.httpx,
        "AsyncClient",
        lambda **kwargs: FakeClient(),
    )
    stocks = [
        StockQuoteRequest(str(2000 + index), f"Stock {index}", "銝?")
        for index in range(official_market_data.MIS_BATCH_SIZE)
    ]

    result = asyncio.run(provider.get_quotes(stocks, force_refresh=True))

    assert set(result) == {stock.symbol for stock in stocks}
    assert any(len(call.split("|")) == official_market_data.MIS_FALLBACK_BATCH_SIZE for call in calls)


def test_parse_twse_mis_quote_uses_latest_trade_and_converts_lots_to_shares() -> None:
    quote = parse_mis_quote(
        {
            "c": "2317",
            "n": "鴻海",
            "z": "252.5000",
            "y": "257.5000",
            "o": "254.0000",
            "h": "256.0000",
            "l": "251.5000",
            "v": "32703",
            "d": "20260724",
            "t": "13:30:00",
            "b": "252.0000_251.5000_",
            "a": "252.5000_253.0000_",
        },
        StockQuoteRequest("2317", "鴻海", "上市"),
    )

    assert quote is not None
    assert quote.price == 252.5
    assert quote.previous_close == 257.5
    assert quote.change == -5
    assert round(quote.change_percent, 2) == -1.94
    assert quote.volume == 32_703_000
    assert quote.quote_timestamp == "2026-07-24T13:30:00+08:00"
    assert quote.source == "TWSE MIS"
    assert quote.best_bid == 252
    assert quote.best_ask == 252.5


def test_parse_twse_mis_quote_uses_labelled_order_book_reference_instead_of_previous_close() -> None:
    quote = parse_mis_quote(
        {
            "c": "2317",
            "n": "鴻海",
            "z": "-",
            "y": "252.5000",
            "o": "253.0000",
            "h": "254.5000",
            "l": "248.0000",
            "v": "16523",
            "d": "20260727",
            "t": "11:06:40",
            "b": "248.0000_247.5000_",
            "a": "248.5000_249.0000_",
        },
        StockQuoteRequest("2317", "鴻海", "上市"),
    )

    assert quote is not None
    assert quote.price == 248.5
    assert quote.previous_close == 252.5
    assert quote.quote_timestamp == "2026-07-27T11:06:40+08:00"
    assert quote.source == "TWSE MIS 五檔參考價"


def test_parse_twse_mis_quote_keeps_last_valid_trade_when_z_is_temporarily_empty() -> None:
    previous = OfficialStockQuote(
        symbol="2317",
        name="鴻海",
        price=248.5,
        previous_close=252.5,
        open=253,
        high=254.5,
        low=248,
        volume=16_520_000,
        change=-4,
        change_percent=-1.5842,
        quote_timestamp="2026-07-27T11:06:35+08:00",
        source="TWSE MIS",
        is_realtime=True,
        best_bid=248,
        best_ask=248.5,
    )
    quote = parse_mis_quote(
        {
            "c": "2317",
            "n": "鴻海",
            "z": "-",
            "y": "252.5000",
            "o": "253.0000",
            "h": "254.5000",
            "l": "247.5000",
            "v": "16523",
            "d": "20260727",
            "t": "11:06:40",
            "b": "248.0000_247.5000_",
            "a": "248.5000_249.0000_",
        },
        StockQuoteRequest("2317", "鴻海", "上市"),
        previous,
        now=datetime.fromisoformat("2026-07-27T11:06:45+08:00"),
    )

    assert quote is not None
    assert quote.price == 248.5
    assert quote.previous_close == 252.5
    assert quote.volume == 16_523_000
    assert quote.quote_timestamp == "2026-07-27T11:06:35+08:00"
    assert quote.is_realtime is True


def test_day_trading_signal_uses_official_quote_and_waits_for_live_warmup() -> None:
    engine = MockDayTradingEngine()
    engine.update_official_quotes({
        "2317": OfficialStockQuote(
            symbol="2317",
            name="鴻海",
            price=252.5,
            previous_close=257.5,
            open=254,
            high=256,
            low=251.5,
            volume=32_703_000,
            change=-5,
            change_percent=-1.9417475728,
            quote_timestamp="2026-07-24T13:30:00+08:00",
            source="TWSE MIS",
            is_realtime=False,
        ),
    })

    signal = next(item for item in engine.signals() if item["symbol"] == "2317")

    assert signal["price"] == 252.5
    assert signal["previousClose"] == 257.5
    assert signal["changePercent"] == -1.94
    assert signal["volume"] == 32_703_000
    assert signal["dataSource"] == "TWSE MIS"
    assert signal["dataMode"] == "warming_up"
    assert signal["quoteStatus"] == "最近有效行情／收盤"
    assert signal["quoteTimestamp"] == "2026-07-24T13:30:00+08:00"
    assert signal["entryMin"] > 240
    assert "177.5" not in signal["action"]
    assert "正在累積實際行情樣本" in signal["dataNotice"]


def test_day_trading_signal_becomes_official_after_real_sample_warmup() -> None:
    engine = MockDayTradingEngine()
    start = datetime.fromisoformat("2026-07-27T09:05:00+08:00")
    offsets = [0, 4, 5, 9, 10, 14, 15, 19, 20, 24, 25, 26]
    for index, minutes in enumerate(offsets):
        quote_time = start + timedelta(minutes=minutes)
        price = 250 + index * .5
        engine.update_official_quotes({
            "2317": OfficialStockQuote(
                symbol="2317",
                name="鴻海",
                price=price,
                previous_close=249,
                open=249.5,
                high=price,
                low=249,
                volume=10_000_000 + index * 100_000,
                change=price - 249,
                change_percent=(price - 249) / 249 * 100,
                quote_timestamp=quote_time.isoformat(),
                source="TWSE MIS",
                is_realtime=True,
                best_bid=price - .5,
                best_ask=price,
            ),
        })

    signal = next(item for item in engine.signals(start + timedelta(minutes=27)) if item["symbol"] == "2317")

    assert signal["dataMode"] == "official"
    assert signal["quoteIsRealtime"] is True
    assert signal["confidenceScore"] >= 75
    assert signal["status"] == "confirmed"
    assert signal["action"] == "5 分 K 突破買進"
    assert "展示" not in signal["dataNotice"]


def test_bearish_five_minute_structure_confirms_short_entry_signal() -> None:
    engine = MockDayTradingEngine()
    start = datetime.fromisoformat("2026-07-27T09:05:00+08:00")
    offsets = [0, 4, 5, 9, 10, 14, 15, 19, 20, 24, 25, 26]
    for index, minutes in enumerate(offsets):
        quote_time = start + timedelta(minutes=minutes)
        price = 260 - index * .7
        engine.update_official_quotes({
            "2317": OfficialStockQuote(
                symbol="2317",
                name="鴻海",
                price=price,
                previous_close=261,
                open=260.5,
                high=260.5,
                low=price,
                volume=10_000_000 + index * 100_000,
                change=price - 261,
                change_percent=(price - 261) / 261 * 100,
                quote_timestamp=quote_time.isoformat(),
                source="TWSE MIS",
                is_realtime=True,
                best_bid=price,
                best_ask=price + .5,
            ),
        })

    signal = next(item for item in engine.signals(start + timedelta(minutes=27)) if item["symbol"] == "2317")

    assert signal["direction"] == "short"
    assert signal["status"] == "confirmed"
    assert signal["action"] == "5 分 K 跌破放空"
    assert signal["fiveMinuteSetup"] == "空方轉弱"


def test_day_trading_quote_warmup_survives_a_process_restart() -> None:
    engine = MockDayTradingEngine()
    start = datetime.fromisoformat("2026-07-27T09:05:00+08:00")
    for index in range(12):
        quote_time = start + timedelta(minutes=index)
        price = 250 + index * .5
        engine.update_official_quotes({
            "2317": OfficialStockQuote(
                symbol="2317",
                name="鴻海",
                price=price,
                previous_close=249,
                open=249.5,
                high=price,
                low=249,
                volume=10_000_000 + index * 100_000,
                change=price - 249,
                change_percent=(price - 249) / 249 * 100,
                quote_timestamp=quote_time.isoformat(),
                source="TWSE MIS",
                is_realtime=True,
                best_bid=price - .5,
                best_ask=price,
            ),
        })

    snapshot = engine.export_official_quote_history(start + timedelta(minutes=12))
    restored = MockDayTradingEngine()
    restored_count = restored.restore_official_quote_history(
        snapshot,
        start + timedelta(minutes=12),
    )

    assert restored_count == 12
    assert restored.sample_count == 12
    assert restored.quote_for("2317") is not None
    assert restored.quote_for("2317").price == 255.5


def test_day_trading_long_signal_blocks_chasing_after_seven_percent_gain() -> None:
    engine = MockDayTradingEngine()
    start = datetime.fromisoformat("2026-07-31T09:05:00+08:00")
    for index in range(13):
        quote_time = start + timedelta(seconds=index * 15)
        price = 106.8 + index * .04
        engine.update_official_quotes({
            "2317": OfficialStockQuote(
                symbol="2317",
                name="鴻海",
                price=price,
                previous_close=100,
                open=106.5,
                high=price,
                low=106.5,
                volume=10_000_000 + index * 100_000,
                change=price - 100,
                change_percent=price - 100,
                quote_timestamp=quote_time.isoformat(),
                source="TWSE MIS",
                is_realtime=True,
                best_bid=price - .1,
                best_ask=price,
            ),
        })

    signal = next(item for item in engine.signals(start + timedelta(minutes=3)) if item["symbol"] == "2317")

    assert signal["direction"] == "long"
    assert signal["changePercent"] >= 7
    assert signal["dailyChaseBlocked"] is True
    assert signal["chaseBlocked"] is True
    assert signal["status"] == "temporary"
    assert signal["action"] == "禁止追價（今日漲幅達 7%）"
    assert "今日漲幅已達 7%，禁止追價" in signal["warnings"]


def test_day_trading_signal_countdown_does_not_reset_on_every_refresh() -> None:
    engine = MockDayTradingEngine()
    started_at = datetime.fromisoformat("2026-07-27T11:40:00+08:00")

    first = engine.signals(started_at)[0]
    refreshed = engine.signals(started_at.replace(second=10))[0]

    assert refreshed["id"] == first["id"]
    assert refreshed["generatedAt"] == first["generatedAt"]
    assert refreshed["expiresAt"] == first["expiresAt"]
    assert refreshed["serverNow"] == "2026-07-27T11:40:10+08:00"
