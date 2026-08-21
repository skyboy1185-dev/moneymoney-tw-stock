from datetime import date

from app.services.day_trading_restrictions import (
    DayTradingRestrictionService,
    active_disposition_symbols,
    parse_disposition_period,
    short_day_trade_symbols,
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


def test_sell_first_day_trade_list_excludes_suspended_and_non_stock_rows() -> None:
    rows = [
        {"Code": "2330", "Suspension": ""},
        {"Code": "2317", "Suspension": "Y"},
        {"Code": "00631L", "Suspension": ""},
        {"證券代號": "8358", "暫停現股賣出後現款買進當沖註記": ""},
    ]

    assert short_day_trade_symbols(rows) == {"2330", "8358"}


def test_candidate_filter_removes_disposal_stock_only() -> None:
    service = DayTradingRestrictionService()
    service._source_symbols["twse"] = {"2492"}
    candidates = [
        {"symbol": "2492", "stockName": "華新科"},
        {"symbol": "2330", "stockName": "台積電"},
    ]

    assert service.filter_candidates(candidates) == [candidates[1]]


def test_market_is_fail_closed_when_its_disposal_source_is_unavailable() -> None:
    service = DayTradingRestrictionService()
    service._source_status = {"twse": "healthy", "tpex": "error"}
    candidates = [
        {"symbol": "2330", "stockName": "台積電", "market": "上市"},
        {"symbol": "8358", "stockName": "金居", "market": "上櫃"},
    ]

    assert service.filter_candidates(candidates) == [candidates[0]]
    assert service.state["blockedMarkets"] == ["上櫃"]


def test_short_eligibility_uses_exchange_sell_first_list() -> None:
    service = DayTradingRestrictionService()
    service._short_status = {"twse": "healthy", "tpex": "healthy"}
    service._short_symbols = {"twse": {"2330"}, "tpex": {"8358"}}

    candidates = service.enrich_short_eligibility([
        {"symbol": "2330", "market": "上市"},
        {"symbol": "2317", "market": "上市"},
        {"symbol": "8358", "market": "上櫃"},
    ])

    assert candidates[0]["shortAvailabilityKnown"] is True
    assert candidates[0]["shortEligible"] is True
    assert candidates[1]["shortEligible"] is False
    assert candidates[2]["shortEligible"] is True
