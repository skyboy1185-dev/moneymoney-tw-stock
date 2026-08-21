from app.services.three_gate_price import (
    calculate_three_gate_price,
    evaluate_opening_three_gate_retest,
    evaluate_three_gate_direction,
    parse_tpex_daily_rows,
    parse_twse_daily_rows,
)


def test_calculate_three_gate_price_uses_prior_high_low_and_taiwan_ticks() -> None:
    gate = calculate_three_gate_price("2026-08-20", high=100, low=90)

    assert gate is not None
    assert gate.source_date == "2026-08-20"
    assert gate.upper == 104
    assert gate.middle == 95
    assert gate.lower == 86.2


def test_middle_gate_controls_long_and_short_direction() -> None:
    gate = calculate_three_gate_price("2026-08-20", high=110, low=90)
    assert gate is not None

    long_decision = evaluate_three_gate_direction(101, 99, gate)
    short_decision = evaluate_three_gate_direction(99, 101, gate)

    assert long_decision.direction == "long"
    assert long_decision.level == "middle"
    assert long_decision.crossed is True
    assert short_decision.direction == "short"
    assert short_decision.level == "middle"
    assert short_decision.crossed is True


def test_outer_gate_status_is_reported() -> None:
    gate = calculate_three_gate_price("2026-08-20", high=110, low=90)
    assert gate is not None

    assert evaluate_three_gate_direction(120, None, gate).level == "upper"
    assert evaluate_three_gate_direction(80, None, gate).level == "lower"


def test_opening_break_above_middle_waits_for_pullback_and_reclaim() -> None:
    gate = calculate_three_gate_price("2026-08-20", high=110, low=90)
    assert gate is not None

    waiting = evaluate_opening_three_gate_retest(
        open_price=102,
        previous_close=98,
        current_price=103,
        previous_intraday_price=102.5,
        session_high=103,
        session_low=102,
        completed_bar_open=102,
        completed_bar_close=102.5,
        minimum_completed_close=102.5,
        maximum_completed_close=102.5,
        previously_invalidated=False,
        three_gate=gate,
    )
    ready = evaluate_opening_three_gate_retest(
        open_price=102,
        previous_close=98,
        current_price=100.2,
        previous_intraday_price=99.9,
        session_high=103,
        session_low=99.8,
        completed_bar_open=99.8,
        completed_bar_close=100.1,
        minimum_completed_close=100.1,
        maximum_completed_close=102.5,
        previously_invalidated=False,
        three_gate=gate,
    )

    assert waiting.pattern == "open-above-middle"
    assert waiting.ready is False
    assert ready.ready is True
    assert ready.level == "middle"


def test_opening_break_below_lower_waits_for_retest_and_second_break() -> None:
    gate = calculate_three_gate_price("2026-08-20", high=110, low=90)
    assert gate is not None

    waiting = evaluate_opening_three_gate_retest(
        open_price=81,
        previous_close=85,
        current_price=80,
        previous_intraday_price=80.5,
        session_high=81,
        session_low=79.5,
        completed_bar_open=80.5,
        completed_bar_close=80,
        minimum_completed_close=80,
        maximum_completed_close=80,
        previously_invalidated=False,
        three_gate=gate,
    )
    ready = evaluate_opening_three_gate_retest(
        open_price=81,
        previous_close=85,
        current_price=82.2,
        previous_intraday_price=82.5,
        session_high=82.35,
        session_low=80,
        completed_bar_open=82.3,
        completed_bar_close=82.2,
        minimum_completed_close=80,
        maximum_completed_close=82.2,
        previously_invalidated=False,
        three_gate=gate,
    )

    assert waiting.pattern == "open-below-lower"
    assert waiting.ready is False
    assert ready.ready is True
    assert ready.level == "lower"


def test_completed_bar_failure_cancels_opening_plan_for_the_day() -> None:
    gate = calculate_three_gate_price("2026-08-20", high=110, low=90)
    assert gate is not None

    failed_long = evaluate_opening_three_gate_retest(
        open_price=102,
        previous_close=98,
        current_price=98.5,
        previous_intraday_price=98.8,
        session_high=103,
        session_low=98.8,
        completed_bar_open=100,
        completed_bar_close=98.5,
        minimum_completed_close=98.5,
        maximum_completed_close=102,
        previously_invalidated=False,
        three_gate=gate,
    )
    still_cancelled = evaluate_opening_three_gate_retest(
        open_price=102,
        previous_close=98,
        current_price=100.1,
        previous_intraday_price=100,
        session_high=103,
        session_low=98.8,
        completed_bar_open=99.9,
        completed_bar_close=100.1,
        minimum_completed_close=100.1,
        maximum_completed_close=102,
        previously_invalidated=True,
        three_gate=gate,
    )
    failed_short = evaluate_opening_three_gate_retest(
        open_price=81,
        previous_close=85,
        current_price=83,
        previous_intraday_price=82.8,
        session_high=83.2,
        session_low=80,
        completed_bar_open=82.5,
        completed_bar_close=83,
        minimum_completed_close=80,
        maximum_completed_close=83,
        previously_invalidated=False,
        three_gate=gate,
    )

    assert failed_long.invalidated is True
    assert failed_long.ready is False
    assert still_cancelled.invalidated is True
    assert still_cancelled.ready is False
    assert failed_short.invalidated is True
    assert failed_short.ready is False


def test_official_daily_rows_are_parsed_for_twse_and_tpex() -> None:
    twse = parse_twse_daily_rows([{
        "Date": "1150820",
        "Code": "2330",
        "HighestPrice": "2,375",
        "LowestPrice": "2,350",
    }])
    tpex = parse_tpex_daily_rows([{
        "Date": "1150820",
        "SecuritiesCompanyCode": "8299",
        "High": "2,080",
        "Low": "1,965",
    }])

    assert twse["2330"].source_date == "2026-08-20"
    assert twse["2330"].middle == 2365
    assert tpex["8299"].source_date == "2026-08-20"
    assert tpex["8299"].middle == 2025
