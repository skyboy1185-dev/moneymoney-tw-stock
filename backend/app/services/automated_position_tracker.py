from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, time, timedelta
import json
import math
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    DayTradingAlert,
    DayTradingPosition,
    DayTradingRecommendationHistory,
    DayTradingTrade,
)
from .day_trading import evaluate_position


AUTOMATION_USER_ID = "system-automation"
DYNAMIC_AUTOMATION_USER_ID = "system-automation-5m"
# Keep the ledger and its existing open positions for safe historical cleanup,
# but do not create any new dynamic-capital positions while the strategy is paused.
DYNAMIC_AUTOMATION_ENABLED = False
AUTOMATION_USER_IDS = (AUTOMATION_USER_ID, DYNAMIC_AUTOMATION_USER_ID)
FIXED_STRATEGY_KEY = "fixed_2_lots"
DYNAMIC_STRATEGY_KEY = "dynamic_5m"
# Legacy fallback used only by recommendation history created before dynamic sizing.
AUTOMATION_QUANTITY_LOTS = 2.0
AUTOMATION_FIXED_MAX_STOP_RISK = 50_000.0
AUTOMATION_DAILY_CAPITAL = 5_000_000.0
AUTOMATION_MAX_POSITION_PERCENT = 30.0
AUTOMATION_RISK_PER_TRADE_PERCENT = 0.5
AUTOMATION_DAILY_LOSS_LIMIT_PERCENT = 2.0
AUTOMATION_PERFORMANCE_START = datetime(2026, 8, 4, tzinfo=ZoneInfo("Asia/Taipei")).astimezone(UTC)
DYNAMIC_AUTOMATION_PERFORMANCE_START = datetime(2026, 8, 17, tzinfo=ZoneInfo("Asia/Taipei")).astimezone(UTC)
TAIPEI = ZoneInfo("Asia/Taipei")


def automation_strategy(user_id: str) -> dict[str, str]:
    if user_id == DYNAMIC_AUTOMATION_USER_ID:
        return {"key": DYNAMIC_STRATEGY_KEY, "label": "新版 500 萬動態配置"}
    return {"key": FIXED_STRATEGY_KEY, "label": "原版固定 2 張"}


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
    strategy = automation_strategy(position.user_id)
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
        "holdingPeriod": position.holding_period,
        "holdingPeriodLabel": "隔日多單" if position.holding_period == "overnight_long" else "當沖",
        "entryConfidence": position.entry_confidence,
        "strategyConfidence": position.strategy_confidence,
        "overnightEligible": (
            position.direction == "long"
            and position.entry_confidence >= 85
            and position.strategy_confidence >= 85
        ),
        "automaticTracking": True,
        "automationStrategy": strategy["key"],
        "automationStrategyLabel": strategy["label"],
    }


