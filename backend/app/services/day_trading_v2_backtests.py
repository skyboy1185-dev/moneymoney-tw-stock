"""Automatic, minute-only data preparation for day-trading V2 backtests."""

from __future__ import annotations

import asyncio
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
import json
import re
import time as monotonic_time
from typing import Iterable, Mapping, Sequence
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import or_, select

from ..config import get_settings
from ..database import SessionLocal
from ..day_trading_v2_models import (
    DayTradeV2AuditEvent,
    DayTradeV2BacktestJob,
    DayTradeV2OptimizationDataset,
)
from .day_trading_v2 import BACKTEST_ENGINE_VERSION, MinuteBar, STRATEGIES, run_backtest
from .day_trading_v2_datasets import (
    DatasetValidationError,
    load_dataset,
    parse_dataset,
    persist_dataset,
    quality_json,
)
from .popular_stock_universe import OfficialPopularStockProvider
from .theme_stock_universe import ELECTRONIC_ALERT_STOCKS
from .fugle_request_budget import FugleBudgetUnavailable, FugleRequestBudget, get_fugle_request_budget


TAIPEI = ZoneInfo("Asia/Taipei")
MINUTE_DATA_START = date(2023, 5, 23)
WARMUP_TRADING_DAYS = 20
TERMINAL_STATUSES = {"COMPLETED", "DATA_INSUFFICIENT", "FAILED", "CANCELLED"}


class BacktestPreparationError(RuntimeError):
    def __init__(self, code: str, message: str, *, status: str = "DATA_INSUFFICIENT") -> None:
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class HistoricalMinuteCandle:
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    average: Decimal


def _chunks(start: date, end: date, days: int = 180) -> Iterable[tuple[date, date]]:
    cursor = start
    while cursor <= end:
        chunk_end = min(end, cursor + timedelta(days=days - 1))
        yield cursor, chunk_end
        cursor = chunk_end + timedelta(days=1)


