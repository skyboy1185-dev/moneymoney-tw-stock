"""Read-only quote subscriptions; candidate selection never changes trading eligibility."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Iterable
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..day_trading_v2_models import (
    DayTradeV2CandidateState, DayTradeV2Position, DayTradeV2RuntimeState, DayTradeV2Setting,
)
from ..models import DayTradingPosition
from .official_market_data import StockQuoteRequest
from .popular_stock_universe import POPULAR_ALERT_FALLBACK_STOCKS
from .theme_stock_universe import ELECTRONIC_ALERT_STOCKS, THEME_STOCKS, ThemeStock

PRIORITY_CHANNEL_LIMIT = 30
V2_CANDIDATE_LIMIT_PER_USER = 10
V2_CANDIDATE_MAX_AGE_SECONDS = 120


@dataclass(frozen=True)
class PriorityCandidate:
    symbol: str
    confidence: Decimal


@dataclass(frozen=True)
class QuoteTargetInputs:
    held_symbols: frozenset[str]
    candidates: tuple[PriorityCandidate, ...]
    legacy_open_count: int = 0
    automatic_open_count: int = 0


@dataclass(frozen=True)
class QuoteTargets:
    priority: tuple[StockQuoteRequest, ...]
    baseline: tuple[StockQuoteRequest, ...]
    mandatory_symbols: frozenset[str]
    tracking_symbols: frozenset[str]
    unresolved_symbols: tuple[str, ...]
    overflow_symbols: tuple[str, ...]
    candidate_overflow_count: int


def read_quote_target_inputs(db: Session, now: datetime, automatic_users: Iterable[str] = ()) -> QuoteTargetInputs:
    """No ensure/default-account helpers: this query must not create or alter records."""
    current = now.replace(tzinfo=UTC) if now.tzinfo is None else now
    today = current.astimezone(ZoneInfo("Asia/Taipei")).date()
    legacy = db.execute(select(DayTradingPosition.symbol, DayTradingPosition.user_id).where(
        DayTradingPosition.status == "open",
    )).all()
    v2_held = db.scalars(select(DayTradeV2Position.symbol).where(
        DayTradeV2Position.mode == "PAPER", DayTradeV2Position.status == "OPEN",
    )).all()
    ranked = select(
        DayTradeV2CandidateState.symbol,
        DayTradeV2CandidateState.confidence,
        func.row_number().over(
            partition_by=DayTradeV2CandidateState.user_id,
            order_by=(DayTradeV2CandidateState.confidence.desc(), DayTradeV2CandidateState.symbol.asc()),
        ).label("priority_rank"),
    ).join(DayTradeV2Setting, DayTradeV2Setting.user_id == DayTradeV2CandidateState.user_id).join(
        DayTradeV2RuntimeState,
        (DayTradeV2RuntimeState.user_id == DayTradeV2CandidateState.user_id)
        & (DayTradeV2RuntimeState.mode == "PAPER")
        & (DayTradeV2RuntimeState.trading_date == today),
    ).where(
        DayTradeV2Setting.trade_mode == "PAPER", DayTradeV2Setting.live_enabled.is_(False),
        DayTradeV2RuntimeState.status.in_(("RUNNING", "PAUSED")),
        DayTradeV2CandidateState.mode == "PAPER", DayTradeV2CandidateState.trading_date == today,
        DayTradeV2CandidateState.scanned_at >= current - timedelta(seconds=V2_CANDIDATE_MAX_AGE_SECONDS),
        DayTradeV2CandidateState.scanned_at <= current,
    ).subquery()
    candidates = db.execute(select(ranked.c.symbol, ranked.c.confidence).where(
        ranked.c.priority_rank <= V2_CANDIDATE_LIMIT_PER_USER,
    )).all()
    automatic = set(automatic_users)
    return QuoteTargetInputs(
        held_symbols=frozenset(str(symbol) for symbol in [*(row.symbol for row in legacy), *v2_held] if symbol),
        candidates=tuple(PriorityCandidate(str(row.symbol), row.confidence) for row in candidates),
        legacy_open_count=len(legacy),
        automatic_open_count=sum(row.user_id in automatic for row in legacy),
    )


class QuoteTargetSelector:
    def __init__(self) -> None:
        # Remember real exchange metadata after a stock leaves the momentum pool.
        self._known: dict[str, ThemeStock] = {
            stock.symbol: stock for stock in (*THEME_STOCKS, *ELECTRONIC_ALERT_STOCKS, *POPULAR_ALERT_FALLBACK_STOCKS)
        }

    def select(self, universe: Iterable[ThemeStock], inputs: QuoteTargetInputs, high_frequency_symbols: Iterable[str]) -> QuoteTargets:
        universe = tuple(universe)
        self._known.update({stock.symbol: stock for stock in universe if stock.market in {"上市", "上櫃"}})
        high_frequency = tuple(dict.fromkeys(str(symbol) for symbol in high_frequency_symbols if symbol))
        mandatory = frozenset({"t00", *inputs.held_symbols, *high_frequency})
        scores: dict[str, Decimal] = {}
        for row in inputs.candidates:
            scores[row.symbol] = max(scores.get(row.symbol, row.confidence), row.confidence)
        candidate_symbols = sorted(scores, key=lambda symbol: (-scores[symbol], symbol))
        ordered = tuple(dict.fromkeys(("t00", *sorted(inputs.held_symbols), *high_frequency, *candidate_symbols)))
        unresolved: set[str] = set()

        def request(symbol: str) -> StockQuoteRequest | None:
            if symbol == "t00":
                return StockQuoteRequest(symbol="t00", name="加權指數", market="上市")
            stock = self._known.get(symbol)
            if stock is None or stock.market not in {"上市", "上櫃"}:
                unresolved.add(symbol)
                return None
            return StockQuoteRequest(symbol=stock.symbol, name=stock.name, market=stock.market)

        resolved = [item for symbol in ordered if (item := request(symbol)) is not None]
        mandatory_count = sum(item.symbol in mandatory for item in resolved)
        priority = tuple(resolved[:max(PRIORITY_CHANNEL_LIMIT, mandatory_count)])
        priority_symbols = {item.symbol for item in priority}
        overflow = tuple(item.symbol for item in resolved if item.symbol not in priority_symbols)
        baseline = tuple(
            item for symbol in dict.fromkeys((*(stock.symbol for stock in universe), *overflow))
            if symbol not in priority_symbols and (item := request(symbol)) is not None
        )
        existing = mandatory | set(high_frequency)
        return QuoteTargets(
            priority=priority, baseline=baseline, mandatory_symbols=mandatory,
            tracking_symbols=frozenset({*inputs.held_symbols, *high_frequency, *candidate_symbols}),
            unresolved_symbols=tuple(sorted(unresolved)), overflow_symbols=overflow,
            candidate_overflow_count=sum(symbol in scores and symbol not in existing for symbol in overflow),
        )
