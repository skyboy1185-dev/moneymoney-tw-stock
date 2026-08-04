from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, ROUND_HALF_UP
import json
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..adaptive_schemas import AdaptiveScanPayload
from ..models import (
    AdaptivePaperTrade,
    AdaptiveSignal,
    AdaptiveStockCandidate,
    AdaptiveStockMonitoring,
)
from .adaptive_entry_window import adaptive_entry_window_open


PAPER_TRADE_SHARES = 1000
AUTOMATION_USER_ID = "system-adaptive-electronic"
COMMISSION_RATE = Decimal("0.001425")
COMMISSION_DISCOUNT = Decimal("0.6")
SECURITIES_TAX_RATE = Decimal("0.003")
MINIMUM_COMMISSION = Decimal("20")
MONEY = Decimal("0.01")
TAIPEI = ZoneInfo("Asia/Taipei")


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def estimated_trade_result(
    entry_price: Decimal,
    exit_price: Decimal,
    quantity_shares: int = PAPER_TRADE_SHARES,
) -> dict[str, Decimal]:
    quantity = Decimal(quantity_shares)
    buy_amount = entry_price * quantity
    sell_amount = exit_price * quantity
    buy_commission = max(MINIMUM_COMMISSION, buy_amount * COMMISSION_RATE * COMMISSION_DISCOUNT)
    sell_commission = max(MINIMUM_COMMISSION, sell_amount * COMMISSION_RATE * COMMISSION_DISCOUNT)
    tax = sell_amount * SECURITIES_TAX_RATE
    gross = sell_amount - buy_amount
    cost = buy_commission + sell_commission + tax
    net = gross - cost
    invested = buy_amount + buy_commission
    return {
        "grossProfit": _money(gross),
        "tradingCost": _money(cost),
        "netProfit": _money(net),
        "returnPercentage": (net / invested * Decimal(100)).quantize(
            Decimal("0.0001"),
            rounding=ROUND_HALF_UP,
        ),
    }


def win_rate_from_profits(profits: Iterable[Decimal]) -> float:
    values = list(profits)
    if not values:
        return 0.0
    return round(sum(1 for value in values if value > 0) / len(values) * 100, 2)