class FugleHistoricalMinuteClient:
    """Small provider adapter with deterministic parsing and rate limiting."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str,
        timeout_seconds: float = 30,
        minimum_interval_seconds: float = 1.05,
        transport: httpx.AsyncBaseTransport | None = None,
        budget: FugleRequestBudget | None = None,
    ) -> None:
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.minimum_interval_seconds = max(0, minimum_interval_seconds)
        self.transport = transport
        self._budget = budget or (FugleRequestBudget(self.api_key) if transport is not None else get_fugle_request_budget(self.api_key))
        self._last_request_at = 0.0

    async def _wait_for_slot(self) -> None:
        try:
            await self._budget.acquire(priority="metadata")
        except FugleBudgetUnavailable as exc:
            raise BacktestPreparationError("FUGLE_BUDGET_UNAVAILABLE", "行情共用額度服務暫時無法使用，請稍後重試", status="FAILED") from exc
        elapsed = monotonic_time.monotonic() - self._last_request_at
        if elapsed < self.minimum_interval_seconds:
            await asyncio.sleep(self.minimum_interval_seconds - elapsed)
        self._last_request_at = monotonic_time.monotonic()

    async def fetch(
        self, symbol: str, start: date, end: date, *, is_index: bool = False,
    ) -> list[HistoricalMinuteCandle]:
        if not self.api_key:
            raise BacktestPreparationError(
                "FUGLE_API_KEY_MISSING", "尚未設定 FUGLE_MARKETDATA_API_KEY，請改用CSV／Parquet上傳。", status="FAILED",
            )
        rows: dict[datetime, HistoricalMinuteCandle] = {}
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            headers={"X-API-KEY": self.api_key, "User-Agent": "Moneymoney-DTV2-Backtest/1.0"},
            transport=self.transport,
        ) as client:
            for chunk_start, chunk_end in _chunks(start, end):
                response: httpx.Response | None = None
                for attempt in range(4):
                    await self._wait_for_slot()
                    current_response = await client.get(
                        f"{self.base_url}/stock/historical/candles/{symbol}",
                        params={
                            "timeframe": "1", "from": chunk_start.isoformat(), "to": chunk_end.isoformat(),
                            "fields": "open,high,low,close,volume,average", "sort": "asc",
                        },
                    )
                    response = current_response
                    await self._budget.observe_response(current_response.status_code, current_response.headers)
                    if current_response.status_code != 429:
                        break
                    retry_after = current_response.headers.get("retry-after", "")
                    try:
                        delay = max(float(retry_after), float(2 ** attempt))
                    except ValueError:
                        delay = float(2 ** attempt)
                    await asyncio.sleep(min(delay, 30))
                assert response is not None
                if response.status_code == 404:
                    continue
                if response.status_code in {401, 403}:
                    raise BacktestPreparationError(
                        "FUGLE_AUTH_FAILED",
                        "Fugle金鑰無效或目前方案不支援歷史1分鐘行情，請改用CSV／Parquet上傳。",
                        status="FAILED",
                    )
                if response.status_code == 429:
                    raise BacktestPreparationError("FUGLE_RATE_LIMIT", "Fugle請求次數已達上限，稍後可重新執行。", status="FAILED")
                try:
                    response.raise_for_status()
                    payload = response.json()
                except (httpx.HTTPError, ValueError) as exc:
                    raise BacktestPreparationError("FUGLE_REQUEST_FAILED", f"Fugle歷史行情讀取失敗：{str(exc)[:160]}", status="FAILED") from exc
                for raw in payload.get("data", []):
                    try:
                        timestamp = datetime.fromisoformat(str(raw["date"]).replace("Z", "+00:00"))
                        if timestamp.tzinfo is None:
                            raise ValueError("missing timezone")
                        timestamp = timestamp.astimezone(TAIPEI)
                        if not time(9, 0) <= timestamp.time() <= time(13, 30):
                            continue
                        close = Decimal(str(raw["close"]))
                        volume = int(Decimal(str(raw.get("volume") or 0)))
                        # Fugle minute volume for listed/OTC equities is reported in lots.
                        if not is_index:
                            volume *= 1000
                        item = HistoricalMinuteCandle(
                            timestamp=timestamp,
                            open=Decimal(str(raw["open"])), high=Decimal(str(raw["high"])),
                            low=Decimal(str(raw["low"])), close=close, volume=volume,
                            average=Decimal(str(raw.get("average") or close)),
                        )
                    except (ArithmeticError, KeyError, TypeError, ValueError) as exc:
                        raise BacktestPreparationError("FUGLE_INVALID_DATA", f"{symbol}分鐘行情格式錯誤：{str(exc)[:120]}") from exc
                    if min(item.open, item.high, item.low, item.close) <= 0 or item.high < item.low or item.volume < 0:
                        raise BacktestPreparationError("FUGLE_INVALID_DATA", f"{symbol}包含無效的分鐘OHLCV")
                    rows[item.timestamp] = item
        return [rows[key] for key in sorted(rows)]

    async def resolve_weighted_index_symbol(self) -> str:
        """Resolve the TWSE capitalization-weighted index without guessing its code."""
        if not self.api_key:
            return ""
        await self._wait_for_slot()
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            headers={"X-API-KEY": self.api_key, "User-Agent": "Moneymoney-DTV2-Backtest/1.0"},
            transport=self.transport,
        ) as client:
            response = await client.get(
                f"{self.base_url}/stock/intraday/tickers",
                params={"type": "INDEX", "exchange": "TWSE"},
            )
            await self._budget.observe_response(response.status_code, response.headers)
        if response.status_code in {401, 403}:
            raise BacktestPreparationError(
                "FUGLE_AUTH_FAILED", "Fugle金鑰無效或目前方案不支援指數行情。", status="FAILED",
            )
        try:
            response.raise_for_status()
            data = response.json().get("data", [])
        except (httpx.HTTPError, ValueError, AttributeError):
            return ""
        exact_names = {"發行量加權股價指數", "臺灣證券交易所發行量加權股價指數"}
        exact = [str(item.get("symbol") or "") for item in data if isinstance(item, dict) and str(item.get("name") or "") in exact_names]
        if len(exact) == 1:
            return exact[0]
        matches = [
            str(item.get("symbol") or "") for item in data
            if isinstance(item, dict) and "發行量加權" in str(item.get("name") or "")
        ]
        return matches[0] if len(matches) == 1 else ""


def _previous_close_by_day(
    datasets: Mapping[str, Sequence[HistoricalMinuteCandle]],
) -> dict[tuple[str, date], Decimal]:
    result: dict[tuple[str, date], Decimal] = {}
    for symbol, bars in datasets.items():
        closes: dict[date, Decimal] = {}
        for bar in bars:
            closes[bar.timestamp.date()] = bar.close
        days = sorted(closes)
        for index in range(1, len(days)):
            result[(symbol, days[index])] = closes[days[index - 1]]
    return result


def _cumulative_market_volume(
    datasets: Mapping[str, Sequence[HistoricalMinuteCandle]],
) -> dict[date, dict[time, int]]:
    minute_totals: dict[date, dict[time, int]] = defaultdict(lambda: defaultdict(int))
    for bars in datasets.values():
        for bar in bars:
            minute_totals[bar.timestamp.date()][bar.timestamp.time().replace(tzinfo=None)] += bar.volume
    result: dict[date, dict[time, int]] = {}
    for day, values in minute_totals.items():
        total = 0
        result[day] = {}
        for minute in sorted(values):
            total += values[minute]
            result[day][minute] = total
    return result


def build_market_context_rows(
    datasets: Mapping[str, Sequence[HistoricalMinuteCandle]],
    index_bars: Sequence[HistoricalMinuteCandle],
    sector_by_symbol: Mapping[str, str],
    *,
    requested_start: date,
    requested_end: date,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Build causal market fields from the frozen universe and index minute series."""
    symbols = sorted(datasets)
    if not symbols:
        raise BacktestPreparationError("NO_STOCK_DATA", "指定股票池沒有可用的1分鐘行情。")
    bars_at: dict[datetime, dict[str, HistoricalMinuteCandle]] = defaultdict(dict)
    days_by_symbol: dict[str, set[date]] = defaultdict(set)
    for symbol, bars in datasets.items():
        for bar in bars:
            bars_at[bar.timestamp][symbol] = bar
            days_by_symbol[symbol].add(bar.timestamp.date())
    index_by_day: dict[date, list[HistoricalMinuteCandle]] = defaultdict(list)
    for bar in index_bars:
        index_by_day[bar.timestamp.date()].append(bar)
    for values in index_by_day.values():
        values.sort(key=lambda item: item.timestamp)
    previous_close = _previous_close_by_day(datasets)
    cumulative_volume = _cumulative_market_volume(datasets)
    all_days = sorted(cumulative_volume)
    rows: list[dict[str, object]] = []
    included_days: set[date] = set()
    daily_coverages: list[Decimal] = []
    for day in (item for item in all_days if requested_start <= item <= requested_end):
        prior_days = [item for item in all_days if item < day][-WARMUP_TRADING_DAYS:]
        if len(prior_days) < WARMUP_TRADING_DAYS:
            continue
        index_values = index_by_day.get(day, [])
        if not index_values:
            continue
        available_symbols = sum(day in days_by_symbol[symbol] for symbol in symbols)
        daily_coverages.append(Decimal(available_symbols) * Decimal(100) / Decimal(len(symbols)))
        index_timestamps = [item.timestamp for item in index_values]
        day_timestamps = sorted(value for value in bars_at if value.date() == day)
        for timestamp in day_timestamps:
            index_position = bisect_right(index_timestamps, timestamp) - 1
            if index_position < 0:
                continue
            index_bar = index_values[index_position]
            previous_1 = index_values[max(0, index_position - 1)].close
            previous_5 = index_values[max(0, index_position - 5)].close
            trend_1 = (index_bar.close / previous_1 - 1) * 100 if previous_1 else Decimal(0)
            trend_5 = (index_bar.close / previous_5 - 1) * 100 if previous_5 else Decimal(0)
            current = bars_at[timestamp]
            returns: dict[str, Decimal] = {}
            for symbol, bar in current.items():
                prior_close = previous_close.get((symbol, day))
                if prior_close:
                    returns[symbol] = (bar.close / prior_close - 1) * 100
            breadth = Decimal(50)
            if returns:
                breadth = Decimal(sum(value > 0 for value in returns.values())) * Decimal(100) / Decimal(len(returns))
            minute = timestamp.time().replace(tzinfo=None)
            current_cumulative = cumulative_volume[day].get(minute, 0)
            baselines = [
                cumulative_volume[prior].get(minute, 0)
                for prior in prior_days if cumulative_volume[prior].get(minute, 0) > 0
            ]
            baseline = Decimal(sum(baselines)) / Decimal(len(baselines)) if baselines else Decimal(0)
            relative_volume = Decimal(current_cumulative) / baseline if baseline else Decimal(1)
            coverage = Decimal(len(current)) * Decimal(100) / Decimal(len(symbols))
            sector_returns: dict[str, list[Decimal]] = defaultdict(list)
            for symbol, value in returns.items():
                sector_returns[sector_by_symbol.get(symbol, "未分類")].append(value)
            sector_averages = [sum(values, Decimal(0)) / Decimal(len(values)) for values in sector_returns.values()]
            strong_sectors = sum(value >= Decimal("0.5") for value in sector_averages)
            weak_sectors = sum(value <= Decimal("-0.5") for value in sector_averages)
            for symbol, bar in current.items():
                rows.append({
                    "symbol": symbol, "timestamp": timestamp.isoformat(),
                    "open": bar.open, "high": bar.high, "low": bar.low, "close": bar.close,
                    "volume": bar.volume, "sector": sector_by_symbol.get(symbol, "未分類"),
                    "index_price": index_bar.close, "index_vwap": index_bar.average,
                    "index_trend_1m_pct": trend_1, "index_trend_5m_pct": trend_5,
                    "market_breadth_pct": breadth, "market_relative_volume": relative_volume,
                    "quote_coverage_pct": coverage, "strong_sector_count": strong_sectors,
                    "weak_sector_count": weak_sectors,
                })
            included_days.add(day)
    if not rows:
        raise BacktestPreparationError(
            "WARMUP_OR_INDEX_DATA_MISSING",
            "找不到具備20個交易日暖機資料與加權指數分鐘行情的回測區間。",
        )
    average_daily_coverage = sum(daily_coverages, Decimal(0)) / Decimal(len(daily_coverages)) if daily_coverages else Decimal(0)
    quality = {
        "warmupTradingDays": WARMUP_TRADING_DAYS,
        "averageDailySymbolCoveragePct": str(average_daily_coverage.quantize(Decimal("0.01"))),
        "includedTradingDays": len(included_days),
    }
    if average_daily_coverage < Decimal(95):
        raise BacktestPreparationError(
            "MINUTE_COVERAGE_TOO_LOW",
            f"分鐘資料每日股票覆蓋率僅{quality['averageDailySymbolCoveragePct']}%，低於95%門檻。",
        )
    return rows, quality


