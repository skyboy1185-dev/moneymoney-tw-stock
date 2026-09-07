from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal
import json
from uuid import uuid4

from sqlalchemy import select

from ..database import SessionLocal
from ..day_trading_v2_models import (
    DayTradeV2ChallengerRun, DayTradeV2Notification, DayTradeV2OptimizationDataset,
    DayTradeV2OptimizationJob, DayTradeV2OptimizationTrial,
    DayTradeV2StrategyDeployment, DayTradeV2StrategyVersion,
)
from .day_trading_v2 import DEFAULT_STRATEGY_PARAMETERS, dec, performance, run_backtest
from .day_trading_v2_datasets import DatasetValidationError, load_dataset
from .day_trading_v2_optimization import (
    bounded_parameter_candidates, candidate_passes, parameter_checksum, walk_forward_splits,
)


PARAMETER_BOUNDS: dict[str, dict[str, tuple[object, object]]] = {
    "OPENING_RANGE_BREAKOUT": {"volumeMultiplier": ("0.8", "2.5"), "baseScore": (55, 90), "targetRiskReward": ("2", "3")},
    "VWAP_TREND_PULLBACK": {"pullbackRangePct": ("0.1", "0.8"), "volumeMultiplier": ("0.5", "2"), "baseScore": (55, 90), "targetRiskReward": ("2", "3")},
    "VOLUME_HIGH_BREAKOUT": {"lookbackBars": (15, 60), "volumeMultiplier": ("1", "3"), "baseScore": (55, 90), "targetRiskReward": ("2", "3")},
    "FALSE_BREAKDOWN_REVERSAL": {"lookbackBars": (10, 40), "confirmationBars": (2, 8), "volumeMultiplier": ("0.6", "2"), "baseScore": (55, 90), "targetRiskReward": ("2", "3")},
    "AFTERNOON_STRENGTH_BREAKOUT": {"lookbackBars": (15, 60), "volumeMultiplier": ("0.7", "2.5"), "baseScore": (55, 90), "targetRiskReward": ("2", "3")},
}


def _slice(datasets, start: date, end: date):
    return {
        symbol: [bar for bar in bars if start <= bar.timestamp.date() <= end]
        for symbol, bars in datasets.items()
        if any(start <= bar.timestamp.date() <= end for bar in bars)
    }


def _run_ranges(datasets, ranges, strategy_id: str, parameters: dict[str, object], config: dict[str, object], sectors=None, regimes=None):
    trades: list[dict[str, object]] = []
    for start, end in ranges:
        result = run_backtest(
            _slice(datasets, start, end), strategy_id=strategy_id, config=config, portfolio=False,
            strategy_parameters={strategy_id: parameters}, sector_by_symbol=sectors,
            market_regime_by_time=regimes, controller_filter=True,
        )
        trades.extend(result["trades"])
    return trades


def _summary(trades, sectors, datasets):
    base = performance(trades)
    pnls = [dec(row["netPnl"]) for row in trades]
    equity = peak = drawdown = Decimal("0")
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    positive = [(index, pnl) for index, pnl in enumerate(pnls) if pnl > 0]
    gross_profit = sum((pnl for _, pnl in positive), Decimal("0"))
    top_two = sum(sorted((pnl for _, pnl in positive), reverse=True)[:2], Decimal("0"))
    by_symbol: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    by_sector: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    by_month: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    participation = Decimal("0")
    volume_index = {
        (symbol, bar.timestamp): bar.volume for symbol, bars in datasets.items() for bar in bars
    }
    for row, pnl in zip(trades, pnls):
        symbol = str(row["symbol"])
        by_symbol[symbol] += max(pnl, Decimal("0"))
        by_sector[sectors.get(symbol, "未分類")] += max(pnl, Decimal("0"))
        by_month[str(row["exitTime"])[:7]] += pnl
        volume = volume_index.get((symbol, row["entryTime"]), 0)
        if volume:
            participation = max(participation, Decimal(int(row["quantity"])) / volume * 100)
    denominator = max(gross_profit, Decimal("0.01"))
    months = list(by_month.values())
    base.update({
        "maxDrawdown": str(drawdown.quantize(Decimal("0.01"))),
        "topTwoProfitSharePct": str((top_two / denominator * 100).quantize(Decimal("0.1"))),
        "topSymbolProfitSharePct": str((max(by_symbol.values(), default=Decimal("0")) / denominator * 100).quantize(Decimal("0.1"))),
        "topSectorProfitSharePct": str((max(by_sector.values(), default=Decimal("0")) / denominator * 100).quantize(Decimal("0.1"))),
        "monthCount": len(months),
        "nonNegativeMonthPct": str((Decimal(sum(value >= 0 for value in months)) / len(months) * 100).quantize(Decimal("0.1")) if months else 0),
        "maxParticipationPct": str(participation.quantize(Decimal("0.01"))),
    })
    return base