def _reasons(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
        return [str(item) for item in parsed] if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        return []


def _exit_reason(
    trade: AdaptivePaperTrade,
    price: Decimal,
    regime: str,
    candidate: AdaptiveStockCandidate | None,
) -> str | None:
    if price <= trade.stop_loss_price:
        return "跌破停損價"
    if price >= trade.target_price_2:
        return "到達第二目標價"
    if regime == "CRASH":
        return "市場切換為崩盤防守模式"
    if candidate is not None and candidate.health_score < Decimal("55"):
        return "健康度跌破 55 分"
    return None


def update_adaptive_paper_trades(
    db: Session,
    payload: AdaptiveScanPayload,
    candidates: list[AdaptiveStockCandidate],
    signals: list[AdaptiveSignal],
    regime: str,
) -> list[AdaptiveSignal]:
    """Update the global AI paper ledger from official adaptive scan prices."""
    stocks = {stock.stock_code: stock for stock in payload.stocks}
    candidate_by_symbol = {candidate.stock_code: candidate for candidate in candidates}
    emitted: list[AdaptiveSignal] = []
    exited_symbols: set[str] = set()

    open_trades = list(db.scalars(select(AdaptivePaperTrade).where(
        AdaptivePaperTrade.status == "open",
    )).all())
    for trade in open_trades:
        stock = stocks.get(trade.stock_code)
        if stock is None or stock.price <= 0:
            continue
        price = Decimal(str(stock.price))
        result = estimated_trade_result(trade.entry_price, price, trade.quantity_shares)
        trade.last_price = price
        trade.unrealized_profit = result["netProfit"]
        trade.updated_at = payload.market.updated_at
        reason = _exit_reason(
            trade,
            price,
            regime,
            candidate_by_symbol.get(trade.stock_code),
        )
        if reason is None:
            continue

        signal_key = f"adaptive-exit:{payload.market.trade_date}:{trade.stock_code}:{trade.id}"
        trade.status = "closed"
        trade.exit_signal_key = signal_key
        trade.exit_price = price
        trade.exit_time = payload.market.updated_at
        trade.exit_reason = reason
        trade.gross_profit = result["grossProfit"]
        trade.trading_cost = result["tradingCost"]
        trade.net_profit = result["netProfit"]
        trade.return_percentage = result["returnPercentage"]
        trade.unrealized_profit = Decimal("0")
        exited_symbols.add(trade.stock_code)

        if db.scalar(select(AdaptiveSignal.id).where(AdaptiveSignal.signal_key == signal_key)) is None:
            signal = AdaptiveSignal(
                signal_key=signal_key,
                stock_code=trade.stock_code,
                stock_name=trade.stock_name,
                signal_type="exit_triggered",
                action=f"模擬賣出（{reason}）",
                strategy_type=trade.strategy_type,
                price=price,
                health_score=(
                    candidate_by_symbol[trade.stock_code].health_score
                    if trade.stock_code in candidate_by_symbol
                    else None
                ),
                reasons_json=json.dumps(
                    [reason, f"模擬損益 {result['netProfit']:+,.0f} 元"],
                    ensure_ascii=False,
                ),
                line_push_status="pending",
                created_at=payload.market.updated_at,
            )
            db.add(signal)
            emitted.append(signal)

        monitor = db.scalar(select(AdaptiveStockMonitoring).where(
            AdaptiveStockMonitoring.user_id == AUTOMATION_USER_ID,
            AdaptiveStockMonitoring.stock_code == trade.stock_code,
        ).order_by(AdaptiveStockMonitoring.updated_at.desc()).limit(1))
        if monitor is not None:
            monitor.entry_price = trade.entry_price
            monitor.monitor_status = "closed"
            monitor.last_signal = "exit_triggered"
            monitor.removed_reason = reason
            monitor.updated_at = payload.market.updated_at

    if not adaptive_entry_window_open(
        payload.market.updated_at,
        payload.market.market_open,
        payload.market.trade_date,
    ):
        return emitted

    entry_signals = {
        signal.signal_key: signal
        for signal in signals
        if signal.signal_type == "entry_confirmed"
    }
    recent_entries = db.scalars(select(AdaptiveSignal).where(
        AdaptiveSignal.signal_type == "entry_confirmed",
    ).order_by(AdaptiveSignal.created_at.desc()).limit(100)).all()
    for signal in recent_entries:
        created_at = signal.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=TAIPEI)
        if created_at.astimezone(TAIPEI).date() == payload.market.trade_date:
            entry_signals[signal.signal_key] = signal

    for signal in entry_signals.values():
        if signal.signal_type != "entry_confirmed" or signal.stock_code is None or signal.price is None:
            continue
        if signal.stock_code in exited_symbols:
            continue
        candidate = candidate_by_symbol.get(signal.stock_code)
        if candidate is None or candidate.candidate_status != "can_enter":
            continue
        existing = db.scalar(select(AdaptivePaperTrade.id).where(
            (AdaptivePaperTrade.entry_signal_key == signal.signal_key)
            | (
                (AdaptivePaperTrade.stock_code == signal.stock_code)
                & (AdaptivePaperTrade.status == "open")
            ),
        ))
        if existing is not None:
            continue
        reasons = _reasons(signal.reasons_json)
        trade = AdaptivePaperTrade(
            stock_code=signal.stock_code,
            stock_name=signal.stock_name or candidate.stock_name,
            strategy_type=signal.strategy_type or candidate.strategy_type,
            entry_signal_key=signal.signal_key,
            quantity_shares=PAPER_TRADE_SHARES,
            entry_price=signal.price,
            entry_time=signal.created_at,
            entry_reason="；".join(reasons[:5]) or "AI 選股正式進場條件成立",
            stop_loss_price=candidate.stop_loss_price,
            target_price_1=candidate.target_price_1,
            target_price_2=candidate.target_price_2,
            last_price=signal.price,
            status="open",
            unrealized_profit=Decimal("0"),
            created_at=signal.created_at,
            updated_at=signal.created_at,
        )
        db.add(trade)
        monitor = db.scalar(select(AdaptiveStockMonitoring).where(
            AdaptiveStockMonitoring.user_id == AUTOMATION_USER_ID,
            AdaptiveStockMonitoring.stock_code == signal.stock_code,
        ).order_by(AdaptiveStockMonitoring.updated_at.desc()).limit(1))
        if monitor is not None:
            monitor.entry_price = signal.price
            monitor.monitor_status = "holding"
            monitor.last_signal = "entry_confirmed"
            monitor.updated_at = signal.created_at
    return emitted