def execute_backtest(
    datasets: Mapping[str, Sequence[MinuteBar]], sectors: Mapping[str, str], regimes: Mapping[datetime, str],
    *, backtest_mode: str, strategy_id: str,
) -> dict[str, object]:
    if backtest_mode == "INDIVIDUAL" and strategy_id == "ALL":
        return {
            "engineVersion": BACKTEST_ENGINE_VERSION,
            "validationStatus": "VALIDATED",
            "individual": {
                strategy: run_backtest(
                    datasets, strategy_id=strategy, portfolio=False, sector_by_symbol=sectors,
                    market_regime_by_time=regimes,
                )
                for strategy, _, _ in STRATEGIES
            },
            "message": "各機器人分別使用獨立3,000,000元；結果不可直接加總。",
        }
    return run_backtest(
        datasets, strategy_id=strategy_id, portfolio=backtest_mode == "PORTFOLIO",
        sector_by_symbol=sectors, market_regime_by_time=regimes,
    )


def _progress(job_id: str, status: str, percent: Decimal, details: Mapping[str, object]) -> None:
    with SessionLocal() as db:
        row = db.get(DayTradeV2BacktestJob, job_id)
        if row is None or row.status in TERMINAL_STATUSES:
            return
        row.status = status
        row.progress_pct = percent
        row.progress_json = json.dumps(dict(details), ensure_ascii=False, default=str)
        row.lease_until = datetime.now(UTC) + timedelta(minutes=30 if status == "RUNNING" else 5)
        db.commit()


