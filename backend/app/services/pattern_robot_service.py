from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import csv
import io
import json
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import Session

from ..models import (
    PatternDailyEquity, PatternDetection, PatternFill, PatternOrder, PatternPosition,
    PatternPositionLot, PatternRobotRun, PatternRobotSetting, PatternSignal,
    PatternTradeCycle, PatternTradeMessage, PatternWatchlist, WatchlistItem,
)
from ..pattern_schemas import PatternScanPayload, PatternStockInput
from .pattern_detection import Candle, PatternResult, detect_patterns, risk_sized_quantity


COMMISSION_RATE = .001425
SELL_TAX_RATE = .003
PATTERN_LABELS = {
    "HEAD_SHOULDERS_BOTTOM": "頭肩底", "DOUBLE_BOTTOM": "W底／雙重底",
    "ROUNDED_BOTTOM": "圓弧底", "CUP_HANDLE": "杯柄型態", "ASCENDING_TRIANGLE": "上升三角形",
}
TAIPEI = ZoneInfo("Asia/Taipei")
STATUS_LABELS = {
    "FORMING": "形成中", "NEAR_BREAKOUT": "接近突破", "INTRADAY_BREAKOUT": "盤中暫時突破",
    "CONFIRMED_BREAKOUT": "收盤有效突破", "FAILED_BREAKOUT": "突破失敗", "INVALIDATED": "型態失效",
}


def _d(value: float | Decimal, digits: int = 4) -> Decimal:
    return Decimal(str(round(float(value), digits)))


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _loads(value: str, fallback):
    try:
        parsed = json.loads(value)
        return parsed
    except (TypeError, ValueError):
        return fallback


def _average(values) -> float:
    rows = list(values)
    return sum(rows) / len(rows) if rows else 0.0


def ensure_pattern_settings(db: Session, at: datetime | None = None) -> PatternRobotSetting:
    settings = db.get(PatternRobotSetting, 1)
    if settings is None:
        now = at or datetime.now(UTC)
        settings = PatternRobotSetting(id=1, updated_at=now)
        db.add(settings)
        db.flush()
    return settings


def _cash_field(performance_mode: str) -> str:
    return {
        "PAPER_LIVE": "paper_live_cash", "MANUAL_PAPER": "manual_paper_cash",
        "BACKTEST": "backtest_cash",
    }[performance_mode]


def _set_cash(settings: PatternRobotSetting, value: float | Decimal) -> None:
    cash = _d(value, 2)
    settings.cash = cash
    setattr(settings, _cash_field(settings.performance_mode), cash)


def settings_dict(item: PatternRobotSetting) -> dict:
    return {
        "enabled": item.enabled, "robotMode": item.robot_mode, "performanceMode": item.performance_mode,
        "initialCapital": float(item.initial_capital), "cash": float(item.cash),
        "maxPositions": item.max_positions, "maxPositionPct": float(item.max_position_pct),
        "maxSectorPct": float(item.max_sector_pct), "riskPerTradePct": float(item.risk_per_trade_pct),
        "minimumScore": float(item.minimum_score), "minimumRiskReward": float(item.minimum_risk_reward),
        "pivotWindow": item.pivot_window, "minimumSwingPct": float(item.minimum_swing_pct),
        "allowProbe": item.allow_probe, "allowAdd": item.allow_add,
        "trailingStopEnabled": item.trailing_stop_enabled,
        "openingReminderEnabled": item.opening_reminder_enabled,
        "brokerFeeDiscount": float(item.broker_fee_discount), "slippageRate": float(item.slippage_rate),
        "dayTradeCloseTime": item.day_trade_close_time, "settingsVersion": item.settings_version,
        "updatedAt": item.updated_at.isoformat(),
    }


def update_settings(db: Session, values: dict, user_id: str, at: datetime) -> PatternRobotSetting:
    item = ensure_pattern_settings(db, at)
    previous_mode = item.performance_mode
    mapping = {
        "robotMode": "robot_mode", "performanceMode": "performance_mode", "initialCapital": "initial_capital",
        "maxPositions": "max_positions", "maxPositionPct": "max_position_pct", "maxSectorPct": "max_sector_pct",
        "riskPerTradePct": "risk_per_trade_pct", "minimumScore": "minimum_score",
        "minimumRiskReward": "minimum_risk_reward", "pivotWindow": "pivot_window",
        "minimumSwingPct": "minimum_swing_pct", "allowProbe": "allow_probe", "allowAdd": "allow_add",
        "trailingStopEnabled": "trailing_stop_enabled", "openingReminderEnabled": "opening_reminder_enabled",
        "brokerFeeDiscount": "broker_fee_discount", "slippageRate": "slippage_rate",
        "dayTradeCloseTime": "day_trade_close_time",
    }
    numeric_decimal = {
        "initialCapital", "maxPositionPct", "maxSectorPct", "riskPerTradePct", "minimumScore",
        "minimumRiskReward", "minimumSwingPct", "brokerFeeDiscount", "slippageRate",
    }
    for source, target in mapping.items():
        if source not in values or values[source] is None:
            continue
        if source == "performanceMode" and values[source] != previous_mode:
            setattr(item, _cash_field(previous_mode), item.cash)
        setattr(item, target, _d(values[source], 6) if source in numeric_decimal else values[source])
        if source == "performanceMode" and values[source] != previous_mode:
            item.cash = getattr(item, _cash_field(values[source]))
        if source == "initialCapital" and not db.scalar(select(PatternTradeCycle.id).limit(1)):
            initial = _d(values[source], 2)
            item.paper_live_cash = initial
            item.manual_paper_cash = initial
            item.backtest_cash = initial
            item.cash = initial
    item.settings_version += 1
    item.updated_by = user_id
    item.updated_at = at
    db.flush()
    return item


def _candles(stock: PatternStockInput, adjusted: bool) -> list[Candle]:
    source = stock.adjusted_prices if adjusted else stock.actual_prices
    return [Candle(row.date, row.open, row.high, row.low, row.close, row.volume, row.turnover) for row in source]


