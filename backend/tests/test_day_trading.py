from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.services.day_trading import (
    MockDayTradingEngine,
    entry_allowed,
    evaluate_position,
    is_signal_expired,
    long_signal_score,
    prioritize_events,
    short_signal_score,
)
from app.services.day_trading_schedule import (
    StableRecommendationSelector,
    TradingScheduleConfig,
    recommendation_qualification,
    trading_session_state,
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
    first_batch = engine.signals()
    second_batch = engine.signals()
    first = first_batch[0]["price"]
    second = second_batch[0]["price"]
    assert first != second
    assert len(second_batch) >= 6
    engine.trigger("data_delay")
    assert engine.market_regime()["dataStatus"] == "severe_delay"
    engine.trigger("long_signal")
    assert engine.signals()[0]["confidenceScore"] == 92


TAIPEI = ZoneInfo("Asia/Taipei")


def _candidate(signal_id: str, confidence: int = 80, **overrides: object) -> dict[str, object]:
    now = datetime(2026, 7, 21, 9, 10, tzinfo=TAIPEI)
    value: dict[str, object] = {
        "id": signal_id, "direction": "long", "status": "confirmed",
        "confidenceScore": confidence, "healthScore": 80, "riskRewardRatio": 2.0,
        "volume": 1_000_000, "turnover": 100_000_000, "spreadPercentage": 0.1,
        "tradingEligible": True, "shortAvailabilityKnown": True, "shortEligible": True,
        "nearLimitDown": False, "excessiveNegativeDeviation": False, "chaseBlocked": False,
        "stopDistancePercent": 1.0, "marketAlignment": 80, "confirmationScore": 80,
        "volumeScore": 80, "activeForce": 80, "largeOrderForce": 70,
        "industryScore": 80, "liquidityScore": 80, "price": 100,
        "entryMin": 99, "entryMax": 101, "generatedAt": now.isoformat(),
        "expiresAt": (now + timedelta(minutes=20)).isoformat(),
    }
    value.update(overrides)
    return value


def test_opening_schedule_and_warmup_use_taipei_time() -> None:
    config = TradingScheduleConfig(warmup_minutes=3, minimum_live_samples=3)
    at_open = trading_session_state(
        config, datetime(2026, 7, 21, 9, 0, tzinfo=TAIPEI),
        quote_samples=3, infrastructure_ok=True,
    )
    assert at_open["phase"] == "warmup"
    assert not at_open["formalSignalsAllowed"]
    after_warmup = trading_session_state(
        config, datetime(2026, 7, 21, 9, 3, tzinfo=TAIPEI),
        quote_samples=3, infrastructure_ok=True,
    )
    assert after_warmup["phase"] == "scanning"
    assert after_warmup["robotStatus"] == "即時掃描中"
    assert after_warmup["formalSignalsAllowed"]


def test_zero_minute_warmup_still_requires_enough_ticks() -> None:
    config = TradingScheduleConfig(warmup_minutes=0, minimum_live_samples=3)
    state = trading_session_state(
        config, datetime(2026, 7, 21, 9, 1, tzinfo=TAIPEI),
        quote_samples=1, infrastructure_ok=True,
    )
    assert state["phase"] == "warmup"
    assert not state["formalSignalsAllowed"]


def test_non_trading_day_and_entry_cutoff_block_formal_signals() -> None:
    config = TradingScheduleConfig()
    saturday = trading_session_state(
        config, datetime(2026, 7, 25, 10, 0, tzinfo=TAIPEI),
        quote_samples=10, infrastructure_ok=True,
    )
    cutoff = trading_session_state(
        config, datetime(2026, 7, 21, 13, 20, tzinfo=TAIPEI),
        quote_samples=10, infrastructure_ok=True,
    )
    assert saturday["phase"] == "non_trading"
    assert cutoff["phase"] == "entry_closed"
    assert not saturday["formalSignalsAllowed"]
    assert not cutoff["formalSignalsAllowed"]


def test_recommendation_hard_filters_and_short_qualification() -> None:
    now = datetime(2026, 7, 21, 9, 10, tzinfo=TAIPEI)
    config = TradingScheduleConfig()
    session = trading_session_state(config, now, quote_samples=10, infrastructure_ok=True)
    assert recommendation_qualification(_candidate("ok"), config, session, now)[0]
    passed, failures = recommendation_qualification(
        _candidate("short", direction="short", shortAvailabilityKnown=False),
        config, session, now,
    )
    assert not passed
    assert "放空資格待確認" in failures


def test_selector_recommends_at_most_five_without_lowering_thresholds() -> None:
    now = datetime(2026, 7, 21, 9, 10, tzinfo=TAIPEI)
    config = TradingScheduleConfig()
    session = trading_session_state(config, now, quote_samples=10, infrastructure_ok=True)
    selector = StableRecommendationSelector()
    official, candidates = selector.select(
        "test-user",
        [
            _candidate("a", 92), _candidate("b", 88), _candidate("c", 84),
            _candidate("d", 82), _candidate("e", 80), _candidate("f", 78),
            _candidate("g", 74),
        ],
        config,
        session,
        now=now,
    )
    assert [item["id"] for item in official] == ["a", "b", "c", "d", "e"]
    assert len(official) == 5
    assert next(item for item in candidates if item["id"] == "f")["isOfficialRecommendation"] is False
    assert next(item for item in candidates if item["id"] == "g")["isOfficialRecommendation"] is False


def test_selector_allows_at_most_five_distinct_admissions_per_taipei_hour() -> None:
    now = datetime(2026, 7, 21, 9, 10, tzinfo=TAIPEI)
    config = TradingScheduleConfig(recommendation_refresh_seconds=5, minimum_retention_minutes=0)
    session = trading_session_state(config, now, quote_samples=10, infrastructure_ok=True)
    selector = StableRecommendationSelector()
    timing = {
        "generatedAt": now.isoformat(),
        "expiresAt": (now + timedelta(hours=2)).isoformat(),
    }
    first_candidates = [_candidate(signal_id, 90 - index, **timing) for index, signal_id in enumerate("abcde")]
    first, _ = selector.select("hourly-user", first_candidates, config, session, now=now)
    assert len(first) == 5

    same_hour = now + timedelta(minutes=10)
    same_session = trading_session_state(config, same_hour, quote_samples=10, infrastructure_ok=True)
    sixth = _candidate("f", 99, **timing)
    within_hour, _ = selector.select(
        "hourly-user",
        [*first_candidates[:4], sixth],
        config,
        same_session,
        now=same_hour,
    )
    assert "f" not in {item["id"] for item in within_hour}

    next_hour = now + timedelta(hours=1)
    next_session = trading_session_state(config, next_hour, quote_samples=10, infrastructure_ok=True)
    after_reset, _ = selector.select(
        "hourly-user",
        [*first_candidates[:4], sixth],
        config,
        next_session,
        now=next_hour,
    )
    assert "f" in {item["id"] for item in after_reset}
    assert len(after_reset) == 5


def test_full_hourly_quota_prevents_churn_until_next_hour() -> None:
    now = datetime(2026, 7, 21, 9, 10, tzinfo=TAIPEI)
    config = TradingScheduleConfig(recommendation_refresh_seconds=5, maximum_recommendations=3)
    session = trading_session_state(config, now, quote_samples=10, infrastructure_ok=True)
    selector = StableRecommendationSelector()
    first, _ = selector.select(
        "stable-user",
        [_candidate("a", 90), _candidate("b", 85), _candidate("c", 80)],
        config, session, now=now,
    )
    too_soon, _ = selector.select(
        "stable-user",
        [_candidate("a", 90), _candidate("b", 85), _candidate("c", 80), _candidate("d", 90)],
        config, session, now=now + timedelta(minutes=1),
    )
    still_capped, _ = selector.select(
        "stable-user",
        [_candidate("a", 90), _candidate("b", 85), _candidate("c", 80), _candidate("d", 90)],
        config, session, now=now + timedelta(minutes=4),
    )
    next_hour = now + timedelta(hours=1)
    next_session = trading_session_state(config, next_hour, quote_samples=10, infrastructure_ok=True)
    reset, _ = selector.select(
        "stable-user",
        [
            _candidate("a", 90, expiresAt=(next_hour + timedelta(minutes=20)).isoformat()),
            _candidate("b", 85, expiresAt=(next_hour + timedelta(minutes=20)).isoformat()),
            _candidate("c", 80, expiresAt=(next_hour + timedelta(minutes=20)).isoformat()),
            _candidate("d", 90, expiresAt=(next_hour + timedelta(minutes=20)).isoformat()),
        ],
        config,
        next_session,
        now=next_hour,
    )
    assert [item["id"] for item in first] == ["a", "b", "c"]
    assert {item["id"] for item in too_soon} == {"a", "b", "c"}
    assert {item["id"] for item in still_capped} == {"a", "b", "c"}
    assert {item["id"] for item in reset} == {"a", "b", "d"}