def _trade_payload(item: AdaptivePaperTrade) -> dict[str, Any]:
    return {
        "id": item.id,
        "stockCode": item.stock_code,
        "stockName": item.stock_name,
        "strategyType": item.strategy_type,
        "quantityShares": item.quantity_shares,
        "quantityLots": item.quantity_shares / 1000,
        "entryPrice": float(item.entry_price),
        "entryTime": item.entry_time.isoformat(),
        "entryReason": item.entry_reason,
        "stopLossPrice": float(item.stop_loss_price),
        "targetPrice1": float(item.target_price_1),
        "targetPrice2": float(item.target_price_2),
        "lastPrice": float(item.last_price),
        "status": item.status,
        "exitPrice": float(item.exit_price) if item.exit_price is not None else None,
        "exitTime": item.exit_time.isoformat() if item.exit_time is not None else None,
        "exitReason": item.exit_reason,
        "grossProfit": float(item.gross_profit),
        "tradingCost": float(item.trading_cost),
        "netProfit": float(item.net_profit),
        "returnPercentage": float(item.return_percentage),
        "unrealizedProfit": float(item.unrealized_profit),
        "updatedAt": item.updated_at.isoformat(),
    }


def _month_bounds(month: str) -> tuple[datetime, datetime]:
    year, month_number = (int(value) for value in month.split("-", 1))
    start = datetime(year, month_number, 1, tzinfo=TAIPEI)
    end = (
        datetime(year + 1, 1, 1, tzinfo=TAIPEI)
        if month_number == 12
        else datetime(year, month_number + 1, 1, tzinfo=TAIPEI)
    )
    return start.astimezone(UTC), end.astimezone(UTC)


def performance_payload(
    db: Session,
    limit: int = 100,
    month: str | None = None,
) -> dict[str, Any]:
    closed_query = select(AdaptivePaperTrade).where(AdaptivePaperTrade.status == "closed")
    open_query = select(AdaptivePaperTrade).where(AdaptivePaperTrade.status == "open")
    if month:
        start, end = _month_bounds(month)
        closed_query = closed_query.where(
            AdaptivePaperTrade.exit_time >= start,
            AdaptivePaperTrade.exit_time < end,
        )
        open_query = open_query.where(
            AdaptivePaperTrade.entry_time >= start,
            AdaptivePaperTrade.entry_time < end,
        )
    closed = list(db.scalars(closed_query.order_by(AdaptivePaperTrade.exit_time.desc())).all())
    open_trades = list(db.scalars(open_query.order_by(AdaptivePaperTrade.entry_time.desc())).all())
    wins = sum(1 for item in closed if item.net_profit > 0)
    losses = sum(1 for item in closed if item.net_profit < 0)
    breakeven = len(closed) - wins - losses
    net_profit = sum((item.net_profit for item in closed), Decimal("0"))
    gross_profit = sum((item.gross_profit for item in closed), Decimal("0"))
    costs = sum((item.trading_cost for item in closed), Decimal("0"))
    unrealized = sum((item.unrealized_profit for item in open_trades), Decimal("0"))
    average = net_profit / len(closed) if closed else Decimal("0")
    win_rate = win_rate_from_profits(item.net_profit for item in closed)
    return {
        "mode": "paper",
        "period": month or "all",
        "assumption": "每筆正式進場訊號模擬買進 1 張，損益已估算手續費與證券交易稅",
        "summary": {
            "totalTrades": len(closed) + len(open_trades),
            "closedTrades": len(closed),
            "openTrades": len(open_trades),
            "wins": wins,
            "losses": losses,
            "breakeven": breakeven,
            "winRate": win_rate,
            "grossProfit": float(_money(gross_profit)),
            "tradingCost": float(_money(costs)),
            "netProfit": float(_money(net_profit)),
            "unrealizedProfit": float(_money(unrealized)),
            "averageProfit": float(_money(average)),
        },
        "openPositions": [_trade_payload(item) for item in open_trades[:limit]],
        "closedTrades": [_trade_payload(item) for item in closed[:limit]],
    }
