from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import json
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    RocketAccount, RocketCandidate, RocketDailyPortfolio, RocketNotification,
    RocketPosition, RocketSignal, RocketTrade,
)


INITIAL_CAPITAL = 1_000_000.0
BASE_ALLOCATIONS = (25.0, 20.0, 15.0, 10.0, 10.0)
COMMISSION_RATE = 0.001425
SELL_TAX_RATE = 0.003
MAX_TRADE_RISK_PCT = 0.01
EventType = Literal[
    "WATCH", "BREAKOUT", "BUY", "ADD", "HOLD", "REDUCE",
    "TAKE_PROFIT", "SELL", "STOP_LOSS", "WARNING", "MARKET", "SECTOR",
]


@dataclass(frozen=True, slots=True)
class RocketEvent:
    key: str
    event_type: EventType
    timestamp: datetime
    stock_code: str | None
    stock_name: str | None
    title: str
    message: str
    reason: str
    price: float | None = None
    rocket_score: float | None = None
    chase_risk: float | None = None
    quantity: int | None = None
    amount: float | None = None
    pnl: float | None = None
    pnl_percent: float | None = None
    strategy_type: str | None = None
    previous_status: str | None = None
    new_status: str = ""


def _decimal(value: float) -> Decimal:
    return Decimal(str(round(value, 4)))


def ensure_rocket_account(db: Session, at: datetime) -> RocketAccount:
    account = db.get(RocketAccount, 1)
    if account is None:
        account = RocketAccount(
            id=1, initial_capital=Decimal("1000000"), cash=Decimal("1000000"),
            realized_pnl=Decimal("0"), broker_fee_discount=Decimal("0.6"),
            slippage_rate=Decimal("0.001"), sound_enabled=False, updated_at=at,
        )
        db.add(account)
        db.flush()
    return account


def _priority(event_type: str) -> int:
    if event_type in {"STOP_LOSS", "SELL", "TAKE_PROFIT"}: return 1
    if event_type in {"BUY", "ADD", "REDUCE"}: return 2
    if event_type in {"BREAKOUT", "WARNING"}: return 3
    return 4


def record_rocket_event(db: Session, event: RocketEvent) -> bool:
    if db.scalar(select(RocketSignal.id).where(RocketSignal.signal_key == event.key)) is not None:
        return False
    db.add(RocketSignal(
        signal_key=event.key, stock_code=event.stock_code, stock_name=event.stock_name,
        signal_type=event.event_type, previous_status=event.previous_status,
        new_status=event.new_status or event.event_type, price=_decimal(event.price) if event.price is not None else None,
        rocket_score=_decimal(event.rocket_score) if event.rocket_score is not None else None,
        chase_risk=_decimal(event.chase_risk) if event.chase_risk is not None else None,
        strategy_type=event.strategy_type, reason=event.reason, created_at=event.timestamp,
    ))
    priority = _priority(event.event_type)
    db.add(RocketNotification(
        dedupe_key=event.key, created_at=event.timestamp, stock_code=event.stock_code,
        stock_name=event.stock_name, notification_type=event.event_type, priority=priority,
        title=event.title, message=event.message,
        price=_decimal(event.price) if event.price is not None else None,
        rocket_score=_decimal(event.rocket_score) if event.rocket_score is not None else None,
        chase_risk=_decimal(event.chase_risk) if event.chase_risk is not None else None,
        quantity=event.quantity, amount=_decimal(event.amount) if event.amount is not None else None,
        pnl=_decimal(event.pnl) if event.pnl is not None else None,
        pnl_percent=_decimal(event.pnl_percent) if event.pnl_percent is not None else None,
        strategy_type=event.strategy_type, reason=event.reason,
        is_read=priority == 4, read_at=event.timestamp if priority == 4 else None,
    ))
    return True


def _fee(amount: float, account: RocketAccount) -> float:
    return round(max(1.0, amount * COMMISSION_RATE * float(account.broker_fee_discount)), 2)


def portfolio_equity(db: Session, account: RocketAccount) -> float:
    market_value = db.scalar(select(func.sum(
        RocketPosition.current_price * RocketPosition.remaining_quantity
    )).where(RocketPosition.status == "open")) or 0
    return float(account.cash) + float(market_value)


