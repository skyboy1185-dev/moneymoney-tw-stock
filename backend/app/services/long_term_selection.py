from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import json
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..adaptive_schemas import AdaptiveScanPayload, AdaptiveStockInput
from ..config import get_settings
from ..models import (
    LongTermBenchmark,
    LongTermPortfolioRun,
    LongTermPosition,
    LongTermPositionSnapshot,
    LongTermTradeEvent,
)
from .day_trading_schedule import is_twse_trading_day
from .long_term_benchmarks import latest_cagr_selections
from .long_term_dividends import (
    DividendHistory,
    dividends_per_share,
    long_term_dividend_provider,
)
from .official_market_data import StockQuoteRequest, official_market_data_provider


LONG_TERM_START_DATE = date(2026, 8, 10)
LONG_TERM_SELECTION_TIME = "09:15"
PORTFOLIO_SIZE = 10
FOCUSED_PORTFOLIO_SIZE = 3
MINIMUM_HOLDING_TRADING_DAYS = 5
SIMULATION_CAPITAL = 1_000_000.0
PortfolioMode = Literal["long_only", "focused_long"]
Direction = Literal["long", "short"]
MODE_TARGET_COUNTS: dict[str, int] = {"long_only": PORTFOLIO_SIZE, "focused_long": FOCUSED_PORTFOLIO_SIZE}
SYNC_DUPLICATE_STATUS = "cancelled_duplicate"
TAIPEI = ZoneInfo("Asia/Taipei")
BENCHMARK_DEFINITIONS = (
    {"symbol": "0050", "name": "元大台灣50", "market": "上市"},
    {"symbol": "00881", "name": "國泰台灣科技龍頭", "market": "上市"},
    {"symbol": "00631L", "name": "元大台灣50正2", "market": "上市"},
)

MODEL_DEFINITIONS = {
    "long_only": (
        {"key": "trend_growth", "name": "趨勢成長", "description": "均線多頭、趨勢斜率與突破動能"},
        {"key": "relative_strength", "name": "相對強勢", "description": "近月報酬與大盤、電子指數相對強弱"},
        {"key": "quality_momentum", "name": "品質動能", "description": "基本面、產業強度與量價結構"},
    ),
    "focused_long": (
        {"key": "trend_growth", "name": "趨勢成長", "description": "均線多頭、趨勢斜率與突破動能"},
        {"key": "relative_strength", "name": "相對強勢", "description": "近月報酬與大盤、電子指數相對強弱"},
        {"key": "quality_momentum", "name": "品質動能", "description": "基本面、產業強度與量價結構"},
    ),
}


@dataclass(frozen=True, slots=True)
class LongTermPick:
    stock_code: str
    stock_name: str
    market_type: str
    industry: str
    direction: Direction
    model_key: str
    model_name: str
    score: float
    price: float
    predicted_month_return_pct: float
    reasons: tuple[str, ...]


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _holidays() -> frozenset[date]:
    values: set[date] = set()
    for raw in get_settings().twse_holidays.split(","):
        try:
            values.add(date.fromisoformat(raw.strip()))
        except ValueError:
            continue
    return frozenset(values)


def trading_days_held(entry_date: date, current_date: date, holidays: frozenset[date] | None = None) -> int:
    if current_date < entry_date:
        return 0
    exclusions = holidays if holidays is not None else _holidays()
    # 進場當天尚未「放滿」一天；從下一個完整交易日開始累計。
    day = entry_date + timedelta(days=1)
    count = 0
    while day <= current_date:
        if is_twse_trading_day(day, exclusions):
            count += 1
        day += timedelta(days=1)
    return count


def minimum_exit_date(entry_date: date, holidays: frozenset[date] | None = None) -> date:
    exclusions = holidays if holidays is not None else _holidays()
    day = entry_date + timedelta(days=1)
    while trading_days_held(entry_date, day, exclusions) < MINIMUM_HOLDING_TRADING_DAYS:
        day += timedelta(days=1)
    return day


def actual_return_percent(entry_price: float, current_price: float, direction: str) -> float:
    if entry_price <= 0:
        return 0.0
    factor = -1 if direction == "short" else 1
    return round(((current_price / entry_price) - 1) * 100 * factor, 4)


def total_return_breakdown(
    entry_price: float,
    current_price: float,
    direction: str,
    cash_dividends_per_share: float = 0.0,
) -> dict[str, float]:
    price_return = actual_return_percent(entry_price, current_price, direction)
    direction_factor = -1 if direction == "short" else 1
    dividend_return = (
        cash_dividends_per_share / entry_price * 100 * direction_factor
        if entry_price > 0 else 0.0
    )
    return {
        "priceReturnPercent": round(price_return, 4),
        "dividendReturnPercent": round(dividend_return, 4),
        "totalReturnPercent": round(price_return + dividend_return, 4),
    }


def _eligible(stock: AdaptiveStockInput) -> bool:
    return (
        stock.price >= 10
        and stock.data_completeness >= 0.75
        and stock.average_volume_20d_shares >= 500_000
        and stock.average_turnover_20d >= 50_000_000
        and stock.has_recent_trade
        and not stock.is_full_delivery
        and not stock.is_alternate_trading
        and not stock.is_disposed
        and not stock.is_suspended
        and not stock.is_delisted
        and not stock.abnormal_trading
    )


def _long_scores(stock: AdaptiveStockInput) -> dict[str, float]:
    above20 = stock.ma20 is not None and stock.price >= stock.ma20
    above60 = stock.ma60 is not None and stock.price >= stock.ma60
    positive_slope = (stock.ma20_slope or 0) > 0 and (stock.ma60_slope or 0) >= 0
    trend = 38 + 14 * above20 + 12 * above60 + 10 * positive_slope + 8 * stock.higher_low + 8 * stock.breakout_20d
    trend += _clamp(stock.return_20d, -12, 18) * 0.5

    relative = 42 + _clamp(stock.relative_strength_market, -20, 25) * 1.1
    relative += _clamp(stock.relative_strength_electronic, -20, 25) * 0.7
    relative += _clamp(stock.return_5d, -10, 12) * 0.8 + 8 * bool(stock.macd_histogram_rising)

    fundamental_points = sum([
        8 if (stock.trailing_eps or 0) > 0 else 0,
        7 if (stock.revenue_yoy or 0) > 0 else 0,
        6 if (stock.gross_margin_change or 0) > 0 else 0,
        6 if (stock.foreign_net_5d or 0) > 0 else 0,
        5 if (stock.trust_net_5d or 0) > 0 else 0,
    ])
    quality = 40 + fundamental_points + stock.industry_strength_score * 0.2
    quality += 6 if stock.down_volume_less_than_up else 0
    quality -= 12 if stock.fundamental_risk else 0
    return {
        "trend_growth": round(_clamp(trend, 0, 100), 2),
        "relative_strength": round(_clamp(relative, 0, 100), 2),
        "quality_momentum": round(_clamp(quality, 0, 100), 2),
    }


