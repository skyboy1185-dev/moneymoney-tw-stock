from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
import math
import json
from statistics import mean
from typing import Any, Iterable
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.orm import Session

from ..adaptive_schemas import AdaptiveStockInput
from ..models import LongTermBacktestCache
from .long_term_benchmarks import latest_cagr_selections
from .long_term_selection import MINIMUM_HOLDING_TRADING_DAYS


YAHOO_CHART_URLS = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
    "https://query2.finance.yahoo.com/v8/finance/chart/{ticker}",
)
BACKTEST_UNIVERSE_LIMIT = 300
BACKTEST_CACHE_SECONDS = 6 * 60 * 60
BACKTEST_VERSION = "v4"
STABLE_ROTATION_SIZE = 50
STABLE_ROTATION_PROTECTED_RANK = 20
STABLE_ROTATION_SCORE_GAP = 5.0
STABLE_ROTATION_MAX_WEEKLY_REPLACEMENTS = 2
STABLE_ROTATION_HOLDING_VARIANTS = (5, 10, 20, 40)
COMMISSION_RATE = 0.001425
COMMISSION_DISCOUNT = 0.6
STOCK_TAX_RATE = 0.003
SLIPPAGE_RATE = 0.001
TAIPEI = ZoneInfo("Asia/Taipei")


@dataclass(frozen=True, slots=True)
class HistoricalBar:
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True, slots=True)
class BacktestSecurity:
    symbol: str
    name: str
    market: str
    bars: tuple[HistoricalBar, ...]


@dataclass(slots=True)
class SimulatedPosition:
    shares: float
    entry_session: int


_cache: tuple[datetime, str, dict[str, object]] | None = None
_cache_lock = asyncio.Lock()
_history_cache: dict[str, BacktestSecurity] = {}


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 and math.isfinite(parsed) else None


def parse_yahoo_daily_history(payload: dict[str, Any]) -> tuple[HistoricalBar, ...]:
    """Parse adjusted Yahoo OHLC so dividends/splits do not create false signals."""
    results = payload.get("chart", {}).get("result") or []
    if not results:
        return ()
    result = results[0]
    timestamps = result.get("timestamp") or []
    indicators = result.get("indicators") or {}
    quote_groups = indicators.get("quote") or []
    quote_row = quote_groups[0] if quote_groups else {}
    adjusted_groups = indicators.get("adjclose") or []
    adjusted = adjusted_groups[0].get("adjclose") if adjusted_groups else []
    opens = quote_row.get("open") or []
    highs = quote_row.get("high") or []
    lows = quote_row.get("low") or []
    closes = quote_row.get("close") or []
    volumes = quote_row.get("volume") or []
    rows: dict[date, HistoricalBar] = {}
    for index, raw_timestamp in enumerate(timestamps):
        try:
            observed = datetime.fromtimestamp(int(raw_timestamp), UTC).date()
        except (TypeError, ValueError, OSError):
            continue
        raw_close = _number(closes[index] if index < len(closes) else None)
        adjusted_close = _number(adjusted[index] if index < len(adjusted) else None) or raw_close
        raw_open = _number(opens[index] if index < len(opens) else None)
        raw_high = _number(highs[index] if index < len(highs) else None)
        raw_low = _number(lows[index] if index < len(lows) else None)
        if not all((raw_close, adjusted_close, raw_open, raw_high, raw_low)):
            continue
        factor = adjusted_close / raw_close
        volume = volumes[index] if index < len(volumes) else 0
        try:
            parsed_volume = max(0.0, float(volume or 0))
        except (TypeError, ValueError):
            parsed_volume = 0.0
        rows[observed] = HistoricalBar(
            trade_date=observed,
            open=raw_open * factor,
            high=raw_high * factor,
            low=raw_low * factor,
            close=adjusted_close,
            volume=parsed_volume,
        )
    return tuple(rows[key] for key in sorted(rows))


