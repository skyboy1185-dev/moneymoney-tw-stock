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


def test_day_trading_signal_uses_official_quote_but_keeps_strategy_as_demo() -> None:
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
    assert signal["dataMode"] == "official_quote_demo_strategy"
    assert signal["quoteStatus"] == "最近有效行情／收盤"
    assert signal["quoteTimestamp"] == "2026-07-24T13:30:00+08:00"
    assert signal["entryMin"] > 240
    assert "策略分數" in signal["dataNotice"]