def _short_scores(stock: AdaptiveStockInput) -> dict[str, float]:
    below20 = stock.ma20 is not None and stock.price < stock.ma20
    below60 = stock.ma60 is not None and stock.price < stock.ma60
    negative_slope = (stock.ma20_slope or 0) < 0 and (stock.ma60_slope or 0) <= 0
    downtrend = 36 + 15 * below20 + 13 * below60 + 10 * negative_slope
    downtrend += _clamp(-stock.return_20d, -12, 22) * 0.8
    downtrend += _clamp(-stock.relative_strength_market, -12, 20) * 0.6

    decay = 39 + 12 * (not bool(stock.macd_histogram_rising)) + 10 * below20
    decay += _clamp(-stock.return_5d, -10, 15) * 1.0
    decay += _clamp(-stock.relative_strength_electronic, -15, 20) * 0.7
    decay += 6 if (stock.volume_ratio_20d or 0) >= 1.2 and stock.return_1d < 0 else 0
    return {
        "downtrend_short": round(_clamp(downtrend, 0, 100), 2),
        "momentum_decay_short": round(_clamp(decay, 0, 100), 2),
    }


def _expected_return(stock: AdaptiveStockInput, direction: Direction) -> float:
    projected_price_return = (
        stock.return_20d * 0.45
        + stock.return_5d * 4 * 0.25
        + stock.relative_strength_market * 0.20
        + stock.relative_strength_electronic * 0.10
    )
    directional = projected_price_return if direction == "long" else -projected_price_return
    return round(_clamp(max(0.5, directional), 0.5, 20), 2)


def _pick(stock: AdaptiveStockInput, direction: Direction) -> LongTermPick:
    if direction == "long":
        scores = _long_scores(stock)
        model_key = max(scores, key=scores.get)  # type: ignore[arg-type]
        names = {
            "trend_growth": "趨勢成長",
            "relative_strength": "相對強勢",
            "quality_momentum": "品質動能",
        }
        reasons = (
            f"近 20 日報酬 {stock.return_20d:+.2f}%",
            f"相對大盤強弱 {stock.relative_strength_market:+.2f}%",
            "MACD 動能回升" if stock.macd_histogram_rising else "以趨勢與產業分數入選",
        )
    else:
        scores = _short_scores(stock)
        model_key = max(scores, key=scores.get)  # type: ignore[arg-type]
        names = {"downtrend_short": "弱勢趨勢空單", "momentum_decay_short": "動能衰退空單"}
        reasons = (
            f"近 20 日報酬 {stock.return_20d:+.2f}%",
            f"相對大盤強弱 {stock.relative_strength_market:+.2f}%",
            "價格位於月線下" if stock.ma20 is not None and stock.price < stock.ma20 else "動能與相對強弱轉弱",
        )
    return LongTermPick(
        stock_code=stock.stock_code,
        stock_name=stock.stock_name,
        market_type=stock.market_type,
        industry=stock.sub_industry,
        direction=direction,
        model_key=model_key,
        model_name=names[model_key],
        score=scores[model_key],
        price=stock.price,
        predicted_month_return_pct=_expected_return(stock, direction),
        reasons=reasons,
    )


def rank_long_term_candidates(payload: AdaptiveScanPayload, mode: PortfolioMode) -> list[LongTermPick]:
    stocks = [stock for stock in payload.stocks if _eligible(stock)]
    longs = sorted((_pick(stock, "long") for stock in stocks), key=lambda item: item.score, reverse=True)
    if mode == "long_only":
        return longs[:PORTFOLIO_SIZE]
    return longs[:FOCUSED_PORTFOLIO_SIZE]


def _decimal(value: float) -> Decimal:
    return Decimal(str(round(value, 4)))


def allocation_weights(picks: list[LongTermPick], mode: PortfolioMode) -> dict[tuple[str, str], float]:
    """Score-weight each direction while keeping the intended long/short exposure."""
    result: dict[tuple[str, str], float] = {}
    direction_budgets = {"long": 100.0, "short": 0.0}
    for direction, budget in direction_budgets.items():
        group = [pick for pick in picks if pick.direction == direction]
        if not group or budget <= 0:
            continue
        scores = [max(20.0, pick.score) for pick in group]
        score_total = sum(scores)
        assigned = 0.0
        for index, (pick, score) in enumerate(zip(group, scores, strict=True)):
            weight = round(budget - assigned, 4) if index == len(group) - 1 else round(budget * score / score_total, 4)
            result[(pick.stock_code, pick.direction)] = weight
            assigned += weight
    return result


def benchmark_definitions(db: Session | None = None) -> list[dict[str, object]]:
    definitions: list[dict[str, object]] = [
        {**item, "benchmarkType": "market", "rank10Year": None, "annualizedReturn10Year": None}
        for item in BENCHMARK_DEFINITIONS
    ]
    if db is None:
        return definitions
    definitions.extend({
        "symbol": item.symbol,
        "name": item.name,
        "market": item.market,
        "benchmarkType": "ten_year_cagr",
        "rank10Year": item.rank,
        "annualizedReturn10Year": float(item.annualized_return_10y),
        "selectionDate": item.selection_date.isoformat(),
        "historyStartDate": item.history_start_date.isoformat(),
        "historyEndDate": item.history_end_date.isoformat(),
        "historyEndPrice": float(item.history_end_price),
    } for item in latest_cagr_selections(db))
    return definitions


def benchmark_quote_requests(db: Session | None = None) -> list[StockQuoteRequest]:
    return [
        StockQuoteRequest(str(item["symbol"]), str(item["name"]), str(item["market"]))
        for item in benchmark_definitions(db)
    ]


def update_benchmarks(
    db: Session,
    trade_date: date,
    at: datetime,
    prices: dict[str, float],
    definitions: list[dict[str, object]] | None = None,
) -> None:
    for definition in definitions or benchmark_definitions(db):
        symbol = str(definition["symbol"])
        price = prices.get(symbol)
        if (price is None or price <= 0) and definition.get("historyEndPrice") is not None:
            price = float(definition["historyEndPrice"])
        if price is None or price <= 0:
            continue
        benchmark = db.get(LongTermBenchmark, symbol)
        if benchmark is None:
            db.add(LongTermBenchmark(
                symbol=symbol,
                name=str(definition["name"]),
                start_date=trade_date,
                entry_price=_decimal(price),
                last_price=_decimal(price),
                updated_at=at,
            ))
        else:
            benchmark.last_price = _decimal(price)
            benchmark.updated_at = at


def _snapshot(db: Session, position: LongTermPosition, trade_date: date, at: datetime) -> None:
    existing = db.scalar(select(LongTermPositionSnapshot).where(
        LongTermPositionSnapshot.position_id == position.id,
        LongTermPositionSnapshot.trade_date == trade_date,
    ))
    actual = actual_return_percent(float(position.entry_price), float(position.last_price), position.direction)
    if existing is None:
        db.add(LongTermPositionSnapshot(
            position_id=position.id,
            trade_date=trade_date,
            price=position.last_price,
            actual_return_pct=_decimal(actual),
            predicted_month_return_pct=position.predicted_month_return_pct,
            recorded_at=at,
        ))
    else:
        existing.price = position.last_price
        existing.actual_return_pct = _decimal(actual)
        existing.recorded_at = at


