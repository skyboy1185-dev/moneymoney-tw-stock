import asyncio
from datetime import UTC, date, datetime
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    LongTermBenchmark,
    LongTermBenchmarkSelection,
    LongTermPortfolioRun,
    LongTermPosition,
    LongTermPositionSnapshot,
    LongTermTradeEvent,
)
from app.services.long_term_selection import (
    LONG_TERM_START_DATE,
    SIMULATION_CAPITAL,
    actual_return_percent,
    allocation_weights,
    benchmark_definitions,
    minimum_exit_date,
    portfolio_payload,
    rank_long_term_candidates,
    repair_long_term_position_overflow,
    replenish_long_term_vacancies,
    replace_long_term_position,
    run_long_term_selection,
    trading_days_held,
    total_return_breakdown,
)
from app.services.official_market_data import official_market_data_provider
from app.services.long_term_dividends import (
    DividendEvent,
    DividendHistory,
    dividends_per_share,
    long_term_dividend_provider,
    parse_yahoo_dividends,
)
from app.services.long_term_benchmarks import (
    TenYearCagrCandidate,
    parse_yahoo_cagr_candidate,
    save_cagr_selection,
)


def stock(index: int) -> SimpleNamespace:
    strong = index < 8
    price = 100 + index
    return SimpleNamespace(
        stock_code=f"{2000 + index}", stock_name=f"測試股{index}", market_type="上市",
        sub_industry="半導體" if index % 2 == 0 else "電腦及週邊",
        price=price, data_completeness=1, average_volume_20d_shares=2_000_000,
        average_turnover_20d=300_000_000, has_recent_trade=True,
        is_full_delivery=False, is_alternate_trading=False, is_disposed=False,
        is_suspended=False, is_delisted=False, abnormal_trading=False,
        ma20=price * (0.95 if strong else 1.05), ma60=price * (0.9 if strong else 1.1),
        ma20_slope=1 if strong else -1, ma60_slope=0.5 if strong else -0.5,
        higher_low=strong, breakout_20d=strong, return_20d=12 - index * 2,
        return_5d=4 - index, return_1d=1 if strong else -2,
        relative_strength_market=10 - index * 1.5,
        relative_strength_electronic=8 - index,
        macd_histogram_rising=strong, trailing_eps=5, revenue_yoy=10,
        gross_margin_change=1, foreign_net_5d=100 if strong else -100,
        trust_net_5d=50 if strong else -50, industry_strength_score=70 if strong else 30,
        down_volume_less_than_up=strong, fundamental_risk=False,
        volume_ratio_20d=1.5,
    )


def payload(day: date = LONG_TERM_START_DATE) -> SimpleNamespace:
    return SimpleNamespace(
        market=SimpleNamespace(trade_date=day),
        stocks=[stock(index) for index in range(15)],
    )


def test_directional_actual_return_calculation() -> None:
    assert actual_return_percent(100, 110, "long") == 10
    assert actual_return_percent(100, 90, "short") == 10
    assert actual_return_percent(100, 110, "short") == -10


def test_cash_dividends_are_added_to_total_return_after_entry_date() -> None:
    entry_timestamp = int(datetime(2026, 6, 10, tzinfo=UTC).timestamp())
    later_timestamp = int(datetime(2026, 7, 10, tzinfo=UTC).timestamp())
    events = parse_yahoo_dividends({
        "chart": {"result": [{"events": {"dividends": {
            "entry": {"date": entry_timestamp, "amount": 2.0},
            "later": {"date": later_timestamp, "amount": 3.0},
        }}}]},
    })

    paid = dividends_per_share(events, date(2026, 6, 10), date(2026, 8, 1))
    returns = total_return_breakdown(100, 105, "long", paid)

    assert paid == 3
    assert returns["priceReturnPercent"] == 5
    assert returns["dividendReturnPercent"] == 3
    assert returns["totalReturnPercent"] == 8


def test_minimum_holding_period_is_five_trading_days() -> None:
    monday = date(2026, 8, 10)
    assert minimum_exit_date(monday, frozenset()) == date(2026, 8, 17)
    assert trading_days_held(monday, date(2026, 8, 13), frozenset()) == 3
    assert trading_days_held(monday, date(2026, 8, 14), frozenset()) == 4
    assert trading_days_held(monday, date(2026, 8, 17), frozenset()) == 5