def _persist_validation_trial(
    job_id: str, candidate_index: int, parameters: dict[str, object], summary: dict[str, object],
) -> None:
    with SessionLocal() as db:
        row = db.scalar(select(DayTradeV2OptimizationTrial).where(
            DayTradeV2OptimizationTrial.job_id == job_id,
            DayTradeV2OptimizationTrial.candidate_index == candidate_index,
        ))
        values = {
            "parameters_json": json.dumps(parameters, default=str, ensure_ascii=False),
            "validation_metrics_json": json.dumps(summary, default=str, ensure_ascii=False),
            "status": "VALIDATED",
        }
        if row is None:
            row = DayTradeV2OptimizationTrial(
                id=str(uuid4()), job_id=job_id, candidate_index=candidate_index, **values,
            )
            db.add(row)
        else:
            for key, value in values.items():
                setattr(row, key, value)
        db.commit()


def execute_optimization_job(job_id: str) -> None:
    with SessionLocal() as db:
        job = db.get(DayTradeV2OptimizationJob, job_id)
        if not job or job.status != "RUNNING":
            return
        dataset_row = db.get(DayTradeV2OptimizationDataset, job.dataset_id)
        champion_row = db.scalar(select(DayTradeV2StrategyVersion).where(
            DayTradeV2StrategyVersion.strategy_id == job.strategy_id,
            DayTradeV2StrategyVersion.version == job.champion_version,
        ))
        if not dataset_row or not champion_row:
            job.status = "DATA_INSUFFICIENT"
            job.error_message = "找不到合格資料集或正式策略版本"
            job.completed_at = datetime.now(UTC)
            db.commit()
            return
        dataset_meta = (dataset_row.storage_path, dataset_row.checksum, dataset_row.data_format)
        champion_parameters = json.loads(champion_row.parameters_json or "{}") or DEFAULT_STRATEGY_PARAMETERS[job.strategy_id]
        champion_definition = champion_row.definition_json
        config = {}
    try:
        datasets, sectors, regimes, quality = load_dataset(*dataset_meta)
        days = sorted({bar.timestamp.date() for bars in datasets.values() for bar in bars})
        splits = walk_forward_splits(days)
        if not splits:
            raise DatasetValidationError("至少需要160個交易日才能執行Walk-Forward")
        candidates = bounded_parameter_candidates(champion_parameters, PARAMETER_BOUNDS[job.strategy_id])
        validation_ranges = [fold["validation"] for fold in splits]
        scored = []
        for index, parameters in enumerate(candidates):
            trades = _run_ranges(datasets, validation_ranges, job.strategy_id, parameters, config, sectors, regimes)
            summary = _summary(trades, sectors, datasets)
            scored.append((dec(summary["netPnl"]), dec(summary.get("profitFactor") or 0), index, parameters, summary))
            _persist_validation_trial(job_id, index, parameters, summary)
            if index % 5 == 0:
                with SessionLocal() as db:
                    active = db.get(DayTradeV2OptimizationJob, job_id)
                    if active:
                        active.progress_pct = min(Decimal("70"), Decimal(index + 1) / len(candidates) * 70)
                        db.commit()
        _, _, selected_index, selected, validation_summary = max(scored, key=lambda row: (row[0], row[1]))
        oos_ranges = [fold["oos"] for fold in splits]
        candidate_trades = _run_ranges(datasets, oos_ranges, job.strategy_id, selected, config, sectors, regimes)
        champion_trades = _run_ranges(datasets, oos_ranges, job.strategy_id, champion_parameters, config, sectors, regimes)
        candidate_summary = _summary(candidate_trades, sectors, datasets)
        champion_summary = _summary(champion_trades, sectors, datasets)
        neighbor_stable = True
        for key, value in selected.items():
            if not isinstance(value, (int, float, Decimal, str)):
                continue
            for factor in (Decimal("0.95"), Decimal("1.05")):
                neighbor = dict(selected)
                low, high = map(dec, PARAMETER_BOUNDS[job.strategy_id][key])
                neighbor[key] = max(low, min(high, dec(value) * factor))
                neighbor_summary = _summary(_run_ranges(datasets, oos_ranges, job.strategy_id, neighbor, config, sectors, regimes), sectors, datasets)
                if dec(neighbor_summary["netPnl"]) <= 0 or dec(neighbor_summary.get("profitFactor") or 0) < Decimal("1.1") or dec(neighbor_summary["maxDrawdown"]) > dec(champion_summary["maxDrawdown"]) * Decimal("1.1"):
                    neighbor_stable = False
                    break
            if not neighbor_stable:
                break
        candidate_summary["neighborStable"] = neighbor_stable
        passed, failures = candidate_passes(candidate_summary, champion_summary)
        with SessionLocal() as db:
            job = db.get(DayTradeV2OptimizationJob, job_id)
            if not job:
                return
            result = {
                "parameters": {key: str(value) for key, value in selected.items()},
                "validation": validation_summary, "outOfSample": candidate_summary,
                "championOutOfSample": champion_summary, "walkForwardFolds": splits,
                "quality": quality, "passed": passed, "failures": failures,
            }
            job.result_json = json.dumps(result, default=str, ensure_ascii=False)
            job.progress_pct = Decimal("100")
            job.status = "CHALLENGER_SIMULATION" if passed else "REJECTED"
            job.completed_at = datetime.now(UTC)
            trial = db.scalar(select(DayTradeV2OptimizationTrial).where(
                DayTradeV2OptimizationTrial.job_id == job_id,
                DayTradeV2OptimizationTrial.candidate_index == selected_index,
            ))
            if trial:
                trial.selected = True
                trial.passed = passed
                trial.status = "CHALLENGER_SIMULATION" if passed else "REJECTED"
                trial.oos_metrics_json = json.dumps(candidate_summary, default=str, ensure_ascii=False)
                trial.failures_json = json.dumps(failures, ensure_ascii=False)
            if passed:
                version = DayTradeV2StrategyVersion(
                    strategy_id=job.strategy_id, version=job.candidate_version,
                    parent_version=job.champion_version,
                    definition_json=champion_definition,
                    parameters_json=json.dumps(result["parameters"], ensure_ascii=False),
                    change_reason="績效警戒或使用者要求的受限參數優化",
                    data_period_json=json.dumps({"startDate": quality["startDate"], "endDate": quality["endDate"], "folds": splits}, default=str),
                    backtest_result_json=json.dumps(validation_summary, default=str),
                    oos_result_json=json.dumps(candidate_summary, default=str),
                    checksum=parameter_checksum(result["parameters"]), validation_status="CHALLENGER",
                )
                db.add(version)
                deployment = DayTradeV2StrategyDeployment(
                    id=str(uuid4()), user_id=job.user_id, strategy_id=job.strategy_id,
                    version=job.candidate_version, role="CHALLENGER", status="RUNNING",
                )
                run = DayTradeV2ChallengerRun(
                    id=str(uuid4()), user_id=job.user_id, strategy_id=job.strategy_id,
                    champion_version=job.champion_version, challenger_version=job.candidate_version,
                    champion_metrics_json=json.dumps(champion_summary, default=str),
                    challenger_metrics_json="{}",
                )
                db.add_all([deployment, run])
                db.add(DayTradeV2Notification(
                    user_id=job.user_id, event_id=f"challenger-started:{run.id}", mode="PAPER",
                    event_type="CHALLENGER_STARTED", title="【挑戰者策略開始模擬】",
                    message=f"{job.strategy_id} {job.candidate_version} 已通過Walk-Forward，僅進入平行模擬，不會送出真實委託。",
                    payload_json=json.dumps({"runId": run.id, "version": job.candidate_version}),
                ))
            db.commit()
    except Exception as exc:
        with SessionLocal() as db:
            job = db.get(DayTradeV2OptimizationJob, job_id)
            if job:
                job.status = "FAILED"
                job.error_message = f"{type(exc).__name__}: {str(exc)[:1000]}"
                job.completed_at = datetime.now(UTC)
                db.commit()


def claim_next_optimization_job() -> str | None:
    with SessionLocal() as db:
        job = db.scalar(select(DayTradeV2OptimizationJob).where(
            DayTradeV2OptimizationJob.status == "QUEUED",
        ).order_by(DayTradeV2OptimizationJob.created_at).with_for_update(skip_locked=True))
        if not job:
            return None
        job.status = "RUNNING"
        job.progress_pct = Decimal("1")
        db.commit()
        return job.id


def process_next_optimization_job() -> None:
    job_id = claim_next_optimization_job()
    if job_id:
        execute_optimization_job(job_id)
