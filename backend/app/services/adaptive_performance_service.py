from __future__ import annotations

from datetime import UTC, date, datetime, time
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
    discount_label,
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
FORCED_DAY_TRADE_CLOSE_START = time(13, 25)
STOP_LOSS_BUFFER_PCT = Decimal("0")
TP1_PARTIAL_MARKER = "TP1_PARTIAL_TAKEN"
TP1_PARTIAL_EXIT_PCT = Decimal("0.30")
TRAILING_STOP_R_MULTIPLE = Decimal("0.50")
BREAKEVEN_LOCK_R_MULTIPLE = Decimal("0.10")


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def _reasons(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
        return [str(item) for item in parsed] if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        return []


def _is_target_hit(trade: AdaptivePaperTrade, price: Decimal, target: Decimal) -> bool:
    return price <= target if trade.side == "SHORT" else price >= target


def _partial_quantity(quantity: int) -> int:
    partial = int(Decimal(quantity) * TP1_PARTIAL_EXIT_PCT)
    if partial >= 1000:
        partial = (partial // 1000) * 1000
    elif partial >= 100:
        partial = (partial // 100) * 100
    return max(0, min(partial, quantity - 100))


def _append_exit_marker(trade: AdaptivePaperTrade, marker: str) -> None:
    reasons = _reasons(trade.exit_reasons_json)
    if marker not in reasons:
        reasons.append(marker)
        trade.exit_reasons_json = json.dumps(reasons, ensure_ascii=False)


def _has_exit_marker(trade: AdaptivePaperTrade, marker: str) -> bool:
    return marker in _reasons(trade.exit_reasons_json)


def _raise_trailing_stop_after_tp1(
    trade: AdaptivePaperTrade,
    *,
    price: Decimal,
    previous_price: Decimal,
) -> None:
    if not _has_exit_marker(trade, TP1_PARTIAL_MARKER):
        return
    one_r = Decimal(trade.initial_r) if trade.initial_r and trade.initial_r > 0 else abs(Decimal(trade.entry_price) - Decimal(trade.stop_loss_price))
    if one_r <= 0:
        return
    if trade.side == "SHORT":
        favorable = min(previous_price, price)
        protected = min(
            Decimal(trade.stop_loss_price),
            Decimal(trade.entry_price) - one_r * BREAKEVEN_LOCK_R_MULTIPLE,
            favorable + one_r * TRAILING_STOP_R_MULTIPLE,
        )
        trade.stop_loss_price = _money(protected)
    else:
        favorable = max(previous_price, price)
        protected = max(
            Decimal(trade.stop_loss_price),
            Decimal(trade.entry_price) + one_r * BREAKEVEN_LOCK_R_MULTIPLE,
            favorable - one_r * TRAILING_STOP_R_MULTIPLE,
        )
        trade.stop_loss_price = _money(protected)


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


def _release_reserved_capital(settings: Any, trade: AdaptivePaperTrade, net_profit: Decimal) -> None:
    """Restore paper cash when a Super AI position is closed.

    Entries reserve entry notional from available_capital. On exit, release that
    reserved notional plus the realized net P/L after fee, tax and rebate rules.
    """
    released = Decimal(trade.entry_price) * Decimal(trade.quantity_shares) + Decimal(net_profit)
    settings.available_capital = _money(max(Decimal("0"), Decimal(settings.available_capital) + released))


def _commission(amount: Decimal, commission_discount: Decimal) -> Decimal:
    if amount <= 0:
        return Decimal("0")
    return max(MINIMUM_COMMISSION, amount * COMMISSION_RATE * commission_discount)


def _commission_totals(
    trade: AdaptivePaperTrade,
    commission_discount: Decimal,
    include_exit: bool,
) -> tuple[Decimal, Decimal]:
    quantity = Decimal(trade.quantity_shares)
    gross = _commission(trade.entry_price * quantity, Decimal("1"))
    actual = _commission(trade.entry_price * quantity, commission_discount)
    if include_exit and trade.exit_price is not None:
        gross += _commission(trade.exit_price * quantity, Decimal("1"))
        actual += _commission(trade.exit_price * quantity, commission_discount)
    return _money(gross), _money(actual)


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
    at: datetime | None = None,
) -> str | None:
    current = at or datetime.now(UTC)
    local = current if current.tzinfo else current.replace(tzinfo=UTC)
    if local.astimezone(TAIPEI).time().replace(tzinfo=None) >= FORCED_DAY_TRADE_CLOSE_START:
        return "DAY_TRADE_CLOSE"
    if trade.side == "SHORT":
        if _has_exit_marker(trade, TP1_PARTIAL_MARKER) and price >= trade.stop_loss_price:
            return "TRAILING_STOP"
        if price >= trade.stop_loss_price * (Decimal("1") - STOP_LOSS_BUFFER_PCT):
            return "STOP_LOSS"
        if price <= trade.target_price_2:
            return "TAKE_PROFIT"
        if regime in {"BREAKOUT", "RECOVERY"}:
            return "MARKET_RISK"
    else:
        if _has_exit_marker(trade, TP1_PARTIAL_MARKER) and price <= trade.stop_loss_price:
            return "TRAILING_STOP"
        if price <= trade.stop_loss_price * (Decimal("1") + STOP_LOSS_BUFFER_PCT):
            return "STOP_LOSS"
        if price >= trade.target_price_2:
            return "TAKE_PROFIT"
        if regime == "CRASH":
            return "MARKET_RISK"
    if candidate is not None:
        if trade.side == "SHORT":
            if candidate.strategy_type != "CRASH" or candidate.total_score < Decimal("60"):
                return "SCORE_WEAKENED"
        elif candidate.health_score < Decimal("55"):
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
            f"\u505c\u640d\u8ddd\u96e2\uff1a{float(gate['stopDistancePct']):.2f}%\n"
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


def _take_partial_profit_at_tp1(
    db: Session,
    settings: Any,
    trade: AdaptivePaperTrade,
    price: Decimal,
    at: datetime,
) -> str | None:
    if _has_exit_marker(trade, TP1_PARTIAL_MARKER):
        return None
    if not _is_target_hit(trade, price, Decimal(trade.target_price_1)):
        return None
    if _is_target_hit(trade, price, Decimal(trade.target_price_2)):
        return None
    quantity = _partial_quantity(trade.quantity_shares)
    if quantity <= 0:
        return None
    signal_key = f"adaptive-partial:{at.astimezone(TAIPEI).date()}:{trade.stock_code}:{trade.id}:tp1"
    if db.scalar(select(AdaptiveSignal.id).where(AdaptiveSignal.signal_key == signal_key)) is not None:
        _append_exit_marker(trade, TP1_PARTIAL_MARKER)
        return None
    result = estimated_trade_result(
        trade.entry_price,
        price,
        quantity,
        trade.side,
        Decimal(settings.commission_discount),
    )
    partial_trade = AdaptivePaperTrade(
        stock_code=trade.stock_code,
        stock_name=trade.stock_name,
        strategy_type=trade.strategy_type,
        entry_signal_key=f"{trade.entry_signal_key}:tp1:{trade.id}"[:180],
        exit_signal_key=signal_key,
        side=trade.side,
        trade_mode=trade.trade_mode,
        quantity_shares=quantity,
        entry_price=trade.entry_price,
        entry_time=trade.entry_time,
        entry_reason=trade.entry_reason,
        stop_loss_price=trade.stop_loss_price,
        target_price_1=trade.target_price_1,
        target_price_2=trade.target_price_2,
        last_price=price,
        ai_score=trade.ai_score,
        market_regime=trade.market_regime,
        sector_status=trade.sector_status,
        initial_capital=trade.initial_capital,
        risk_amount=_money(Decimal(trade.risk_amount) * Decimal(quantity) / Decimal(max(1, trade.quantity_shares))),
        initial_r=trade.initial_r,
        realized_r=(
            result["netProfit"] / max(Decimal("0.01"), Decimal(trade.risk_amount) * Decimal(quantity) / Decimal(max(1, trade.quantity_shares)))
        ),
        entry_reasons_json=trade.entry_reasons_json,
        exit_reasons_json=json.dumps(["TAKE_PROFIT_1_PARTIAL"], ensure_ascii=False),
        status="closed",
        exit_price=price,
        exit_time=at,
        exit_reason="TAKE_PROFIT_1_PARTIAL",
        gross_profit=result["grossProfit"],
        trading_cost=result["tradingCost"],
        net_profit=result["netProfit"],
        return_percentage=result["returnPercentage"],
        unrealized_profit=Decimal("0"),
        created_at=trade.created_at,
        updated_at=at,
    )
    db.add(partial_trade)
    db.flush()
    trade.quantity_shares -= quantity
    remaining_ratio = Decimal(trade.quantity_shares) / Decimal(max(1, trade.quantity_shares + quantity))
    trade.risk_amount = _money(Decimal(trade.risk_amount) * remaining_ratio)
    _append_exit_marker(trade, TP1_PARTIAL_MARKER)
    _release_reserved_capital(settings, partial_trade, result["netProfit"])
    db.add(AdaptiveSignal(
        signal_key=signal_key,
        stock_code=trade.stock_code,
        stock_name=trade.stock_name,
        signal_type="exit_triggered",
        action="TAKE_PROFIT_1_PARTIAL",
        strategy_type=trade.strategy_type,
        price=price,
        health_score=None,
        reasons_json=json.dumps(["TAKE_PROFIT_1_PARTIAL", f"{SYSTEM_NAME} TP1 partial PnL {result['netProfit']:+,.0f}"], ensure_ascii=False),
        line_push_status="pending",
        created_at=at,
    ))
    _record_exit_notification(db, partial_trade, price, result, "TAKE_PROFIT_1_PARTIAL", at)
    return signal_key


def update_open_trade_from_market_price(
    db: Session,
    *,
    trade_id: int,
    price: Decimal,
    at: datetime,
    regime: str,
    candidate: AdaptiveStockCandidate | None = None,
) -> str | None:
    trade = db.get(AdaptivePaperTrade, trade_id)
    if trade is None or trade.status != "open":
        return None
    settings = ensure_super_ai_settings(db, at)
    previous_price = Decimal(trade.last_price)
    result = estimated_trade_result(
        trade.entry_price,
        price,
        trade.quantity_shares,
        trade.side,
        Decimal(settings.commission_discount),
    )
    trade.last_price = price
    trade.unrealized_profit = result["netProfit"]
    trade.return_percentage = result["returnPercentage"]
    trade.updated_at = at
    partial_signal_key = _take_partial_profit_at_tp1(db, settings, trade, price, at)
    if partial_signal_key is not None:
        result = estimated_trade_result(
            trade.entry_price,
            price,
            trade.quantity_shares,
            trade.side,
            Decimal(settings.commission_discount),
        )
        trade.unrealized_profit = result["netProfit"]
        trade.return_percentage = result["returnPercentage"]
    _raise_trailing_stop_after_tp1(trade, price=price, previous_price=previous_price)
    reason = _exit_reason(trade, price, regime, candidate, at)
    if reason is None:
        return partial_signal_key

    signal_key = f"adaptive-exit:{at.astimezone(TAIPEI).date()}:{trade.stock_code}:{trade.id}"
    trade.status = "closed"
    trade.exit_signal_key = signal_key
    trade.exit_price = price
    trade.exit_time = at
    trade.exit_reason = reason
    trade.gross_profit = result["grossProfit"]
    trade.trading_cost = result["tradingCost"]
    trade.net_profit = result["netProfit"]
    _release_reserved_capital(settings, trade, result["netProfit"])
    trade.realized_r = (
        result["netProfit"] / trade.risk_amount
        if trade.risk_amount and trade.risk_amount > 0
        else Decimal("0")
    )
    trade.unrealized_profit = Decimal("0")
    trade.exit_reasons_json = json.dumps([reason], ensure_ascii=False)

    if db.scalar(select(AdaptiveSignal.id).where(AdaptiveSignal.signal_key == signal_key)) is None:
        db.add(AdaptiveSignal(
            signal_key=signal_key,
            stock_code=trade.stock_code,
            stock_name=trade.stock_name,
            signal_type="exit_triggered",
            action=reason,
            strategy_type=trade.strategy_type,
            price=price,
            health_score=candidate.health_score if candidate is not None else None,
            reasons_json=json.dumps([reason, f"{SYSTEM_NAME} PnL {result['netProfit']:+,.0f}"], ensure_ascii=False),
            line_push_status="pending",
            created_at=at,
        ))

    monitor = db.scalar(select(AdaptiveStockMonitoring).where(
        AdaptiveStockMonitoring.user_id == AUTOMATION_USER_ID,
        AdaptiveStockMonitoring.stock_code == trade.stock_code,
    ).order_by(AdaptiveStockMonitoring.updated_at.desc()).limit(1))
    if monitor is not None:
        monitor.entry_price = trade.entry_price
        monitor.monitor_status = "closed"
        monitor.last_signal = "exit_triggered"
        monitor.removed_reason = reason
        monitor.updated_at = at
    _record_exit_notification(db, trade, price, result, reason, at)
    return signal_key


def _manage_open_trades(
    db: Session,
    payload: AdaptiveScanPayload,
    candidates: dict[str, AdaptiveStockCandidate],
    regime: str,
) -> list[AdaptiveSignal]:
    stocks = {stock.stock_code: stock for stock in payload.stocks}
    emitted: list[AdaptiveSignal] = []
    open_trades = list(db.scalars(select(AdaptivePaperTrade).where(
        AdaptivePaperTrade.status == "open",
    )).all())
    for trade in open_trades:
        stock = stocks.get(trade.stock_code)
        if stock is None or stock.price <= 0:
            continue
        price = Decimal(str(stock.price))
        signal_key = update_open_trade_from_market_price(
            db,
            trade_id=trade.id,
            price=price,
            at=payload.market.updated_at,
            regime=regime,
            candidate=candidates.get(trade.stock_code),
        )
        if signal_key is None:
            continue
        signal = db.scalar(select(AdaptiveSignal).where(AdaptiveSignal.signal_key == signal_key))
        if signal is not None:
            emitted.append(signal)
    return emitted


def _stored_signal_created_at(signal: AdaptiveSignal) -> datetime:
    """Return signal created_at with DB compatibility.

    SQLite strips timezone information in tests after round-tripping aware
    datetimes.  Those values represent the original wall time, not UTC.  Keep
    that compatibility for persisted signals while the live scan timestamp is
    still normalized by adaptive_entry_window_open().
    """
    created_at = signal.created_at
    return created_at.replace(tzinfo=TAIPEI) if created_at.tzinfo is None else created_at


def _signal_open_for_new_entry(signal: AdaptiveSignal, trade_date: date) -> bool:
    return adaptive_entry_window_open(
        _stored_signal_created_at(signal),
        True,
        trade_date,
    )


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
        if signal.stock_code and signal.price is not None and signal.signal_type == "entry_confirmed"
    }
    recent = db.scalars(select(AdaptiveSignal).where(
        AdaptiveSignal.signal_type == "entry_confirmed",
    ).order_by(AdaptiveSignal.created_at.desc()).limit(100)).all()
    for signal in recent:
        created_at = _stored_signal_created_at(signal)
        if created_at.astimezone(TAIPEI).date() == payload.market.trade_date and signal.stock_code:
            entry_signals[signal.signal_key] = signal

    for signal in entry_signals.values():
        if signal.stock_code is None or signal.price is None:
            continue
        if not _signal_open_for_new_entry(signal, payload.market.trade_date):
            continue
        candidate = candidate_by_symbol.get(signal.stock_code)
        if candidate is None:
            continue
        if candidate.candidate_status != "can_enter":
            continue
        if regime in {"BREAKOUT", "RECOVERY"} and candidate.strategy_type == "CRASH":
            continue
        existing = db.scalar(select(AdaptivePaperTrade.id).where(
            (AdaptivePaperTrade.entry_signal_key == signal.signal_key)
            | ((AdaptivePaperTrade.stock_code == signal.stock_code) & (AdaptivePaperTrade.status == "open")),
        ))
        if existing is not None:
            continue

        gate = trading_gate(db, settings, candidate, regime, payload.market.updated_at)
        if not gate["allowed"]:
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
    commission_discount = Decimal(settings.commission_discount)
    gross_commission = Decimal("0")
    actual_commission = Decimal("0")
    for item in closed:
        item_gross, item_actual = _commission_totals(item, commission_discount, include_exit=True)
        gross_commission += item_gross
        actual_commission += item_actual
    for item in open_trades:
        item_gross, item_actual = _commission_totals(item, commission_discount, include_exit=False)
        gross_commission += item_gross
        actual_commission += item_actual
    commission_rebate = max(Decimal("0"), gross_commission - actual_commission)
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
            "commissionDiscountLabel": discount_label(Decimal(settings.commission_discount)),
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
            "grossCommission": float(_money(gross_commission)),
            "actualCommission": float(_money(actual_commission)),
            "commissionRebate": float(_money(commission_rebate)),
            "rebateAccumulated": float(_money(commission_rebate)),
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