def test_yahoo_adjusted_history_calculates_ten_year_cagr() -> None:
    candidate = parse_yahoo_cagr_candidate("2330", "台積電", "上市", {
        "chart": {"result": [{
            "timestamp": [1470787200, 1786320000],
            "indicators": {"adjclose": [{"adjclose": [100, 200]}]},
        }]},
    })

    assert candidate is not None
    assert candidate.symbol == "2330"
    assert 7.0 < candidate.annualized_return < 7.3


def test_daily_top_fifty_cagr_snapshot_is_persisted_and_added_to_benchmarks() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    candidates = [TenYearCagrCandidate(
        symbol=f"{3000 + index}",
        name=f"十年股{index}",
        market="上市",
        annualized_return=45 - index,
        history_start_date=date(2016, 8, 10),
        history_end_date=date(2026, 8, 10),
        history_start_price=10 + index,
        history_end_price=500 + index,
    ) for index in range(50)]
    at = datetime(2026, 8, 10, 1, 15, tzinfo=UTC)
    with Session(engine) as db:
        save_cagr_selection(db, date(2026, 8, 10), candidates, at)
        definitions = benchmark_definitions(db)
        snapshots = list(db.scalars(select(LongTermBenchmarkSelection)).all())

    assert len(snapshots) == 50
    assert len(definitions) == 53
    assert definitions[2]["symbol"] == "00631L"
    assert definitions[3]["symbol"] == "3000"
    assert definitions[3]["rank10Year"] == 1
    assert definitions[3]["annualizedReturn10Year"] == 45


def test_top_fifty_cagr_benchmarks_are_merged_into_one_equal_weight_row(monkeypatch) -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    candidates = [TenYearCagrCandidate(
        symbol=f"{3000 + index}", name=f"十年股{index}", market="上市",
        annualized_return=50 - index,
        history_start_date=date(2016, 8, 10), history_end_date=date(2026, 8, 10),
        history_start_price=10, history_end_price=500,
    ) for index in range(50)]
    at = datetime(2026, 8, 10, 1, 15, tzinfo=UTC)

    async def no_live_quotes(_requests):
        return {}

    async def no_cash_dividends(requests):
        fetched_at = datetime(2026, 8, 10, 1, 15, tzinfo=UTC)
        return {
            symbol: DividendHistory(symbol, (), True, fetched_at)
            for symbol, _ in requests
        }

    monkeypatch.setattr(official_market_data_provider, "get_quotes", no_live_quotes)
    monkeypatch.setattr(long_term_dividend_provider, "get_histories", no_cash_dividends)
    with Session(engine) as db:
        save_cagr_selection(db, date(2026, 8, 10), candidates, at)
        definitions = benchmark_definitions(db)
        prices = {"0050": 60, "00881": 25, "00631L": 210, **{
            item.symbol: item.history_end_price for item in candidates
        }}
        run_long_term_selection(db, payload(), at, prices, definitions)
        response = asyncio.run(portfolio_payload(db, "long_only"))

    rows = response["performanceComparison"]["rows"]
    group = next(item for item in rows if item["benchmarkType"] == "ten_year_cagr_group")
    assert len(rows) == 5
    assert group["name"] == "近10年年化報酬率最高50檔"
    assert group["componentCount"] == 50
    assert len(group["constituents"]) == 50
    assert group["constituents"][0]["entryPrice"] == 500
    assert group["constituents"][0]["allocationWeightPercent"] == 2
    assert group["cumulativeReturnPercent"] == 0
    assert group["annualizedReturn10Year"] == 25.5


def test_focused_long_ranking_keeps_three_long_positions() -> None:
    picks = rank_long_term_candidates(payload(), "focused_long")
    assert len(picks) == 3
    assert all(item.direction == "long" for item in picks)
    assert len({item.stock_code for item in picks}) == 3
    weights = allocation_weights(picks, "focused_long")
    assert round(sum(weights[(item.stock_code, item.direction)] for item in picks), 4) == 100