def _complete_backtest_job(job_id: str, result: Mapping[str, object], *, dataset_id: str) -> None:
    with SessionLocal() as db:
        job = db.get(DayTradeV2BacktestJob, job_id)
        if job is None:
            return
        job.status = "COMPLETED"
        job.progress_pct = Decimal(100)
        job.progress_json = json.dumps({
            "stage": "COMPLETED", "message": "回測完成",
            "engineVersion": BACKTEST_ENGINE_VERSION,
        }, ensure_ascii=False)
        job.result_json = json.dumps(dict(result), ensure_ascii=False, default=str)
        job.completed_at = datetime.now(UTC)
        job.lease_owner = ""
        job.lease_until = None
        db.add(DayTradeV2AuditEvent(
            user_id=job.user_id, action="BACKTEST_COMPLETED", mode="BACKTEST",
            entity_type="BACKTEST_JOB", entity_id=job.id,
            details_json=json.dumps({
                "dataSource": job.data_source, "datasetId": dataset_id,
                "engineVersion": BACKTEST_ENGINE_VERSION,
            }, ensure_ascii=False),
        ))
        db.commit()


async def _resolve_universe(request: Mapping[str, object]) -> list[dict[str, str]]:
    raw_symbols = request.get("symbols") or []
    settings = get_settings()
    if raw_symbols:
        unique: list[str] = []
        for raw in raw_symbols if isinstance(raw_symbols, list) else []:
            symbol = str(raw).strip()
            if not re.fullmatch(r"[1-9]\d{3}", symbol):
                raise BacktestPreparationError("INVALID_SYMBOL", f"股票代碼格式錯誤：{symbol}", status="FAILED")
            if symbol not in unique:
                unique.append(symbol)
        if not unique:
            raise BacktestPreparationError("EMPTY_UNIVERSE", "股票池不可為空。", status="FAILED")
        if len(unique) > settings.dtv2_backtest_max_symbols:
            raise BacktestPreparationError(
                "UNIVERSE_TOO_LARGE", f"自訂股票池最多{settings.dtv2_backtest_max_symbols}檔。", status="FAILED",
            )
        return [{"symbol": symbol, "name": symbol, "sector": "自訂股票池", "market": ""} for symbol in unique]
    stocks = await OfficialPopularStockProvider(include_financial=True).fetch()
    if not stocks:
        raise BacktestPreparationError("UNIVERSE_UNAVAILABLE", "無法取得上市、上櫃高流動性股票池。", status="FAILED")
    known_sectors = {stock.symbol: stock.industry for stock in ELECTRONIC_ALERT_STOCKS}
    return [
        {
            "symbol": stock.symbol, "name": stock.name,
            "sector": known_sectors.get(stock.symbol) or (stock.industry if stock.industry != "市場熱門" else "未分類"),
            "market": stock.market,
        }
        for stock in stocks[: settings.dtv2_backtest_universe_size]
    ]