async def _fetch_history(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    symbol: str,
    name: str,
    market: str,
) -> BacktestSecurity | None:
    cached = _history_cache.get(symbol)
    if cached is not None:
        return cached
    suffix = "TWO" if market == "上櫃" else "TW"
    ticker = quote(f"{symbol}.{suffix}", safe="")
    for attempt, url in enumerate(YAHOO_CHART_URLS):
        try:
            async with semaphore:
                response = await client.get(
                    url.format(ticker=ticker),
                    params={"interval": "1d", "range": "2y", "events": "history"},
                )
                response.raise_for_status()
                bars = parse_yahoo_daily_history(response.json())
            if len(bars) >= 100:
                security = BacktestSecurity(symbol, name, market, bars)
                _history_cache[symbol] = security
                return security
        except (httpx.HTTPError, ValueError, TypeError):
            if attempt + 1 < len(YAHOO_CHART_URLS):
                await asyncio.sleep(0.2)
    return None


def _pct(closes: list[float], index: int, sessions: int) -> float:
    if index < sessions or closes[index - sessions] <= 0:
        return 0.0
    return (closes[index] / closes[index - sessions] - 1) * 100


def _average(values: Iterable[float]) -> float:
    rows = list(values)
    return mean(rows) if rows else 0.0


def _candidate_score(
    bars: tuple[HistoricalBar, ...],
    closes: list[float],
    index: int,
    market_return_20d: float,
    universe_return_20d: float,
) -> float | None:
    if index < 65:
        return None
    window20 = bars[index - 19:index + 1]
    volumes = [item.volume for item in window20]
    price = bars[index].close
    average_volume = _average(volumes)
    average_turnover = _average(item.close * item.volume for item in window20)
    if price < 10 or average_volume < 500_000 or average_turnover < 50_000_000:
        return None

    ma20 = _average(closes[index - 19:index + 1])
    ma60 = _average(closes[index - 59:index + 1])
    previous_ma20 = _average(closes[index - 24:index - 4])
    previous_ma60 = _average(closes[index - 64:index - 4])
    return20 = _pct(closes, index, 20)
    return5 = _pct(closes, index, 5)
    previous20_high = max(item.high for item in bars[index - 20:index])
    recent5_low = min(item.low for item in bars[index - 4:index + 1])
    prior5_low = min(item.low for item in bars[index - 9:index - 4])
    above20 = price >= ma20
    above60 = price >= ma60
    positive_slope = ma20 > previous_ma20 and ma60 >= previous_ma60
    higher_low = recent5_low >= prior5_low
    breakout20 = price > previous20_high

    trend = 38 + 14 * above20 + 12 * above60 + 10 * positive_slope + 8 * higher_low + 8 * breakout20
    trend += max(-12, min(18, return20)) * 0.5

    relative_market = return20 - market_return_20d
    relative_universe = return20 - universe_return_20d
    relative = 42 + max(-20, min(25, relative_market)) * 1.1
    relative += max(-20, min(25, relative_universe)) * 0.7
    relative += max(-10, min(12, return5)) * 0.8
    relative += 8 * (bars[index].close > bars[index - 1].close)

    up_volume = _average(
        bars[cursor].volume
        for cursor in range(index - 19, index + 1)
        if bars[cursor].close >= bars[cursor - 1].close
    )
    down_volume = _average(
        bars[cursor].volume
        for cursor in range(index - 19, index + 1)
        if bars[cursor].close < bars[cursor - 1].close
    )
    # Historical fundamentals and institutional holdings are not backfilled here.
    # Keep the same neutral base instead of leaking today's values into the past.
    quality = 40 + (6 if down_volume <= up_volume else 0)
    return round(max(0.0, min(100.0, max(trend, relative, quality))), 4)