def automation_capital_state(
    db: Session,
    now: datetime | None = None,
    user_id: str = DYNAMIC_AUTOMATION_USER_ID,
) -> dict[str, Any]:
    current = now or datetime.now(UTC)
    local_day = current.astimezone(TAIPEI).date()
    day_start = datetime.combine(local_day, time.min, tzinfo=TAIPEI).astimezone(UTC)
    day_end = day_start + timedelta(days=1)
    open_positions = list(db.scalars(select(DayTradingPosition).where(
        DayTradingPosition.user_id == user_id,
        DayTradingPosition.status == "open",
    )).all())
    today_trades = list(db.scalars(select(DayTradingTrade).where(
        DayTradingTrade.user_id == user_id,
        DayTradingTrade.exit_time >= day_start,
        DayTradingTrade.exit_time < day_end,
    )).all())
    used_capital = sum(
        float(position.entry_price) * float(position.quantity) * 1000
        for position in open_positions
    )
    unrealized_profit = sum(
        (float(position.current_price) - float(position.entry_price))
        * float(position.quantity) * 1000
        * (1 if position.direction == "long" else -1)
        for position in open_positions
    )
    realized_profit = sum(float(trade.profit) for trade in today_trades)
    daily_pnl = realized_profit + unrealized_profit
    daily_loss_limit = AUTOMATION_DAILY_CAPITAL * AUTOMATION_DAILY_LOSS_LIMIT_PERCENT / 100
    return {
        "strategyKey": DYNAMIC_STRATEGY_KEY,
        "strategyLabel": "新版 500 萬動態配置",
        "dailyCapital": AUTOMATION_DAILY_CAPITAL,
        "usedCapital": round(used_capital, 2),
        "availableCapital": round(max(0.0, AUTOMATION_DAILY_CAPITAL - used_capital), 2),
        "maxPositionCapital": round(AUTOMATION_DAILY_CAPITAL * AUTOMATION_MAX_POSITION_PERCENT / 100, 2),
        "maxPositionPercent": AUTOMATION_MAX_POSITION_PERCENT,
        "riskPerTradeBudget": round(AUTOMATION_DAILY_CAPITAL * AUTOMATION_RISK_PER_TRADE_PERCENT / 100, 2),
        "riskPerTradePercent": AUTOMATION_RISK_PER_TRADE_PERCENT,
        "dailyLossLimit": round(daily_loss_limit, 2),
        "dailyLossLimitPercent": AUTOMATION_DAILY_LOSS_LIMIT_PERCENT,
        "realizedProfit": round(realized_profit, 2),
        "unrealizedProfit": round(unrealized_profit, 2),
        "dailyPnl": round(daily_pnl, 2),
        "lossLimitReached": daily_pnl <= -daily_loss_limit,
        "openPositionCount": len(open_positions),
        "sizingMethod": "停損風險與可用資金兩者取較小值；支援零股，張數不設固定上限",
    }


def calculate_automation_quantity_lots(
    entry_price: float,
    stop_loss: float,
    available_capital: float,
) -> float:
    if entry_price <= 0 or stop_loss <= 0 or available_capital <= 0:
        return 0.0
    stop_distance = abs(entry_price - stop_loss)
    if stop_distance <= 0:
        return 0.0
    position_budget = min(
        available_capital,
        AUTOMATION_DAILY_CAPITAL * AUTOMATION_MAX_POSITION_PERCENT / 100,
    )
    risk_budget = AUTOMATION_DAILY_CAPITAL * AUTOMATION_RISK_PER_TRADE_PERCENT / 100
    capital_limited_shares = math.floor(position_budget / entry_price)
    risk_limited_shares = math.floor(risk_budget / stop_distance)
    shares = max(0, min(capital_limited_shares, risk_limited_shares))
    return round(shares / 1000, 3)


