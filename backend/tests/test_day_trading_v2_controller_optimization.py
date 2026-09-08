from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from io import BytesIO

import pytest
import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.day_trading_v2_models import (
    DayTradeV2ChallengerEvent, DayTradeV2ChallengerRun, DayTradeV2OptimizationJob,
    DayTradeV2OptimizationTrial, DayTradeV2StrategyDeployment, DayTradeV2StrategyRiskOverride,
    DayTradeV2StrategyVersion, DayTradeV2Trade,
)
from app.day_trading_v2_models import DayTradeV2ControllerCandidate, DayTradeV2Order, DayTradeV2RuntimeState, DayTradeV2Signal
from app.services.day_trading_v2_controller import (
    ControllerCandidateInput, MarketInputs, REGIME_CRASH, REGIME_MILD, REGIME_RANGE,
    REGIME_STRONG, REGIME_WEAK, apply_regime_hysteresis, classify_market,
    rank_candidates, score_candidate,
)
from app.services.day_trading_v2_datasets import DatasetValidationError, load_dataset, parse_dataset, persist_dataset
from app.services.day_trading_v2_health import (
    _ensure_optimization_job, active_version, apply_pending_deployments, handle_strategy_runtime_error,
    run_health_diagnosis,
)
from app.services.day_trading_v2_challenger import _record_event
from app.services.day_trading_v2 import DEFAULT_CONFIG
from app.services.day_trading_v2_optimization import (
    bounded_parameter_candidates, candidate_passes, challenger_ready, diagnose_health,
    walk_forward_splits,
)


def market(**overrides):
    values = {
        "index_price": Decimal("101"), "index_vwap": Decimal("100"),
        "trend_1m_pct": Decimal("0.1"), "trend_5m_pct": Decimal("0.5"),
        "breadth_pct": Decimal("65"), "relative_volume": Decimal("1.2"),
        "strong_sector_count": 4, "weak_sector_count": 0,
        "quote_coverage_pct": Decimal("95"), "data_normal": True,
    }
    values.update(overrides)
    return MarketInputs(**values)


def test_market_regimes_cover_strong_mild_range_weak_and_crash():
    assert classify_market(market()).effective == REGIME_STRONG
    assert classify_market(market(index_price=Decimal("100"), trend_5m_pct=Decimal("0.1"), breadth_pct=Decimal("55"), relative_volume=Decimal("1"), strong_sector_count=1)).effective == REGIME_MILD
    assert classify_market(market(index_price=Decimal("100"), trend_5m_pct=Decimal("0"), breadth_pct=Decimal("50"), relative_volume=Decimal("0.8"), strong_sector_count=0, vwap_crosses_30m=4)).effective == REGIME_RANGE
    assert classify_market(market(index_price=Decimal("99"), trend_1m_pct=Decimal("-0.1"), trend_5m_pct=Decimal("-0.4"), breadth_pct=Decimal("35"), relative_volume=Decimal("0.8"), strong_sector_count=0, weak_sector_count=4)).effective == REGIME_WEAK
    assert classify_market(market(trend_1m_pct=Decimal("-0.6"))).effective == REGIME_CRASH
    blocked = classify_market(market(quote_coverage_pct=Decimal("50")))
    assert blocked.effective == REGIME_CRASH
    assert blocked.data_blocked is True


def test_regime_hysteresis_changes_normal_state_after_two_cycles_and_crash_immediately():
    mild = classify_market(market(index_price=Decimal("100"), trend_5m_pct=Decimal("0.1"), breadth_pct=Decimal("55"), relative_volume=Decimal("1"), strong_sector_count=1))
    first = apply_regime_hysteresis(mild, current=REGIME_STRONG, recent_proposals=[])
    second = apply_regime_hysteresis(mild, current=REGIME_STRONG, recent_proposals=[REGIME_MILD])
    assert first.effective == REGIME_STRONG
    assert second.effective == REGIME_MILD
    crash = classify_market(market(trend_1m_pct=Decimal("-0.7")))
    assert apply_regime_hysteresis(crash, current=REGIME_STRONG).effective == REGIME_CRASH