def _historical_rankings(
    securities: list[BacktestSecurity],
    benchmark: BacktestSecurity,
    start_date: date,
    last_date: date,
) -> dict[date, list[tuple[str, float]]]:
    indexed = {
        item.symbol: {bar.trade_date: offset for offset, bar in enumerate(item.bars)}
        for item in securities
    }
    closes_by_symbol = {
        item.symbol: [bar.close for bar in item.bars]
        for item in securities
    }
    benchmark_index = {bar.trade_date: offset for offset, bar in enumerate(benchmark.bars)}
    benchmark_closes = [item.close for item in benchmark.bars]
    ranking_by_date: dict[date, list[tuple[str, float]]] = {}
    signal_floor = start_date - timedelta(days=30)
    signal_dates = [
        bar.trade_date for bar in benchmark.bars
        if signal_floor <= bar.trade_date < last_date
    ]
    for signal_date in signal_dates:
        benchmark_cursor = benchmark_index[signal_date]
        market_return = _pct(benchmark_closes, benchmark_cursor, 20)
        raw_returns: list[float] = []
        for security in securities:
            cursor = indexed[security.symbol].get(signal_date)
            if cursor is not None and cursor >= 20:
                raw_returns.append(_pct(closes_by_symbol[security.symbol], cursor, 20))
        universe_return = _average(raw_returns)
        scored: list[tuple[str, float]] = []
        for security in securities:
            cursor = indexed[security.symbol].get(signal_date)
            if cursor is None:
                continue
            score = _candidate_score(
                security.bars,
                closes_by_symbol[security.symbol],
                cursor,
                market_return,
                universe_return,
            )
            if score is not None:
                scored.append((security.symbol, score))
        ranking_by_date[signal_date] = sorted(
            scored,
            key=lambda item: (item[1], item[0]),
            reverse=True,
        )
    return ranking_by_date


def _drawdown(curve: list[float]) -> float:
    peak = curve[0]
    worst = 0.0
    for value in curve:
        peak = max(peak, value)
        worst = min(worst, value / peak - 1)
    return worst * 100


def _is_first_session_of_week(sessions: list[date], index: int) -> bool:
    if index == 0:
        return True
    return sessions[index - 1].isocalendar()[:2] != sessions[index].isocalendar()[:2]