async def _prepare_and_run(job_id: str) -> None:
    settings = get_settings()
    with SessionLocal() as db:
        job = db.get(DayTradeV2BacktestJob, job_id)
        if job is None:
            return
        request = json.loads(job.request_json)
        requested_start, requested_end = job.start_date, job.end_date
        existing_dataset_id = job.dataset_id
        stored_universe = json.loads(job.universe_json or "[]")
        user_id = job.user_id
    if existing_dataset_id:
        with SessionLocal() as db:
            dataset = db.get(DayTradeV2OptimizationDataset, existing_dataset_id)
            if dataset is None or dataset.user_id != user_id:
                raise BacktestPreparationError(
                    "RECOVERY_DATASET_MISSING", "回測暫存分鐘資料不存在，無法安全恢復。", status="FAILED",
                )
            datasets, sectors, regimes, quality = load_dataset(
                dataset.storage_path, dataset.checksum, dataset.data_format,
            )
            quality.update(json.loads(dataset.quality_json or "{}"))
        _progress(job_id, "RUNNING", Decimal(85), {
            "stage": "RUNNING", "message": "沿用已驗證的分鐘資料恢復回測",
            "datasetId": existing_dataset_id, "engineVersion": BACKTEST_ENGINE_VERSION,
        })
        result = execute_backtest(
            datasets, sectors, regimes,
            backtest_mode=str(request.get("backtest_mode") or "PORTFOLIO"),
            strategy_id=str(request.get("strategy_id") or "ALL"),
        )
        result["dataQuality"] = quality
        result["universeNotice"] = f"回測使用任務建立時凍結的 {len(stored_universe)} 檔股票池。"
        result["recoveredFromStaleJob"] = True
        _complete_backtest_job(job_id, result, dataset_id=existing_dataset_id)
        return
    universe = await _resolve_universe(request)
    with SessionLocal() as db:
        job = db.get(DayTradeV2BacktestJob, job_id)
        if job is None:
            return
        job.universe_json = json.dumps(universe, ensure_ascii=False)
        db.commit()
    warmup_start = max(MINUTE_DATA_START, requested_start - timedelta(days=45))
    client = FugleHistoricalMinuteClient(
        settings.fugle_marketdata_api_key,
        base_url=settings.fugle_marketdata_base_url,
        timeout_seconds=settings.fugle_historical_timeout_seconds,
        minimum_interval_seconds=settings.fugle_historical_min_request_interval_seconds,
    )
    stock_data: dict[str, list[HistoricalMinuteCandle]] = {}
    failed: list[str] = []
    total = len(universe) + 1
    for index, stock in enumerate(universe, start=1):
        symbol = stock["symbol"]
        try:
            bars = await client.fetch(symbol, warmup_start, requested_end)
        except BacktestPreparationError as exc:
            if exc.code in {"FUGLE_AUTH_FAILED", "FUGLE_API_KEY_MISSING", "FUGLE_RATE_LIMIT"}:
                raise
            bars = []
        if bars:
            stock_data[symbol] = bars
        else:
            failed.append(symbol)
        _progress(job_id, "DOWNLOADING", Decimal(index * 55) / Decimal(total), {
            "stage": "DOWNLOADING", "totalSymbols": len(universe), "completedSymbols": index,
            "failedSymbols": failed, "message": f"正在下載 {symbol} 的1分鐘行情",
        })
    if Decimal(len(stock_data)) / Decimal(len(universe)) < Decimal("0.95"):
        raise BacktestPreparationError(
            "MINUTE_SYMBOL_COVERAGE_TOO_LOW",
            f"只有{len(stock_data)}/{len(universe)}檔取得分鐘行情，低於95%資料品質門檻。",
        )
    index_symbol = settings.fugle_market_index_symbol.strip()
    if not index_symbol:
        raise BacktestPreparationError("MARKET_INDEX_NOT_CONFIGURED", "尚未設定FUGLE_MARKET_INDEX_SYMBOL。", status="FAILED")
    index_data = await client.fetch(index_symbol, warmup_start, requested_end, is_index=True)
    if not index_data:
        resolved_symbol = await client.resolve_weighted_index_symbol()
        if resolved_symbol and resolved_symbol != index_symbol:
            index_symbol = resolved_symbol
            index_data = await client.fetch(index_symbol, warmup_start, requested_end, is_index=True)
    if not index_data:
        raise BacktestPreparationError(
            "MARKET_INDEX_DATA_MISSING", f"找不到加權指數 {index_symbol} 的1分鐘行情，已停止回測。",
        )
    _progress(job_id, "VALIDATING", Decimal(65), {"stage": "VALIDATING", "message": "正在建立因果市場脈絡並檢查資料品質"})
    sectors = {item["symbol"]: item["sector"] for item in universe if item["symbol"] in stock_data}
    rows, auto_quality = build_market_context_rows(
        stock_data, index_data, sectors, requested_start=requested_start, requested_end=requested_end,
    )
    import pyarrow as pa  # type: ignore[import-not-found]
    import pyarrow.parquet as pq  # type: ignore[import-not-found]
    buffer = BytesIO()
    pq.write_table(pa.Table.from_pylist(rows), buffer, compression="zstd")
    content = buffer.getvalue()
    datasets, parsed_sectors, regimes, quality = parse_dataset(content, "PARQUET")
    quality.update(auto_quality)
    quality.update({
        "source": "FUGLE_AUTO", "provider": "Fugle Historical Candles",
        "timeframe": "1_MINUTE", "requestedStartDate": requested_start.isoformat(),
        "requestedEndDate": requested_end.isoformat(), "universeSnapshotAt": datetime.now(UTC).isoformat(),
        "universeType": str(request.get("universe_preset") or "TOP_LIQUID_100"),
        "indexSymbol": index_symbol, "failedSymbols": failed,
    })
    content_checksum = sha256(content).hexdigest()
    with SessionLocal() as db:
        job = db.get(DayTradeV2BacktestJob, job_id)
        if job is None:
            return
        dataset = db.scalar(select(DayTradeV2OptimizationDataset).where(
            DayTradeV2OptimizationDataset.user_id == job.user_id,
            DayTradeV2OptimizationDataset.checksum == content_checksum,
        ))
        if dataset is not None:
            try:
                load_dataset(dataset.storage_path, dataset.checksum, dataset.data_format)
            except (DatasetValidationError, OSError):
                dataset = None
        if dataset is None:
            dataset_id = str(uuid4())
            path, checksum = persist_dataset(content, dataset_id=dataset_id, data_format="PARQUET")
            dataset = DayTradeV2OptimizationDataset(
                id=dataset_id, user_id=job.user_id,
                name=f"Fugle自動分鐘資料 {requested_start.isoformat()}～{requested_end.isoformat()}",
                storage_path=str(path), checksum=checksum, data_format="PARQUET",
                start_date=date.fromisoformat(str(quality["startDate"])), end_date=date.fromisoformat(str(quality["endDate"])),
                trading_day_count=int(quality["tradingDayCount"]), symbol_count=int(quality["symbolCount"]),
                row_count=int(quality["rowCount"]),
                quality_status="READY" if int(quality["tradingDayCount"]) >= 160 else "BACKTEST_READY",
                quality_json=quality_json(quality),
            )
            db.add(dataset)
        dataset_id = dataset.id
        job.dataset_id = dataset_id
        job.data_source = f"FUGLE_AUTO:{dataset_id}"
        job.data_precision = "1_MINUTE"
        db.commit()
    _progress(job_id, "RUNNING", Decimal(85), {
        "stage": "RUNNING", "rowCount": quality["rowCount"], "tradingDayCount": quality["tradingDayCount"],
        "message": "分鐘資料驗證完成，正在執行策略回測",
    })
    result = execute_backtest(
        datasets, parsed_sectors, regimes,
        backtest_mode=str(request.get("backtest_mode") or "PORTFOLIO"),
        strategy_id=str(request.get("strategy_id") or "ALL"),
    )
    result["dataQuality"] = quality
    result["universeNotice"] = (
        f"固定股票池研究回測；名單凍結於任務建立時，共{len(universe)}檔，"
        "不代表無存活偏差的歷史全市場回測。"
    )
    with SessionLocal() as db:
        job = db.get(DayTradeV2BacktestJob, job_id)
        if job is None:
            return
        job.status = "COMPLETED"
        job.progress_pct = Decimal(100)
        job.progress_json = json.dumps({"stage": "COMPLETED", "message": "回測完成"}, ensure_ascii=False)
        job.result_json = json.dumps(result, ensure_ascii=False, default=str)
        job.completed_at = datetime.now(UTC)
        job.lease_owner = ""
        job.lease_until = None
        db.add(DayTradeV2AuditEvent(
            user_id=job.user_id, action="BACKTEST_COMPLETED", mode="BACKTEST",
            entity_type="BACKTEST_JOB", entity_id=job.id,
            details_json=json.dumps({"dataSource": job.data_source, "datasetId": dataset_id}, ensure_ascii=False),
        ))
        db.commit()


