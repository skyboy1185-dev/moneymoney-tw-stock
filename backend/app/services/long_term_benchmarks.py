from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, date, datetime
import logging
import math
from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..models import LargeHolderWeeklySummary, LongTermBenchmarkSelection
from .large_holders import tdcc_large_holder_provider


logger = logging.getLogger(__name__)
TOP_CAGR_COUNT = 50
MIN_HISTORY_YEARS = 9.5
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"


@dataclass(frozen=True, slots=True)
class TenYearCagrCandidate:
    symbol: str
    name: str
    market: str
    annualized_return: float
    history_start_date: date
    history_end_date: date
    history_start_price: float
    history_end_price: float


def parse_yahoo_cagr_candidate(
    symbol: str,
    name: str,
    market: str,
    payload: dict[str, Any],
) -> TenYearCagrCandidate | None:
    results = payload.get("chart", {}).get("result") or []
    if not results:
        return None
    result = results[0]
    timestamps = result.get("timestamp") or []
    indicators = result.get("indicators") or {}
    adjusted_groups = indicators.get("adjclose") or []
    adjusted = adjusted_groups[0].get("adjclose") if adjusted_groups else None
    if not isinstance(adjusted, list):
        quote_groups = indicators.get("quote") or []
        adjusted = quote_groups[0].get("close") if quote_groups else []
    observations: list[tuple[date, float]] = []
    for raw_time, raw_price in zip(timestamps, adjusted or [], strict=False):
        try:
            price = float(raw_price)
            observed = datetime.fromtimestamp(int(raw_time), UTC).date()
        except (TypeError, ValueError, OSError):
            continue
        if price > 0 and math.isfinite(price):
            observations.append((observed, price))
    if len(observations) < 2:
        return None
    start_date, start_price = observations[0]
    end_date, end_price = observations[-1]
    years = (end_date - start_date).days / 365.2425
    if years < MIN_HISTORY_YEARS or start_price <= 0 or end_price <= 0:
        return None
    annualized = ((end_price / start_price) ** (1 / years) - 1) * 100
    if not math.isfinite(annualized):
        return None
    return TenYearCagrCandidate(
        symbol=symbol,
        name=name,
        market=market,
        annualized_return=round(annualized, 4),
        history_start_date=start_date,
        history_end_date=end_date,
        history_start_price=round(start_price, 4),
        history_end_price=round(end_price, 4),
    )


async def _fetch_candidate(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    symbol: str,
    metadata: dict[str, Any],
) -> TenYearCagrCandidate | None:
    market = str(metadata.get("market") or "上市")
    suffix = "TWO" if market == "上櫃" else "TW"
    ticker = quote(f"{symbol}.{suffix}", safe="")
    try:
        async with semaphore:
            response = await client.get(
                YAHOO_CHART_URL.format(ticker=ticker),
                params={"interval": "1mo", "range": "10y", "events": "history"},
            )
        response.raise_for_status()
        return parse_yahoo_cagr_candidate(
            symbol,
            str(metadata.get("name") or symbol),
            market,
            response.json(),
        )
    except (httpx.HTTPError, ValueError, TypeError):
        return None


def stored_stock_directory(db: Session) -> dict[str, dict[str, Any]]:
    latest_date = db.scalar(select(func.max(LargeHolderWeeklySummary.report_date)))
    if latest_date is None:
        return {}
    rows = db.scalars(select(LargeHolderWeeklySummary).where(
        LargeHolderWeeklySummary.report_date == latest_date,
    )).all()
    return {
        item.stock_code: {
            "name": item.stock_name or item.stock_code,
            "market": item.market,
        }
        for item in rows
        if item.stock_code.isdigit()
        and len(item.stock_code) == 4
        and not item.stock_code.startswith("00")
    }


async def discover_top_ten_year_cagr(
    fallback_directory: dict[str, dict[str, Any]] | None = None,
) -> list[TenYearCagrCandidate]:
    """Rank ordinary TWSE/TPEX shares by adjusted trailing 10-year CAGR."""
    try:
        directory = await tdcc_large_holder_provider.fetch_stock_directory()
    except (httpx.HTTPError, ValueError, TypeError):
        logger.warning("Official stock directory unavailable; using persisted TDCC directory")
        directory = dict(fallback_directory or {})
    else:
        for symbol, metadata in (fallback_directory or {}).items():
            directory.setdefault(symbol, metadata)
    if not directory:
        return []
    limits = httpx.Limits(max_connections=16, max_keepalive_connections=8)
    headers = {"Accept": "application/json", "User-Agent": "Moneymoney-TWSE-Dashboard"}
    async with httpx.AsyncClient(timeout=15.0, limits=limits, headers=headers) as client:
        semaphore = asyncio.Semaphore(16)
        candidates = await asyncio.gather(*(
            _fetch_candidate(client, semaphore, symbol, metadata)
            for symbol, metadata in directory.items()
        ))
    available = [item for item in candidates if item is not None]
    available.sort(key=lambda item: (item.annualized_return, item.symbol), reverse=True)
    logger.info(
        "Calculated trailing 10-year CAGR for %s/%s Taiwan stocks",
        len(available), len(directory),
    )
    return available[:TOP_CAGR_COUNT]


def has_cagr_selection(db: Session, selection_date: date) -> bool:
    count = db.scalar(select(func.count(LongTermBenchmarkSelection.symbol)).where(
        LongTermBenchmarkSelection.selection_date == selection_date,
    ))
    return int(count or 0) >= TOP_CAGR_COUNT


def save_cagr_selection(
    db: Session,
    selection_date: date,
    candidates: list[TenYearCagrCandidate],
    created_at: datetime,
) -> None:
    if len(candidates) < TOP_CAGR_COUNT:
        return
    # Older deployments stored only 10 constituents. Replace an incomplete
    # same-day snapshot atomically before inserting the full TOP 50.
    if has_cagr_selection(db, selection_date):
        return
    db.execute(delete(LongTermBenchmarkSelection).where(
        LongTermBenchmarkSelection.selection_date == selection_date,
    ))
    for rank, item in enumerate(candidates[:TOP_CAGR_COUNT], start=1):
        db.add(LongTermBenchmarkSelection(
            selection_date=selection_date,
            symbol=item.symbol,
            name=item.name,
            market=item.market,
            rank=rank,
            annualized_return_10y=item.annualized_return,
            history_start_date=item.history_start_date,
            history_end_date=item.history_end_date,
            history_start_price=item.history_start_price,
            history_end_price=item.history_end_price,
            created_at=created_at,
        ))
    db.commit()


def latest_cagr_selections(db: Session) -> list[LongTermBenchmarkSelection]:
    latest_date = db.scalar(select(func.max(LongTermBenchmarkSelection.selection_date)))
    if latest_date is None:
        return []
    return list(db.scalars(select(LongTermBenchmarkSelection).where(
        LongTermBenchmarkSelection.selection_date == latest_date,
    ).order_by(LongTermBenchmarkSelection.rank)).all())