def _simulate_stable_rotation(
    securities: list[BacktestSecurity],
    benchmark: BacktestSecurity,
    start_date: date,
    ranking_by_date: dict[date, list[tuple[str, float]]],
    minimum_holding_days: int,
    end_date: date,
) -> dict[str, object]:
    """Weekly 50-stock rotation with rank protection and a strict replacement hurdle."""
    indexed = {
        item.symbol: ({bar.trade_date: offset for offset, bar in enumerate(item.bars)}, item)
        for item in securities
    }
    benchmark_index = {bar.trade_date: offset for offset, bar in enumerate(benchmark.bars)}
    sessions = [bar.trade_date for bar in benchmark.bars if start_date <= bar.trade_date <= end_date]
    if not sessions:
        raise ValueError("今年尚無可用交易日行情")
    all_benchmark_dates = [bar.trade_date for bar in benchmark.bars]
    buy_cost = COMMISSION_RATE * COMMISSION_DISCOUNT + SLIPPAGE_RATE
    sell_cost = COMMISSION_RATE * COMMISSION_DISCOUNT + STOCK_TAX_RATE + SLIPPAGE_RATE
    positions: dict[str, SimulatedPosition] = {}
    cash = 1.0
    curve = [1.0]
    entries = 0
    exits = 0
    weekly_review_count = 0

    for session_number, session in enumerate(sessions):
        benchmark_cursor = benchmark_index[session]
        previous_date = all_benchmark_dates[benchmark_cursor - 1] if benchmark_cursor > 0 else None
        ranking = ranking_by_date.get(previous_date, []) if previous_date else []

        if _is_first_session_of_week(sessions, session_number) and ranking:
            weekly_review_count += 1
            if not positions:
                initial = [
                    (symbol, score) for symbol, score in ranking
                    if symbol in indexed and indexed[symbol][0].get(session) is not None
                ][:STABLE_ROTATION_SIZE]
                if initial:
                    allocation = cash / len(initial)
                    for symbol, _ in initial:
                        lookup, security = indexed[symbol]
                        cursor = lookup[session]
                        execution_price = security.bars[cursor].open * (1 + buy_cost)
                        positions[symbol] = SimulatedPosition(allocation / execution_price, session_number)
                        entries += 1
                    cash = 0.0
            else:
                rank_by_symbol = {
                    symbol: (rank, score)
                    for rank, (symbol, score) in enumerate(ranking, start=1)
                }
                candidates = [
                    (symbol, score) for symbol, score in ranking
                    if symbol not in positions
                    and symbol in indexed
                    and indexed[symbol][0].get(session) is not None
                ]
                eligible = []
                for symbol, position in positions.items():
                    held_sessions = session_number - position.entry_session
                    rank, score = rank_by_symbol.get(symbol, (10**9, -10**9))
                    if held_sessions < minimum_holding_days or rank <= STABLE_ROTATION_PROTECTED_RANK:
                        continue
                    eligible.append((symbol, score, rank))
                eligible.sort(key=lambda item: (item[1], -item[2], item[0]))

                replacements: list[tuple[str, str]] = []
                used_candidates: set[str] = set()
                for held_symbol, held_score, _ in eligible:
                    replacement = next((
                        (candidate_symbol, candidate_score)
                        for candidate_symbol, candidate_score in candidates
                        if candidate_symbol not in used_candidates
                        and candidate_score >= held_score + STABLE_ROTATION_SCORE_GAP
                    ), None)
                    if replacement is None:
                        continue
                    replacements.append((held_symbol, replacement[0]))
                    used_candidates.add(replacement[0])
                    if len(replacements) >= STABLE_ROTATION_MAX_WEEKLY_REPLACEMENTS:
                        break

                for held_symbol, replacement_symbol in replacements:
                    held_lookup, held_security = indexed[held_symbol]
                    replacement_lookup, replacement_security = indexed[replacement_symbol]
                    held_cursor = held_lookup.get(session)
                    replacement_cursor = replacement_lookup.get(session)
                    if held_cursor is None or replacement_cursor is None:
                        continue
                    position = positions.pop(held_symbol)
                    proceeds = position.shares * held_security.bars[held_cursor].open * (1 - sell_cost)
                    execution_price = replacement_security.bars[replacement_cursor].open * (1 + buy_cost)
                    positions[replacement_symbol] = SimulatedPosition(
                        proceeds / execution_price,
                        session_number,
                    )
                    exits += 1
                    entries += 1

        equity = cash
        for symbol, position in positions.items():
            lookup, security = indexed[symbol]
            cursor = lookup.get(session)
            if cursor is not None:
                equity += position.shares * security.bars[cursor].close
            else:
                previous = max(
                    (bar for bar in security.bars if bar.trade_date < session),
                    key=lambda bar: bar.trade_date,
                    default=None,
                )
                if previous is not None:
                    equity += position.shares * previous.close
        curve.append(equity)

    total_return = (curve[-1] - 1) * 100
    elapsed_days = max(1, (sessions[-1] - sessions[0]).days)
    annualized = ((curve[-1] ** (365.2425 / elapsed_days)) - 1) * 100 if curve[-1] > 0 else -100.0
    maximum_drawdown = _drawdown(curve)
    balance_score = total_return - abs(maximum_drawdown) * 0.5
    return {
        "minimumHoldingDays": minimum_holding_days,
        "returnPercent": round(total_return, 2),
        "annualizedReturnPercent": round(annualized, 2),
        "maximumDrawdownPercent": round(maximum_drawdown, 2),
        "balanceScore": round(balance_score, 2),
        "entryCount": entries,
        "replacementCount": exits,
        "weeklyReviewCount": weekly_review_count,
        "currentHoldings": sorted(positions),
    }


