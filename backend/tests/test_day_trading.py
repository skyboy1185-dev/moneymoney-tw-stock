from datetime import UTC, datetime, timedelta

from app.services.day_trading import (
    MockDayTradingEngine,
    entry_allowed,
    evaluate_position,
    is_signal_expired,
    long_signal_score,
    prioritize_events,
    short_signal_score,
)


def test_long_and_short_scores_use_multiple_conditions() -> None:
    assert long_signal_score({
        "vwap_up": True, "above_vwap": True, "breakout": True, "volume": True,
        "active_buy": True, "large_buy": True, "short_trend": True,
        "market_fit": True, "industry_fit": True,
    }) == 100
    assert short_signal_score({
        "vwap_down": True, "below_vwap": True, "breakdown": True, "volume": True,
        "active_sell": True, "large_sell": True, "short_trend": True,
        "market_fit": False, "industry_fit": False,
    }) == 90


def test_signal_expiry() -> None:
    now = datetime.now(UTC)
    assert is_signal_expired(now - timedelta(seconds=1), now)
    assert not is_signal_expired(now + timedelta(seconds=1), now)


def test_long_and_short_hard_stops_are_emergency() -> None:
    long_result = evaluate_position("long", 99, 100, 110, 120)
    short_result = evaluate_position("short", 101, 100, 90, 80)
    assert long_result == {"level": "emergency", "action": "立即全部賣出", "reason": "跌破停損價"}
    assert short_result == {"level": "emergency", "action": "立即全部回補", "reason": "突破停損價"}


def test_targets_and_trailing_stop() -> None:
    assert evaluate_position("long", 110, 95, 110, 120)["action"] == "減碼 50%"
    assert evaluate_position("long", 120, 95, 110, 120)["action"] == "全部賣出"
    assert evaluate_position("short", 90, 105, 90, 80)["action"] == "回補 50%"
    assert evaluate_position("short", 80, 105, 90, 80)["action"] == "全部回補"
    assert evaluate_position("long", 103, 95, 110, 120, trailing_stop=104)["reason"] == "觸發移動停利"


def test_data_delay_and_risk_limits_stop_entries_only() -> None:
    assert not entry_allowed(12, False, 0, 3)
    assert not entry_allowed(1, True, 0, 3)
    assert not entry_allowed(1, False, 3, 3)
    assert entry_allowed(1, False, 2, 3)
    assert evaluate_position("long", 105, 100, 110, 120, data_status="severe_delay")["level"] == "emergency"


def test_exit_events_always_precede_entry_events() -> None:
    events = [
        {"type": "new_signal"}, {"type": "market_update"},
        {"type": "emergency_exit"}, {"type": "exit_warning"},
    ]
    assert [item["type"] for item in prioritize_events(events)] == [
        "emergency_exit", "exit_warning", "new_signal", "market_update",
    ]


def test_mock_streaming_data_changes_and_supports_scenarios() -> None:
    engine = MockDayTradingEngine()
    first = engine.signals()[0]["price"]
    second = engine.signals()[0]["price"]
    assert first != second
    engine.trigger("data_delay")
    assert engine.market_regime()["dataStatus"] == "severe_delay"
    engine.trigger("long_signal")
    assert engine.signals()[0]["confidenceScore"] == 92
