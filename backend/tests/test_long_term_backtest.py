from datetime import date, datetime, timedelta, UTC

from app.services.long_term_backtest import (
    BacktestSecurity,
    HistoricalBar,
    _equal_weight_benchmark_result,
    _simulate_stable_rotation,
    _simulate_strategy,
    parse_yahoo_daily_history,
)


def test_yahoo_history_adjusts_ohlc_with_adjusted_close() -> None:
    timestamp = int(datetime(2026, 1, 2, tzinfo=UTC).timestamp())
    rows = parse_yahoo_daily_history({
        "chart": {"result": [{
            "timestamp": [timestamp],
            "indicators": {
                "quote": [{
                    "open": [100], "high": [110], "low": [90],
                    "close": [100], "volume": [1_000_000],
                }],
                "adjclose": [{"adjclose": [50]}],
            },
        }]},
    })

    assert len(rows) == 1
    assert rows[0].open == 50
    assert rows[0].high == 55
    assert rows[0].low == 45
    assert rows[0].close == 50


def _security(symbol: str, start: date, sessions: int, daily_gain: float = 0) -> BacktestSecurity:
    rows: list[HistoricalBar] = []
    current = start
    price = 100.0
    while len(rows) < sessions:
        if current.weekday() < 5:
            open_price = price
            price *= 1 + daily_gain
            rows.append(HistoricalBar(current, open_price, max(open_price, price), min(open_price, price), price, 2_000_000))
        current += timedelta(days=1)
    return BacktestSecurity(symbol, symbol, "上市", tuple(rows))


def test_strategy_uses_prior_close_ranking_and_respects_five_day_lock() -> None:
    benchmark = _security("0050", date(2025, 12, 1), 190)
    first = _security("1001", date(2025, 12, 1), 190, daily_gain=0.001)
    second = _security("1002", date(2025, 12, 1), 190, daily_gain=0.002)
    session_dates = [bar.trade_date for bar in benchmark.bars]
    ytd = [item for item in session_dates if item >= date(2026, 1, 1)]
    previous = session_dates[session_dates.index(ytd[0]) - 1]
    rankings = {previous: [("1001", 90.0)]}
    for index, session in enumerate(ytd[:-1]):
        rankings[session] = [("1002", 95.0)] if index == 0 else [("1002", 95.0)]

    result = _simulate_strategy(
        [first, second], benchmark, date(2026, 1, 1), 1, rankings, date(2026, 8, 12),
    )

    assert result["entryCount"] == 2
    assert result["replacementCount"] == 1
    assert result["currentHoldings"] == ["1002"]


def test_stable_rotation_reviews_weekly_and_replaces_at_most_two() -> None:
    benchmark = _security("0050", date(2025, 12, 1), 190)
    securities = [
        _security(f"{1000 + index}", date(2025, 12, 1), 190, daily_gain=index / 100_000)
        for index in range(53)
    ]
    sessions = [bar.trade_date for bar in benchmark.bars]
    ytd = [item for item in sessions if date(2026, 1, 1) <= item <= date(2026, 2, 2)]
    previous = sessions[sessions.index(ytd[0]) - 1]
    initial = [(security.symbol, 80 - index / 10) for index, security in enumerate(securities[:50])]
    rankings = {previous: initial}
    replacement_ranking = [
        (securities[50].symbol, 100),
        (securities[51].symbol, 99),
        (securities[52].symbol, 98),
        *[(security.symbol, 80 - index / 10) for index, security in enumerate(securities[:50])],
    ]
    for session in ytd[:-1]:
        rankings[session] = replacement_ranking

    result = _simulate_stable_rotation(
        securities,
        benchmark,
        date(2026, 1, 1),
        rankings,
        minimum_holding_days=20,
        end_date=date(2026, 2, 2),
    )

    assert result["entryCount"] == 52
    assert result["replacementCount"] == 2
    assert len(result["currentHoldings"]) == 50
    assert securities[50].symbol in result["currentHoldings"]
    assert securities[51].symbol in result["currentHoldings"]
    assert securities[52].symbol not in result["currentHoldings"]


def test_equal_weight_top_fifty_benchmark_reports_all_components() -> None:
    securities = [
        _security(f"{2000 + index}", date(2025, 12, 1), 190, daily_gain=0.001)
        for index in range(50)
    ]
    result = _equal_weight_benchmark_result(
        securities,
        date(2026, 1, 1),
        date(2026, 8, 13),
    )

    assert result["constituentCount"] == 50
    assert result["entryCount"] == 50
    assert result["replacementCount"] == 0
    assert len(result["currentHoldings"]) == 50
