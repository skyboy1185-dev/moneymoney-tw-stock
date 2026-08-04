from datetime import date

from app.services.day_trading_restrictions import (
    DayTradingRestrictionService,
    active_disposition_symbols,
    parse_disposition_period,
)


def test_twse_roc_period_is_active_on_inclusive_last_day() -> None:
    rows = [
        {"Code": "2492", "DispositionPeriod": "115/07/21～115/08/03"},
        {"Code": "2330", "DispositionPeriod": "115/07/01～115/07/14"},
        {"Code": "24920", "DispositionPeriod": "115/07/21～115/08/03"},
    ]

    assert active_disposition_symbols(rows, date(2026, 8, 3)) == {"2492"}


def test_tpex_compact_period_and_four_digit_stocks_are_supported() -> None:
    rows = [
        {"SecuritiesCompanyCode": "3624", "DispositionPeriod": "1150803~1150814"},
        {"SecuritiesCompanyCode": "61828", "DispositionPeriod": "1150803~1150814"},
    ]

    assert parse_disposition_period("1150803~1150814") == (
        date(2026, 8, 3),
        date(2026, 8, 14),
    )
    assert active_disposition_symbols(rows, date(2026, 8, 3)) == {"3624"}


def test_candidate_filter_removes_disposal_stock_only() -> None:
    service = DayTradingRestrictionService()
    service._source_symbols["twse"] = {"2492"}
    candidates = [
        {"symbol": "2492", "stockName": "華新科"},
        {"symbol": "2330", "stockName": "台積電"},
    ]

    assert service.filter_candidates(candidates) == [candidates[1]]
