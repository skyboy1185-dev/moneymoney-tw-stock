from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    DayTradingAlert,
    DayTradingPosition,
    DayTradingTrade,
    LineDeliveryLog,
)
from .day_trading import evaluate_position


AUTOMATION_USER_ID = "system-automation"
AUTOMATION_QUANTITY_LOTS = 2.0


def _as_datetime(value: Any, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return fallback
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _position_payload(position: DayTradingPosition) -> dict[str, Any]:
    return {
        "id": position.id,
        "signalId": position.signal_id,
        "symbol": position.symbol,
        "stockName": position.stock_name,
        "direction": position.direction,
        "entryPrice": position.entry_price,
        "quantity": position.quantity,
        "openedAt": position.opened_at.isoformat(),
        "stopLoss": position.stop_loss,
        "target1": position.target_1,
        "target2": position.target_2,
        "trailingStop": position.trailing_stop,
        "currentPrice": position.current_price,
        "unrealizedProfit": position.unrealized_profit,
        "latestAction": position.latest_action,
        "status": position.status,
        "automaticTracking": True,
    }


def ensure_positions_for_delivered_entries(
    db: Session,
    recommendations: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> list[DayTradingPosition]:
    """Create one persisted virtual position for every delivered formal entry."""
    current = now or datetime.now(UTC)
    created: list[DayTradingPosition] = []
    for signal in recommendations:
        if not signal.get("isOfficialRecommendation"):
            continue
        direction = str(signal.get("direction", ""))
        if direction not in {"long", "short"}:
            continue
        signal_id = str(signal.get("id", ""))
        symbol = str(signal.get("symbol", ""))
        if not signal_id or not symbol:
            continue
        event_type = "long_entry" if direction == "long" else "short_entry"
        delivered = db.scalar(
            select(LineDeliveryLog)
            .where(
                LineDeliveryLog.signal_id == signal_id,
                LineDeliveryLog.event_type == event_type,
                LineDeliveryLog.status == "sent",
            )
            .order_by(LineDeliveryLog.sent_at.desc())
            .limit(1)
        )
        if delivered is None:
            continue
        already_tracked = db.scalar(
            select(DayTradingPosition.id)
            .where(
                DayTradingPosition.user_id == AUTOMATION_USER_ID,
                DayTradingPosition.signal_id == signal_id,
            )
            .limit(1)
        )
        if already_tracked is not None:
            continue
        open_symbol_position = db.scalar(
            select(DayTradingPosition.id)
            .where(
                DayTradingPosition.user_id == AUTOMATION_USER_ID,
                DayTradingPosition.symbol == symbol,
                DayTradingPosition.status == "open",
            )
            .limit(1)
        )
        if open_symbol_position is not None:
            continue
        try:
            entry_price = float(signal["price"])
            stop_loss = float(signal["stopLoss"])
            target_1 = float(signal["target1"])
            target_2 = float(signal["target2"])
        except (KeyError, TypeError, ValueError):
            continue
        opened_at = delivered.sent_at or _as_datetime(signal.get("generatedAt"), current)
        position = DayTradingPosition(
            user_id=AUTOMATION_USER_ID,
            signal_id=signal_id,
            symbol=symbol,
            stock_name=str(signal.get("stockName", symbol)),
            direction=direction,
            entry_price=entry_price,
            quantity=AUTOMATION_QUANTITY_LOTS,
            opened_at=opened_at,
            stop_loss=stop_loss,
            target_1=target_1,
            target_2=target_2,
            current_price=entry_price,
            unrealized_profit=0,
            health_score=float(signal.get("healthScore", 0)),
            latest_action="自動追蹤多單" if direction == "long" else "自動追蹤空單",
            status="open",
        )
        db.add(position)
        db.flush()
        created.append(position)
    return created


def pending_automatic_position_events(
    db: Session,
    quote_for: Callable[[str], float | None],
    *,
    data_status: str,
    force_close: bool = False,
    risk_for: Callable[[str], dict[str, str] | None] | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Evaluate automatic positions without relying on a browser SSE connection."""
    current = now or datetime.now(UTC)
    if data_status != "normal" and not force_close:
        return []
    positions = db.scalars(
        select(DayTradingPosition).where(
            DayTradingPosition.user_id == AUTOMATION_USER_ID,
            DayTradingPosition.status == "open",
        )
    ).all()
    events: list[dict[str, Any]] = []
    for position in positions:
        quote = quote_for(position.symbol)
        if quote is None:
            if not force_close:
                continue
            quote = position.current_price
        try:
            price = float(quote)
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        position.current_price = price
        factor = 1 if position.direction == "long" else -1
        position.unrealized_profit = (
            price - position.entry_price
        ) * position.quantity * 1000 * factor
        if force_close:
            result = {
                "level": "important",
                "action": "收盤前全部賣出" if position.direction == "long" else "收盤前全部回補",
                "reason": "當沖策略於收盤前強制平倉",
            }
        elif position.direction == "long" and risk_for is not None:
            result = risk_for(position.symbol) or evaluate_position(
                position.direction,
                price,
                position.stop_loss,
                position.target_1,
                position.target_2,
                position.trailing_stop,
                data_status,
            )
        else:
            result = evaluate_position(
                position.direction,
                price,
                position.stop_loss,
                position.target_1,
                position.target_2,
                position.trailing_stop,
                data_status,
            )
        position.latest_action = result["action"]
        if result["level"] not in {"important", "emergency"}:
            continue
        already_finalized = db.scalar(
            select(DayTradingAlert.id)
            .where(
                DayTradingAlert.position_id == position.id,
                DayTradingAlert.action == result["action"],
            )
            .limit(1)
        )
        if already_finalized is not None:
            continue
        terminal = (
            "全部賣出" in result["action"]
            or "全部回補" in result["action"]
        )
        events.append({
            "type": "emergency_exit" if result["level"] == "emergency" else "exit_warning",
            "level": result["level"],
            "action": result["action"],
            "reason": result["reason"],
            "price": price,
            "createdAt": current.isoformat(),
            "position": _position_payload(position),
            "_positionId": position.id,
            "_terminal": terminal,
        })
    return events


def finalize_automatic_position_event(
    db: Session,
    event: dict[str, Any],
    *,
    now: datetime | None = None,
) -> DayTradingPosition | None:
    """Persist a generated alert and close terminal virtual positions once."""
    position_id = int(event["_positionId"])
    position = db.get(DayTradingPosition, position_id)
    if position is None:
        return None
    existing_alert = db.scalar(
        select(DayTradingAlert.id)
        .where(
            DayTradingAlert.position_id == position.id,
            DayTradingAlert.action == str(event["action"]),
        )
        .limit(1)
    )
    if existing_alert is not None or position.status != "open":
        return position
    exit_time = now or datetime.now(UTC)
    exit_price = float(event["price"])
    db.add(DayTradingAlert(
        user_id=AUTOMATION_USER_ID,
        position_id=position.id,
        signal_id=position.signal_id,
        alert_level=str(event["level"]),
        alert_type=str(event["type"]),
        title="自動持倉出場通知",
        message=f"{position.symbol} {position.stock_name}：{event['action']}",
        action=str(event["action"]),
        reason=str(event["reason"]),
        price=exit_price,
        created_at=exit_time,
    ))
    terminal = bool(event.get("_terminal"))
    close_quantity = position.quantity if terminal else position.quantity * 0.5
    factor = 1 if position.direction == "long" else -1
    gross = (
        exit_price - position.entry_price
    ) * close_quantity * 1000 * factor
    turnover = (exit_price + position.entry_price) * close_quantity * 1000
    fee = round(turnover * 0.001425 * 0.6, 2)
    tax = round(exit_price * close_quantity * 1000 * 0.0015, 2)
    slippage = round(exit_price * close_quantity * 1000 * 0.0002, 2)
    profit = round(gross - fee - tax - slippage, 2)
    capital = position.entry_price * close_quantity * 1000
    unrealized_share = (
        position.unrealized_profit * close_quantity / position.quantity
        if position.quantity else 0
    )
    db.add(DayTradingTrade(
        user_id=AUTOMATION_USER_ID,
        symbol=position.symbol,
        stock_name=position.stock_name,
        direction=position.direction,
        entry_time=position.opened_at,
        entry_price=position.entry_price,
        exit_time=exit_time,
        exit_price=exit_price,
        quantity=close_quantity,
        fee=fee,
        tax=tax,
        slippage=slippage,
        profit=profit,
        return_percentage=round(profit / capital * 100, 2) if capital else 0,
        max_profit=max(0, unrealized_share),
        max_loss=min(0, unrealized_share),
        entry_reason="LINE 正式訊號自動建立虛擬追蹤持倉",
        exit_reason=str(event["reason"]),
        strategy_name="AI 當沖機器人自動追蹤",
        followed_signal=True,
    ))
    position.realized_profit = round((position.realized_profit or 0) + profit, 2)
    position.latest_action = str(event["action"])
    if terminal:
        position.status = "closed"
        position.closed_at = exit_time
        position.exit_price = exit_price
    else:
        position.quantity = round(position.quantity - close_quantity, 4)
        position.unrealized_profit = round(position.unrealized_profit - unrealized_share, 2)
    return position