def test_first_run_creates_ten_and_three_stock_portfolios_with_buy_events() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    at = datetime(2026, 8, 10, 1, 15, tzinfo=UTC)
    with Session(engine) as db:
        result = run_long_term_selection(
            db, payload(), at, {"0050": 60, "00881": 25, "00631L": 210},
        )
        positions = list(db.scalars(select(LongTermPosition)).all())
        runs = list(db.scalars(select(LongTermPortfolioRun)).all())
        snapshots = list(db.scalars(select(LongTermPositionSnapshot)).all())
        benchmarks = list(db.scalars(select(LongTermBenchmark)).all())
        events = list(db.scalars(select(LongTermTradeEvent)).all())

    assert result["opened"] == 13
    assert len(positions) == 13
    assert len(runs) == 2
    assert len(snapshots) == 13
    assert len(benchmarks) == 3
    assert len(events) == 13
    assert all(item.event_type == "BUY" for item in events)
    assert all(float(item.portfolio_nav) == 100 for item in runs)
    assert sum(item.portfolio_mode == "long_only" for item in positions) == 10
    assert sum(item.portfolio_mode == "focused_long" for item in positions) == 3
    assert all(item.direction == "long" for item in positions)
    for mode in ("long_only", "focused_long"):
        mode_positions = [item for item in positions if item.portfolio_mode == mode]
        assert round(sum(float(item.allocation_weight_pct) for item in mode_positions), 4) == 100
        assert round(sum(float(item.allocated_capital) for item in mode_positions), 2) == SIMULATION_CAPITAL
        assert all(item.quantity > 0 for item in mode_positions)
    assert all(item.minimum_exit_date == date(2026, 8, 17) for item in positions)


def test_portfolio_performance_includes_cash_dividend_income(monkeypatch) -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    at = datetime(2026, 8, 10, 1, 15, tzinfo=UTC)

    async def no_live_quotes(_requests):
        return {}

    async def one_dollar_dividend(requests):
        return {
            symbol: DividendHistory(
                symbol,
                (DividendEvent(date(2026, 8, 11), 1.0),),
                True,
                at,
            )
            for symbol, _ in requests
        }

    monkeypatch.setattr(official_market_data_provider, "get_quotes", no_live_quotes)
    monkeypatch.setattr(long_term_dividend_provider, "get_histories", one_dollar_dividend)
    with Session(engine) as db:
        run_long_term_selection(
            db, payload(), at, {"0050": 60, "00881": 25, "00631L": 210},
        )
        response = asyncio.run(portfolio_payload(db, "long_only"))

    expected_income = sum(item["quantity"] for item in response["items"])
    assert all(item["dividendPerShare"] == 1 for item in response["items"])
    assert all(item["totalReturnPercent"] == item["dividendReturnPercent"] for item in response["items"])
    assert response["capitalAllocation"]["dividendIncome"] == expected_income
    assert response["capitalAllocation"]["totalProfit"] == expected_income
    assert response["capitalAllocation"]["estimatedEquity"] == SIMULATION_CAPITAL + expected_income


def test_sync_overflow_is_quarantined_without_creating_sell_performance() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    at = datetime(2026, 8, 10, 1, 15, tzinfo=UTC)
    with Session(engine) as db:
        run_long_term_selection(db, payload(), at)
        originals = list(db.scalars(select(LongTermPosition).order_by(LongTermPosition.id)).all())
        for index, original in enumerate(originals):
            values = {
                column.name: getattr(original, column.name)
                for column in LongTermPosition.__table__.columns
                if column.name != "id"
            }
            values.update({
                "entry_key": f"sync-overflow:{index}",
                "entry_date": date(2026, 8, 13),
                "entry_time": datetime(2026, 8, 13, 1, 15, tzinfo=UTC),
                "created_at": datetime(2026, 8, 13, 1, 15, tzinfo=UTC),
                "updated_at": datetime(2026, 8, 13, 1, 15, tzinfo=UTC),
            })
            db.add(LongTermPosition(**values))
        db.commit()

        repaired = repair_long_term_position_overflow(
            db, datetime(2026, 8, 17, 1, 15, tzinfo=UTC),
        )
        db.commit()
        open_positions = list(db.scalars(select(LongTermPosition).where(
            LongTermPosition.status == "open",
        )).all())
        cancelled = list(db.scalars(select(LongTermPosition).where(
            LongTermPosition.status == "cancelled_duplicate",
        )).all())

    assert repaired == {"long_only": 10, "focused_long": 3}
    assert sum(item.portfolio_mode == "long_only" for item in open_positions) == 10
    assert sum(item.portfolio_mode == "focused_long" for item in open_positions) == 3
    assert len(cancelled) == 13
    assert all(item.exit_time is None and item.actual_return_pct == 0 for item in cancelled)