def record_official_recommendations(
    db: Session,
    recommendations: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> int:
    current = now or datetime.now(UTC)
    created = 0
    for signal in recommendations:
        if not signal.get("isOfficialRecommendation"):
            continue
        signal_id = str(signal.get("id", ""))
        if not signal_id or db.scalar(select(DayTradingRecommendationHistory.id).where(
            DayTradingRecommendationHistory.signal_id == signal_id,
        ).limit(1)) is not None:
            continue
        recommended_at = _as_datetime(
            signal.get("recommendedAt") or signal.get("generatedAt"),
            current,
        )
        history_payload = {
            **signal,
            "recommendedQuantityLots": float(signal.get("recommendedQuantityLots", 0)),
        }
        db.add(DayTradingRecommendationHistory(
            signal_id=signal_id,
            trading_date=recommended_at.astimezone(TAIPEI).date(),
            symbol=str(signal.get("symbol", "")),
            stock_name=str(signal.get("stockName", signal.get("symbol", ""))),
            market=str(signal.get("market", "")),
            direction=str(signal.get("direction", "")),
            action=str(signal.get("action", "")),
            payload_json=json.dumps(history_payload, ensure_ascii=False, default=str),
            recommended_at=recommended_at,
        ))
        created += 1
    return created


def ensure_positions_for_official_recommendations(
    db: Session,
    recommendations: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> list[DayTradingPosition]:
    """Create independent fixed-lot and dynamic-capital virtual positions."""
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
        try:
            entry_price = float(signal["price"])
            stop_loss = float(signal["stopLoss"])
            target_1 = float(signal["target1"])
            target_2 = float(signal["target2"])
        except (KeyError, TypeError, ValueError):
            signal["strategyAllocations"] = {
                FIXED_STRATEGY_KEY: {"quantityLots": 0, "status": "價格資料不完整，未建倉"},
            }
            continue
        opened_at = _as_datetime(
            signal.get("recommendedAt") or signal.get("generatedAt"),
            current,
        )
        allocations: dict[str, dict[str, Any]] = {}
        strategy_accounts = [(AUTOMATION_USER_ID, FIXED_STRATEGY_KEY)]
        if DYNAMIC_AUTOMATION_ENABLED:
            strategy_accounts.append((DYNAMIC_AUTOMATION_USER_ID, DYNAMIC_STRATEGY_KEY))
        for user_id, strategy_key in strategy_accounts:
            existing_position = db.scalar(
                select(DayTradingPosition)
                .where(
                    DayTradingPosition.user_id == user_id,
                    DayTradingPosition.signal_id == signal_id,
                )
                .limit(1)
            )
            if existing_position is not None:
                allocations[strategy_key] = {
                    "quantityLots": float(existing_position.quantity),
                    "allocatedCapital": round(
                        float(existing_position.entry_price) * float(existing_position.quantity) * 1000,
                        2,
                    ),
                    "status": "已建立模擬持倉",
                }
                continue
            open_symbol_position = db.scalar(
                select(DayTradingPosition.id)
                .where(
                    DayTradingPosition.user_id == user_id,
                    DayTradingPosition.symbol == symbol,
                    DayTradingPosition.status == "open",
                )
                .limit(1)
            )
            if open_symbol_position is not None:
                allocations[strategy_key] = {
                    "quantityLots": 0,
                    "allocatedCapital": 0,
                    "status": "重複確認，未加碼",
                }
                continue
            if user_id == AUTOMATION_USER_ID:
                quantity_lots = AUTOMATION_QUANTITY_LOTS
                estimated_stop_risk = abs(entry_price - stop_loss) * quantity_lots * 1000
                blocked_status = (
                    f"固定 2 張預估停損 {estimated_stop_risk:,.0f} 元，"
                    f"超過單筆上限 {AUTOMATION_FIXED_MAX_STOP_RISK:,.0f} 元，未建倉"
                    if estimated_stop_risk > AUTOMATION_FIXED_MAX_STOP_RISK else ""
                )
                if blocked_status:
                    quantity_lots = 0.0
            else:
                capital = automation_capital_state(db, current, user_id)
                if bool(capital["lossLimitReached"]):
                    quantity_lots = 0.0
                    blocked_status = "已達每日虧損上限，停止建倉"
                else:
                    quantity_lots = calculate_automation_quantity_lots(
                        entry_price,
                        stop_loss,
                        float(capital["availableCapital"]),
                    )
                    blocked_status = "可用資金或風險額度不足，未建倉"
            if quantity_lots <= 0:
                allocations[strategy_key] = {
                    "quantityLots": 0,
                    "allocatedCapital": 0,
                    "status": blocked_status,
                }
                continue
            allocated_capital = round(entry_price * quantity_lots * 1000, 2)
            allocations[strategy_key] = {
                "quantityLots": quantity_lots,
                "allocatedCapital": allocated_capital,
                "estimatedStopRisk": round(
                    abs(entry_price - stop_loss) * quantity_lots * 1000,
                    2,
                ),
                "status": "已建立模擬持倉",
            }
            strategy = automation_strategy(user_id)
            position = DayTradingPosition(
                user_id=user_id,
                signal_id=signal_id,
                symbol=symbol,
                stock_name=str(signal.get("stockName", symbol)),
                direction=direction,
                entry_price=entry_price,
                quantity=quantity_lots,
                opened_at=opened_at,
                stop_loss=stop_loss,
                target_1=target_1,
                target_2=target_2,
                current_price=entry_price,
                unrealized_profit=0,
                health_score=float(signal.get("healthScore", 0)),
                latest_action=(
                    f"{strategy['label']}・自動追蹤多單 {quantity_lots:g} 張"
                    if direction == "long"
                    else f"{strategy['label']}・自動追蹤空單 {quantity_lots:g} 張"
                ),
                status="open",
                holding_period="intraday",
                entry_confidence=float(signal.get("confidenceScore", 0)),
                strategy_confidence=float(signal.get("strategyConfidence", 0)),
            )
            db.add(position)
            db.flush()
            created.append(position)
        signal["strategyAllocations"] = allocations
        fixed_allocation = allocations.get(FIXED_STRATEGY_KEY, {})
        signal["recommendedQuantityLots"] = float(fixed_allocation.get("quantityLots", 0))
        signal["trackingStatus"] = str(fixed_allocation.get("status", "未建倉"))
    return created


# Backward-compatible import for callers outside this module.
ensure_positions_for_delivered_entries = ensure_positions_for_official_recommendations


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
            DayTradingPosition.user_id.in_(AUTOMATION_USER_IDS),
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
        opened_at = position.opened_at if position.opened_at.tzinfo else position.opened_at.replace(tzinfo=UTC)
        opened_day = opened_at.astimezone(TAIPEI).date()
        current_day = current.astimezone(TAIPEI).date()
        overnight_eligible = (
            position.direction == "long"
            and position.entry_confidence >= 85
            and position.strategy_confidence >= 85
        )
        if (
            force_close
            and position.holding_period == "overnight_long"
            and opened_day == current_day
        ):
            position.latest_action = "隔日多單持續監控"
            continue
        if (
            force_close
            and position.direction == "long"
            and position.holding_period == "intraday"
            and opened_day == current_day
            and overnight_eligible
        ):
            result = {
                "level": "important",
                "action": "轉為隔日多單",
                "reason": "個股與盤勢策略信心度皆達 85，保留至下一交易日並持續執行停損",
            }
        elif force_close:
            result = {
                "level": "important",
                "action": (
                    "隔日多單到期，全部賣出"
                    if position.direction == "long" and position.holding_period == "overnight_long"
                    else "收盤前全部賣出"
                    if position.direction == "long"
                    else "收盤前全部回補"
                ),
                "reason": (
                    "隔日多單最多持有至下一交易日收盤前"
                    if position.direction == "long" and position.holding_period == "overnight_long"
                    else "當沖策略於收盤前強制平倉"
                ),
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
            "_transitionOnly": result["action"] == "轉為隔日多單",
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
    strategy = automation_strategy(position.user_id)
    db.add(DayTradingAlert(
        user_id=position.user_id,
        position_id=position.id,
        signal_id=position.signal_id,
        alert_level=str(event["level"]),
        alert_type=str(event["type"]),
        title=f"{strategy['label']}出場通知",
        message=f"{strategy['label']}｜{position.symbol} {position.stock_name}：{event['action']}",
        action=str(event["action"]),
        reason=str(event["reason"]),
        price=exit_price,
        created_at=exit_time,
    ))
    if bool(event.get("_transitionOnly")):
        position.holding_period = "overnight_long"
        position.latest_action = str(event["action"])
        return position
    terminal = bool(event.get("_terminal"))
    close_quantity = position.quantity if terminal else position.quantity * 0.5
    factor = 1 if position.direction == "long" else -1
    gross = (
        exit_price - position.entry_price
    ) * close_quantity * 1000 * factor
    turnover = (exit_price + position.entry_price) * close_quantity * 1000
    fee = round(turnover * 0.001425 * 0.6, 2)
    sell_price = exit_price if position.direction == "long" else position.entry_price
    tax = round(sell_price * close_quantity * 1000 * 0.0015, 2)
    slippage = round(exit_price * close_quantity * 1000 * 0.0002, 2)
    profit = round(gross - fee - tax - slippage, 2)
    capital = position.entry_price * close_quantity * 1000
    unrealized_share = (
        position.unrealized_profit * close_quantity / position.quantity
        if position.quantity else 0
    )
    db.add(DayTradingTrade(
        user_id=position.user_id,
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
        entry_reason=f"{automation_strategy(position.user_id)['label']}依正式訊號建立模擬持倉",
        exit_reason=str(event["reason"]),
        strategy_name=f"AI 當沖機器人・{automation_strategy(position.user_id)['label']}",
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
