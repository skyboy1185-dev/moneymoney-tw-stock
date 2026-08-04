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
from app.services.official_market_data import OfficialStockQuote
from app.services.day_trading_schedule import (
    MIN_DAY_TRADING_TURNOVER,
    MIN_DAY_TRADING_VOLUME_SHARES,
    StableRecommendationSelector,
    TradingScheduleConfig,
    intraday_liquidity_minimums,
    recommendation_qualification,
    trading_session_state,
)
from app.services.theme_stock_universe import THEME_STOCKS_BY_SYMBOL, themes_for_symbol


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


def test_short_mis_staleness_is_delay_before_source_interruption() -> None:
    now = datetime(2026, 8, 3, 10, 20, tzinfo=TAIPEI)

    class FixedClockEngine(MockDayTradingEngine):
        def _now(self) -> datetime:
            return now

    def index_quote(seconds_old: int) -> OfficialStockQuote:
        quote_time = now - timedelta(seconds=seconds_old)
        return OfficialStockQuote(
            symbol="t00", name="加權指數", price=43_700, previous_close=43_000,
            open=43_100, high=43_800, low=42_900, volume=1_000_000,
            change=700, change_percent=1.63,
            quote_timestamp=quote_time.isoformat(), source="TWSE MIS",
            is_realtime=seconds_old <= 120,
        )

    engine = FixedClockEngine()
    engine.update_official_quotes({"t00": index_quote(150)})
    assert engine.market_regime()["dataStatus"] == "severe_delay"

    engine.update_official_quotes({"t00": index_quote(301)})
    assert engine.market_regime()["dataStatus"] == "source_error"


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
    assert {item["symbol"] for item in second_batch} == set(THEME_STOCKS_BY_SYMBOL)
    assert all(item["themes"] for item in second_batch)
    assert "2603" not in {item["symbol"] for item in second_batch}
    assert {"PCB", "ABF載板"} <= set(themes_for_symbol("3037"))
    assert themes_for_symbol("2327") == ("AI", "被動元件")
    assert themes_for_symbol("2408") == ("AI", "記憶體")
    assert themes_for_symbol("1815") == ("AI", "玻纖布")
    assert themes_for_symbol("3661") == ("AI", "IC設計")
    engine.trigger("data_delay")
    assert engine.market_regime()["dataStatus"] == "severe_delay"
    engine.trigger("long_signal")
    assert engine.signals()[0]["confidenceScore"] == 92


TAIPEI = ZoneInfo("Asia/Taipei")