def candidate(strategy="OPENING_RANGE_BREAKOUT", symbol="2330", score="85"):
    return ControllerCandidateInput(
        key=f"{strategy}:{symbol}", symbol=symbol, stock_name="測試", sector="半導體",
        strategy_id=strategy, strategy_version="2.0.0",
        signal_time=datetime(2026, 9, 7, 1, 30, tzinfo=UTC), raw_score=Decimal(score),
        entry_price=Decimal("100"), stop_price=Decimal("98"), target_price=Decimal("104"),
        risk_reward=Decimal("2"), sector_strength=Decimal("80"),
        liquidity_score=Decimal("95"), vwap_deviation_pct=Decimal("0.1"),
    )


def test_controller_score_keeps_components_and_blocks_incompatible_strategy():
    strong = score_candidate(candidate(), regime=REGIME_STRONG, local_time=time(9, 30))
    assert strong.allowed is True
    assert strong.regime_adjustment == 10
    assert strong.score_details["rawScore"] == "85"
    weak = score_candidate(candidate(), regime=REGIME_WEAK, local_time=time(9, 30))
    assert weak.allowed is False
    assert "策略不適合目前盤勢" in weak.blocked_reasons


def test_unified_pool_deduplicates_symbol_and_retains_rejection_reason():
    first = score_candidate(candidate(score="90"), regime=REGIME_STRONG, local_time=time(9, 30))
    second = score_candidate(candidate("VWAP_TREND_PULLBACK", score="82"), regime=REGIME_STRONG, local_time=time(9, 30))
    ranked = rank_candidates([second, first])
    assert sum(row.allowed for row in ranked) == 1
    assert ranked[0].candidate.strategy_id == "OPENING_RANGE_BREAKOUT"
    assert any("重複股票訊號" in reason for reason in ranked[1].blocked_reasons)


def test_unified_pool_tie_breaks_with_oos_profit_factor_then_health():
    base = candidate(symbol="2330", score="82")
    stronger_oos = ControllerCandidateInput(**{
        **base.__dict__, "symbol": "2317", "key": "oos", "oos_profit_factor": Decimal("1.5"),
        "health_score": Decimal("80"),
    })
    weaker_oos = ControllerCandidateInput(**{
        **base.__dict__, "symbol": "2454", "key": "weak", "oos_profit_factor": Decimal("1.2"),
        "health_score": Decimal("100"),
    })
    ranked = rank_candidates([
        score_candidate(weaker_oos, regime=REGIME_STRONG, local_time=time(9, 30)),
        score_candidate(stronger_oos, regime=REGIME_STRONG, local_time=time(9, 30)),
    ])
    assert ranked[0].candidate.symbol == "2317"


def test_strategy_time_and_missing_sector_are_hard_blocks():
    row = candidate("AFTERNOON_STRENGTH_BREAKOUT")
    early = score_candidate(row, regime=REGIME_STRONG, local_time=time(10, 0))
    assert "不在策略有效時段" in early.blocked_reasons
    missing = score_candidate(ControllerCandidateInput(**{**row.__dict__, "sector": ""}), regime=REGIME_STRONG, local_time=time(13, 0))
    assert "缺少產業分類" in missing.blocked_reasons


def test_health_diagnosis_respects_sample_size_then_reduces_and_pauses():
    insufficient = diagnose_health([{"netPnl": -100}] * 19)
    assert insufficient.status == "INSUFFICIENT"
    alert = diagnose_health([{"netPnl": -100}] * 20)
    assert alert.status == "ALERT"
    assert alert.recommended_action == "REDUCE"
    assert alert.risk_multiplier == Decimal("0.5")
    assert alert.metrics["last10"]["tradeCount"] == 10
    assert "last5TradingDays" in alert.metrics
    assert "month" in alert.metrics
    assert "historical" in alert.metrics
    paused = diagnose_health([{"netPnl": -100}] * 20, consecutive_alert_days=1)
    assert paused.recommended_action == "PAUSE"
    assert paused.risk_multiplier == 0


