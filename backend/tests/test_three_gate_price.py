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
        three_gate=gate,
    )
    ready = evaluate_opening_three_gate_retest(
        open_price=102,
        previous_close=98,
        current_price=100.2,
        previous_intraday_price=99.9,
        session_high=103,
        session_low=99.8,
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
        three_gate=gate,
    )
    ready = evaluate_opening_three_gate_retest(
        open_price=81,
        previous_close=85,
        current_price=82.2,
        previous_intraday_price=82.5,
        session_high=82.35,
        session_low=80,
        three_gate=gate,
    )

    assert waiting.pattern == "open-below-lower"
    assert waiting.ready is False
    assert ready.ready is True
    assert ready.level == "lower"


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