def process_next_backtest_job() -> str | None:
    """Claim and process one queued job. Safe to call repeatedly from the coordinator."""
    now = datetime.now(UTC)
    worker_id = f"backtest:{uuid4()}"
    with SessionLocal() as db:
        job = db.scalar(select(DayTradeV2BacktestJob).where(
            DayTradeV2BacktestJob.data_source.like("FUGLE_AUTO%"),
            or_(
                DayTradeV2BacktestJob.status == "QUEUED",
                (
                    DayTradeV2BacktestJob.status.in_(("DOWNLOADING", "VALIDATING", "RUNNING"))
                    & or_(DayTradeV2BacktestJob.lease_until.is_(None), DayTradeV2BacktestJob.lease_until < now)
                ),
            ),
        ).order_by(DayTradeV2BacktestJob.created_at.asc()))
        if job is None:
            return None
        if job.attempts >= 3:
            job.status = "FAILED"
            job.error_message = "背景服務重啟後重試3次仍未完成，請重新建立回測任務。"
            job.completed_at = now
            db.commit()
            return job.id
        job.status = "DOWNLOADING"
        job.progress_pct = Decimal(1)
        job.progress_json = json.dumps({"stage": "DOWNLOADING", "message": "正在建立固定股票池"}, ensure_ascii=False)
        job.lease_owner = worker_id
        job.lease_until = now + timedelta(minutes=5)
        job.attempts += 1
        job_id = job.id
        db.commit()
    try:
        asyncio.run(_prepare_and_run(job_id))
    except BacktestPreparationError as exc:
        with SessionLocal() as db:
            job = db.get(DayTradeV2BacktestJob, job_id)
            if job:
                job.status = exc.status
                job.error_message = str(exc)
                job.result_json = json.dumps({"code": exc.code, "message": str(exc), "summary": None, "trades": []}, ensure_ascii=False)
                job.progress_json = json.dumps({"stage": exc.status, "code": exc.code, "message": str(exc)}, ensure_ascii=False)
                job.completed_at = datetime.now(UTC)
                job.lease_owner = ""
                job.lease_until = None
                db.commit()
    except (DatasetValidationError, httpx.HTTPError, OSError, ValueError) as exc:
        with SessionLocal() as db:
            job = db.get(DayTradeV2BacktestJob, job_id)
            if job:
                job.status = "FAILED"
                job.error_message = str(exc)[:500]
                job.result_json = json.dumps({"code": "BACKTEST_PREPARATION_FAILED", "message": job.error_message}, ensure_ascii=False)
                job.progress_json = json.dumps({"stage": "FAILED", "message": job.error_message}, ensure_ascii=False)
                job.completed_at = datetime.now(UTC)
                job.lease_owner = ""
                job.lease_until = None
                db.commit()
    except Exception as exc:
        with SessionLocal() as db:
            job = db.get(DayTradeV2BacktestJob, job_id)
            if job:
                job.status = "FAILED"
                job.error_message = f"回測背景工作失敗：{str(exc)[:460]}"
                job.result_json = json.dumps({"code": "BACKTEST_WORKER_FAILED", "message": job.error_message}, ensure_ascii=False)
                job.progress_json = json.dumps({"stage": "FAILED", "message": job.error_message}, ensure_ascii=False)
                job.completed_at = datetime.now(UTC)
                job.lease_owner = ""
                job.lease_until = None
                db.commit()
    return job_id