def test_parameter_search_is_bounded_to_twenty_percent_and_walk_forward_is_ordered():
    rows = bounded_parameter_candidates({"volumeMultiplier": "1"}, {"volumeMultiplier": ("0.5", "2")})
    assert {row["volumeMultiplier"] for row in rows} == {Decimal("0.8"), Decimal("0.9"), Decimal("1"), Decimal("1.1"), Decimal("1.2")}
    days = [date(2025, 1, 1) + timedelta(days=index) for index in range(180)]
    folds = walk_forward_splits(days)
    assert folds
    for fold in folds:
        assert fold["train"][1] < fold["validation"][0] <= fold["validation"][1] < fold["oos"][0]


def passing_result():
    return {
        "tradeCount": 120, "netPnl": "50000", "profitFactor": "1.5", "payoffRatio": "2",
        "maxDrawdown": "10000", "topTwoProfitSharePct": "20", "topSymbolProfitSharePct": "20",
        "topSectorProfitSharePct": "30", "monthCount": 4, "nonNegativeMonthPct": "75",
        "maxParticipationPct": "2", "neighborStable": True,
    }


def test_candidate_gate_rejects_win_rate_only_improvement_and_challenger_never_skips_observation():
    result = passing_result()
    ok, failures = candidate_passes(result, {"netPnl": "40000", "payoffRatio": "1.8", "maxDrawdown": "12000"})
    assert ok and not failures
    result["payoffRatio"] = "1.5"
    ok, failures = candidate_passes(result, {"netPnl": "40000", "payoffRatio": "1.8", "maxDrawdown": "12000"})
    assert not ok and "賺賠比低於正式策略" in failures
    ready, reasons = challenger_ready(full_trading_days=9, trade_count=29, net_pnl=1000, max_drawdown=100, champion_max_drawdown=200, error_count=0)
    assert not ready and reasons
    ready, _ = challenger_ready(full_trading_days=10, trade_count=5, net_pnl=1000, max_drawdown=100, champion_max_drawdown=200, error_count=0)
    assert ready


def test_csv_dataset_requires_timezone_and_sector():
    good = b"symbol,timestamp,open,high,low,close,volume,sector,index_price,index_vwap,index_trend_1m_pct,index_trend_5m_pct,market_breadth_pct,market_relative_volume,quote_coverage_pct\n2330,2026-09-07T09:00:00+08:00,100,101,99,100,1000,semiconductor,25000,24990,0.1,0.2,55,1.0,95\n"
    datasets, sectors, regimes, quality = parse_dataset(good, "CSV")
    assert quality["rowCount"] == 1
    assert sectors["2330"] == "semiconductor"
    assert datasets["2330"][0].timestamp.utcoffset() == timedelta(hours=8)
    assert regimes
    bad = good.replace(b"+08:00", b"")
    with pytest.raises(DatasetValidationError, match="time zone|時區|timestamp"):
        parse_dataset(bad, "CSV")


def test_parquet_dataset_keeps_binary_content_and_market_context():
    table = pa.table({
        "symbol": ["2330"], "timestamp": ["2026-09-07T09:00:00+08:00"],
        "open": [100], "high": [101], "low": [99], "close": [100], "volume": [1000],
        "sector": ["semiconductor"], "index_price": [25000], "index_vwap": [24990],
        "index_trend_1m_pct": [0.1], "index_trend_5m_pct": [0.2],
        "market_breadth_pct": [55], "market_relative_volume": [1.0],
        "quote_coverage_pct": [95],
    })
    buffer = BytesIO()
    pq.write_table(table, buffer)
    datasets, sectors, regimes, quality = parse_dataset(buffer.getvalue(), "PARQUET")
    assert datasets["2330"][0].close == Decimal("100")
    assert sectors["2330"] == "semiconductor"
    assert regimes and quality["marketContextVerified"] is True


