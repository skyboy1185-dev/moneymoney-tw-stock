import asyncio
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.day_trading_v2_models import DayTradeV2BacktestJob, DayTradeV2OptimizationDataset
from app.routers.day_trading_v2 import _enrich_backtest_result
from app.services import day_trading_v2_backtests as backtest_service
from app.services.day_trading_v2_backtests import (
    BacktestPreparationError,
    FugleHistoricalMinuteClient,
    HistoricalMinuteCandle,
    build_market_context_rows,
)


TAIPEI = ZoneInfo("Asia/Taipei")


def test_legacy_backtest_results_are_enriched_without_rerunning():
    result = _enrich_backtest_result({
        "individual": {
            "OPENING_RANGE_BREAKOUT": {
                "summary": {"initialCapital": "3000000.00", "netPnl": "60.00"},
                "trades": [
                    {"grossPnl": "120", "cost": "20", "netPnl": "100"},
                    {"grossPnl": "-30", "cost": "10", "netPnl": "-40"},
                ],
            },
        },
    })
    summary = result["individual"]["OPENING_RANGE_BREAKOUT"]["summary"]
    assert summary["winRate"] == "50.0"
    assert summary["totalProfit"] == "100.00"
    assert summary["totalLoss"] == "40.00"
    assert summary["totalCost"] == "30.00"
    assert summary["netPnl"] == "60.00"


def test_legacy_portfolio_backtest_gets_five_strategy_summaries():
    result = _enrich_backtest_result({
        "summary": {"initialCapital": "3000000.00", "netPnl": "60.00"},
        "trades": [
            {"strategyId": "OPENING_RANGE_BREAKOUT", "grossPnl": "120", "cost": "20", "netPnl": "100"},
            {"strategyId": "VWAP_TREND_PULLBACK", "grossPnl": "-30", "cost": "10", "netPnl": "-40"},
        ],
    }, "ALL")
    assert len(result["strategySummaries"]) == 5
    assert result["strategySummaries"]["OPENING_RANGE_BREAKOUT"]["netPnl"] == "100.00"
    assert result["strategySummaries"]["VWAP_TREND_PULLBACK"]["netPnl"] == "-40.00"
    assert result["summary"]["netPnl"] == "60.00"


def test_fugle_minute_client_keeps_timezone_and_converts_equity_lots_to_shares():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-API-KEY"] == "secret"
        assert request.url.params["timeframe"] == "1"
        return httpx.Response(200, json={"data": [{
            "date": "2026-08-03T09:01:00.000+08:00", "open": 100, "high": 101,
            "low": 99, "close": 100.5, "volume": 12, "average": 100.2,
        }]})

    client = FugleHistoricalMinuteClient(
        "secret", base_url="https://example.test/marketdata/v1.0",
        minimum_interval_seconds=0, transport=httpx.MockTransport(handler),
    )
    rows = asyncio.run(client.fetch("2330", date(2026, 8, 3), date(2026, 8, 3)))
    assert rows[0].timestamp.utcoffset() == timedelta(hours=8)
    assert rows[0].volume == 12_000
    assert rows[0].average == Decimal("100.2")


def test_fugle_auth_failure_is_actionable_and_never_falls_back_to_daily_bars():
    client = FugleHistoricalMinuteClient(
        "bad", base_url="https://example.test/marketdata/v1.0",
        minimum_interval_seconds=0,
        transport=httpx.MockTransport(lambda _: httpx.Response(403, json={"message": "forbidden"})),
    )
    with pytest.raises(BacktestPreparationError, match="CSV／Parquet") as caught:
        asyncio.run(client.fetch("2330", date(2026, 8, 3), date(2026, 8, 3)))
    assert caught.value.code == "FUGLE_AUTH_FAILED"
    assert caught.value.status == "FAILED"


def _bar(day: date, price: str, volume: int = 1000) -> HistoricalMinuteCandle:
    timestamp = datetime.combine(day, time(9, 1), TAIPEI)
    value = Decimal(price)
    return HistoricalMinuteCandle(timestamp, value, value, value, value, volume, value)