def _equal_weight_benchmark_result(
    securities: list[BacktestSecurity],
    start_date: date,
    end_date: date,
) -> dict[str, object]:
    usable = [
        (security, [bar for bar in security.bars if start_date <= bar.trade_date <= end_date])
        for security in securities
    ]
    usable = [(security, bars) for security, bars in usable if bars]
    if not usable:
        raise ValueError("近10年年化最高50檔缺少今年行情")
    weight = 1 / len(usable)
    shares = {
        security.symbol: weight / (bars[0].open * (1 + COMMISSION_RATE * COMMISSION_DISCOUNT + SLIPPAGE_RATE))
        for security, bars in usable
    }
    all_dates = sorted({bar.trade_date for _, bars in usable for bar in bars})
    bars_by_symbol = {
        security.symbol: {bar.trade_date: bar for bar in bars}
        for security, bars in usable
    }
    latest_prices: dict[str, float] = {}
    curve = [1.0]
    for session in all_dates:
        for security, _ in usable:
            bar = bars_by_symbol[security.symbol].get(session)
            if bar is not None:
                latest_prices[security.symbol] = bar.close
        curve.append(sum(
            shares[symbol] * price for symbol, price in latest_prices.items()
        ))
    total_return = (curve[-1] - 1) * 100
    elapsed_days = max(1, (all_dates[-1] - all_dates[0]).days)
    annualized = ((curve[-1] ** (365.2425 / elapsed_days)) - 1) * 100 if curve[-1] > 0 else -100.0
    return {
        "key": "ten_year_cagr_top50_ytd",
        "name": "近10年年化報酬率最高50檔（等權）",
        "symbol": None,
        "strategyType": "benchmark",
        "returnPercent": round(total_return, 2),
        "annualizedReturnPercent": round(annualized, 2),
        "maximumDrawdownPercent": round(_drawdown(curve), 2),
        "entryCount": len(usable),
        "replacementCount": 0,
        "currentHoldings": sorted(security.symbol for security, _ in usable),
        "constituentCount": len(usable),
    }


def _simulate_strategy(
    securities: list[BacktestSecurity],
    benchmark: BacktestSecurity,
    start_date: date,
    target_count: int,
    ranking_by_date: dict[date, list[tuple[str, float]]] | None = None,
    end_date: date | None = None,
) -> dict[str, object]:
    indexed = {
        item.symbol: ({bar.trade_date: offset for offset, bar in enumerate(item.bars)}, item)
        for item in securities
    }
    benchmark_index = {bar.trade_date: offset for offset, bar in enumerate(benchmark.bars)}
    final_date = end_date or datetime.now(UTC).astimezone(TAIPEI).date()
    sessions = [bar.trade_date for bar in benchmark.bars if start_date <= bar.trade_date <= final_date]
    if not sessions:
        raise ValueError("今年尚無可用交易日行情")
    rankings = ranking_by_date or _historical_rankings(
        securities, benchmark, start_date, sessions[-1],
    )
    positions: dict[str, SimulatedPosition] = {}
    cash = 1.0
    curve = [1.0]
    entries = 0
    exits = 0
    buy_cost = COMMISSION_RATE * COMMISSION_DISCOUNT + SLIPPAGE_RATE
    sell_cost = COMMISSION_RATE * COMMISSION_DISCOUNT + STOCK_TAX_RATE + SLIPPAGE_RATE
    all_benchmark_dates = [bar.trade_date for bar in benchmark.bars]

    for session_number, session in enumerate(sessions):
        benchmark_cursor = benchmark_index[session]
        previous_date = all_benchmark_dates[benchmark_cursor - 1] if benchmark_cursor > 0 else None
        targets = rankings.get(previous_date, [])[:target_count] if previous_date else []
        target_symbols = {symbol for symbol, _ in targets}
        released_cash = 0.0
        for symbol in list(positions):
            position = positions[symbol]
            held_sessions = session_number - position.entry_session
            if symbol in target_symbols or held_sessions < MINIMUM_HOLDING_TRADING_DAYS:
                continue
            lookup, security = indexed[symbol]
            cursor = lookup.get(session)
            if cursor is None:
                continue
            released_cash += position.shares * security.bars[cursor].open * (1 - sell_cost)
            del positions[symbol]
            exits += 1
        cash += released_cash
        vacancies = max(0, target_count - len(positions))
        replacements = [(symbol, score) for symbol, score in targets if symbol not in positions][:vacancies]
        if replacements and cash > 0:
            score_total = sum(max(20.0, score) for _, score in replacements)
            available_cash = cash
            spent = 0.0
            for index, (symbol, score) in enumerate(replacements):
                lookup, security = indexed[symbol]
                cursor = lookup.get(session)
                if cursor is None:
                    continue
                allocation = (
                    available_cash - spent
                    if index == len(replacements) - 1
                    else available_cash * max(20.0, score) / score_total
                )
                execution_price = security.bars[cursor].open * (1 + buy_cost)
                positions[symbol] = SimulatedPosition(allocation / execution_price, session_number)
                spent += allocation
                entries += 1
            cash = max(0.0, available_cash - spent)
        equity = cash
        for symbol, position in positions.items():
            lookup, security = indexed[symbol]
            cursor = lookup.get(session)
            if cursor is not None:
                equity += position.shares * security.bars[cursor].close
            else:
                previous = max(
                    (bar for bar in security.bars if bar.trade_date < session),
                    key=lambda bar: bar.trade_date,
                    default=None,
                )
                if previous is not None:
                    equity += position.shares * previous.close
        curve.append(equity)
    total_return = (curve[-1] - 1) * 100
    elapsed_days = max(1, (sessions[-1] - sessions[0]).days)
    annualized = ((curve[-1] ** (365.2425 / elapsed_days)) - 1) * 100 if curve[-1] > 0 else -100.0
    return {
        "returnPercent": round(total_return, 2),
        "annualizedReturnPercent": round(annualized, 2),
        "maximumDrawdownPercent": round(_drawdown(curve), 2),
        "entryCount": entries,
        "replacementCount": exits,
        "currentHoldings": sorted(positions),
    }