def test_backtest_dataset_can_be_persisted_and_loaded(monkeypatch, tmp_path):
    content = b"symbol,timestamp,open,high,low,close,volume,sector,index_price,index_vwap,index_trend_1m_pct,index_trend_5m_pct,market_breadth_pct,market_relative_volume,quote_coverage_pct\n2330,2026-09-07T09:00:00+08:00,100,101,99,100,1000,semiconductor,25000,24990,0.1,0.2,55,1.0,95\n"
    monkeypatch.setenv("DTV2_OPTIMIZATION_DATA_DIR", str(tmp_path))
    path, checksum = persist_dataset(content, dataset_id="backtest-fixture", data_format="CSV")
    datasets, sectors, regimes, quality = load_dataset(str(path), checksum, "CSV")
    assert datasets["2330"][0].close == Decimal("100")
    assert sectors == {"2330": "semiconductor"}
    assert regimes
    assert quality["rowCount"] == 1


def test_pending_strategy_version_activates_only_on_effective_day_and_history_stays_immutable():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        db.add_all([
            DayTradeV2StrategyVersion(strategy_id="OPENING_RANGE_BREAKOUT", version="2.0.0", definition_json="{}"),
            DayTradeV2StrategyVersion(strategy_id="OPENING_RANGE_BREAKOUT", version="2.0.1", parent_version="2.0.0", definition_json="{}"),
            DayTradeV2StrategyDeployment(id="old", user_id="test-user", strategy_id="OPENING_RANGE_BREAKOUT", version="2.0.0", role="CHAMPION", status="ACTIVE", activated_at=datetime(2026, 9, 1, tzinfo=UTC)),
            DayTradeV2StrategyDeployment(id="new", user_id="test-user", strategy_id="OPENING_RANGE_BREAKOUT", version="2.0.1", role="CHALLENGER", status="PENDING_ACTIVATION", effective_date=date(2026, 9, 8)),
            DayTradeV2ChallengerRun(id="run", user_id="test-user", strategy_id="OPENING_RANGE_BREAKOUT", champion_version="2.0.0", challenger_version="2.0.1", status="APPROVED_PENDING_ACTIVATION", started_at=datetime(2026, 9, 1, tzinfo=UTC)),
        ])
        db.commit()
        assert apply_pending_deployments(db, "test-user", date(2026, 9, 7), datetime(2026, 9, 7, tzinfo=UTC)) == 0
        assert active_version(db, "test-user", "OPENING_RANGE_BREAKOUT") == "2.0.0"
        assert apply_pending_deployments(db, "test-user", date(2026, 9, 8), datetime(2026, 9, 8, tzinfo=UTC)) == 1
        assert active_version(db, "test-user", "OPENING_RANGE_BREAKOUT") == "2.0.1"
        old = db.get(DayTradeV2StrategyDeployment, "old")
        assert old.status == "SUPERSEDED"
        assert db.get(DayTradeV2ChallengerRun, "run").status == "PROMOTED"
        assert db.scalar(select(DayTradeV2StrategyVersion).where(DayTradeV2StrategyVersion.version == "2.0.0")) is not None