def _candidate(signal_id: str, confidence: int = 80, **overrides: object) -> dict[str, object]:
    now = datetime(2026, 7, 21, 9, 20, tzinfo=TAIPEI)
    value: dict[str, object] = {
        "id": signal_id, "symbol": "2330", "direction": "long", "status": "confirmed",
        "dataMode": "official", "quoteIsRealtime": True,
        "confidenceScore": confidence, "healthScore": 80, "riskRewardRatio": 2.0,
        "volume": 1_000_000, "turnover": 100_000_000, "spreadPercentage": 0.1,
        "tradingEligible": True, "shortAvailabilityKnown": True, "shortEligible": True,
        "nearLimitDown": False, "excessiveNegativeDeviation": False, "chaseBlocked": False,
        "stopDistancePercent": 1.0, "marketAlignment": 80, "confirmationScore": 80,
        "volumeScore": 80, "activeForce": 80, "largeOrderForce": 70,
        "largeOrderDataAvailable": True, "largeOrderContinuousBuy": True,
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
    before_signal_start = trading_session_state(
        config, datetime(2026, 7, 21, 9, 14, tzinfo=TAIPEI),
        quote_samples=3, infrastructure_ok=True,
    )
    assert before_signal_start["phase"] == "warmup"
    assert not before_signal_start["formalSignalsAllowed"]
    after_warmup = trading_session_state(
        config, datetime(2026, 7, 21, 9, 15, tzinfo=TAIPEI),
        quote_samples=3, infrastructure_ok=True,
    )
    assert after_warmup["phase"] == "scanning"
    assert after_warmup["robotStatus"] == "5 分 K 強勢股掃描中"
    assert after_warmup["formalSignalsAllowed"]
    assert after_warmup["schedule"]["signalStartTime"] == "09:15"


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
    before_cutoff = trading_session_state(
        config, datetime(2026, 7, 21, 10, 29, tzinfo=TAIPEI),
        quote_samples=10, infrastructure_ok=True,
    )
    cutoff = trading_session_state(
        config, datetime(2026, 7, 21, 10, 30, tzinfo=TAIPEI),
        quote_samples=10, infrastructure_ok=True,
    )
    assert saturday["phase"] == "non_trading"
    assert before_cutoff["phase"] == "scanning"
    assert before_cutoff["formalSignalsAllowed"]
    assert cutoff["phase"] == "entry_closed"
    assert not saturday["formalSignalsAllowed"]
    assert not cutoff["formalSignalsAllowed"]


def _five_minute_quotes(prices: list[float], *, opening: float) -> list[OfficialStockQuote]:
    start = datetime(2026, 8, 3, 9, 0, tzinfo=TAIPEI)
    offsets = [0, 4, 5, 9, 10, 14, 15, 19, 20, 24, 25, 26]
    return [
        OfficialStockQuote(
            symbol="2330",
            name="台積電",
            price=price,
            previous_close=opening,
            open=opening,
            high=max(prices[: index + 1]),
            low=min(prices[: index + 1]),
            volume=100_000 + index * 50_000,
            change=price - opening,
            change_percent=(price - opening) / opening * 100,
            quote_timestamp=(start + timedelta(minutes=offsets[index])).isoformat(),
            source="TWSE MIS",
            is_realtime=True,
            best_bid=price - 0.5,
            best_ask=price + 0.5,
        )
        for index, price in enumerate(prices)
    ]


def test_completed_five_minute_bars_confirm_higher_lows_and_long_setup() -> None:
    quotes = _five_minute_quotes(
        [100, 100.4, 100.7, 101, 101.2, 101.6, 102, 102.4, 103, 103.6, 104.2, 105],
        opening=100,
    )

    metrics = MockDayTradingEngine._live_metrics(quotes)

    assert metrics["fiveMinuteBarCount"] == 5
    assert metrics["fiveMinuteHigherLows"]
    assert metrics["fiveMinuteMaRising"]
    assert metrics["fiveMinuteBreakout"]
    assert metrics["fiveMinuteLongSetup"]
    assert metrics["qualified"]


def test_completed_five_minute_bars_detect_bearish_long_exit_only() -> None:
    quotes = _five_minute_quotes(
        [110, 109.8, 109.4, 109.1, 108.7, 108.2, 107.8, 107.2, 106.8, 106.1, 105.4, 104.7],
        opening=110,
    )
    engine = MockDayTradingEngine()
    for quote in quotes:
        engine.update_official_quotes({quote.symbol: quote})

    metrics = engine._live_metrics(quotes)

    assert metrics["fiveMinuteLowerHighs"]
    assert metrics["fiveMinuteMaFalling"]
    assert metrics["fiveMinuteBearishExit"]
    assert engine.position_risk_for("2330") == {
        "level": "important",
        "action": "5 分 K 轉弱，全部賣出",
        "reason": "跌破開盤價、5 分 K 短均線向下，且形成頭頭低或跌破近期低點",
    }


def test_after_close_is_summary_even_when_service_has_just_restarted() -> None:
    config = TradingScheduleConfig()
    state = trading_session_state(
        config, datetime(2026, 7, 21, 13, 35, tzinfo=TAIPEI),
        quote_samples=1, infrastructure_ok=True,
    )
    assert state["phase"] == "summary"
    assert state["robotStatus"] == "今日掃描完成"
    assert not state["formalSignalsAllowed"]


def test_recommendation_hard_filters_and_short_qualification() -> None:
    now = datetime(2026, 7, 21, 9, 20, tzinfo=TAIPEI)
    config = TradingScheduleConfig()
    session = trading_session_state(config, now, quote_samples=10, infrastructure_ok=True)
    assert recommendation_qualification(_candidate("ok"), config, session, now)[0]
    passed, failures = recommendation_qualification(
        _candidate("short", direction="short", shortAvailabilityKnown=False),
        config, session, now,
    )
    assert not passed
    assert "放空資格待確認" in failures
    demo_passed, demo_failures = recommendation_qualification(
        _candidate("demo", dataMode="official_quote_demo_strategy"),
        config, session, now,
    )
    assert not demo_passed
    assert "策略或歷史行情仍為展示資料，禁止正式訊號" in demo_failures
    unrelated_passed, unrelated_failures = recommendation_qualification(
        _candidate("unrelated", symbol="2603"),
        config,
        session,
        now,
    )
    assert not unrelated_passed
    assert "不屬於 AI 或低軌衛星主題股票池" in unrelated_failures
    no_large_order_passed, no_large_order_failures = recommendation_qualification(
        _candidate("no-large-order", largeOrderContinuousBuy=False),
        config,
        session,
        now,
    )
    assert not no_large_order_passed
    assert "大單尚未持續敲進" in no_large_order_failures


def test_disposal_stock_is_never_qualified_for_day_trading() -> None:
    now = datetime(2026, 7, 21, 9, 20, tzinfo=TAIPEI)
    config = TradingScheduleConfig()
    session = trading_session_state(config, now, quote_samples=10, infrastructure_ok=True)

    passed, failures = recommendation_qualification(
        _candidate("disposed", isDisposed=True),
        config,
        session,
        now,
    )

    assert not passed
    assert "處置股或交易受限股票禁止列入當沖" in failures


def test_intraday_liquidity_filter_rejects_thin_volume() -> None:
    now = datetime(2026, 7, 21, 9, 20, tzinfo=TAIPEI)
    config = TradingScheduleConfig()
    session = trading_session_state(config, now, quote_samples=10, infrastructure_ok=True)

    passed, failures = recommendation_qualification(
        _candidate("thin", volume=90_000, turnover=9_000_000),
        config,
        session,
        now,
    )

    assert not passed
    assert any("1,000" in failure for failure in failures)
    assert any("億元" in failure for failure in failures)


def test_intraday_liquidity_target_scales_with_market_progress() -> None:
    config = TradingScheduleConfig()
    early_volume, early_turnover = intraday_liquidity_minimums(
        config,
        datetime(2026, 7, 21, 9, 5, tzinfo=TAIPEI),
    )
    noon_volume, noon_turnover = intraday_liquidity_minimums(
        config,
        datetime(2026, 7, 21, 12, 0, tzinfo=TAIPEI),
    )

    assert early_volume == MIN_DAY_TRADING_VOLUME_SHARES * 0.10
    assert early_turnover == MIN_DAY_TRADING_TURNOVER * 0.10
    assert round(noon_volume) == 666_667
    assert round(noon_turnover) == 66_666_667


def test_long_entry_at_seven_percent_gain_is_never_recommended() -> None:
    now = datetime(2026, 7, 21, 9, 20, tzinfo=TAIPEI)
    config = TradingScheduleConfig()
    session = trading_session_state(config, now, quote_samples=10, infrastructure_ok=True)

    below_limit, _ = recommendation_qualification(
        _candidate("below-limit", changePercent=6.99),
        config,
        session,
        now,
    )
    at_limit, failures = recommendation_qualification(
        _candidate("at-limit", changePercent=7.0),
        config,
        session,
        now,
    )

    assert below_limit
    assert not at_limit
    assert "今日漲幅已達 7%，禁止追價" in failures


def test_theme_universe_is_ai_or_low_earth_orbit_satellite_only() -> None:
    assert themes_for_symbol("2330") == ("AI",)
    assert themes_for_symbol("3491") == ("低軌衛星",)
    assert themes_for_symbol("6285") == ("AI", "低軌衛星")
    assert themes_for_symbol("2603") == ()


def test_selector_recommends_at_most_five_without_lowering_thresholds() -> None:
    now = datetime(2026, 7, 21, 9, 20, tzinfo=TAIPEI)
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
    now = datetime(2026, 7, 21, 9, 20, tzinfo=TAIPEI)
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
    now = datetime(2026, 7, 21, 9, 20, tzinfo=TAIPEI)
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
