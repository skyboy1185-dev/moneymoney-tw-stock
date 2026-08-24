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
from .super_ai_daytrade_service import (
    SYSTEM_NAME,
    ensure_settings as ensure_super_ai_settings,
    record_notification,
    strategy_analytics,
    time_bucket_analytics,
    trading_gate,
)


AUTOMATION_USER_ID = "system-adaptive-electronic"
COMMISSION_RATE = Decimal("0.001425")
DEFAULT_COMMISSION_DISCOUNT = Decimal("0.2")
SECURITIES_TAX_RATE = Decimal("0.003")
MINIMUM_COMMISSION = Decimal("20")
MONEY = Decimal("0.01")
TAIPEI = ZoneInfo("Asia/Taipei")


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def _reasons(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
        return [str(item) for item in parsed] if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        return []


def estimated_trade_result(
    entry_price: Decimal,
    exit_price: Decimal,
    quantity_shares: int,
    side: str = "LONG",
    commission_discount: Decimal = DEFAULT_COMMISSION_DISCOUNT,
) -> dict[str, Decimal]:
    quantity = Decimal(quantity_shares)
    buy_amount = entry_price * quantity
    sell_amount = exit_price * quantity
    buy_commission = max(MINIMUM_COMMISSION, buy_amount * COMMISSION_RATE * commission_discount)
    sell_commission = max(MINIMUM_COMMISSION, sell_amount * COMMISSION_RATE * commission_discount)
    tax = sell_amount * SECURITIES_TAX_RATE
    gross = sell_amount - buy_amount if side != "SHORT" else buy_amount - sell_amount
    cost = buy_commission + sell_commission + tax
    net = gross - cost
    invested = buy_amount + buy_commission
    return {
        "grossProfit": _money(gross),
        "tradingCost": _money(cost),
        "netProfit": _money(net),
        "returnPercentage": (net / max(Decimal("0.01"), invested) * Decimal(100)).quantize(
            Decimal("0.0001"),
            rounding=ROUND_HALF_UP,
        ),
    }


def win_rate_from_profits(profits: Iterable[Decimal]) -> float:
    values = list(profits)
    if not values:
        return 0.0
    return round(sum(1 for value in values if value > 0) / len(values) * 100, 2)


def _exit_reason(
    trade: AdaptivePaperTrade,
    price: Decimal,
    regime: str,
    candidate: AdaptiveStockCandidate | None,
) -> str | None:
    if trade.side == "SHORT":
        if price >= trade.stop_loss_price:
            return "STOP_LOSS"
        if price <= trade.target_price_2:
            return "TAKE_PROFIT"
        if regime == "BREAKOUT":
            return "MARKET_RISK"
    else:
        if price <= trade.stop_loss_price:
            return "STOP_LOSS"
        if price >= trade.target_price_2:
            return "TAKE_PROFIT"
        if regime == "CRASH":
            return "MARKET_RISK"
    if candidate is not None and candidate.health_score < Decimal("55"):
        return "SCORE_WEAKENED"
    return None


def _record_exit_notification(
    db: Session,
    trade: AdaptivePaperTrade,
    price: Decimal,
    result: dict[str, Decimal],
    reason: str,
    at: datetime,
) -> None:
    category = "STOP_LOSS" if reason == "STOP_LOSS" else "TAKE_PROFIT" if reason == "TAKE_PROFIT" else "EXIT"
    level = "danger" if category == "STOP_LOSS" else "success" if category == "TAKE_PROFIT" else "info"
    record_notification(
        db,
        category=category,
        level=level,
        title=f"\u3010{SYSTEM_NAME}\uff5c{category}\u3011{trade.stock_code} {trade.stock_name}",
        message=(
            f"\u80a1\u7968\uff1a{trade.stock_code} {trade.stock_name}\n"
            f"\u65b9\u5411\uff1a{trade.side}\n"
            f"\u7b56\u7565\uff1a{trade.strategy_type}\n"
            f"\u9032\u5834\uff1a{float(trade.entry_price):,.2f}\n"
            f"\u51fa\u5834\uff1a{float(price):,.2f}\n"
            f"\u80a1\u6578\uff1a{trade.quantity_shares:,}\n"
            f"\u640d\u76ca\uff1a{float(result['netProfit']):+,.0f}\n"
            f"R\u500d\u6578\uff1a{float(trade.realized_r):+.2f}\n"
            f"\u539f\u56e0\uff1a{reason}"
        ),
        dedupe_key=f"super-ai-exit:{trade.id}:{reason}",
        symbol=trade.stock_code,
        symbol_name=trade.stock_name,
        strategy=trade.strategy_type,
        side=trade.side,
        price=price,
        quantity=trade.quantity_shares,
        stop_loss=trade.stop_loss_price,
        take_profit_1=trade.target_price_1,
        take_profit_2=trade.target_price_2,
        ai_score_value=trade.ai_score,
        at=at,
    )


def _record_watch_notification(
    db: Session,
    payload: AdaptiveScanPayload,
    candidate: AdaptiveStockCandidate,
    gate: dict[str, Any],
) -> None:
    record_notification(
        db,
        category="WATCH",
        level="warning",
        title=f"\u3010{SYSTEM_NAME}\uff5c\u89c0\u5bdf\u3011{candidate.stock_code} {candidate.stock_name}",
        message=(
            f"AI\u8a55\u5206\uff1a{float(gate['aiScore']):.0f}\n"
            f"\u7b56\u7565\uff1a{candidate.strategy_type}\n"
            f"\u65b9\u5411\uff1a{gate['side']}\n"
            f"\u672a\u901a\u904e\u689d\u4ef6\uff1a{', '.join(gate['failures'])}\n"
            f"R/R\uff1a1:{float(gate['riskReward']):.2f}"
        ),
        dedupe_key=f"super-ai-watch:{payload.market.trade_date}:{candidate.stock_code}:{candidate.strategy_type}",
        symbol=candidate.stock_code,
        symbol_name=candidate.stock_name,
        strategy=candidate.strategy_type,
        side=gate["side"],
        price=gate["entry"],
        quantity=gate["quantity"],
        stop_loss=gate["stop"],
        take_profit_1=gate["takeProfit1"],
        take_profit_2=gate["takeProfit2"],
        ai_score_value=gate["aiScore"],
        risk_reward_value=gate["riskReward"],
        at=payload.market.updated_at,
    )


def _record_entry_notification(
    db: Session,
    payload: AdaptiveScanPayload,
    candidate: AdaptiveStockCandidate,
    gate: dict[str, Any],
    quantity: int,
) -> None:
    category = "SHORT" if gate["side"] == "SHORT" else "BUY"
    action_label = "\u653e\u7a7a" if category == "SHORT" else "\u8cb7\u9032"
    record_notification(
        db,
        category=category,
        level="danger" if category == "SHORT" else "success",
        title=f"\u3010{SYSTEM_NAME}\uff5c{action_label}\u3011{candidate.stock_code} {candidate.stock_name} @ {float(gate['entry']):,.2f}",
        message=(
            f"\u80a1\u7968\uff1a{candidate.stock_code} {candidate.stock_name}\n"
            f"\u65b9\u5411\uff1a{gate['side']}\n"
            f"\u7b56\u7565\uff1a{candidate.strategy_type}\n"
            f"AI\u8a55\u5206\uff1a{float(gate['aiScore']):.0f}\n"
            f"\u9032\u5834\u50f9\uff1a{float(gate['entry']):,.2f}\n"
            f"\u80a1\u6578\uff1a{quantity:,}\n"
            f"\u505c\u640d\uff1a{float(gate['stop']):,.2f}\n"
            f"TP1\uff1a{float(gate['takeProfit1']):,.2f}\n"
            f"TP2\uff1a{float(gate['takeProfit2']):,.2f}\n"
            f"R/R\uff1a1:{float(gate['riskReward']):.2f}\n"
            f"\u539f\u56e0\uff1a{'; '.join(gate['reasons'][:8])}"
        ),
        dedupe_key=f"super-ai-entry:{payload.market.trade_date}:{candidate.stock_code}:{gate['side']}:{candidate.strategy_type}",
        symbol=candidate.stock_code,
        symbol_name=candidate.stock_name,
        strategy=candidate.strategy_type,
        side=gate["side"],
        price=gate["entry"],
        quantity=quantity,
        stop_loss=gate["stop"],
        take_profit_1=gate["takeProfit1"],
        take_profit_2=gate["takeProfit2"],
        ai_score_value=gate["aiScore"],
        risk_reward_value=gate["riskReward"],
        at=payload.market.updated_at,
    )


def _manage_open_trades(
    db: Session,
    payload: AdaptiveScanPayload,
    candidates: dict[str, AdaptiveStockCandidate],
    regime: str,
) -> list[AdaptiveSignal]:
    stocks = {stock.stock_code: stock for stock in payload.stocks}
    emitted: list[AdaptiveSignal] = []
    settings = ensure_super_ai_settings(db, payload.market.updated_at)
    open_trades = list(db.scalars(select(AdaptivePaperTrade).where(
        AdaptivePaperTrade.status == "open",
    )).all())
    for trade in open_trades:
        stock = stocks.get(trade.stock_code)
        if stock is None or stock.price <= 0:
            continue
        price = Decimal(str(stock.price))
        result = estimated_trade_result(
            trade.entry_price,
            price,
            trade.quantity_shares,
            trade.side,
            Decimal(settings.commission_discount),
        )
        trade.last_price = price
        trade.unrealized_profit = result["netProfit"]
        trade.updated_at = payload.market.updated_at
        reason = _exit_reason(trade, price, regime, candidates.get(trade.stock_code))
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
        trade.realized_r = (
            result["netProfit"] / trade.risk_amount
            if trade.risk_amount and trade.risk_amount > 0
            else Decimal("0")
        )
        trade.unrealized_profit = Decimal("0")
        trade.exit_reasons_json = json.dumps([reason], ensure_ascii=False)

        if db.scalar(select(AdaptiveSignal.id).where(AdaptiveSignal.signal_key == signal_key)) is None:
            signal = AdaptiveSignal(
                signal_key=signal_key,
                stock_code=trade.stock_code,
                stock_name=trade.stock_name,
                signal_type="exit_triggered",
                action=reason,
                strategy_type=trade.strategy_type,
                price=price,
                health_score=candidates[trade.stock_code].health_score if trade.stock_code in candidates else None,
                reasons_json=json.dumps([reason, f"{SYSTEM_NAME} PnL {result['netProfit']:+,.0f}"], ensure_ascii=False),
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
        _record_exit_notification(db, trade, price, result, reason, payload.market.updated_at)
    return emitted


def update_adaptive_paper_trades(
    db: Session,
    payload: AdaptiveScanPayload,
    candidates: list[AdaptiveStockCandidate],
    signals: list[AdaptiveSignal],
    regime: str,
) -> list[AdaptiveSignal]:
    """Update the Super AI day-trade paper ledger from official adaptive scan prices."""
    candidate_by_symbol = {candidate.stock_code: candidate for candidate in candidates}
    emitted = _manage_open_trades(db, payload, candidate_by_symbol, regime)

    if not adaptive_entry_window_open(
        payload.market.updated_at,
        payload.market.market_open,
        payload.market.trade_date,
    ):
        return emitted

    settings = ensure_super_ai_settings(db, payload.market.updated_at)
    entry_signals = {
        signal.signal_key: signal
        for signal in signals
        if signal.stock_code and signal.price is not None and signal.signal_type in {"entry_confirmed", "new_top5"}
    }
    recent = db.scalars(select(AdaptiveSignal).where(
        AdaptiveSignal.signal_type.in_(["entry_confirmed", "new_top5"]),
    ).order_by(AdaptiveSignal.created_at.desc()).limit(100)).all()
    for signal in recent:
        created_at = signal.created_at if signal.created_at.tzinfo else signal.created_at.replace(tzinfo=TAIPEI)
        if created_at.astimezone(TAIPEI).date() == payload.market.trade_date and signal.stock_code:
            entry_signals[signal.signal_key] = signal

    watched: set[str] = set()
    for signal in entry_signals.values():
        if signal.stock_code is None or signal.price is None:
            continue
        candidate = candidate_by_symbol.get(signal.stock_code)
        if candidate is None:
            continue
        existing = db.scalar(select(AdaptivePaperTrade.id).where(
            (AdaptivePaperTrade.entry_signal_key == signal.signal_key)
            | ((AdaptivePaperTrade.stock_code == signal.stock_code) & (AdaptivePaperTrade.status == "open")),
        ))
        if existing is not None:
            continue

        gate = trading_gate(db, settings, candidate, regime, payload.market.updated_at)
        if not gate["allowed"]:
            if signal.stock_code not in watched and float(gate["aiScore"]) >= float(settings.min_ai_score_to_watch):
                _record_watch_notification(db, payload, candidate, gate)
                watched.add(signal.stock_code)
            continue

        quantity = int(gate["quantity"])
        trade = AdaptivePaperTrade(
            stock_code=signal.stock_code,
            stock_name=signal.stock_name or candidate.stock_name,
            strategy_type=signal.strategy_type or candidate.strategy_type,
            entry_signal_key=signal.signal_key,
            side=gate["side"],
            trade_mode=settings.trading_mode,
            quantity_shares=quantity,
            entry_price=gate["entry"],
            entry_time=payload.market.updated_at,
            entry_reason="; ".join(gate["reasons"][:8]),
            stop_loss_price=gate["stop"],
            target_price_1=gate["takeProfit1"],
            target_price_2=gate["takeProfit2"],
            last_price=gate["entry"],
            ai_score=gate["aiScore"],
            market_regime=regime,
            sector_status=candidate.sub_industry,
            initial_capital=settings.max_capital,
            risk_amount=gate["riskAmount"],
            initial_r=abs(Decimal(gate["entry"]) - Decimal(gate["stop"])),
            entry_reasons_json=json.dumps(gate["reasons"], ensure_ascii=False),
            status="open",
            unrealized_profit=Decimal("0"),
            created_at=payload.market.updated_at,
            updated_at=payload.market.updated_at,
        )
        db.add(trade)
        settings.available_capital = max(
            Decimal("0"),
            Decimal(settings.available_capital) - Decimal(gate["entry"]) * quantity,
        )
        signal.signal_type = "entry_confirmed"
        signal.action = "SHORT" if gate["side"] == "SHORT" else "BUY"
        _record_entry_notification(db, payload, candidate, gate, quantity)

        monitor = db.scalar(select(AdaptiveStockMonitoring).where(
            AdaptiveStockMonitoring.user_id == AUTOMATION_USER_ID,
            AdaptiveStockMonitoring.stock_code == signal.stock_code,
        ).order_by(AdaptiveStockMonitoring.updated_at.desc()).limit(1))
        if monitor is not None:
            monitor.entry_price = gate["entry"]
            monitor.monitor_status = "holding"
            monitor.last_signal = "entry_confirmed"
            monitor.updated_at = payload.market.updated_at
    return emitted


def _trade_payload(item: AdaptivePaperTrade) -> dict[str, Any]:
    return {
        "id": item.id,
        "stockCode": item.stock_code,
        "stockName": item.stock_name,
        "strategyType": item.strategy_type,
        "side": item.side,
        "tradeMode": item.trade_mode,
        "quantityShares": item.quantity_shares,
        "quantityLots": item.quantity_shares / 1000,
        "entryPrice": float(item.entry_price),
        "entryTime": item.entry_time.isoformat(),
        "entryReason": item.entry_reason,
        "entryReasons": _reasons(item.entry_reasons_json),
        "stopLossPrice": float(item.stop_loss_price),
        "targetPrice1": float(item.target_price_1),
        "targetPrice2": float(item.target_price_2),
        "lastPrice": float(item.last_price),
        "aiScore": float(item.ai_score),
        "marketRegime": item.market_regime,
        "sectorStatus": item.sector_status,
        "riskAmount": float(item.risk_amount),
        "initialR": float(item.initial_r),
        "realizedR": float(item.realized_r),
        "status": item.status,
        "exitPrice": float(item.exit_price) if item.exit_price is not None else None,
        "exitTime": item.exit_time.isoformat() if item.exit_time is not None else None,
        "exitReason": item.exit_reason,
        "exitReasons": _reasons(item.exit_reasons_json),
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
        closed_query = closed_query.where(AdaptivePaperTrade.exit_time >= start, AdaptivePaperTrade.exit_time < end)
        open_query = open_query.where(AdaptivePaperTrade.entry_time >= start, AdaptivePaperTrade.entry_time < end)
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
    long_closed = [item for item in closed if item.side == "LONG"]
    short_closed = [item for item in closed if item.side == "SHORT"]
    average_r = sum((item.realized_r for item in closed), Decimal("0")) / len(closed) if closed else Decimal("0")
    gross_loss = abs(sum((item.net_profit for item in closed if item.net_profit < 0), Decimal("0")))
    profit_factor = float(sum((item.net_profit for item in closed if item.net_profit > 0), Decimal("0")) / gross_loss) if gross_loss else (999 if net_profit > 0 else 0)
    settings = ensure_super_ai_settings(db)
    return {
        "mode": "paper",
        "systemName": SYSTEM_NAME,
        "period": month or "all",
        "assumption": f"{SYSTEM_NAME} uses paper trades, risk sizing, fees, tax and stop rules. Risk control overrides AI signals.",
        "settings": {
            "maxCapital": float(settings.max_capital),
            "availableCapital": float(settings.available_capital),
            "riskPerTradePct": float(settings.risk_per_trade_pct),
            "dailyMaxLossPct": float(settings.daily_max_loss_pct),
            "tradingMode": settings.trading_mode,
            "commissionRate": float(COMMISSION_RATE),
            "commissionDiscount": float(settings.commission_discount),
            "commissionDiscountLabel": f"{float(settings.commission_discount) * 10:.1f}折",
            "taxRate": float(SECURITIES_TAX_RATE),
            "costFormula": "手續費=max(20, 成交金額×0.1425%×退水折扣)；賣出另計證交稅=成交金額×0.3%",
        },
        "summary": {
            "totalTrades": len(closed) + len(open_trades),
            "closedTrades": len(closed),
            "openTrades": len(open_trades),
            "wins": wins,
            "losses": losses,
            "breakeven": breakeven,
            "winRate": win_rate,
            "longWinRate": win_rate_from_profits(item.net_profit for item in long_closed),
            "shortWinRate": win_rate_from_profits(item.net_profit for item in short_closed),
            "grossProfit": float(_money(gross_profit)),
            "tradingCost": float(_money(costs)),
            "netProfit": float(_money(net_profit)),
            "unrealizedProfit": float(_money(unrealized)),
            "averageProfit": float(_money(average)),
            "profitFactor": profit_factor,
            "averageR": float(average_r.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)),
        },
        "openPositions": [_trade_payload(item) for item in open_trades[:limit]],
        "closedTrades": [_trade_payload(item) for item in closed[:limit]],
        "strategyAnalytics": strategy_analytics(db),
        "timeBucketAnalytics": time_bucket_analytics(db),
    }