def _benchmark_result(
    security: BacktestSecurity,
    start_date: date,
    end_date: date | None = None,
) -> dict[str, object]:
    final_date = end_date or datetime.now(UTC).astimezone(TAIPEI).date()
    rows = [item for item in security.bars if start_date <= item.trade_date <= final_date]
    if not rows:
        raise ValueError(f"{security.symbol} 今年沒有可用行情")
    buy_cost = COMMISSION_RATE * COMMISSION_DISCOUNT + SLIPPAGE_RATE
    shares = 1 / (rows[0].open * (1 + buy_cost))
    curve = [shares * row.close for row in rows]
    total_return = (curve[-1] - 1) * 100
    elapsed_days = max(1, (rows[-1].trade_date - rows[0].trade_date).days)
    annualized = ((curve[-1] ** (365.2425 / elapsed_days)) - 1) * 100 if curve[-1] > 0 else -100.0
    return {
        "key": security.symbol,
        "name": security.name,
        "symbol": security.symbol,
        "strategyType": "benchmark",
        "returnPercent": round(total_return, 2),
        "annualizedReturnPercent": round(annualized, 2),
        "maximumDrawdownPercent": round(_drawdown([1.0, *curve]), 2),
        "entryCount": 1,
        "replacementCount": 0,
        "currentHoldings": [security.symbol],
    }