def _new_position(
    mode: str,
    pick: LongTermPick,
    trade_date: date,
    at: datetime,
    allocation_weight_pct: float,
    allocated_capital: float | None = None,
) -> LongTermPosition:
    capital = allocated_capital if allocated_capital is not None else SIMULATION_CAPITAL * allocation_weight_pct / 100
    quantity = int(capital / pick.price) if pick.price > 0 else 0
    return LongTermPosition(
        entry_key=f"{mode}:{trade_date.isoformat()}:{pick.stock_code}:{pick.direction}:{at.strftime('%H%M%S%f')}",
        portfolio_mode=mode,
        stock_code=pick.stock_code,
        stock_name=pick.stock_name,
        market_type=pick.market_type,
        industry=pick.industry,
        direction=pick.direction,
        model_key=pick.model_key,
        model_name=pick.model_name,
        entry_date=trade_date,
        entry_time=at,
        minimum_exit_date=minimum_exit_date(trade_date),
        entry_price=_decimal(pick.price),
        last_price=_decimal(pick.price),
        selection_score=_decimal(pick.score),
        current_score=_decimal(pick.score),
        predicted_month_return_pct=_decimal(pick.predicted_month_return_pct),
        allocation_weight_pct=_decimal(allocation_weight_pct),
        allocated_capital=Decimal(str(round(capital, 2))),
        quantity=quantity,
        reasons_json=json.dumps(pick.reasons, ensure_ascii=False),
        status="open",
        actual_return_pct=Decimal("0"),
        created_at=at,
        updated_at=at,
    )


def _record_trade_event(
    db: Session,
    position: LongTermPosition,
    event_type: Literal["BUY", "SELL"],
    trade_date: date,
    at: datetime,
    reason: str,
) -> LongTermTradeEvent:
    if position.id is None:
        db.flush()
    event_key = f"long-term:{position.id}:{event_type}"
    existing = db.scalar(select(LongTermTradeEvent).where(LongTermTradeEvent.event_key == event_key))
    if existing is not None:
        return existing
    price = position.entry_price if event_type == "BUY" else (position.exit_price or position.last_price)
    pnl_percent = None if event_type == "BUY" else position.actual_return_pct
    pnl = None
    if pnl_percent is not None:
        pnl = Decimal(str(round(
            float(position.entry_price) * position.quantity * float(pnl_percent) / 100,
            2,
        )))
    event = LongTermTradeEvent(
        event_key=event_key,
        portfolio_mode=position.portfolio_mode,
        position_id=position.id,
        stock_code=position.stock_code,
        stock_name=position.stock_name,
        direction=position.direction,
        event_type=event_type,
        trade_date=trade_date,
        price=price,
        allocation_weight_pct=position.allocation_weight_pct,
        allocated_capital=position.allocated_capital,
        quantity=position.quantity,
        pnl=pnl,
        pnl_percent=pnl_percent,
        reason=reason,
        is_read=False,
        created_at=at,
    )
    db.add(event)
    return event


def _trade_event_payload(event: LongTermTradeEvent) -> dict[str, object]:
    return {
        "id": event.id,
        "timestamp": event.created_at.isoformat(),
        "tradeDate": event.trade_date.isoformat(),
        "portfolioMode": event.portfolio_mode,
        "positionId": event.position_id,
        "stockCode": event.stock_code,
        "stockName": event.stock_name,
        "eventType": event.event_type,
        "price": float(event.price),
        "allocationWeightPercent": float(event.allocation_weight_pct),
        "allocatedCapital": float(event.allocated_capital),
        "quantity": event.quantity,
        "pnl": float(event.pnl) if event.pnl is not None else None,
        "pnlPercent": float(event.pnl_percent) if event.pnl_percent is not None else None,
        "reason": event.reason,
        "isRead": event.is_read,
    }


def list_long_term_trade_events(
    db: Session,
    mode: PortfolioMode,
    after_id: int = 0,
    limit: int = 100,
) -> list[dict[str, object]]:
    statement = select(LongTermTradeEvent).where(
        LongTermTradeEvent.portfolio_mode == mode,
        LongTermTradeEvent.id > after_id,
    ).order_by(LongTermTradeEvent.id.desc()).limit(max(1, min(limit, 200)))
    events = list(db.scalars(statement).all())
    if after_id > 0:
        events.reverse()
    return [_trade_event_payload(event) for event in events]


def mark_long_term_trade_event_read(db: Session, event_id: int, at: datetime) -> bool:
    event = db.get(LongTermTradeEvent, event_id)
    if event is None:
        return False
    if not event.is_read:
        event.is_read = True
        event.read_at = at
        db.commit()
    return True