def test_manual_replacement_is_locked_before_day_five_and_keeps_ten_positions() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        run_long_term_selection(db, payload(), datetime(2026, 8, 10, 1, 15, tzinfo=UTC))
        position = db.scalar(select(LongTermPosition).where(
            LongTermPosition.portfolio_mode == "long_only",
            LongTermPosition.status == "open",
        ))
        assert position is not None
        original_weight = float(position.allocation_weight_pct)
        original_capital = float(position.allocated_capital)
        try:
            replace_long_term_position(
            db, position.id, payload(date(2026, 8, 14)),
            datetime(2026, 8, 14, 1, 15, tzinfo=UTC),
            )
        except ValueError as error:
            assert "尚未持有滿" in str(error)
        else:
            raise AssertionError("第四個交易日不應允許汰換")

        result = replace_long_term_position(
            db, position.id, payload(date(2026, 8, 17)),
            datetime(2026, 8, 17, 1, 15, tzinfo=UTC),
        )
        open_count = len(list(db.scalars(select(LongTermPosition).where(
            LongTermPosition.portfolio_mode == "long_only",
            LongTermPosition.status == "open",
        )).all()))
        replacement = db.get(LongTermPosition, result["newPositionId"])
        position_events = list(db.scalars(select(LongTermTradeEvent).where(
            LongTermTradeEvent.position_id.in_([position.id, result["newPositionId"]]),
        )).all())

    assert result["status"] == "replaced"
    assert open_count == 10
    assert replacement is not None
    assert float(replacement.allocation_weight_pct) == original_weight
    assert float(replacement.allocated_capital) == original_capital
    assert [item.event_type for item in position_events] == ["BUY", "SELL", "BUY"]


def test_vacancy_is_immediately_filled_with_a_new_stock() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    at = datetime(2026, 8, 10, 1, 15, tzinfo=UTC)
    with Session(engine) as db:
        run_long_term_selection(db, payload(), at)
        eliminated = db.scalar(select(LongTermPosition).where(
            LongTermPosition.portfolio_mode == "long_only",
            LongTermPosition.status == "open",
        ).order_by(LongTermPosition.id))
        assert eliminated is not None
        eliminated.status = "closed"
        eliminated.exit_date = LONG_TERM_START_DATE
        eliminated.exit_time = at
        eliminated.exit_price = eliminated.last_price
        eliminated.exit_reason = "test elimination"
        db.commit()

        result = replenish_long_term_vacancies(db, payload(), at)
        open_positions = list(db.scalars(select(LongTermPosition).where(
            LongTermPosition.portfolio_mode == "long_only",
            LongTermPosition.status == "open",
        )).all())
        eliminated_code = eliminated.stock_code

    assert result["long_only"] == 1
    assert len(open_positions) == 10
    assert eliminated_code not in {item.stock_code for item in open_positions}


def test_daily_equal_weight_nav_keeps_rotation_performance_history() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        run_long_term_selection(
            db, payload(), datetime(2026, 8, 10, 1, 15, tzinfo=UTC),
            {"0050": 60, "00881": 25, "00631L": 210},
        )
        next_payload = payload(date(2026, 8, 11))
        for item in next_payload.stocks:
            item.price *= 1.1
        run_long_term_selection(
            db, next_payload, datetime(2026, 8, 11, 1, 15, tzinfo=UTC),
            {"0050": 61.2, "00881": 24.5, "00631L": 214.2},
        )
        long_only_run = db.scalar(select(LongTermPortfolioRun).where(
            LongTermPortfolioRun.portfolio_mode == "long_only",
            LongTermPortfolioRun.trade_date == date(2026, 8, 11),
        ))
        focused_long_run = db.scalar(select(LongTermPortfolioRun).where(
            LongTermPortfolioRun.portfolio_mode == "focused_long",
            LongTermPortfolioRun.trade_date == date(2026, 8, 11),
        ))
        benchmark_0050 = db.get(LongTermBenchmark, "0050")

    assert long_only_run is not None and float(long_only_run.portfolio_nav) == 110
    assert focused_long_run is not None and float(focused_long_run.portfolio_nav) == 110
    assert benchmark_0050 is not None
    assert float(benchmark_0050.entry_price) == 60
    assert float(benchmark_0050.last_price) == 61.2