async def _calculate_ytd_backtest(
    universe_requests: list[tuple[str, str, str]],
    today: date,
    cagr_requests: list[tuple[str, str, str]],
    universe_source: str,
) -> dict[str, object]:
    requests = universe_requests[:BACKTEST_UNIVERSE_LIMIT]
    benchmark_requests = [
        ("0050", "元大台灣50", "上市"),
        ("00881", "國泰台灣科技龍頭", "上市"),
        ("00631L", "元大台灣50正2", "上市"),
    ]
    limits = httpx.Limits(max_connections=12, max_keepalive_connections=8)
    headers = {"Accept": "application/json", "User-Agent": "Moneymoney-TWSE-Dashboard"}
    unique_requests = list({
        symbol: (symbol, name, market)
        for symbol, name, market in [*requests, *benchmark_requests, *cagr_requests]
    }.values())
    async with httpx.AsyncClient(timeout=15.0, limits=limits, headers=headers) as client:
        semaphore = asyncio.Semaphore(6)
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*(
                    _fetch_history(client, semaphore, symbol, name, market)
                    for symbol, name, market in unique_requests
                )),
                timeout=90,
            )
        except TimeoutError as error:
            raise ValueError("歷史行情下載超過90秒，請稍後再試") from error
    available = {item.symbol: item for item in results if item is not None}
    benchmark_0050 = available.get("0050")
    if benchmark_0050 is None:
        raise ValueError("0050 歷史行情暫時無法取得，不能建立公平比較基準")
    universe = [available[symbol] for symbol, _, _ in requests if symbol in available]
    if len(universe) < 30:
        raise ValueError(f"可用歷史行情股票只有 {len(universe)} 檔，暫不產生可能失真的回測")
    start_date = date(today.year, 1, 1)
    ranking_by_date = _historical_rankings(universe, benchmark_0050, start_date, today)
    stable = _simulate_strategy(universe, benchmark_0050, start_date, 10, ranking_by_date, today)
    focused = _simulate_strategy(universe, benchmark_0050, start_date, 3, ranking_by_date, today)
    stable_rotation_variants = [
        _simulate_stable_rotation(
            universe,
            benchmark_0050,
            start_date,
            ranking_by_date,
            minimum_holding_days,
            today,
        )
        for minimum_holding_days in STABLE_ROTATION_HOLDING_VARIANTS
    ]
    selected_variant = max(
        stable_rotation_variants,
        key=lambda item: (float(item["balanceScore"]), float(item["returnPercent"])),
    )
    for item in stable_rotation_variants:
        item["selected"] = item is selected_variant
    rows: list[dict[str, object]] = [
        {
            "key": "stable_rotation_50",
            "name": f"50檔穩健輪動（均衡選定 {selected_variant['minimumHoldingDays']} 日）",
            "symbol": None,
            "strategyType": "model",
            **selected_variant,
        },
        {
            "key": "long_only",
            "name": "10 檔穩健多方",
            "symbol": None,
            "strategyType": "model",
            **stable,
        },
        {
            "key": "focused_long",
            "name": "3 檔精選多方",
            "symbol": None,
            "strategyType": "model",
            **focused,
        },
    ]
    rows.extend(
        _benchmark_result(available[symbol], start_date, today)
        for symbol in ("0050", "00881", "00631L")
        if symbol in available
    )
    cagr_securities = [
        available[symbol] for symbol, _, _ in cagr_requests if symbol in available
    ]
    if cagr_securities:
        rows.append(_equal_weight_benchmark_result(cagr_securities, start_date, today))
    benchmark_0050_result = _benchmark_result(benchmark_0050, start_date, today)
    rows.sort(key=lambda item: float(item["returnPercent"]), reverse=True)
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
        row["leadVs0050Percent"] = round(
            float(row["returnPercent"]) - float(benchmark_0050_result["returnPercent"]),
            2,
        )
    ytd_sessions = [item.trade_date for item in benchmark_0050.bars if start_date <= item.trade_date <= today]
    return {
        "periodLabel": f"{today.year} 年初至今",
        "fromDate": ytd_sessions[0].isoformat(),
        "toDate": ytd_sessions[-1].isoformat(),
        "rows": rows,
        "universeCount": len(universe),
        "requestedUniverseCount": len(requests),
        "stableRotation": {
            "name": "50檔穩健輪動",
            "selectedMinimumHoldingDays": selected_variant["minimumHoldingDays"],
            "selectionMethod": "均衡分＝年初至今報酬－0.5×最大回撤絕對值；均衡分最高者入選",
            "rules": {
                "targetCount": STABLE_ROTATION_SIZE,
                "reviewFrequency": "每週第一個交易日",
                "protectedRank": STABLE_ROTATION_PROTECTED_RANK,
                "minimumScoreGap": STABLE_ROTATION_SCORE_GAP,
                "maximumWeeklyReplacements": STABLE_ROTATION_MAX_WEEKLY_REPLACEMENTS,
            },
            "variants": stable_rotation_variants,
        },
        "dataSource": f"Yahoo Finance 台股還原權息日線；{universe_source}",
        "returnBasis": "adjusted_total_return",
        "methodology": (
            "每日收盤後依趨勢成長、相對強勢與可歷史重建的品質動能欄位排名，下一交易日開盤成交；"
            "50檔穩健輪動僅在每週第一個交易日檢查，持股跌出前20名、持有期已滿且新股高出5分才可汰換，每週最多2檔。"
            "股票與ETF均使用還原權息價計算含息總報酬，等同配息再投入；"
            "模型績效已計入手續費折扣、股票交易稅與滑價；ETF基準採年初買進持有。"
        ),
        "limitations": [
            "歷史基本面、法人與股權分散資料未回填，避免把今日資料偷看進歷史；品質動能僅使用可重建的價量欄位。",
            "股票池取自目前仍在掃描名單且成交值最高的300檔，仍可能存在生存者偏差。",
            "近10年年化最高50檔是依目前可見的10年績效選出，放回今年比較含有後見與生存者偏差，只能當參考基準。",
            "回測以還原權息價假設股息再投入；實際領息日期、稅負及再投入成交價可能使實際績效不同。",
            "日線無法完整模擬漲跌停、盤中流動性與實際成交順位，結果不代表未來報酬。",
        ],
        "calculatedAt": datetime.now(UTC).isoformat(),
    }