def _eligible(stock: PatternStockInput, trade_date: date) -> tuple[bool, str]:
    if stock.is_etf or stock.is_etn or stock.is_warrant:
        return False, "非普通股"
    if stock.is_disposed or stock.is_full_delivery:
        return False, "處置或全額交割股"
    if stock.current_volume <= 0 or stock.current_turnover <= 0:
        return False, "當日無成交"
    if len(stock.adjusted_prices) < 180 or len(stock.actual_prices) < 20:
        return False, "歷史資料不足180個交易日"
    if stock.listing_date and (trade_date - stock.listing_date).days < 168:
        return False, "上市櫃未滿120個交易日"
    average_turnover = sum(row.turnover for row in stock.actual_prices[-20:]) / 20
    if average_turnover < 30_000_000:
        return False, "20日平均成交金額低於3,000萬元"
    return True, ""


def _threshold(settings: PatternRobotSetting, market_regime: str) -> float:
    base = float(settings.minimum_score)
    if market_regime == "strong_bear":
        return max(85, base)
    if market_regime == "bear":
        return max(80, base)
    return base


def _lock_scan(db: Session, trade_date: date) -> bool:
    if db.bind and db.bind.dialect.name == "postgresql":
        lock_key = 8_240_000 + trade_date.toordinal()
        return bool(db.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": lock_key}))
    return True


def _store_detection(
    db: Session, stock: PatternStockInput, result: PatternResult, payload: PatternScanPayload,
    now: datetime, all_patterns: list[str], primary: bool, settings: PatternRobotSetting,
) -> PatternDetection:
    item = db.scalar(select(PatternDetection).where(
        PatternDetection.trade_date == payload.trade_date,
        PatternDetection.stock_code == stock.stock_code,
        PatternDetection.pattern_type == result.pattern_type,
    ))
    values = {
        "stock_name": stock.stock_name, "market_type": stock.market_type, "sector_name": stock.sector_name,
        "pattern_status": result.pattern_status, "pattern_score": _d(result.score, 2), "primary_pattern": primary,
        "start_date": result.start_date, "confirmed_at": result.confirmed_at,
        "detected_at": now, "pivot_confirmed_at": datetime.combine(result.pivot_confirmed_date, datetime.min.time(), UTC),
        "neckline_price": _d(result.neckline_price), "breakout_price": _d(result.breakout_price),
        "current_price": _d(stock.current_price), "target_price": _d(result.target_price),
        "invalidation_price": _d(result.invalidation_price), "stop_loss_price": _d(result.stop_loss_price),
        "entry_price_low": _d(result.entry_price_low), "entry_price_high": _d(result.entry_price_high),
        "add_price": _d(result.add_price), "take_profit_1": _d(result.take_profit_1),
        "take_profit_2": _d(result.take_profit_2), "trailing_stop_price": _d(result.trailing_stop_price),
        "volume_ratio": _d(result.volume_ratio), "distance_to_breakout_pct": _d(result.distance_to_breakout_pct),
        "risk_reward_ratio": _d(result.risk_reward_ratio), "completion_pct": _d(result.completion_pct, 2),
        "action": result.action, "action_label": result.action_label,
        "suggested_position_pct": _d(result.suggested_position_pct, 2),
        "market_regime": payload.market_regime, "volume_confirmed": result.volume_ratio >= 1.3,
        "key_points_json": _json(result.key_points), "score_breakdown_json": _json(result.score_breakdown),
        "reasons_json": _json(result.reasons), "missing_conditions_json": _json(result.missing_conditions),
        "risk_warnings_json": _json(result.risk_warnings), "all_patterns_json": _json(all_patterns),
        "source_json": _json({"quote": stock.quote_source, "adjustedHistory": payload.sources, "quoteTime": stock.quote_time}),
        "updated_at": now,
    }
    equity = portfolio_equity(db, settings)
    values["suggested_quantity"] = risk_sized_quantity(
        equity=equity, cash=float(settings.cash), entry_price=stock.current_price,
        stop_loss_price=result.stop_loss_price, risk_per_trade_pct=float(settings.risk_per_trade_pct),
        max_position_pct=float(settings.max_position_pct),
    )
    if item is None:
        item = PatternDetection(
            trade_date=payload.trade_date, stock_code=stock.stock_code, pattern_type=result.pattern_type,
            created_at=now, **values,
        )
        db.add(item)
    else:
        for key, value in values.items():
            setattr(item, key, value)
    db.flush()
    return item


def _record_signal(
    db: Session, detection: PatternDetection, signal_type: str, at: datetime,
    *, action_override: str | None = None,
) -> PatternSignal | None:
    existing = db.scalar(select(PatternSignal).where(
        PatternSignal.trade_date == detection.trade_date, PatternSignal.stock_code == detection.stock_code,
        PatternSignal.pattern_type == detection.pattern_type, PatternSignal.signal_type == signal_type,
        PatternSignal.signal_version == 1,
    ))
    if existing:
        return None
    signal = PatternSignal(
        detection_id=detection.id, trade_date=detection.trade_date, stock_code=detection.stock_code,
        stock_name=detection.stock_name, pattern_type=detection.pattern_type, signal_type=signal_type,
        signal_version=1, action=action_override or detection.action, signal_price=detection.current_price,
        quantity=detection.suggested_quantity, reasons_json=detection.reasons_json, signal_time=at,
    )
    db.add(signal)
    db.flush()
    message_label = {
        "WATCH": "加入觀察", "PREPARE": "接近突破", "PROBE_BUY": "模擬試單",
        "BUY": "模擬正式買進", "ADD": "模擬加碼", "STOP_LOSS": "模擬停損",
        "EXIT": "模擬出場", "REDUCE": "模擬部分停利",
    }.get(signal_type, signal_type)
    db.add(PatternTradeMessage(
        signal_id=signal.id, message_type=signal_type, message_version=1,
        stock_code=detection.stock_code, stock_name=detection.stock_name,
        pattern_type=detection.pattern_type, action=detection.action,
        title=f"型態選股機器人｜{message_label}",
        message=(f"{detection.stock_code} {detection.stock_name}｜{PATTERN_LABELS.get(detection.pattern_type, detection.pattern_type)}｜"
                 f"{STATUS_LABELS.get(detection.pattern_status, detection.pattern_status)}｜{float(detection.pattern_score):.0f}分"),
        price=detection.current_price, quantity=detection.suggested_quantity,
        amount=_d(float(detection.current_price) * detection.suggested_quantity, 2),
        reasons_json=detection.reasons_json, created_at=at,
    ))
    return signal


def _fee(amount: float, settings: PatternRobotSetting) -> float:
    return round(max(1, amount * COMMISSION_RATE * float(settings.broker_fee_discount)), 2)


def portfolio_equity(db: Session, settings: PatternRobotSetting) -> float:
    market_value = db.scalar(select(func.sum(PatternPosition.current_price * PatternPosition.quantity)).where(
        PatternPosition.status == "OPEN", PatternPosition.performance_mode == settings.performance_mode,
    )) or 0
    return float(settings.cash) + float(market_value)


def _buy_signal(
    db: Session, settings: PatternRobotSetting, detection: PatternDetection,
    signal: PatternSignal, stock: PatternStockInput, at: datetime,
    *, requested_quantity: int | None = None, requested_action: str | None = None,
) -> PatternPosition | None:
    if settings.robot_mode == "ALERT_ONLY" or settings.performance_mode == "BACKTEST":
        signal.processed_at = at
        return None
    trade_action = requested_action or detection.action
    if trade_action not in {"PROBE_BUY", "BUY", "ADD"}:
        return None
    if trade_action == "PROBE_BUY" and not settings.allow_probe:
        return None
    open_positions = list(db.scalars(select(PatternPosition).where(
        PatternPosition.status == "OPEN", PatternPosition.performance_mode == settings.performance_mode,
    )).all())
    position = next((item for item in open_positions if item.stock_code == detection.stock_code), None)
    if position is None and len(open_positions) >= settings.max_positions:
        return _reject_order(db, settings, signal, "已達同時持股上限", at)
    if position and trade_action != "ADD":
        signal.processed_at = at
        return position
    if position and (not settings.allow_add or position.auto_trade_paused):
        return None
    quantity = requested_quantity if requested_quantity is not None else detection.suggested_quantity
    if requested_quantity is not None:
        quantity = requested_quantity
    elif trade_action == "PROBE_BUY":
        quantity = int(quantity * .35)
    elif trade_action == "BUY":
        quantity = int(quantity * .60)
    else:
        quantity = int(quantity * .30)
    # Never assume a large market order fully fills; cap it at 1% of observed daily volume.
    quantity = min(quantity, max(0, int(stock.current_volume * .01)))
    if quantity <= 0:
        return _reject_order(db, settings, signal, "風險或流動性可買股數為0", at)
    execution_price = stock.current_price * (1 + float(settings.slippage_rate))
    gross = execution_price * quantity
    fee = _fee(gross, settings)
    if gross + fee > float(settings.cash):
        quantity = int(max(0, (float(settings.cash) - 1) / execution_price))
        gross = execution_price * quantity
        fee = _fee(gross, settings) if quantity else 0
    if quantity <= 0:
        return _reject_order(db, settings, signal, "可用資金不足", at)
    order = PatternOrder(
        signal_id=signal.id, performance_mode=settings.performance_mode, order_action=trade_action,
        stock_code=detection.stock_code, quantity=quantity, order_price=detection.current_price,
        status="PENDING", filled_quantity=0, created_at=at, updated_at=at,
    )
    db.add(order)
    db.flush()
    fill = PatternFill(
        order_id=order.id, signal_id=signal.id, stock_code=detection.stock_code, side="BUY",
        signal_price=detection.current_price, filled_price=_d(execution_price), quantity=quantity,
        gross_amount=_d(gross, 2), fee=_d(fee, 2), tax=Decimal("0"),
        slippage=_d((execution_price - stock.current_price) * quantity, 2),
        net_amount=_d(-(gross + fee), 2), realized_pnl=Decimal("0"), filled_at=at,
    )
    db.add(fill)
    db.flush()
    _set_cash(settings, float(settings.cash) - gross - fee)
    settings.updated_at = at
    if position is None:
        cycle = PatternTradeCycle(
            stock_code=detection.stock_code, stock_name=detection.stock_name,
            primary_pattern=detection.pattern_type, all_patterns_json=detection.all_patterns_json,
            pattern_score=detection.pattern_score, robot_mode=settings.robot_mode,
            performance_mode=settings.performance_mode, market_regime=detection.market_regime,
            sector_strength=detection.sector_strength, status="OPEN", first_entry_at=at,
            cumulative_buy_quantity=quantity, cumulative_buy_amount=_d(gross + fee, 2),
            trading_cost=_d(fee, 2), reasons_json=detection.reasons_json, created_at=at, updated_at=at,
        )
        db.add(cycle)
        db.flush()
        position = PatternPosition(
            trade_cycle_id=cycle.id, stock_code=detection.stock_code, stock_name=detection.stock_name,
            primary_pattern=detection.pattern_type, pattern_status=detection.pattern_status,
            robot_mode=settings.robot_mode, performance_mode=settings.performance_mode,
            status="OPEN", quantity=quantity, sellable_quantity=quantity,
            average_cost=_d((gross + fee) / quantity), current_price=_d(stock.current_price),
            invested_cost=_d(gross + fee, 2), stop_loss_price=detection.stop_loss_price,
            take_profit_1=detection.take_profit_1, take_profit_2=detection.take_profit_2,
            pattern_target_price=detection.target_price, trailing_stop_price=detection.trailing_stop_price,
            highest_price=_d(stock.current_price), lowest_price=_d(stock.current_price),
            first_entry_at=at, last_add_at=None, updated_at=at,
        )
        db.add(position)
        db.flush()
    else:
        cycle = db.get(PatternTradeCycle, position.trade_cycle_id)
        if cycle is None:
            raise RuntimeError(f"trade cycle {position.trade_cycle_id} not found")
        old_total = float(position.average_cost) * position.quantity
        position.quantity += quantity
        position.sellable_quantity += quantity
        position.average_cost = _d((old_total + gross + fee) / position.quantity)
        position.invested_cost = _d(float(position.invested_cost) + gross + fee, 2)
        position.last_add_at = at
        position.updated_at = at
        cycle.cumulative_buy_quantity += quantity
        cycle.cumulative_buy_amount = _d(float(cycle.cumulative_buy_amount) + gross + fee, 2)
        cycle.trading_cost = _d(float(cycle.trading_cost) + fee, 2)
        cycle.updated_at = at
    db.add(PatternPositionLot(
        position_id=position.id, fill_id=fill.id, quantity=quantity, remaining_quantity=quantity,
        cost_per_share=_d((gross + fee) / quantity), acquired_at=at,
    ))
    order.status = "FILLED"
    order.filled_quantity = quantity
    order.updated_at = at
    signal.quantity = quantity
    signal.processed_at = at
    message = db.scalar(select(PatternTradeMessage).where(PatternTradeMessage.signal_id == signal.id))
    if message:
        message.quantity = quantity
        message.amount = _d(gross + fee, 2)
        message.cash_impact = _d(-(gross + fee), 2)
        message.position_impact = quantity
    return position


def _reject_order(db: Session, settings: PatternRobotSetting, signal: PatternSignal, reason: str, at: datetime):
    db.add(PatternOrder(
        signal_id=signal.id, performance_mode=settings.performance_mode, order_action=signal.action,
        stock_code=signal.stock_code, quantity=max(0, signal.quantity), order_price=signal.signal_price,
        status="REJECTED", filled_quantity=0, rejection_reason=reason, created_at=at, updated_at=at,
    ))
    signal.processed_at = at
    return None


def _sell_position(
    db: Session, settings: PatternRobotSetting, position: PatternPosition, stock: PatternStockInput,
    quantity: int, reason: str, action: str, at: datetime, *, signal_type: str | None = None,
) -> None:
    quantity = min(quantity, position.sellable_quantity, position.quantity)
    if quantity <= 0:
        return
    detection = db.scalar(select(PatternDetection).where(
        PatternDetection.stock_code == position.stock_code,
    ).order_by(PatternDetection.trade_date.desc(), PatternDetection.pattern_score.desc()).limit(1))
    if detection is None:
        return
    signal = _record_signal(db, detection, signal_type or action, at, action_override=action)
    if signal is None:
        return
    signal.quantity = quantity
    execution_price = stock.current_price * (1 - float(settings.slippage_rate))
    gross = execution_price * quantity
    fee, tax = _fee(gross, settings), round(gross * SELL_TAX_RATE, 2)
    net = gross - fee - tax
    cost = float(position.average_cost) * quantity
    pnl = net - cost
    order = PatternOrder(
        signal_id=signal.id, performance_mode=settings.performance_mode, order_action=action,
        stock_code=position.stock_code, quantity=quantity, order_price=_d(stock.current_price),
        status="FILLED", filled_quantity=quantity, created_at=at, updated_at=at,
    )
    db.add(order)
    db.flush()
    db.add(PatternFill(
        order_id=order.id, signal_id=signal.id, stock_code=position.stock_code, side="SELL",
        signal_price=_d(stock.current_price), filled_price=_d(execution_price), quantity=quantity,
        gross_amount=_d(gross, 2), fee=_d(fee, 2), tax=_d(tax, 2),
        slippage=_d((stock.current_price - execution_price) * quantity, 2), net_amount=_d(net, 2),
        realized_pnl=_d(pnl, 2), filled_at=at,
    ))
    # FIFO lots keep partial-sale cost and available shares consistent.
    remaining = quantity
    lots = list(db.scalars(select(PatternPositionLot).where(
        PatternPositionLot.position_id == position.id, PatternPositionLot.remaining_quantity > 0,
    ).order_by(PatternPositionLot.acquired_at, PatternPositionLot.id)).all())
    for lot in lots:
        used = min(remaining, lot.remaining_quantity)
        lot.remaining_quantity -= used
        remaining -= used
        if remaining == 0:
            break
    position.quantity -= quantity
    position.sellable_quantity -= quantity
    position.realized_pnl = _d(float(position.realized_pnl) + pnl, 2)
    position.invested_cost = _d(max(0, float(position.invested_cost) - cost), 2)
    position.updated_at = at
    _set_cash(settings, float(settings.cash) + net)
    settings.updated_at = at
    cycle = db.get(PatternTradeCycle, position.trade_cycle_id)
    if cycle is None:
        raise RuntimeError(f"trade cycle {position.trade_cycle_id} not found")
    cycle.cumulative_sell_quantity += quantity
    cycle.cumulative_sell_amount = _d(float(cycle.cumulative_sell_amount) + net, 2)
    cycle.realized_pnl = _d(float(cycle.realized_pnl) + pnl, 2)
    cycle.trading_cost = _d(float(cycle.trading_cost) + fee + tax, 2)
    cycle.updated_at = at
    if position.quantity == 0:
        position.status = "CLOSED"
        position.closed_at = at
        position.unrealized_pnl = Decimal("0")
        cycle.status = "CLOSED"
        cycle.closed_at = at
        cycle.exit_reason = reason
        cycle.unrealized_pnl = Decimal("0")
    signal.processed_at = at
    message = db.scalar(select(PatternTradeMessage).where(PatternTradeMessage.signal_id == signal.id))
    if message:
        message.message = f"{position.stock_code} {position.stock_name}｜{reason}｜成交 {quantity:,} 股｜本次損益 {pnl:+,.0f} 元"
        message.quantity = quantity
        message.amount = _d(net, 2)
        message.cash_impact = _d(net, 2)
        message.position_impact = -quantity


def _manage_position(
    db: Session, settings: PatternRobotSetting, position: PatternPosition,
    stock: PatternStockInput, at: datetime,
) -> None:
    price = stock.current_price
    position.current_price = _d(price)
    position.highest_price = _d(max(float(position.highest_price), price))
    position.lowest_price = _d(min(float(position.lowest_price), price))
    unrealized = (price - float(position.average_cost)) * position.quantity
    position.unrealized_pnl = _d(unrealized, 2)
    position.updated_at = at
    cycle = db.get(PatternTradeCycle, position.trade_cycle_id)
    if cycle is None:
        raise RuntimeError(f"trade cycle {position.trade_cycle_id} not found")
    cycle.unrealized_pnl = position.unrealized_pnl
    cycle.mfe = _d(max(float(cycle.mfe), unrealized), 2)
    cycle.mae = _d(min(float(cycle.mae), unrealized), 2)
    cycle.updated_at = at
    if position.auto_trade_paused:
        return
    if settings.trailing_stop_enabled and price >= float(position.average_cost) * 1.08:
        actual = _candles(stock, False)
        ma10 = sum(row.close for row in actual[-10:]) / 10
        trailing = max(ma10, actual[-2].low if len(actual) > 1 else ma10, price * .96)
        position.trailing_stop_price = _d(max(float(position.trailing_stop_price or 0), trailing))
    effective_stop = max(float(position.stop_loss_price), float(position.trailing_stop_price or 0))
    if price <= effective_stop:
        _sell_position(db, settings, position, stock, position.quantity, "TRAILING_STOP" if price > float(position.average_cost) else "STOP_LOSS", "STOP_LOSS", at)
    elif position.take_profit_stage == 0 and price >= float(position.take_profit_1):
        _sell_position(db, settings, position, stock, max(1, int(position.quantity * .20)), "TAKE_PROFIT_1", "REDUCE", at)
        position.take_profit_stage = 1
    elif position.take_profit_stage == 1 and price >= float(position.take_profit_2):
        _sell_position(db, settings, position, stock, max(1, int(position.quantity * .375)), "TAKE_PROFIT_2", "REDUCE", at)
        position.take_profit_stage = 2
    elif position.take_profit_stage == 2 and price >= float(position.pattern_target_price):
        _sell_position(db, settings, position, stock, max(1, int(position.quantity * .60)), "PATTERN_TARGET", "REDUCE", at)
        position.take_profit_stage = 3
    local_time = at.astimezone(TAIPEI).strftime("%H:%M")
    if settings.robot_mode == "DAY_TRADE" and local_time >= settings.day_trade_close_time and position.status == "OPEN":
        _sell_position(db, settings, position, stock, position.quantity, "DAY_TRADE_CLOSE", "EXIT", at)


def record_daily_equity(db: Session, settings: PatternRobotSetting, trade_date: date, at: datetime) -> PatternDailyEquity:
    positions = list(db.scalars(select(PatternPosition).where(
        PatternPosition.status == "OPEN", PatternPosition.performance_mode == settings.performance_mode,
    )).all())
    market_value = sum(float(item.current_price) * item.quantity for item in positions)
    unrealized = sum(float(item.unrealized_pnl) for item in positions)
    realized = db.scalar(select(func.sum(PatternTradeCycle.realized_pnl)).where(
        PatternTradeCycle.performance_mode == settings.performance_mode,
    )) or 0
    equity = float(settings.cash) + market_value
    previous = db.scalar(select(PatternDailyEquity).where(
        PatternDailyEquity.trade_date < trade_date,
        PatternDailyEquity.robot_mode == settings.robot_mode,
        PatternDailyEquity.performance_mode == settings.performance_mode,
    ).order_by(PatternDailyEquity.trade_date.desc()).limit(1))
    daily_pnl = equity - (float(previous.total_equity) if previous else float(settings.initial_capital))
    peak = db.scalar(select(func.max(PatternDailyEquity.total_equity)).where(
        PatternDailyEquity.robot_mode == settings.robot_mode,
        PatternDailyEquity.performance_mode == settings.performance_mode,
    )) or settings.initial_capital
    drawdown = (equity / max(float(peak), equity) - 1) * 100
    row = db.scalar(select(PatternDailyEquity).where(
        PatternDailyEquity.trade_date == trade_date, PatternDailyEquity.robot_mode == settings.robot_mode,
        PatternDailyEquity.performance_mode == settings.performance_mode,
    ))
    values = {
        "performance_mode": settings.performance_mode, "cash": _d(settings.cash, 2),
        "market_value": _d(market_value, 2), "total_equity": _d(equity, 2),
        "daily_pnl": _d(daily_pnl, 2), "cumulative_pnl": _d(equity - float(settings.initial_capital), 2),
        "realized_pnl": _d(realized, 2),
        "unrealized_pnl": _d(unrealized, 2), "drawdown_pct": _d(drawdown, 4), "recorded_at": at,
    }
    if row is None:
        row = PatternDailyEquity(trade_date=trade_date, robot_mode=settings.robot_mode, **values)
        db.add(row)
    else:
        for key, value in values.items():
            setattr(row, key, value)
    return row


def process_pattern_scan(db: Session, payload: PatternScanPayload, *, force: bool = False) -> dict:
    now = payload.generated_at.astimezone(UTC)
    if not payload.is_trading_day:
        return {"status": "skipped_non_trading_day", "tradeDate": payload.trade_date.isoformat()}
    if not _lock_scan(db, payload.trade_date):
        return {"status": "locked", "tradeDate": payload.trade_date.isoformat()}
    settings = ensure_pattern_settings(db, now)
    if not settings.enabled and not force:
        return {"status": "disabled", "tradeDate": payload.trade_date.isoformat()}
    run = db.scalar(select(PatternRobotRun).where(
        PatternRobotRun.trade_date == payload.trade_date, PatternRobotRun.run_type == "OPEN_SCAN",
    ))
    if run and run.status == "COMPLETED" and not force:
        return {"status": "already_completed", "runId": run.id, "matchedCount": run.matched_count}
    if run is None:
        run = PatternRobotRun(
            trade_date=payload.trade_date, run_type="OPEN_SCAN", status="RUNNING",
            parameter_version=settings.settings_version, started_at=now,
        )
        db.add(run)
        db.flush()
    else:
        run.status, run.error_message, run.started_at = "RUNNING", None, now
    eligible_count = 0
    detections: list[PatternDetection] = []
    counts = {key: 0 for key in PATTERN_LABELS}
    status_counts = {key: 0 for key in ["FORMING", "NEAR_BREAKOUT", "INTRADAY_BREAKOUT", "CONFIRMED_BREAKOUT", "FAILED_BREAKOUT", "INVALIDATED"]}
    stock_map = {stock.stock_code: stock for stock in payload.stocks}
    for stock in payload.stocks:
        eligible, _ = _eligible(stock, payload.trade_date)
        if not eligible:
            continue
        eligible_count += 1
        results = detect_patterns(
            _candles(stock, True), pivot_window=settings.pivot_window,
            minimum_swing_pct=float(settings.minimum_swing_pct), close_complete=stock.close_complete,
            market_regime=payload.market_regime, vwap=stock.vwap,
        )
        all_patterns = [item.pattern_type for item in results]
        for index, result in enumerate(results):
            previous_confirmed = db.scalar(select(PatternDetection.id).where(
                PatternDetection.stock_code == stock.stock_code,
                PatternDetection.pattern_type == result.pattern_type,
                PatternDetection.trade_date < payload.trade_date,
                PatternDetection.pattern_status == "CONFIRMED_BREAKOUT",
            ).limit(1))
            if previous_confirmed and stock.current_price < result.neckline_price * .99:
                result.pattern_status = "FAILED_BREAKOUT"
                result.action, result.action_label = "STOP_LOSS", "突破失敗停損"
                result.risk_warnings.append("先前突破已跌回關鍵線下方，不得加碼")
            detection = _store_detection(db, stock, result, payload, now, all_patterns, index == 0, settings)
            detections.append(detection)
            counts[result.pattern_type] += 1
            status_counts[result.pattern_status] += 1
    # First restore and mark-to-market every existing position, then act on new signals.
    for position in list(db.scalars(select(PatternPosition).where(
        PatternPosition.status == "OPEN", PatternPosition.performance_mode == settings.performance_mode,
    )).all()):
        stock = stock_map.get(position.stock_code)
        if stock:
            _manage_position(db, settings, position, stock, now)
            if payload.market_regime == "strong_bear" and position.status == "OPEN":
                _sell_position(db, settings, position, stock, position.quantity, "MARKET_RISK", "EXIT", now)
            elif payload.market_regime == "bear" and position.status == "OPEN" and position.quantity > 1:
                _sell_position(db, settings, position, stock, max(1, int(position.quantity * .30)), "MARKET_RISK", "REDUCE", now)
    minimum_score = _threshold(settings, payload.market_regime)
    for detection in sorted(detections, key=lambda item: float(item.pattern_score), reverse=True):
        if detection.pattern_status in {"FORMING", "NEAR_BREAKOUT"}:
            signal = _record_signal(db, detection, detection.action, now) if float(detection.pattern_score) >= 60 else None
            if signal:
                signal.processed_at = now
            system_watch = db.scalar(select(PatternWatchlist).where(
                PatternWatchlist.user_id == "system-pattern-robot",
                PatternWatchlist.stock_code == detection.stock_code,
                PatternWatchlist.pattern_type == detection.pattern_type,
            ))
            if system_watch is None:
                db.add(PatternWatchlist(
                    user_id="system-pattern-robot", detection_id=detection.id,
                    stock_code=detection.stock_code, stock_name=detection.stock_name,
                    pattern_type=detection.pattern_type, active=True, added_at=now, updated_at=now,
                ))
            else:
                system_watch.detection_id, system_watch.active, system_watch.updated_at = detection.id, True, now
        elif detection.pattern_status in {"FAILED_BREAKOUT", "INVALIDATED"}:
            for watch in db.scalars(select(PatternWatchlist).where(
                PatternWatchlist.stock_code == detection.stock_code,
                PatternWatchlist.pattern_type == detection.pattern_type,
                PatternWatchlist.active.is_(True),
            )):
                watch.active, watch.removed_at = False, now
                watch.removed_reason, watch.updated_at = detection.pattern_status, now
        if float(detection.pattern_score) < minimum_score:
            continue
        if payload.market_regime == "strong_bear" and detection.action in {"BUY", "PROBE_BUY", "ADD"}:
            continue
        if detection.action in {"PROBE_BUY", "BUY", "ADD"}:
            signal = _record_signal(db, detection, detection.action, now)
            if signal:
                _buy_signal(db, settings, detection, signal, stock_map[detection.stock_code], now)
        else:
            open_position = db.scalar(select(PatternPosition).where(
                PatternPosition.stock_code == detection.stock_code,
                PatternPosition.status == "OPEN",
                PatternPosition.performance_mode == settings.performance_mode,
            ).limit(1))
            stock = stock_map[detection.stock_code]
            current_candle = stock.actual_prices[-1]
            successful_retest = (
                open_position is not None and settings.allow_add and not open_position.auto_trade_paused
                and detection.pattern_status == "CONFIRMED_BREAKOUT"
                and float(detection.neckline_price) <= stock.current_price <= float(detection.neckline_price) * 1.03
                and float(detection.volume_ratio) < 1.3 and current_candle.close >= current_candle.open
                and (stock.vwap is None or stock.current_price >= stock.vwap)
            )
            if successful_retest:
                detection.action, detection.action_label = "ADD", "回測成功加碼"
                signal = _record_signal(db, detection, "ADD", now)
                if signal:
                    _buy_signal(db, settings, detection, signal, stock, now)
    record_daily_equity(db, settings, payload.trade_date, now)
    run.status = "COMPLETED"
    run.scanned_count = eligible_count
    run.matched_count = len({item.stock_code for item in detections if float(item.pattern_score) >= minimum_score})
    run.counts_json = _json({"patterns": counts, "statuses": status_counts})
    run.source_json = _json({"sources": payload.sources, "sourceStatus": payload.source_status})
    run.completed_at = now
    day_start = datetime.combine(payload.trade_date, datetime.min.time(), UTC)
    existing_scan_message = db.scalar(select(PatternTradeMessage.id).where(
        PatternTradeMessage.signal_id.is_(None), PatternTradeMessage.message_type == "SCAN_COMPLETED",
        PatternTradeMessage.created_at >= day_start,
        PatternTradeMessage.created_at < day_start + timedelta(days=1),
    ))
    if not existing_scan_message:
        matched = run.matched_count
        db.add(PatternTradeMessage(
            signal_id=None, message_type="SCAN_COMPLETED", message_version=1,
            title="型態選股機器人掃描完成",
            message=(f"本次偵測到{matched}檔股票符合多方技術型態，其中"
                     f"{status_counts['CONFIRMED_BREAKOUT']}檔有效突破、"
                     f"{status_counts['NEAR_BREAKOUT']}檔接近突破、"
                     f"{status_counts['FORMING']}檔形成中。型態僅代表技術結構，仍需等待量價、買點及風險報酬確認。"),
            reasons_json=_json({"patterns": counts, "statuses": status_counts, "runId": run.id}),
            created_at=now,
        ))
    db.commit()
    return {
        "status": "completed", "runId": run.id, "tradeDate": payload.trade_date.isoformat(),
        "scannedCount": eligible_count, "matchedCount": run.matched_count,
        "patternCounts": counts, "statusCounts": status_counts,
    }


def detection_dict(item: PatternDetection) -> dict:
    return {
        "id": item.id, "tradeDate": item.trade_date.isoformat(), "stockCode": item.stock_code,
        "stockName": item.stock_name, "marketType": item.market_type, "sectorName": item.sector_name,
        "patternType": item.pattern_type, "patternLabel": PATTERN_LABELS.get(item.pattern_type, item.pattern_type),
        "patternStatus": item.pattern_status, "statusLabel": STATUS_LABELS.get(item.pattern_status, item.pattern_status),
        "patternScore": float(item.pattern_score), "primaryPattern": item.primary_pattern,
        "startDate": item.start_date.isoformat(), "confirmedAt": item.confirmed_at.isoformat() if item.confirmed_at else None,
        "detectedAt": item.detected_at.isoformat(), "necklinePrice": float(item.neckline_price),
        "breakoutPrice": float(item.breakout_price), "currentPrice": float(item.current_price),
        "targetPrice": float(item.target_price), "invalidationPrice": float(item.invalidation_price),
        "stopLossPrice": float(item.stop_loss_price), "entryPriceLow": float(item.entry_price_low),
        "entryPriceHigh": float(item.entry_price_high), "addPrice": float(item.add_price or 0),
        "takeProfit1": float(item.take_profit_1), "takeProfit2": float(item.take_profit_2),
        "trailingStopPrice": float(item.trailing_stop_price or 0), "volumeRatio": float(item.volume_ratio),
        "volumeConfirmed": item.volume_confirmed, "distanceToBreakoutPct": float(item.distance_to_breakout_pct),
        "riskRewardRatio": float(item.risk_reward_ratio), "completionPct": float(item.completion_pct),
        "action": item.action, "actionLabel": item.action_label,
        "suggestedPositionPct": float(item.suggested_position_pct), "suggestedQuantity": item.suggested_quantity,
        "marketRegime": item.market_regime, "sectorStrength": float(item.sector_strength),
        "keyPoints": _loads(item.key_points_json, []), "scoreBreakdown": _loads(item.score_breakdown_json, {}),
        "operationReasons": _loads(item.reasons_json, []), "missingConditions": _loads(item.missing_conditions_json, []),
        "riskWarnings": _loads(item.risk_warnings_json, []), "allPatterns": _loads(item.all_patterns_json, []),
    }


def position_dict(item: PatternPosition) -> dict:
    entered_at = item.first_entry_at if item.first_entry_at.tzinfo else item.first_entry_at.replace(tzinfo=UTC)
    held_days = max(0, (datetime.now(UTC) - entered_at).days)
    return {
        "id": item.id, "tradeCycleId": item.trade_cycle_id, "stockCode": item.stock_code,
        "stockName": item.stock_name, "primaryPattern": item.primary_pattern,
        "patternLabel": PATTERN_LABELS.get(item.primary_pattern, item.primary_pattern),
        "patternStatus": item.pattern_status, "robotMode": item.robot_mode,
        "performanceMode": item.performance_mode, "status": item.status, "quantity": item.quantity,
        "sellableQuantity": item.sellable_quantity, "averageCost": float(item.average_cost),
        "currentPrice": float(item.current_price), "investedCost": float(item.invested_cost),
        "marketValue": float(item.current_price) * item.quantity,
        "realizedPnl": float(item.realized_pnl), "unrealizedPnl": float(item.unrealized_pnl),
        "unrealizedReturnPct": float(item.unrealized_pnl) / max(.01, float(item.invested_cost)) * 100,
        "stopLossPrice": float(item.stop_loss_price), "takeProfit1": float(item.take_profit_1),
        "takeProfit2": float(item.take_profit_2), "patternTargetPrice": float(item.pattern_target_price),
        "trailingStopPrice": float(item.trailing_stop_price or 0), "holdingDays": held_days,
        "firstEntryAt": item.first_entry_at.isoformat(), "lastAddAt": item.last_add_at.isoformat() if item.last_add_at else None,
        "autoTradePaused": item.auto_trade_paused, "note": item.note,
    }


def performance(db: Session, performance_mode: str = "PAPER_LIVE") -> dict:
    settings = ensure_pattern_settings(db)
    account_cash = float(getattr(settings, _cash_field(performance_mode)))
    cycles = list(db.scalars(select(PatternTradeCycle).where(
        PatternTradeCycle.performance_mode == performance_mode,
    )).all())
    completed = [item for item in cycles if item.status == "CLOSED"]
    winners = [float(item.realized_pnl) for item in completed if item.realized_pnl > 0]
    losers = [float(item.realized_pnl) for item in completed if item.realized_pnl < 0]
    gross_profit, gross_loss = sum(winners), abs(sum(losers))
    average_win = _average(winners)
    average_loss = abs(_average(losers))
    win_rate = len(winners) / len(completed) * 100 if completed else 0
    profit_factor = gross_profit / gross_loss if gross_loss else (999 if gross_profit else 0)
    expectancy = win_rate / 100 * average_win - (1 - win_rate / 100) * average_loss
    positions = list(db.scalars(select(PatternPosition).where(
        PatternPosition.status == "OPEN", PatternPosition.performance_mode == performance_mode,
    )).all())
    market_value = sum(float(item.current_price) * item.quantity for item in positions)
    unrealized = sum(float(item.unrealized_pnl) for item in positions)
    equity = account_cash + market_value
    curves = list(db.scalars(select(PatternDailyEquity).where(
        PatternDailyEquity.performance_mode == performance_mode,
    ).order_by(PatternDailyEquity.trade_date)).all())
    maximum_drawdown = abs(min((float(item.drawdown_pct) for item in curves), default=0))
    holding_days = [(item.closed_at - item.first_entry_at).total_seconds() / 86400 for item in completed if item.closed_at]
    return {
        "performanceMode": performance_mode, "initialCapital": float(settings.initial_capital),
        "cash": account_cash, "marketValue": market_value, "totalEquity": equity,
        "realizedPnl": sum(float(item.realized_pnl) for item in completed), "unrealizedPnl": unrealized,
        "netPnl": equity - float(settings.initial_capital),
        "totalReturnPct": (equity / float(settings.initial_capital) - 1) * 100,
        "completedTrades": len(completed), "winRate": win_rate, "averageWin": average_win,
        "averageLoss": average_loss, "payoffRatio": average_win / average_loss if average_loss else 0,
        "profitFactor": profit_factor, "expectancy": expectancy,
        "largestWin": max(winners, default=0), "largestLoss": min(losers, default=0),
        "maximumDrawdownPct": maximum_drawdown, "averageHoldingDays": _average(holding_days),
        "capitalUsagePct": market_value / max(.01, equity) * 100,
    }


def performance_by_pattern(db: Session, performance_mode: str = "PAPER_LIVE") -> list[dict]:
    detections = list(db.scalars(select(PatternDetection)).all())
    cycles = list(db.scalars(select(PatternTradeCycle).where(
        PatternTradeCycle.performance_mode == performance_mode,
    )).all())
    result = []
    for key, label in PATTERN_LABELS.items():
        matches = [item for item in cycles if key in _loads(item.all_patterns_json, [item.primary_pattern])]
        completed = [item for item in matches if item.status == "CLOSED"]
        profits = [float(item.realized_pnl) for item in completed if item.realized_pnl > 0]
        losses = [float(item.realized_pnl) for item in completed if item.realized_pnl < 0]
        pattern_detections = [item for item in detections if item.pattern_type == key]
        failed = [item for item in pattern_detections if item.pattern_status == "FAILED_BREAKOUT"]
        result.append({
            "patternType": key, "patternLabel": label, "occurrences": len(pattern_detections),
            "entries": len(matches), "completedTrades": len(completed),
            "winRate": len(profits) / len(completed) * 100 if completed else 0,
            "averageReturnPct": _average([
                float(item.realized_pnl) / max(.01, float(item.cumulative_buy_amount)) * 100 for item in completed
            ]),
            "netPnl": sum(float(item.realized_pnl) for item in completed),
            "payoffRatio": _average(profits) / abs(_average(losses)) if losses else 0,
            "profitFactor": sum(profits) / abs(sum(losses)) if losses else (999 if profits else 0),
            "falseBreakoutRate": len(failed) / len(pattern_detections) * 100 if pattern_detections else 0,
            "averageHoldingDays": _average([
                (item.closed_at - item.first_entry_at).total_seconds() / 86400
                for item in completed if item.closed_at
            ]),
        })
    return result


def manual_position_trade(
    db: Session, position_id: int, *, action: str, quantity: int, price: float | None,
    reason: str, at: datetime,
) -> PatternPosition:
    position = db.get(PatternPosition, position_id)
    if position is None or position.status != "OPEN":
        raise ValueError("找不到未平倉部位")
    settings = ensure_pattern_settings(db, at)
    if position.performance_mode != settings.performance_mode:
        raise ValueError("目前績效模式與持倉不一致")
    detection = db.scalar(select(PatternDetection).where(
        PatternDetection.stock_code == position.stock_code,
    ).order_by(PatternDetection.trade_date.desc(), PatternDetection.pattern_score.desc()).limit(1))
    if detection is None:
        raise ValueError("找不到對應型態訊號")
    execution_reference = price or float(position.current_price)
    stock = PatternStockInput.model_construct(
        stock_code=position.stock_code, stock_name=position.stock_name,
        current_price=execution_reference, current_volume=1_000_000_000,
    )
    suffix = at.strftime("%H%M%S%f")[-10:]
    if action == "ADD":
        signal = _record_signal(db, detection, f"MADD{suffix}", at, action_override="ADD")
        if signal is None:
            raise ValueError("手動加碼訊號重複")
        signal.signal_price = _d(execution_reference)
        result = _buy_signal(
            db, settings, detection, signal, stock, at,
            requested_quantity=quantity, requested_action="ADD",
        )
        if result is None:
            raise ValueError("資金、流動性或持倉限制拒絕加碼")
    else:
        if quantity > position.sellable_quantity:
            raise ValueError("賣出股數不得超過可賣股數")
        sell_action = "EXIT" if action == "EXIT" else "REDUCE"
        sell_quantity = position.quantity if action == "EXIT" else quantity
        _sell_position(
            db, settings, position, stock, sell_quantity,
            f"MANUAL_{action}: {reason}", sell_action, at,
            signal_type=f"M{action[:3]}{suffix}",
        )
    db.commit()
    db.refresh(position)
    return position


def trades_csv(db: Session, performance_mode: str) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["tradeId", "stockCode", "stockName", "primaryPattern", "status", "firstEntryAt", "closedAt", "buyAmount", "sellAmount", "netPnl", "exitReason"])
    for item in db.scalars(select(PatternTradeCycle).where(
        PatternTradeCycle.performance_mode == performance_mode,
    ).order_by(PatternTradeCycle.first_entry_at.desc())):
        writer.writerow([
            item.id, item.stock_code, item.stock_name, PATTERN_LABELS.get(item.primary_pattern, item.primary_pattern),
            item.status, item.first_entry_at.isoformat(), item.closed_at.isoformat() if item.closed_at else "",
            item.cumulative_buy_amount, item.cumulative_sell_amount, item.realized_pnl, item.exit_reason or "",
        ])
    return "\ufeff" + output.getvalue()
