from datetime import datetime, timedelta

from app.services.day_trading import MockDayTradingEngine
from app.services.official_market_data import (
    OfficialStockQuote,
    StockQuoteRequest,
    parse_mis_quote,
)


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
    for index in range(13):
        quote_time = start + timedelta(seconds=index * 15)
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

    signal = next(item for item in engine.signals(start + timedelta(minutes=3)) if item["symbol"] == "2317")

    assert signal["dataMode"] == "official"
    assert signal["quoteIsRealtime"] is True
    assert signal["confidenceScore"] >= 75
    assert signal["status"] == "confirmed"
    assert signal["action"] == "突破買進"
    assert "展示" not in signal["dataNotice"]


def test_day_trading_signal_countdown_does_not_reset_on_every_refresh() -> None:
    engine = MockDayTradingEngine()
    started_at = datetime.fromisoformat("2026-07-27T11:40:00+08:00")

    first = engine.signals(started_at)[0]
    refreshed = engine.signals(started_at.replace(second=10))[0]

    assert refreshed["id"] == first["id"]
    assert refreshed["generatedAt"] == first["generatedAt"]
    assert refreshed["expiresAt"] == first["expiresAt"]
    assert refreshed["serverNow"] == "2026-07-27T11:40:10+08:00"
