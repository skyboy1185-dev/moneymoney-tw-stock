from app.services.indicators import calculate_indicators, generate_macd_signals
from app.services.mock_market import industry_hotspots, stock_payload


def test_macd_signal_only_on_zero_crossing_day() -> None:
    macd = [
        {"histogram": -2.0},
        {"histogram": -0.2},
        {"histogram": 0.0},
        {"histogram": 0.7},
        {"histogram": -0.1},
        {"histogram": -0.4},
    ]
    assert generate_macd_signals(macd) == [None, None, "entry", None, "exit", None]


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
    assert len(payload["prices"]) == len(payload["indicators"]) == 420
    assert payload["dataMode"] == "demo"
    hotspots = industry_hotspots()
    assert hotspots
    assert {"industry", "changePercent", "momentum", "leaders"} <= hotspots[0].keys()