def test_challenger_events_are_idempotent_and_keep_simulation_context():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    occurred_at = datetime(2026, 9, 7, 1, 30, tzinfo=UTC)
    with sessions() as db:
        run = DayTradeV2ChallengerRun(
            id="event-run", user_id="event-user", strategy_id="VWAP_TREND_PULLBACK",
            champion_version="2.0.0", challenger_version="2.0.1", status="RUNNING", started_at=occurred_at,
        )
        db.add(run)
        db.flush()
        for _ in range(2):
            _record_event(
                db, run=run, role="CHALLENGER", version="2.0.1", event_type="SIGNAL_REJECTED",
                event_id="event-run:rejected:2330:202609070930", occurred_at=occurred_at,
                signal_key="2330:202609070930", symbol="2330",
                payload={"score": "76", "reasons": ["信心分數不足"], "marketRegime": "RANGE"},
            )
        db.commit()
        events = list(db.scalars(select(DayTradeV2ChallengerEvent)).all())
        assert len(events) == 1
        assert '"score": "76"' in events[0].payload_json
        assert "信心分數不足" in events[0].payload_json


def test_active_challenger_prevents_duplicate_automatic_optimization_job():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        db.add(DayTradeV2ChallengerRun(
            id="active-run", user_id="health-user", strategy_id="VOLUME_HIGH_BREAKOUT",
            champion_version="2.0.0", challenger_version="2.0.1", status="WAITING_APPROVAL",
            started_at=datetime(2026, 9, 7, tzinfo=UTC),
        ))
        db.commit()
        _ensure_optimization_job(db, "health-user", "VOLUME_HIGH_BREAKOUT", "2.0.0", date(2026, 9, 7))
        db.flush()
        assert db.scalar(select(DayTradeV2OptimizationJob)) is None


def test_optimization_job_detail_returns_every_candidate_trial():
    from app.routers.day_trading_v2 import optimization_job_detail

    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        db.add(DayTradeV2OptimizationJob(
            id="job-detail", user_id="detail-user", strategy_id="OPENING_RANGE_BREAKOUT",
            champion_version="2.0.0", candidate_version="2.0.1", status="COMPLETED",
        ))
        db.add_all([
            DayTradeV2OptimizationTrial(id="trial-1", job_id="job-detail", candidate_index=0, parameters_json='{"volumeMultiplier":"1.0"}', validation_metrics_json='{"netPnl":"100"}'),
            DayTradeV2OptimizationTrial(id="trial-2", job_id="job-detail", candidate_index=1, parameters_json='{"volumeMultiplier":"1.1"}', validation_metrics_json='{"netPnl":"200"}', selected=True),
        ])
        db.commit()
        result = optimization_job_detail("job-detail", "detail-user", db)
        assert [row["candidateIndex"] for row in result["trials"]] == [0, 1]
        assert result["trials"][1]["selected"] is True


def test_optimizer_persists_and_updates_each_validation_trial(monkeypatch):
    from app.services import day_trading_v2_optimizer as optimizer

    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(optimizer, "SessionLocal", sessions)
    optimizer._persist_validation_trial("persist-job", 3, {"volumeMultiplier": Decimal("1.1")}, {"netPnl": "100"})
    optimizer._persist_validation_trial("persist-job", 3, {"volumeMultiplier": Decimal("1.2")}, {"netPnl": "250"})
    with sessions() as db:
        rows = list(db.scalars(select(DayTradeV2OptimizationTrial)).all())
        assert len(rows) == 1
        assert '"1.2"' in rows[0].parameters_json
        assert '"250"' in rows[0].validation_metrics_json