def _buy(
    db: Session,
    account: RocketAccount,
    candidate: RocketCandidate,
    quantity: int,
    target_allocation: float,
    at: datetime,
    *,
    position: RocketPosition | None = None,
    event_type: Literal["BUY", "ADD"] = "BUY",
    reason: str,
) -> RocketPosition | None:
    if quantity <= 0:
        return None
    signal_price = float(candidate.current_price)
    execution_price = signal_price * (1 + float(account.slippage_rate))
    gross = execution_price * quantity
    fee = _fee(gross, account)
    total = gross + fee
    if total > float(account.cash):
        quantity = int(max(0, (float(account.cash) - 1) / execution_price))
        gross = execution_price * quantity
        fee = _fee(gross, account) if quantity else 0
        total = gross + fee
    if quantity <= 0:
        return None
    slippage = (execution_price - signal_price) * quantity
    account.cash = _decimal(float(account.cash) - total)
    account.updated_at = at
    if position is None:
        position = RocketPosition(
            stock_code=candidate.stock_code, stock_name=candidate.stock_name,
            sector_name=candidate.sector_name, strategy_type=candidate.pattern_type,
            market_regime=candidate.market_regime, status="open", entry_time=at,
            entry_price=_decimal(execution_price), average_cost=_decimal(total / quantity),
            current_price=candidate.current_price, target_allocation=_decimal(target_allocation),
            original_quantity=quantity, remaining_quantity=quantity, add_stage=1, take_profit_stage=0,
            stop_loss_price=candidate.stop_loss_price, trailing_stop_price=None,
            target_price_1=candidate.target_price_1, target_price_2=candidate.target_price_2,
            highest_price=candidate.current_price, lowest_price=candidate.current_price,
            rocket_score_entry=candidate.rocket_score, rocket_score_current=candidate.rocket_score,
            chase_risk_current=candidate.chase_risk_score, invested_cost=_decimal(total),
            realized_pnl=Decimal("0"), unrealized_pnl=Decimal("0"),
            max_favorable_excursion=Decimal("0"), max_adverse_excursion=Decimal("0"),
            latest_action="🟢 突破確認・首筆買進", created_at=at, updated_at=at,
        )
        db.add(position)
        db.flush()
    else:
        old_cost = float(position.average_cost) * position.remaining_quantity
        position.remaining_quantity += quantity
        position.original_quantity += quantity
        position.average_cost = _decimal((old_cost + total) / position.remaining_quantity)
        position.invested_cost = _decimal(float(position.invested_cost) + total)
        position.add_stage += 1
        position.latest_action = "🟣 回踩／站穩後加碼"
        position.updated_at = at
    db.add(RocketTrade(
        position_id=position.id, stock_code=position.stock_code, stock_name=position.stock_name,
        action=event_type, strategy_type=position.strategy_type, price=_decimal(execution_price),
        quantity=quantity, gross_amount=_decimal(gross), fee=_decimal(fee), tax=Decimal("0"),
        slippage=_decimal(slippage), net_amount=_decimal(-total), realized_pnl=Decimal("0"),
        reason=reason, executed_at=at,
    ))
    event_message = (
        f"{position.stock_code} {position.stock_name}｜{execution_price:.2f} 元模擬買進 {quantity:,} 股｜"
        f"NT${total:,.0f}｜Stop {float(candidate.stop_loss_price):.2f}｜"
        f"Target 1 {float(candidate.target_price_1):.2f}｜RR {float(candidate.risk_reward_ratio):.2f}"
        if event_type == "BUY"
        else f"{position.stock_code} {position.stock_name}｜本次加碼 {quantity:,} 股｜"
             f"加碼後 {position.remaining_quantity:,} 股｜新均價 {float(position.average_cost):.2f}"
    )
    record_rocket_event(db, RocketEvent(
        key=f"position:{position.id}:{event_type}:stage:{position.add_stage}", event_type=event_type,
        timestamp=at, stock_code=position.stock_code, stock_name=position.stock_name,
        title=f"飆股雷達｜{'買進' if event_type == 'BUY' else '加碼'}訊號",
        message=event_message,
        reason=reason, price=execution_price, rocket_score=float(candidate.rocket_score),
        chase_risk=float(candidate.chase_risk_score), quantity=quantity, amount=total,
        strategy_type=position.strategy_type, previous_status="WAIT" if event_type == "BUY" else "HOLD",
        new_status=event_type,
    ))
    return position