def test_market_context_requires_warmup_and_uses_only_prior_days_for_relative_volume():
    first = date(2026, 7, 1)
    days = [first + timedelta(days=index) for index in range(21)]
    datasets = {
        "2330": [_bar(day, str(100 + index), 1000 + index * 10) for index, day in enumerate(days)],
        "2317": [_bar(day, str(80 + index), 800 + index * 10) for index, day in enumerate(days)],
    }
    index = [_bar(day, str(20000 + offset), 0) for offset, day in enumerate(days)]
    rows, quality = build_market_context_rows(
        datasets, index, {"2330": "半導體", "2317": "電子"},
        requested_start=days[-1], requested_end=days[-1],
    )
    assert len(rows) == 2
    assert {row["symbol"] for row in rows} == {"2330", "2317"}
    assert all(str(row["timestamp"]).startswith(days[-1].isoformat()) for row in rows)
    assert Decimal(str(rows[0]["market_relative_volume"])) > 1
    assert quality["warmupTradingDays"] == 20
    assert quality["averageDailySymbolCoveragePct"] == "100.00"


def test_market_context_refuses_a_range_without_twenty_prior_sessions():
    day = date(2026, 8, 3)
    with pytest.raises(BacktestPreparationError, match="20個交易日暖機"):
        build_market_context_rows(
            {"2330": [_bar(day, "100")]}, [_bar(day, "20000", 0)], {"2330": "半導體"},
            requested_start=day, requested_end=day,
        )


def test_background_job_persists_validated_parquet_and_completes(monkeypatch, tmp_path):
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(backtest_service, "SessionLocal", sessions)
    monkeypatch.setenv("DTV2_OPTIMIZATION_DATA_DIR", str(tmp_path))
    first = date(2026, 7, 1)
    days = [first + timedelta(days=index) for index in range(21)]

    async def universe(_request):
        return [{"symbol": "2330", "name": "台積電", "sector": "半導體", "market": "上市"}]

    async def fetch(_self, symbol, _start, _end, *, is_index=False):
        base = Decimal("20000") if is_index else Decimal("100")
        return [_bar(day, str(base + index), 0 if is_index else 1000 + index) for index, day in enumerate(days)]

    monkeypatch.setattr(backtest_service, "_resolve_universe", universe)
    monkeypatch.setattr(backtest_service.FugleHistoricalMinuteClient, "fetch", fetch)
    with sessions() as db:
        db.add(DayTradeV2BacktestJob(
            id="job-1", user_id="test-user", backtest_mode="PORTFOLIO", strategy_id="ALL",
            start_date=days[-1], end_date=days[-1], status="QUEUED", data_source="FUGLE_AUTO",
            data_precision="1_MINUTE", request_json=(
                '{"backtest_mode":"PORTFOLIO","strategy_id":"ALL","universe_preset":"TOP_LIQUID_100"}'
            ), result_json="{}", progress_json="{}", universe_json="[]",
        ))
        db.commit()
    assert backtest_service.process_next_backtest_job() == "job-1"
    with sessions() as db:
        job = db.get(DayTradeV2BacktestJob, "job-1")
        assert job is not None and job.status == "COMPLETED"
        assert job.data_precision == "1_MINUTE"
        assert job.dataset_id
        assert db.get(DayTradeV2OptimizationDataset, job.dataset_id).quality_status == "BACKTEST_READY"


def test_backtest_api_queues_auto_minute_data_without_requiring_dataset():
    from app.routers.day_trading_v2 import BacktestBody, create_backtest

    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        payload = create_backtest(BacktestBody(
            start_date=date(2026, 8, 3), end_date=date(2026, 8, 4),
            backtest_mode="PORTFOLIO", strategy_id="ALL", data_source="AUTO_FUGLE",
        ), user_id="test-user", db=db)
        assert payload["status"] == "QUEUED"
        assert payload["dataSource"] == "FUGLE_AUTO"
        assert payload["dataPrecision"] == "1_MINUTE"
        assert db.get(DayTradeV2BacktestJob, payload["id"]).dataset_id == ""