def test_bad_paper_performance_reduces_risk_then_pauses_and_queues_offline_optimization():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        base_time = datetime(2026, 9, 1, 5, 0, tzinfo=UTC)
        for index in range(20):
            moment = base_time + timedelta(minutes=index)
            db.add(DayTradeV2Trade(
                id=f"loss-{index}", user_id="poor-user", mode="PAPER", symbol="2330",
                stock_name="台積電", strategy_id="OPENING_RANGE_BREAKOUT", strategy_version="2.0.0",
                quantity=1000, signal_time=moment, entry_order_time=moment, entry_fill_time=moment,
                entry_price=Decimal("100"), exit_signal_time=moment, exit_order_time=moment,
                exit_fill_time=moment, exit_price=Decimal("99"), gross_pnl=Decimal("-1000"),
                buy_fee=Decimal("0"), sell_fee=Decimal("0"), transaction_tax=Decimal("0"),
                slippage=Decimal("0"), other_cost=Decimal("0"), net_pnl=Decimal("-1000"),
                net_return_pct=Decimal("-1"), entry_reason="測試", exit_reason="停損",
            ))
        db.commit()
        first = run_health_diagnosis(db, "poor-user", "PAPER", DEFAULT_CONFIG, datetime(2026, 9, 7, tzinfo=UTC))
        db.flush()
        target = next(row for row in first if row.strategy_id == "OPENING_RANGE_BREAKOUT")
        override = db.scalar(select(DayTradeV2StrategyRiskOverride).where(
            DayTradeV2StrategyRiskOverride.user_id == "poor-user",
            DayTradeV2StrategyRiskOverride.strategy_id == "OPENING_RANGE_BREAKOUT",
        ))
        assert target.status == "ALERT"
        assert override.risk_multiplier == Decimal("0.5") and override.paused is False
        assert db.scalar(select(DayTradeV2OptimizationJob).where(
            DayTradeV2OptimizationJob.strategy_id == "OPENING_RANGE_BREAKOUT",
        )).status == "DATA_INSUFFICIENT"

        second = run_health_diagnosis(db, "poor-user", "PAPER", DEFAULT_CONFIG, datetime(2026, 9, 8, tzinfo=UTC))
        db.flush()
        target = next(row for row in second if row.strategy_id == "OPENING_RANGE_BREAKOUT")
        assert target.recommended_action == "PAUSE"
        assert override.paused is True and override.risk_multiplier == 0


def test_new_strategy_runtime_error_stops_version_and_queues_previous_version():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime(2026, 9, 7, 6, 0, tzinfo=UTC)
    with sessions() as db:
        db.add_all([
            DayTradeV2StrategyDeployment(
                id="old", user_id="error-user", strategy_id="OPENING_RANGE_BREAKOUT",
                version="2.0.0", role="CHAMPION", status="SUPERSEDED", disabled_at=now,
            ),
            DayTradeV2StrategyDeployment(
                id="new", user_id="error-user", strategy_id="OPENING_RANGE_BREAKOUT",
                version="2.0.1", role="CHAMPION", status="ACTIVE", activated_at=now,
            ),
        ])
        db.commit()
        assert handle_strategy_runtime_error(
            db, "error-user", "PAPER", "OPENING_RANGE_BREAKOUT", "2.0.1",
            RuntimeError("bad parameter"), now,
        )
        db.commit()
        assert db.get(DayTradeV2StrategyDeployment, "new").status == "ROLLBACK_DISABLED"
        pending = db.scalar(select(DayTradeV2StrategyDeployment).where(
            DayTradeV2StrategyDeployment.status == "PENDING_ACTIVATION",
        ))
        assert pending is not None and pending.version == "2.0.0"