def _persisted_payload(db: Session, today: date) -> dict[str, object] | None:
    stored = db.get(LongTermBacktestCache, f"ytd:{BACKTEST_VERSION}:{today.year}")
    if stored is None or stored.as_of_date != today:
        return None
    try:
        payload = json.loads(stored.payload_json)
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) and payload.get("rows") else None


def persisted_ytd_backtest_payload(db: Session) -> dict[str, object] | None:
    """Return today's complete result without requiring any external scan service."""
    today = datetime.now(UTC).astimezone(TAIPEI).date()
    return _persisted_payload(db, today)


def _save_persisted_payload(db: Session, today: date, payload: dict[str, object]) -> None:
    key = f"ytd:{BACKTEST_VERSION}:{today.year}"
    stored = db.get(LongTermBacktestCache, key)
    if stored is None:
        stored = LongTermBacktestCache(
            backtest_key=key,
            as_of_date=today,
            payload_json="{}",
            calculated_at=datetime.now(UTC),
        )
        db.add(stored)
    stored.as_of_date = today
    stored.payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    stored.calculated_at = datetime.now(UTC)
    db.commit()


async def ytd_backtest_payload(
    db: Session,
    stocks: list[AdaptiveStockInput] | None = None,
    *,
    force: bool = False,
    fallback_requests: list[tuple[str, str, str]] | None = None,
    universe_source: str | None = None,
) -> dict[str, object]:
    global _cache
    now = datetime.now(UTC)
    today = now.astimezone(TAIPEI).date()
    signature = f"{BACKTEST_VERSION}:{today.isoformat()}"
    if not force and _cache is not None and _cache[0] > now and _cache[1] == signature:
        return _cache[2]
    if not force and (persisted := _persisted_payload(db, today)) is not None:
        _cache = (now + timedelta(seconds=BACKTEST_CACHE_SECONDS), signature, persisted)
        return persisted
    async with _cache_lock:
        now = datetime.now(UTC)
        if not force and _cache is not None and _cache[0] > now and _cache[1] == signature:
            return _cache[2]
        if not force and (persisted := _persisted_payload(db, today)) is not None:
            _cache = (now + timedelta(seconds=BACKTEST_CACHE_SECONDS), signature, persisted)
            return persisted
        try:
            if stocks:
                selected = sorted(
                    (
                        item for item in stocks
                        if not item.is_full_delivery
                        and not item.is_alternate_trading
                        and not item.is_disposed
                        and not item.is_suspended
                        and not item.is_delisted
                    ),
                    key=lambda item: (item.average_turnover_20d, item.stock_code),
                    reverse=True,
                )[:BACKTEST_UNIVERSE_LIMIT]
                universe_requests = [
                    (item.stock_code, item.stock_name, item.market_type)
                    for item in selected
                ]
                source = universe_source or "股票池與標的資格來自目前長線掃描名單"
            else:
                universe_requests = list(fallback_requests or ())[:BACKTEST_UNIVERSE_LIMIT]
                source = universe_source or "股票池來自官方成交熱門排行備援名單"
            if len(universe_requests) < 30:
                raise ValueError("回測股票池不足30檔，請稍後再試")
            cagr_requests = [
                (item.symbol, item.name, item.market)
                for item in latest_cagr_selections(db)
            ]
            payload = await _calculate_ytd_backtest(
                universe_requests,
                today,
                cagr_requests,
                source,
            )
        except ValueError:
            # A forced refresh must never erase the last complete same-day result.
            if (persisted := _persisted_payload(db, today)) is not None:
                return persisted
            raise
        _save_persisted_payload(db, today, payload)
        _cache = (now + timedelta(seconds=BACKTEST_CACHE_SECONDS), signature, payload)
        return payload