def _sell(
    db: Session,
    account: RocketAccount,
    position: RocketPosition,
    candidate: RocketCandidate,
    quantity: int,
    at: datetime,
    event_type: Literal["REDUCE", "TAKE_PROFIT", "SELL", "STOP_LOSS"],
    reason: str,
) -> None:
    quantity = min(quantity, position.remaining_quantity)
    if quantity <= 0:
        return
    signal_price = float(candidate.current_price)
    execution_price = signal_price * (1 - float(account.slippage_rate))
    gross = execution_price * quantity
    fee = _fee(gross, account)
    tax = round(gross * SELL_TAX_RATE, 2)
    net = gross - fee - tax
    cost = float(position.average_cost) * quantity
    pnl = net - cost
    pnl_pct = pnl / cost * 100 if cost else 0
    slippage = (signal_price - execution_price) * quantity
    account.cash = _decimal(float(account.cash) + net)
    account.realized_pnl = _decimal(float(account.realized_pnl) + pnl)
    account.updated_at = at
    position.remaining_quantity -= quantity
    position.realized_pnl = _decimal(float(position.realized_pnl) + pnl)
    position.latest_action = {
        "REDUCE": "🟠 動能下降・減碼", "TAKE_PROFIT": "💰 分批停利",
        "SELL": "🔴 趨勢結束・出場", "STOP_LOSS": "🔴 策略停損",
    }[event_type]
    if position.remaining_quantity == 0:
        position.status = "closed"
        position.exit_time = at
        position.exit_price = _decimal(execution_price)
        position.exit_reason = reason
        position.unrealized_pnl = Decimal("0")
    position.updated_at = at
    db.add(RocketTrade(
        position_id=position.id, stock_code=position.stock_code, stock_name=position.stock_name,
        action=event_type, strategy_type=position.strategy_type, price=_decimal(execution_price),
        quantity=quantity, gross_amount=_decimal(gross), fee=_decimal(fee), tax=_decimal(tax),
        slippage=_decimal(slippage), net_amount=_decimal(net), realized_pnl=_decimal(pnl),
        reason=reason, executed_at=at,
    ))
    record_rocket_event(db, RocketEvent(
        key=f"position:{position.id}:{event_type}:remaining:{position.remaining_quantity}", event_type=event_type,
        timestamp=at, stock_code=position.stock_code, stock_name=position.stock_name,
        title=f"飆股雷達｜{position.latest_action}",
        message=f"{execution_price:.2f} 元賣出 {quantity:,} 股，本次損益 {pnl:+,.0f} 元（{pnl_pct:+.2f}%）",
        reason=reason, price=execution_price, rocket_score=float(candidate.rocket_score),
        chase_risk=float(candidate.chase_risk_score), quantity=quantity, amount=net,
        pnl=pnl, pnl_percent=pnl_pct, strategy_type=position.strategy_type,
        previous_status="HOLD", new_status=event_type,
    ))


def open_new_positions(
    db: Session,
    account: RocketAccount,
    candidates: list[RocketCandidate],
    exposure_pct: float,
    at: datetime,
) -> int:
    open_positions = list(db.scalars(select(RocketPosition).where(RocketPosition.status == "open")).all())
    held = {item.stock_code for item in open_positions}
    slots = max(0, 5 - len(open_positions))
    if slots <= 0 or exposure_pct <= 0:
        return 0
    equity = portfolio_equity(db, account)
    opened = 0
    eligible = [item for item in candidates if item.candidate_status in {"can_enter", "strong_breakout"}]
    for trade_rank, candidate in enumerate(eligible):
        if opened >= slots or candidate.stock_code in held:
            continue
        rank_index = min(trade_rank, 4)
        target_pct = BASE_ALLOCATIONS[rank_index] * exposure_pct / 80
        target_allocation = equity * target_pct / 100
        first_stage_budget = target_allocation * .40
        expected_execution = float(candidate.current_price) * (1 + float(account.slippage_rate))
        per_share_risk = max(.01, expected_execution - float(candidate.stop_loss_price))
        risk_quantity = int(equity * MAX_TRADE_RISK_PCT / per_share_risk)
        budget_quantity = int(first_stage_budget / expected_execution)
        cash_quantity = int(float(account.cash) / expected_execution)
        quantity = min(risk_quantity, budget_quantity, cash_quantity)
        result = _buy(
            db, account, candidate, quantity, target_allocation, at,
            event_type="BUY", reason="正式突破＋量比確認＋Rocket Score 與 RR 達標",
        )
        if result is not None:
            held.add(candidate.stock_code); opened += 1
    return opened


