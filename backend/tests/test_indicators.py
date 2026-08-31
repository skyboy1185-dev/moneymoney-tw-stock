from app.services.indicators import calculate_indicators, generate_macd_signals
from app.services.mock_market import find_stock, industry_hotspots, stock_payload
from app.config import Settings


def test_macd_signal_only_on_zero_crossing_day() -> None:
    macd = [
        {"dif": 1.0, "signal": 3.0, "histogram": -2.0},
        {"dif": 2.8, "signal": 3.0, "histogram": -0.2},
        {"dif": 3.0, "signal": 3.0, "histogram": 0.0},
        {"dif": 3.7, "signal": 3.0, "histogram": 0.7},
        {"dif": 2.9, "signal": 3.0, "histogram": -0.1},
        {"dif": 2.6, "signal": 3.0, "histogram": -0.4},
    ]
    assert generate_macd_signals(macd) == [None, None, "entry", None, "exit", None]


def test_macd_signal_is_suppressed_below_zero_axis() -> None:
    macd = [
        {"dif": -1.5, "signal": -1.4, "histogram": -0.1},
        {"dif": -1.3, "signal": -1.3, "histogram": 0.0},
        {"dif": -1.4, "signal": -1.3, "histogram": -0.1},
    ]
    assert generate_macd_signals(macd) == [None, None, None]


def test_indicators_never_use_future_rows() -> None:
    payload = stock_payload("2330")
    assert payload is not None
    candles = payload["prices"]
    partial = candles[:-1]
    full_indicators = calculate_indicators(candles)
    partial_indicators = calculate_indicators(partial)
    assert partial_indicators == full_indicators[:-1]


def test_mock_stock_and_industry_payloads_are_complete() -> None:
    payload = stock_payload("2330")
    assert payload is not None
    assert payload["meta"]["name"] == "台積電"
    assert len(payload["prices"]) == len(payload["indicators"]) == 5280
    assert payload["dataMode"] == "demo"
    hotspots = industry_hotspots()
    assert hotspots
    assert {"industry", "changePercent", "momentum", "leaders"} <= hotspots[0].keys()


def test_mock_market_includes_6173_fallback_stock() -> None:
    stock = find_stock("6173")
    assert stock is not None
    assert stock["name"] == "信昌電"
    payload = stock_payload("6173")
    assert payload is not None
    assert payload["meta"]["symbol"] == "6173"
    assert payload["meta"]["name"] == "信昌電"
    assert len(payload["prices"]) == len(payload["indicators"]) == 5280


def test_railway_postgres_url_uses_psycopg3_driver() -> None:
    settings = Settings(database_url="postgresql://user:password@postgres:5432/moneymoney")
    assert settings.database_url == "postgresql+psycopg://user:password@postgres:5432/moneymoney"
