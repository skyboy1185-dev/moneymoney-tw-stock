from app.services.three_gate_price import (
    calculate_three_gate_price,
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