def manage_open_positions(
    db: Session,
    account: RocketAccount,
    candidate_map: dict[str, RocketCandidate],
    market_regime: str,
    at: datetime,
) -> int:
    positions = list(db.scalars(select(RocketPosition).where(RocketPosition.status == "open")).all())
    events = 0
    for position in positions:
        candidate = candidate_map.get(position.stock_code)
        if candidate is None:
            continue
        previous_action = position.latest_action
        previous_chase = float(position.chase_risk_current)
        price = float(candidate.current_price)
        average = float(position.average_cost)
        position.current_price = candidate.current_price
        position.highest_price = _decimal(max(float(position.highest_price), price))
        position.lowest_price = _decimal(min(float(position.lowest_price), price))
        position.rocket_score_current = candidate.rocket_score
        position.chase_risk_current = candidate.chase_risk_score
        unrealized = (price - average) * position.remaining_quantity
        position.unrealized_pnl = _decimal(unrealized)
        position.max_favorable_excursion = _decimal(max(float(position.max_favorable_excursion), unrealized))
        position.max_adverse_excursion = _decimal(min(float(position.max_adverse_excursion), unrealized))
        return_pct = (price / average - 1) * 100 if average else 0
        atr = float(candidate.atr or price * .025)
        if return_pct >= 8:
            trailing = max(average, float(position.highest_price) - 2 * atr, float(candidate.ma10 or 0) * .985)
            position.trailing_stop_price = _decimal(max(float(position.trailing_stop_price or 0), trailing))
        elif return_pct >= 5:
            position.stop_loss_price = _decimal(max(float(position.stop_loss_price), average * .998))
        effective_stop = max(float(position.stop_loss_price), float(position.trailing_stop_price or 0))

        if price <= effective_stop:
            profitable = price > average
            _sell(db, account, position, candidate, position.remaining_quantity, at,
                  "TAKE_PROFIT" if profitable else "STOP_LOSS",
                  "跌破移動停利" if profitable else "突破失敗或跌破策略 Stop")
            events += 1; continue
        if market_regime == "bear":
            _sell(db, account, position, candidate, position.remaining_quantity, at, "SELL", "市場轉為空頭／崩跌，全面降低風險")
            events += 1; continue
        if float(candidate.rocket_score) < 65 or (candidate.sector_rank > 10 and return_pct < 0):
            _sell(db, account, position, candidate, position.remaining_quantity, at, "SELL", "Rocket Score 失速或族群快速掉出前十")
            events += 1; continue
        if return_pct >= 18 and position.take_profit_stage < 2:
            quantity = max(1, int(position.original_quantity * .25))
            _sell(db, account, position, candidate, quantity, at, "TAKE_PROFIT", "獲利達 18%，執行第二段 25% 停利")
            position.take_profit_stage = 2; events += 1; continue
        if return_pct >= 12 and position.take_profit_stage < 1:
            quantity = max(1, int(position.original_quantity * .25))
            _sell(db, account, position, candidate, quantity, at, "TAKE_PROFIT", "獲利達 12%，先減碼 25%")
            position.take_profit_stage = 1; events += 1; continue
        if float(candidate.chase_risk_score) >= 85 and return_pct > 0 and position.remaining_quantity > 1:
            _sell(db, account, position, candidate, max(1, position.remaining_quantity // 4), at, "REDUCE", "CHASE Risk 快速升高，降低部位")
            events += 1; continue
        if previous_chase < 72 <= float(candidate.chase_risk_score):
            record_rocket_event(db, RocketEvent(
                key=f"position:{position.id}:WARNING:chase72", event_type="WARNING", timestamp=at,
                stock_code=position.stock_code, stock_name=position.stock_name,
                title="飆股雷達｜追高風險警告",
                message=f"CHASE Risk 由 {previous_chase:.0f} 升至 {float(candidate.chase_risk_score):.0f}，暫停追價",
                reason="過熱條件增加，現有部位改採移動停利", price=price,
                rocket_score=float(candidate.rocket_score), chase_risk=float(candidate.chase_risk_score),
                strategy_type=position.strategy_type, previous_status="HOLD", new_status="WARNING",
            ))
        if position.add_stage < 3 and float(candidate.rocket_score) >= 88 and float(candidate.chase_risk_score) < 70:
            stage_trigger = average * (1.02 if position.add_stage == 1 else 1.04)
            pullback_ready = candidate.pattern_type == "強勢回踩" and price >= float(candidate.ma5 or price)
            if price >= stage_trigger or pullback_ready:
                equity = portfolio_equity(db, account)
                budget = float(position.target_allocation) * .30
                existing_risk = position.remaining_quantity * max(0, average - float(position.stop_loss_price))
                available_risk = max(0, equity * MAX_TRADE_RISK_PCT - existing_risk)
                risk_per_share = max(.01, price - float(position.stop_loss_price))
                quantity = min(int(budget / price), int(available_risk / risk_per_share), int(float(account.cash) / price))
                if _buy(db, account, candidate, quantity, float(position.target_allocation), at,
                        position=position, event_type="ADD", reason="突破站穩／回踩成功，量價與 Rocket Score 維持強勢"):
                    events += 1; continue
        position.latest_action = "🔵 持有／移動停利"
        position.updated_at = at
        if "買進" in previous_action or "加碼" in previous_action:
            record_rocket_event(db, RocketEvent(
                key=f"position:{position.id}:HOLD:stage:{position.add_stage}", event_type="HOLD", timestamp=at,
                stock_code=position.stock_code, stock_name=position.stock_name,
                title="飆股雷達｜持有追蹤", message="部位轉為持有，依 MA10／ATR 移動停利追蹤",
                reason="突破結構仍有效，尚未觸發加碼、減碼或出場", price=price,
                rocket_score=float(candidate.rocket_score), chase_risk=float(candidate.chase_risk_score),
                strategy_type=position.strategy_type, previous_status="BUY" if position.add_stage == 1 else "ADD",
                new_status="HOLD",
            ))
    return events


def record_daily_portfolio(db: Session, account: RocketAccount, trade_date, at: datetime) -> RocketDailyPortfolio:
    positions = list(db.scalars(select(RocketPosition).where(RocketPosition.status == "open")).all())
    market_value = sum(float(item.current_price) * item.remaining_quantity for item in positions)
    unrealized = sum(float(item.unrealized_pnl) for item in positions)
    equity = float(account.cash) + market_value
    previous = db.scalar(select(RocketDailyPortfolio).where(
        RocketDailyPortfolio.trade_date < trade_date,
    ).order_by(RocketDailyPortfolio.trade_date.desc()).limit(1))
    daily_pnl = equity - (float(previous.total_equity) if previous else INITIAL_CAPITAL)
    historical_peak = db.scalar(select(func.max(RocketDailyPortfolio.total_equity))) or INITIAL_CAPITAL
    peak = max(float(historical_peak), equity, INITIAL_CAPITAL)
    drawdown = (equity / peak - 1) * 100 if peak else 0
    item = db.scalar(select(RocketDailyPortfolio).where(RocketDailyPortfolio.trade_date == trade_date))
    values = {
        "cash": _decimal(float(account.cash)), "market_value": _decimal(market_value),
        "total_equity": _decimal(equity), "daily_pnl": _decimal(daily_pnl),
        "cumulative_pnl": _decimal(equity - INITIAL_CAPITAL),
        "realized_pnl": account.realized_pnl, "unrealized_pnl": _decimal(unrealized),
        "drawdown_pct": _decimal(drawdown), "recorded_at": at,
    }
    if item is None:
        item = RocketDailyPortfolio(trade_date=trade_date, **values)
        db.add(item)
    else:
        for key, value in values.items(): setattr(item, key, value)
    return item