def run_long_term_selection(
    db: Session,
    payload: AdaptiveScanPayload,
    at: datetime,
    benchmark_prices: dict[str, float] | None = None,
    active_benchmark_definitions: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    trade_date = payload.market.trade_date
    if trade_date < LONG_TERM_START_DATE:
        return {"status": "waiting_start", "startDate": LONG_TERM_START_DATE.isoformat()}
    repair_long_term_position_overflow(db, at)
    prices = {stock.stock_code: stock.price for stock in payload.stocks}
    stocks_by_code = {stock.stock_code: stock for stock in payload.stocks}
    update_benchmarks(
        db, trade_date, at, benchmark_prices or {},
        active_benchmark_definitions,
    )
    total_opened = 0
    total_closed = 0
    mode_results: dict[str, object] = {}
    for mode in ("long_only", "focused_long"):
        existing_run = db.scalar(select(LongTermPortfolioRun).where(
            LongTermPortfolioRun.portfolio_mode == mode,
            LongTermPortfolioRun.trade_date == trade_date,
        ))
        if existing_run is not None:
            mode_results[mode] = {"status": "already_ran"}
            continue
        picks = rank_long_term_candidates(payload, mode)
        target_weights = allocation_weights(picks, mode)
        targets = {(pick.stock_code, pick.direction): pick for pick in picks}
        positions = list(db.scalars(select(LongTermPosition).where(
            LongTermPosition.portfolio_mode == mode,
            LongTermPosition.status == "open",
        )).all())
        previous_run = db.scalar(select(LongTermPortfolioRun).where(
            LongTermPortfolioRun.portfolio_mode == mode,
            LongTermPortfolioRun.trade_date < trade_date,
        ).order_by(LongTermPortfolioRun.trade_date.desc()).limit(1))
        previous_nav = float(previous_run.portfolio_nav) if previous_run is not None else 100.0
        holding_returns = [
            (
                actual_return_percent(float(position.last_price), prices[position.stock_code], position.direction),
                float(position.allocation_weight_pct),
            )
            for position in positions
            if position.stock_code in prices and float(position.last_price) > 0
        ]
        holding_weight = sum(weight for _, weight in holding_returns)
        daily_return = (
            sum(value * weight for value, weight in holding_returns) / holding_weight
            if holding_weight > 0 else 0.0
        )
        portfolio_nav = previous_nav * (1 + daily_return / 100)
        opened = 0
        closed = 0
        released_allocations: dict[str, list[tuple[float, float]]] = {"long": [], "short": []}
        for position in positions:
            latest = prices.get(position.stock_code)
            if latest is not None:
                position.last_price = _decimal(latest)
            latest_stock = stocks_by_code.get(position.stock_code)
            if latest_stock is not None:
                latest_pick = _pick(latest_stock, position.direction)  # type: ignore[arg-type]
                position.current_score = _decimal(latest_pick.score)
                position.predicted_month_return_pct = _decimal(latest_pick.predicted_month_return_pct)
            current_pick = targets.get((position.stock_code, position.direction))
            if current_pick is not None:
                position.updated_at = at
                continue
            if trading_days_held(position.entry_date, trade_date) < MINIMUM_HOLDING_TRADING_DAYS:
                position.updated_at = at
                continue
            replacement_available = any(
                pick.direction == position.direction
                and all(open_item.stock_code != pick.stock_code for open_item in positions if open_item.status == "open")
                for pick in picks
            )
            if not replacement_available:
                continue
            actual = actual_return_percent(float(position.entry_price), float(position.last_price), position.direction)
            position.status = "closed"
            position.exit_date = trade_date
            position.exit_time = at
            position.exit_price = position.last_price
            position.exit_reason = "持有滿 5 個交易日後跌出模型前段班，執行汰換"
            position.actual_return_pct = _decimal(actual)
            position.updated_at = at
            _record_trade_event(db, position, "SELL", trade_date, at, position.exit_reason)
            released_allocations[position.direction].append((
                float(position.allocation_weight_pct), float(position.allocated_capital),
            ))
            closed += 1

        open_positions = [position for position in positions if position.status == "open"]
        quotas = {"long": MODE_TARGET_COUNTS[mode], "short": 0}
        for direction, quota in quotas.items():
            direction_open = [position for position in open_positions if position.direction == direction]
            for pick in (item for item in picks if item.direction == direction):
                if len(direction_open) >= quota:
                    break
                if any(position.stock_code == pick.stock_code for position in open_positions):
                    continue
                released = released_allocations[direction].pop(0) if released_allocations[direction] else None
                weight = released[0] if released else target_weights.get((pick.stock_code, pick.direction), 10.0)
                capital = released[1] if released else SIMULATION_CAPITAL * weight / 100
                position = _new_position(mode, pick, trade_date, at, weight, capital)
                db.add(position)
                db.flush()
                _record_trade_event(db, position, "BUY", trade_date, at, "模型建立模擬買進部位")
                open_positions.append(position)
                direction_open.append(position)
                opened += 1

        for position in open_positions:
            _snapshot(db, position, trade_date, at)
        run = LongTermPortfolioRun(
            portfolio_mode=mode,
            trade_date=trade_date,
            selected_count=len(open_positions),
            opened_count=opened,
            closed_count=closed,
            portfolio_nav=Decimal(str(round(portfolio_nav, 6))),
            daily_return_pct=Decimal(str(round(daily_return, 6))),
            payload_json=json.dumps({"targetSymbols": [item.stock_code for item in picks]}, ensure_ascii=False),
            ran_at=at,
        )
        db.add(run)
        total_opened += opened
        total_closed += closed
        mode_results[mode] = {"status": "completed", "open": len(open_positions), "opened": opened, "closed": closed}
    db.commit()
    return {"status": "completed", "tradeDate": trade_date.isoformat(), "opened": total_opened, "closed": total_closed, "modes": mode_results}


def repair_long_term_position_overflow(
    db: Session,
    at: datetime | None = None,
) -> dict[str, int]:
    """Keep the configured oldest portfolio and quarantine sync-created overflow.

    Cancelled rows remain available for audit, but are deliberately not treated
    as sells and therefore never enter realized performance.
    """
    updated_at = at or datetime.now(UTC)
    repaired: dict[str, int] = {}
    for mode, target_count in MODE_TARGET_COUNTS.items():
        positions = list(db.scalars(select(LongTermPosition).where(
            LongTermPosition.portfolio_mode == mode,
            LongTermPosition.status == "open",
        ).order_by(
            LongTermPosition.entry_date,
            LongTermPosition.entry_time,
            LongTermPosition.id,
        )).all())
        kept_symbols: set[str] = set()
        kept_count = 0
        cancelled = 0
        for position in positions:
            can_keep = kept_count < target_count and position.stock_code not in kept_symbols
            if can_keep:
                kept_symbols.add(position.stock_code)
                kept_count += 1
                continue
            position.status = SYNC_DUPLICATE_STATUS
            position.exit_date = None
            position.exit_time = None
            position.exit_price = None
            position.exit_reason = "資料同步造成的重複／超額持倉，已取消且不計入交易績效"
            position.actual_return_pct = Decimal("0")
            position.updated_at = updated_at
            cancelled += 1
        repaired[mode] = cancelled
    return repaired


def long_term_portfolio_has_vacancies(db: Session) -> bool:
    for mode, target_count in MODE_TARGET_COUNTS.items():
        open_count = db.scalar(select(func.count(LongTermPosition.id)).where(
            LongTermPosition.portfolio_mode == mode,
            LongTermPosition.status == "open",
        ))
        if int(open_count or 0) < target_count:
            return True
    return False


def replenish_long_term_vacancies(
    db: Session,
    payload: AdaptiveScanPayload,
    at: datetime,
) -> dict[str, int]:
    """Immediately refill any open slot without waiting for the next daily run."""
    trade_date = payload.market.trade_date
    opened_by_mode: dict[str, int] = {"long_only": 0, "focused_long": 0}
    for mode in ("long_only", "focused_long"):
        positions = list(db.scalars(select(LongTermPosition).where(
            LongTermPosition.portfolio_mode == mode,
        )).all())
        open_positions = [item for item in positions if item.status == "open"]
        vacancy_count = MODE_TARGET_COUNTS[mode] - len(open_positions)
        if vacancy_count <= 0:
            continue

        # A stock eliminated today must not be bought straight back into the
        # vacancy. Move down the ranking until a genuinely new symbol is found.
        blocked_symbols = {item.stock_code for item in open_positions}
        blocked_symbols.update(
            item.stock_code for item in positions
            if item.status == "closed" and item.exit_date == trade_date
        )
        candidates = sorted(
            (_pick(stock, "long") for stock in payload.stocks if _eligible(stock)),
            key=lambda item: item.score,
            reverse=True,
        )
        replacements = [
            item for item in candidates if item.stock_code not in blocked_symbols
        ][:vacancy_count]
        if not replacements:
            continue

        available_weight = max(
            0.0,
            100.0 - sum(float(item.allocation_weight_pct) for item in open_positions),
        )
        available_capital = max(
            0.0,
            SIMULATION_CAPITAL - sum(float(item.allocated_capital) for item in open_positions),
        )
        score_total = sum(max(20.0, item.score) for item in replacements)
        assigned_weight = 0.0
        assigned_capital = 0.0
        for index, replacement in enumerate(replacements):
            is_last = index == len(replacements) - 1
            score_share = max(20.0, replacement.score) / score_total
            weight = (
                round(available_weight - assigned_weight, 4)
                if is_last else round(available_weight * score_share, 4)
            )
            capital = (
                round(available_capital - assigned_capital, 4)
                if is_last else round(available_capital * score_share, 4)
            )
            position = _new_position(mode, replacement, trade_date, at, weight, capital)
            db.add(position)
            db.flush()
            _record_trade_event(
                db,
                position,
                "BUY",
                trade_date,
                at,
                "原持股汰汰後立即補入新的高分候選股",
            )
            _snapshot(db, position, trade_date, at)
            blocked_symbols.add(replacement.stock_code)
            open_positions.append(position)
            assigned_weight += weight
            assigned_capital += capital
            opened_by_mode[mode] += 1
    db.commit()
    return opened_by_mode


def replace_long_term_position(
    db: Session,
    position_id: int,
    payload: AdaptiveScanPayload,
    at: datetime,
) -> dict[str, object]:
    position = db.get(LongTermPosition, position_id)
    if position is None or position.status != "open":
        raise LookupError("找不到可汰換的長線模擬部位")
    trade_date = payload.market.trade_date
    held = trading_days_held(position.entry_date, trade_date)
    if held < MINIMUM_HOLDING_TRADING_DAYS:
        raise ValueError(f"尚未持有滿 {MINIMUM_HOLDING_TRADING_DAYS} 個交易日")
    mode = position.portfolio_mode
    if mode not in {"long_only", "focused_long"}:
        raise ValueError("長線組合模式不正確")
    open_positions = list(db.scalars(select(LongTermPosition).where(
        LongTermPosition.portfolio_mode == mode,
        LongTermPosition.status == "open",
    )).all())
    open_symbols = {item.stock_code for item in open_positions}
    replacement_candidates = []
    for stock in payload.stocks:
        if not _eligible(stock):
            continue
        replacement_candidates.append(_pick(stock, "long"))
    replacement_candidates.sort(key=lambda item: item.score, reverse=True)
    replacement = next((
        pick for pick in replacement_candidates
        if pick.stock_code not in open_symbols
    ), None)
    if replacement is None:
        raise ValueError("目前沒有同方向且分數合格的補位標的")
    latest = next((stock.price for stock in payload.stocks if stock.stock_code == position.stock_code), None)
    if latest is not None:
        position.last_price = _decimal(latest)
    actual = actual_return_percent(float(position.entry_price), float(position.last_price), position.direction)
    position.status = "closed"
    position.exit_date = trade_date
    position.exit_time = at
    position.exit_price = position.last_price
    position.exit_reason = "持有滿 5 個交易日後手動賣出汰換"
    position.actual_return_pct = _decimal(actual)
    position.updated_at = at
    _record_trade_event(db, position, "SELL", trade_date, at, position.exit_reason)

    new_position = _new_position(
        mode, replacement, trade_date, at,
        float(position.allocation_weight_pct), float(position.allocated_capital),
    )
    db.add(new_position)
    db.flush()
    _record_trade_event(db, new_position, "BUY", trade_date, at, "賣出汰換後由模型補入新部位")
    _snapshot(db, new_position, trade_date, at)
    db.commit()
    return {
        "status": "replaced",
        "closedPositionId": position.id,
        "closedSymbol": position.stock_code,
        "actualReturnPercent": actual,
        "newPositionId": new_position.id,
        "newSymbol": new_position.stock_code,
        "newName": new_position.stock_name,
        "direction": new_position.direction,
    }


def _position_dividend_per_share(
    position: LongTermPosition,
    history: DividendHistory | None,
    current_date: date,
    not_before: date | None = None,
) -> float:
    if history is None or not history.available:
        return 0.0
    end_date = min(position.exit_date or current_date, current_date)
    entry_date = max(position.entry_date, not_before) if not_before is not None else position.entry_date
    return dividends_per_share(history.events, entry_date, end_date)


def _position_dividend_income(
    position: LongTermPosition,
    history: DividendHistory | None,
    current_date: date,
    not_before: date | None = None,
) -> float:
    per_share = _position_dividend_per_share(position, history, current_date, not_before)
    direction_factor = -1 if position.direction == "short" else 1
    return round(per_share * position.quantity * direction_factor, 2)


def _position_payload(
    position: LongTermPosition,
    current_price: float,
    current_date: date,
    dividend_history: DividendHistory | None = None,
) -> dict[str, object]:
    held = trading_days_held(position.entry_date, current_date)
    dividend_per_share = _position_dividend_per_share(
        position, dividend_history, current_date,
    )
    returns = total_return_breakdown(
        float(position.entry_price), current_price, position.direction, dividend_per_share,
    )
    direction_factor = -1 if position.direction == "short" else 1
    invested_capital = float(position.entry_price) * position.quantity
    price_profit = (current_price - float(position.entry_price)) * position.quantity * direction_factor
    dividend_income = dividend_per_share * position.quantity * direction_factor
    total_profit = price_profit + dividend_income
    try:
        reasons = json.loads(position.reasons_json)
    except ValueError:
        reasons = []
    return {
        "id": position.id,
        "symbol": position.stock_code,
        "name": position.stock_name,
        "market": position.market_type,
        "industry": position.industry,
        "direction": position.direction,
        "modelKey": position.model_key,
        "modelName": position.model_name,
        "entryDate": position.entry_date.isoformat(),
        "entryPrice": float(position.entry_price),
        "currentPrice": current_price,
        "actualReturnPercent": returns["totalReturnPercent"],
        **returns,
        "dividendPerShare": round(dividend_per_share, 4),
        "dividendIncome": round(dividend_income, 2),
        "dividendDataAvailable": bool(dividend_history and dividend_history.available),
        "predictedMonthReturnPercent": float(position.predicted_month_return_pct),
        "allocationWeightPercent": float(position.allocation_weight_pct),
        "allocatedCapital": float(position.allocated_capital),
        "quantity": position.quantity,
        "investedCapital": round(invested_capital, 2),
        "priceProfit": round(price_profit, 2),
        "unrealizedProfit": round(total_profit, 2),
        "totalProfit": round(total_profit, 2),
        "selectionScore": float(position.selection_score),
        "currentScore": float(position.current_score),
        "holdingTradingDays": held,
        "minimumHoldingDays": MINIMUM_HOLDING_TRADING_DAYS,
        "eligibleToReplace": held >= MINIMUM_HOLDING_TRADING_DAYS,
        "minimumExitDate": position.minimum_exit_date.isoformat(),
        "reasons": reasons,
    }


def _closed_position_payload(
    position: LongTermPosition,
    current_date: date,
    dividend_history: DividendHistory | None = None,
) -> dict[str, object]:
    exit_price = float(position.exit_price or position.last_price)
    dividend_per_share = _position_dividend_per_share(
        position, dividend_history, current_date,
    )
    returns = total_return_breakdown(
        float(position.entry_price), exit_price, position.direction, dividend_per_share,
    )
    direction_factor = -1 if position.direction == "short" else 1
    price_profit = (
        exit_price - float(position.entry_price)
    ) * position.quantity * direction_factor
    dividend_income = dividend_per_share * position.quantity * direction_factor
    return {
        "id": position.id,
        "symbol": position.stock_code,
        "name": position.stock_name,
        "direction": position.direction,
        "modelName": position.model_name,
        "entryDate": position.entry_date.isoformat(),
        "exitDate": position.exit_date.isoformat() if position.exit_date else None,
        "entryPrice": float(position.entry_price),
        "exitPrice": exit_price,
        "actualReturnPercent": returns["totalReturnPercent"],
        **returns,
        "dividendPerShare": round(dividend_per_share, 4),
        "dividendIncome": round(dividend_income, 2),
        "priceProfit": round(price_profit, 2),
        "totalProfit": round(price_profit + dividend_income, 2),
        "dividendDataAvailable": bool(dividend_history and dividend_history.available),
        "exitReason": position.exit_reason,
    }


async def portfolio_payload(db: Session, mode: PortfolioMode) -> dict[str, object]:
    current = datetime.now(UTC)
    current_date = current.astimezone(TAIPEI).date()
    open_positions = list(db.scalars(select(LongTermPosition).where(
        LongTermPosition.portfolio_mode == mode,
        LongTermPosition.status == "open",
    ).order_by(LongTermPosition.direction, LongTermPosition.selection_score.desc())).all())
    all_closed_positions = list(db.scalars(select(LongTermPosition).where(
        LongTermPosition.portfolio_mode == mode,
        LongTermPosition.status == "closed",
    ).order_by(LongTermPosition.exit_time.desc())).all())
    closed_positions = all_closed_positions[:30]
    requests = [StockQuoteRequest(position.stock_code, position.stock_name, position.market_type) for position in open_positions]
    active_benchmarks = benchmark_definitions(db)
    requests.extend([
        StockQuoteRequest(str(item["symbol"]), str(item["name"]), str(item["market"]))
        for item in active_benchmarks
    ])
    dividend_requests = [
        (position.stock_code, position.market_type)
        for position in (*open_positions, *all_closed_positions)
    ]
    dividend_requests.extend(
        (str(item["symbol"]), str(item["market"])) for item in active_benchmarks
    )
    quotes, dividend_histories = await asyncio.gather(
        official_market_data_provider.get_quotes(requests),
        long_term_dividend_provider.get_histories(dividend_requests),
    )
    items = [
        _position_payload(
            position,
            quotes[position.stock_code].price if position.stock_code in quotes else float(position.last_price),
            current_date,
            dividend_histories.get(position.stock_code),
        )
        for position in open_positions
    ]
    realized_profit_value = 0.0
    realized_capital_value = 0.0
    for position in all_closed_positions:
        exit_price = float(position.exit_price or position.last_price)
        direction_factor = -1 if position.direction == "short" else 1
        price_profit = (
            exit_price - float(position.entry_price)
        ) * position.quantity * direction_factor
        realized_profit_value += price_profit + _position_dividend_income(
            position, dividend_histories.get(position.stock_code), current_date,
        )
        realized_capital_value += float(position.entry_price) * position.quantity
    item_weight = sum(float(item["allocationWeightPercent"]) for item in items)
    weighted_actual_return = (
        sum(float(item["actualReturnPercent"]) * float(item["allocationWeightPercent"]) for item in items) / item_weight
        if item_weight > 0 else 0.0
    )
    weighted_predicted_return = (
        sum(float(item["predictedMonthReturnPercent"]) * float(item["allocationWeightPercent"]) for item in items) / item_weight
        if item_weight > 0 else 0.0
    )
    last_run = db.scalar(select(LongTermPortfolioRun).where(
        LongTermPortfolioRun.portfolio_mode == mode,
    ).order_by(LongTermPortfolioRun.trade_date.desc()).limit(1))
    stored_nav = float(last_run.portfolio_nav) if last_run is not None else 100.0
    live_returns = [
        (
            actual_return_percent(
                float(position.last_price),
                quotes[position.stock_code].price,
                position.direction,
            ),
            float(position.allocation_weight_pct),
        )
        for position in open_positions
        if position.stock_code in quotes and float(position.last_price) > 0
    ]
    live_weight = sum(weight for _, weight in live_returns)
    live_return = (
        sum(value * weight for value, weight in live_returns) / live_weight
        if live_weight > 0 else 0.0
    )
    price_model_nav = stored_nav * (1 + live_return / 100)
    tracked_positions = [*open_positions, *all_closed_positions]
    model_dividend_income = sum(
        _position_dividend_income(
            position, dividend_histories.get(position.stock_code), current_date,
        )
        for position in tracked_positions
    )
    model_dividend_nav_points = model_dividend_income / SIMULATION_CAPITAL * 100
    current_model_nav = price_model_nav + model_dividend_nav_points
    model_return = round(current_model_nav - 100, 2)
    benchmarks_by_symbol = {
        item.symbol: item for item in db.scalars(select(LongTermBenchmark)).all()
    }
    benchmark_rows: list[dict[str, object]] = []
    benchmark_leads: list[float] = []
    market_benchmarks = [
        item for item in active_benchmarks if item.get("benchmarkType") != "ten_year_cagr"
    ]
    cagr_benchmarks = [
        item for item in active_benchmarks if item.get("benchmarkType") == "ten_year_cagr"
    ]
    for definition in market_benchmarks:
        symbol = str(definition["symbol"])
        stored = benchmarks_by_symbol.get(symbol)
        current_price = (
            quotes[symbol].price if symbol in quotes
            else float(stored.last_price) if stored is not None
            else None
        )
        dividend_history = dividend_histories.get(symbol)
        dividend_per_share = (
            dividends_per_share(dividend_history.events, stored.start_date, current_date)
            if stored is not None and dividend_history is not None and dividend_history.available
            else 0.0
        )
        benchmark_returns = total_return_breakdown(
            float(stored.entry_price), current_price, "long", dividend_per_share,
        ) if stored is not None and current_price is not None else {
            "priceReturnPercent": 0.0,
            "dividendReturnPercent": 0.0,
            "totalReturnPercent": 0.0,
        }
        benchmark_return = round(benchmark_returns["totalReturnPercent"], 2)
        baseline_run = db.scalar(select(LongTermPortfolioRun).where(
            LongTermPortfolioRun.portfolio_mode == mode,
            LongTermPortfolioRun.trade_date <= stored.start_date,
        ).order_by(LongTermPortfolioRun.trade_date.desc()).limit(1)) if stored is not None else None
        baseline_model_nav = float(baseline_run.portfolio_nav) if baseline_run is not None else 100.0
        comparable_dividend_income = sum(
            _position_dividend_income(
                position,
                dividend_histories.get(position.stock_code),
                current_date,
                stored.start_date if stored is not None else LONG_TERM_START_DATE,
            )
            for position in tracked_positions
        )
        comparable_model_nav = (
            price_model_nav + comparable_dividend_income / SIMULATION_CAPITAL * 100
        )
        comparable_model_return = round(
            (comparable_model_nav / baseline_model_nav - 1) * 100,
            2,
        ) if baseline_model_nav > 0 else model_return
        lead = round(comparable_model_return - benchmark_return, 2)
        if stored is not None:
            benchmark_leads.append(lead)
        benchmark_rows.append({
            "key": symbol,
            "name": str(definition["name"]),
            "symbol": symbol,
            "isModel": False,
            "benchmarkType": definition.get("benchmarkType", "market"),
            "rank10Year": definition.get("rank10Year"),
            "annualizedReturn10Year": definition.get("annualizedReturn10Year"),
            "selectionDate": definition.get("selectionDate"),
            "historyStartDate": definition.get("historyStartDate"),
            "historyEndDate": definition.get("historyEndDate"),
            "startDate": stored.start_date.isoformat() if stored is not None else LONG_TERM_START_DATE.isoformat(),
            "startPrice": float(stored.entry_price) if stored is not None else None,
            "currentPrice": current_price,
            "cumulativeReturnPercent": benchmark_return,
            "priceReturnPercent": round(benchmark_returns["priceReturnPercent"], 2),
            "dividendReturnPercent": round(benchmark_returns["dividendReturnPercent"], 2),
            "dividendPerShare": round(dividend_per_share, 4),
            "dividendDataAvailable": bool(dividend_history and dividend_history.available),
            "modelComparableReturnPercent": comparable_model_return,
            "leadVsBenchmarkPercent": lead,
            "status": "leading" if lead > 0 else "trailing" if lead < 0 else "tied",
        })

    cagr_components: list[dict[str, object]] = []
    cagr_component_weight = round(100 / len(cagr_benchmarks), 4) if cagr_benchmarks else 0.0
    for definition in cagr_benchmarks:
        symbol = str(definition["symbol"])
        stored = benchmarks_by_symbol.get(symbol)
        current_price = (
            quotes[symbol].price if symbol in quotes
            else float(stored.last_price) if stored is not None
            else None
        )
        dividend_history = dividend_histories.get(symbol)
        dividend_per_share = (
            dividends_per_share(dividend_history.events, stored.start_date, current_date)
            if stored is not None and dividend_history is not None and dividend_history.available
            else 0.0
        )
        component_returns = total_return_breakdown(
            float(stored.entry_price), current_price, "long", dividend_per_share,
        ) if stored is not None and current_price is not None else {
            "priceReturnPercent": 0.0,
            "dividendReturnPercent": 0.0,
            "totalReturnPercent": 0.0,
        }
        component_return = component_returns["totalReturnPercent"]
        cagr_components.append({
            "rank": definition.get("rank10Year"),
            "symbol": symbol,
            "name": str(definition["name"]),
            "annualizedReturn10Year": definition.get("annualizedReturn10Year"),
            "allocationWeightPercent": cagr_component_weight,
            "startDate": stored.start_date.isoformat() if stored is not None else None,
            "entryPrice": float(stored.entry_price) if stored is not None else None,
            "currentPrice": current_price,
            "returnPercent": component_return,
            "priceReturnPercent": component_returns["priceReturnPercent"],
            "dividendReturnPercent": component_returns["dividendReturnPercent"],
            "dividendPerShare": round(dividend_per_share, 4),
            "dividendDataAvailable": bool(dividend_history and dividend_history.available),
            "hasTrackingPrice": stored is not None,
        })
    if cagr_components:
        tracked_components = [item for item in cagr_components if item["hasTrackingPrice"]]
        group_return = round(
            sum(float(item["returnPercent"]) for item in tracked_components) / len(tracked_components),
            2,
        ) if tracked_components else 0.0
        group_start_date = max(
            (
                date.fromisoformat(str(item["startDate"]))
                for item in tracked_components if item["startDate"] is not None
            ),
            default=LONG_TERM_START_DATE,
        )
        baseline_run = db.scalar(select(LongTermPortfolioRun).where(
            LongTermPortfolioRun.portfolio_mode == mode,
            LongTermPortfolioRun.trade_date <= group_start_date,
        ).order_by(LongTermPortfolioRun.trade_date.desc()).limit(1))
        baseline_model_nav = float(baseline_run.portfolio_nav) if baseline_run is not None else 100.0
        comparable_dividend_income = sum(
            _position_dividend_income(
                position,
                dividend_histories.get(position.stock_code),
                current_date,
                group_start_date,
            )
            for position in tracked_positions
        )
        comparable_model_nav = (
            price_model_nav + comparable_dividend_income / SIMULATION_CAPITAL * 100
        )
        comparable_model_return = round(
            (comparable_model_nav / baseline_model_nav - 1) * 100,
            2,
        ) if baseline_model_nav > 0 else model_return
        group_lead = round(comparable_model_return - group_return, 2)
        if tracked_components:
            benchmark_leads.append(group_lead)
        cagr_values = [
            float(item["annualizedReturn10Year"])
            for item in cagr_components
            if item["annualizedReturn10Year"] is not None
        ]
        selection_dates = [str(item["selectionDate"]) for item in cagr_benchmarks if item.get("selectionDate")]
        history_starts = [str(item["historyStartDate"]) for item in cagr_benchmarks if item.get("historyStartDate")]
        history_ends = [str(item["historyEndDate"]) for item in cagr_benchmarks if item.get("historyEndDate")]
        benchmark_rows.append({
            "key": "ten_year_cagr_top50",
            "name": "近10年年化報酬率最高50檔",
            "symbol": None,
            "isModel": False,
            "benchmarkType": "ten_year_cagr_group",
            "rank10Year": None,
            "annualizedReturn10Year": round(sum(cagr_values) / len(cagr_values), 2) if cagr_values else None,
            "selectionDate": max(selection_dates) if selection_dates else None,
            "historyStartDate": max(history_starts) if history_starts else None,
            "historyEndDate": min(history_ends) if history_ends else None,
            "startDate": group_start_date.isoformat(),
            "startPrice": 100.0 if tracked_components else None,
            "currentPrice": round(100 + group_return, 2) if tracked_components else None,
            "cumulativeReturnPercent": group_return,
            "priceReturnPercent": round(sum(
                float(item["priceReturnPercent"]) for item in tracked_components
            ) / len(tracked_components), 2) if tracked_components else 0.0,
            "dividendReturnPercent": round(sum(
                float(item["dividendReturnPercent"]) for item in tracked_components
            ) / len(tracked_components), 2) if tracked_components else 0.0,
            "dividendDataAvailable": all(
                bool(item["dividendDataAvailable"]) for item in tracked_components
            ) if tracked_components else False,
            "modelComparableReturnPercent": comparable_model_return,
            "leadVsBenchmarkPercent": group_lead,
            "status": "leading" if group_lead > 0 else "trailing" if group_lead < 0 else "tied",
            "componentCount": len(cagr_components),
            "constituents": cagr_components,
        })
    comparison_rows = [{
        "key": mode,
        "name": "10 檔穩健多方" if mode == "long_only" else "3 檔精選多方",
        "symbol": None,
        "isModel": True,
        "benchmarkType": "model",
        "rank10Year": None,
        "annualizedReturn10Year": None,
        "selectionDate": None,
        "historyStartDate": None,
        "historyEndDate": None,
        "startDate": LONG_TERM_START_DATE.isoformat(),
        "startPrice": 100.0,
        "currentPrice": round(100 + model_return, 2),
        "cumulativeReturnPercent": model_return,
        "priceReturnPercent": round(price_model_nav - 100, 2),
        "dividendReturnPercent": round(model_dividend_nav_points, 2),
        "dividendIncome": round(model_dividend_income, 2),
        "dividendDataAvailable": all(
            bool(dividend_histories.get(position.stock_code) and dividend_histories[position.stock_code].available)
            for position in tracked_positions
        ) if tracked_positions else True,
        "leadVsBenchmarkPercent": None,
        "status": "model",
    }, *benchmark_rows]
    closed_payloads = [
        _closed_position_payload(
            position, current_date, dividend_histories.get(position.stock_code),
        )
        for position in all_closed_positions
    ]
    closed_payloads_by_id = {item["id"]: item for item in closed_payloads}
    unrealized_profit = sum(float(item["unrealizedProfit"]) for item in items)
    unrealized_price_profit = sum(float(item["priceProfit"]) for item in items)
    open_dividend_income = sum(float(item["dividendIncome"]) for item in items)
    realized_profit = float(realized_profit_value)
    realized_dividend_income = sum(
        _position_dividend_income(
            position, dividend_histories.get(position.stock_code), current_date,
        )
        for position in all_closed_positions
    )
    realized_price_profit = realized_profit - realized_dividend_income
    total_dividend_income = open_dividend_income + realized_dividend_income
    realized_return = (
        realized_profit / realized_capital_value * 100
        if realized_capital_value > 0 else 0.0
    )
    invested_capital = sum(float(item["investedCapital"]) for item in items)
    planned_capital = sum(float(item["allocatedCapital"]) for item in items)
    trade_messages = list_long_term_trade_events(db, mode, limit=100)
    for message in trade_messages:
        closed = closed_payloads_by_id.get(message["positionId"])
        if message["eventType"] == "SELL" and closed is not None:
            message["pnl"] = closed["totalProfit"]
            message["pnlPercent"] = closed["totalReturnPercent"]
            message["reason"] = f"{message['reason']}（績效已含持有期間配息）"
    return {
        "mode": mode,
        "modeLabel": "10 檔穩健多方" if mode == "long_only" else "3 檔精選多方",
        "startDate": LONG_TERM_START_DATE.isoformat(),
        "selectionTime": LONG_TERM_SELECTION_TIME,
        "targetCount": MODE_TARGET_COUNTS[mode],
        "minimumHoldingTradingDays": MINIMUM_HOLDING_TRADING_DAYS,
        "models": MODEL_DEFINITIONS[mode],
        "items": items,
        "closedItems": closed_payloads[:len(closed_positions)],
        "summary": {
            "openCount": len(items),
            "longCount": sum(item["direction"] == "long" for item in items),
            "shortCount": sum(item["direction"] == "short" for item in items),
            "actualReturnPercent": round(weighted_actual_return, 2),
            "predictedMonthReturnPercent": round(weighted_predicted_return, 2),
            "realizedReturnPercent": round(realized_return, 2),
            "completedTradeCount": len(all_closed_positions),
        },
        "capitalAllocation": {
            "totalCapital": SIMULATION_CAPITAL,
            "plannedCapital": round(planned_capital, 2),
            "investedCapital": round(invested_capital, 2),
            "unallocatedCapital": round(max(0.0, SIMULATION_CAPITAL - invested_capital), 2),
            "unrealizedProfit": round(unrealized_profit, 2),
            "unrealizedPriceProfit": round(unrealized_price_profit, 2),
            "openDividendIncome": round(open_dividend_income, 2),
            "realizedProfit": round(realized_profit, 2),
            "realizedPriceProfit": round(realized_price_profit, 2),
            "realizedDividendIncome": round(realized_dividend_income, 2),
            "dividendIncome": round(total_dividend_income, 2),
            "totalProfit": round(realized_profit + unrealized_profit, 2),
            "estimatedEquity": round(SIMULATION_CAPITAL + realized_profit + unrealized_profit, 2),
            "methodology": f"本組合獨立使用 100 萬模擬本金，{MODE_TARGET_COUNTS[mode]} 檔純多標的依模型分數配置 100%，支援零股並保留未成交餘額；現金股息列入組合資產但不假設再投入。",
        },
        "performanceComparison": {
            "rows": comparison_rows,
            "modelReturnPercent": model_return,
            "beatsAllBenchmarks": bool(benchmark_leads) and all(value > 0 for value in benchmark_leads),
            "goal": "模型累積績效挑戰 0050、00881、00631L 與近 10 年年化報酬率最高的 50 檔台股",
            "methodology": (
                "公平比較：所有標的從加入比較當日的價格起算含息總報酬，現金股息列入但不假設再投入；"
                "10 年 TOP 50 組合採每檔 2% 等權重計算，歷史年化欄為 50 檔成分 CAGR 的等權平均；"
                "年化排名使用還原權息月資料，只用於動態選出比較組，不會回填成模型績效。"
            ),
            "returnBasis": "cash_dividend_total_return",
        },
        "tradeMessages": trade_messages,
        "unreadTradeMessageCount": sum(not bool(item["isRead"]) for item in trade_messages),
        "lastSelectionDate": last_run.trade_date.isoformat() if last_run else None,
        "lastSelectionAt": last_run.ran_at.isoformat() if last_run else None,
        "status": "active" if items else "waiting_start",
        "notice": "模型報酬為一個月方向性估計；實際績效為純多模擬追蹤，已加入持有期間現金股息，未含交易成本與稅費。",
        "dividendData": {
            "source": "Yahoo Finance 除息事件",
            "availableCount": sum(history.available for history in dividend_histories.values()),
            "requestedCount": len(dividend_histories),
            "methodology": "除息日須晚於模擬買進日且不晚於賣出／目前日期；依每股配息乘以實際模擬股數計入，未假設股息再投入。",
        },
        "updatedAt": current.isoformat(),
    }