@pytest.mark.parametrize("scenario", ["fresh", "all_stale", "partial", "expired_at_entry", "stale_book"])
def test_automated_scan_can_only_create_one_order_through_persisted_controller_decision(monkeypatch, scenario):
    from app.routers import day_trading_v2 as router
    from app.services.official_market_data import OfficialStockQuote
    from app.day_trading_v2_models import DayTradeV2CandidateState

    current = datetime(2026, 9, 7, 1, 16, tzinfo=UTC)
    start = datetime(2026, 9, 7, 1, 0, tzinfo=UTC)
    bars = [
        {"timestamp": (start + timedelta(minutes=index)).isoformat(), "open": "99.5", "high": "100", "low": "99", "close": "99.8", "volume": 1000}
        for index in range(15)
    ]
    bars.append({"timestamp": (start + timedelta(minutes=15)).isoformat(), "open": "100", "high": "102.5", "low": "100", "close": "102", "volume": 2500})

    class FakeEngine:
        def market_regime(self):
            return {
                "dataStatus": "normal", "quoteCoverageRatio": 0.95,
                "metrics": {"weightedIndex": 101, "vwap": 100, "oneMinuteTrend": "+0.10%", "fiveMinuteTrend": "+0.50%", "breadth": 65, "relativeVolume": 1.2},
            }

        def signals(self):
            candidate = {
                "symbol": "2330", "stockName": "台積電", "direction": "long", "themes": ["半導體"],
                "dataSource": "TWSE MIS", "quoteIsRealtime": True, "quoteTimestamp": current.isoformat(),
                "volume": 5_000_000, "turnover": 500_000_000, "spreadPercentage": 0.1,
                "vwapDeviationPercent": 0.1, "tradeRestricted": False,
                "industryScore": 85, "liquidityScore": 95, "price": 102,
            }
            return [candidate, {**candidate, "symbol": "2317"}] if scenario == "partial" else [candidate]

        def official_quotes_snapshot(self, symbols=None):
            result = {}
            for symbol in (symbols or [item["symbol"] for item in self.signals()]):
                stamp = current - timedelta(seconds=16) if scenario == "all_stale" or symbol == "2317" else current
                result[symbol] = OfficialStockQuote(symbol, "測試", 102, 100, 100, 103, 99, 5_000_000, 2, 2, stamp.isoformat(), "TWSE MIS", True, best_bid=101.9, best_ask=102.1,
                    book_timestamp=(current - timedelta(seconds=16) if scenario == "stale_book" else stamp).isoformat())
            return result

        def minute_bars_for(self, symbol):
            return bars

        def quote_for(self, symbol):
            return Decimal("102")

    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(router, "day_trading_engine", FakeEngine())
    monkeypatch.setattr(router, "_now", lambda: current + timedelta(seconds=16) if scenario == "expired_at_entry" else current)
    with sessions() as db:
        router._ensure_defaults(db, "controller-user")
        runtime = DayTradeV2RuntimeState(
            user_id="controller-user", mode="PAPER", trading_date=current.astimezone(router.TAIPEI).date(),
            status="RUNNING", initialized=True, receiving_quotes=True, scanning=True, order_allowed=True,
            heartbeat_at=current,
        )
        db.add(runtime)
        db.commit()
        result = router._scan_now("controller-user", db, current)
        if scenario in {"all_stale", "expired_at_entry", "stale_book"}:
            assert result["executed"] == 0
            assert db.scalar(select(DayTradeV2Order)) is None
            return
        assert result["executed"] == 1, (router._quote_observation(current, router.merged_config())["fresh"], runtime.status, runtime.order_allowed, runtime.latest_error, [(row.status, row.blocked_reasons_json) for row in db.scalars(select(DayTradeV2ControllerCandidate))])
        order = db.scalar(select(DayTradeV2Order))
        signal = db.scalar(select(DayTradeV2Signal))
        controller_candidate = db.scalar(select(DayTradeV2ControllerCandidate).where(DayTradeV2ControllerCandidate.status == "EXECUTED"))
        assert order is not None and order.controller_decision_id
        assert signal is not None and signal.controller_decision_id == order.controller_decision_id
        assert controller_candidate is not None and controller_candidate.cycle_id == order.controller_decision_id
        if scenario == "partial":
            stale_candidates = list(db.scalars(select(DayTradeV2ControllerCandidate).where(DayTradeV2ControllerCandidate.symbol == "2317")).all())
            assert stale_candidates and all(not row.allowed for row in stale_candidates)
            own_state = db.scalar(select(DayTradeV2CandidateState).where(DayTradeV2CandidateState.symbol == "2317"))
            assert own_state.quote_at.replace(tzinfo=UTC) == current - timedelta(seconds=16)
        again = router._scan_now("controller-user", db, current)
        assert again["idempotent"] is True
        assert len(list(db.scalars(select(DayTradeV2Order)).all())) == 1
