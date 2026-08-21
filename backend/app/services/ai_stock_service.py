from __future__ import annotations

import json
from datetime import UTC, datetime, time
from decimal import ROUND_DOWN, Decimal
from typing import TYPE_CHECKING, Any, Iterable
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    AIStockAddOn,
    AIStockAlert,
    AIStockMonitor,
    AIStockPartialExit,
    AIStockPosition,
    PortfolioSettings,
)
from ..schemas import AIRecommendationSyncItem, PortfolioSettingsUpdate

if TYPE_CHECKING:
    from .official_market_data import OfficialStockQuote

from .theme_stock_universe import is_ai_related_theme_symbol


TAIPEI = ZoneInfo("Asia/Taipei")
MONEY = Decimal("0.01")
PRICE = Decimal("0.0001")
PERCENT = Decimal("0.01")
ACTIVE_MONITOR_STATUSES = {
    "monitoring", "waiting_breakout", "waiting_pullback", "near_entry",
    "buy_confirmed", "chase_blocked", "signal_weakened", "data_abnormal",
}
ACTIVE_POSITION_STATUSES = {
    "holding", "overnight", "continue_holding", "raise_stop", "add_on_waiting",
    "add_on_confirmed", "reduce", "sell_all", "stop_loss", "awaiting_exit_confirmation",
    "data_abnormal",
}


def decimal_value(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def money(value: Decimal) -> Decimal:
    return value.quantize(MONEY)


def price(value: Decimal) -> Decimal:
    return value.quantize(PRICE)


def percent(value: Decimal) -> Decimal:
    return value.quantize(PERCENT)


def decimal_json(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def quote_is_fresh(quote_timestamp: datetime, now: datetime | None = None) -> bool:
    current = (now or datetime.now(UTC)).astimezone(TAIPEI)
    quote = quote_timestamp if quote_timestamp.tzinfo else quote_timestamp.replace(tzinfo=UTC)
    quote = quote.astimezone(TAIPEI)
    return (
        current.weekday() < 5
        and time(9, 0) <= current.time() <= time(13, 30)
        and quote.date() == current.date()
        and Decimal("0") <= Decimal(str((current - quote).total_seconds())) <= Decimal("120")
    )


def get_portfolio_settings(db: Session, user_id: str) -> PortfolioSettings:
    settings = db.scalar(select(PortfolioSettings).where(PortfolioSettings.user_id == user_id))
    if settings is None:
        settings = PortfolioSettings(user_id=user_id, updated_at=datetime.now(UTC))
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def update_portfolio_settings(
    db: Session,
    user_id: str,
    body: PortfolioSettingsUpdate,
) -> PortfolioSettings:
    settings = get_portfolio_settings(db, user_id)
    if body.initial_entry_ratio + body.first_add_on_ratio + body.second_add_on_ratio != Decimal("100"):
        raise ValueError("初始建倉與兩次加碼比例合計必須為 100%")
    for key, value in body.model_dump().items():
        setattr(settings, key, value)
    settings.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(settings)
    return settings


def settings_payload(settings: PortfolioSettings) -> dict[str, Any]:
    return {
        "id": settings.id,
        "totalCapital": decimal_json(settings.total_capital),
        "minimumCashPercentage": decimal_json(settings.minimum_cash_percentage),
        "maxTotalExposure": decimal_json(settings.max_total_exposure),
        "maxPositionPercentage": decimal_json(settings.max_position_percentage),
        "maxIndustryPercentage": decimal_json(settings.max_industry_percentage),
        "maxRiskPerTrade": decimal_json(settings.max_risk_per_trade),
        "maxPortfolioRisk": decimal_json(settings.max_portfolio_risk),
        "maximumAddOnCount": settings.maximum_add_on_count,
        "initialEntryRatio": decimal_json(settings.initial_entry_ratio),
        "firstAddOnRatio": decimal_json(settings.first_add_on_ratio),
        "secondAddOnRatio": decimal_json(settings.second_add_on_ratio),
        "allowAddOn": settings.allow_add_on,
        "prohibitAveragingDown": settings.prohibit_averaging_down,
        "dailySummaryEnabled": settings.daily_summary_enabled,
        "updatedAt": settings.updated_at.isoformat(),
    }


def active_positions(db: Session, user_id: str) -> list[AIStockPosition]:
    return list(db.scalars(
        select(AIStockPosition).where(
            AIStockPosition.user_id == user_id,
            AIStockPosition.position_status.in_(ACTIVE_POSITION_STATUSES),
        ).order_by(AIStockPosition.created_at),
    ).all())


def allocation_summary(db: Session, user_id: str) -> dict[str, Any]:
    settings = get_portfolio_settings(db, user_id)
    positions = active_positions(db, user_id)
    invested = sum((decimal_value(item.invested_amount) for item in positions), Decimal("0"))
    risk = sum((decimal_value(item.estimated_risk_amount) for item in positions), Decimal("0"))
    capital = decimal_value(settings.total_capital)
    industry_amounts: dict[str, Decimal] = {}
    for item in positions:
        industry_amounts[item.industry] = industry_amounts.get(item.industry, Decimal("0")) + decimal_value(item.invested_amount)
    exposure = invested / capital * 100 if capital else Decimal("0")
    return {
        "totalCapital": decimal_json(capital),
        "investedAmount": decimal_json(money(invested)),
        "availableCapital": decimal_json(money(max(Decimal("0"), capital - invested))),
        "actualExposurePercentage": decimal_json(percent(exposure)),
        "cashPercentage": decimal_json(percent(max(Decimal("0"), Decimal("100") - exposure))),
        "portfolioRiskAmount": decimal_json(money(risk)),
        "portfolioRiskPercentage": decimal_json(percent(risk / capital * 100 if capital else Decimal("0"))),
        "industryExposure": [
            {
                "industry": industry,
                "amount": decimal_json(money(amount)),
                "percentage": decimal_json(percent(amount / capital * 100 if capital else Decimal("0"))),
            }
            for industry, amount in sorted(industry_amounts.items(), key=lambda item: item[1], reverse=True)
        ],
        "redisMode": "由快取服務回報",
    }


def calculate_position_allocation(
    settings: PortfolioSettings,
    *,
    entry_price: Decimal,
    stop_loss: Decimal,
    score: Decimal,
    strategy_fit: Decimal,
    health_score: Decimal,
    current_invested: Decimal = Decimal("0"),
    industry_invested: Decimal = Decimal("0"),
) -> dict[str, Decimal | int | str]:
    capital = decimal_value(settings.total_capital)
    per_share_risk = entry_price - stop_loss
    if per_share_risk <= 0:
        return {"quantity": 0, "reason": "停損價格必須低於預計進場價格"}
    quality = max(Decimal("0"), min(Decimal("1"), (score + strategy_fit + health_score) / Decimal("300")))
    desired_percentage = min(
        decimal_value(settings.max_position_percentage),
        Decimal("8") + quality * Decimal("12"),
    )
    portfolio_room = max(
        Decimal("0"),
        capital * decimal_value(settings.max_total_exposure) / 100 - current_invested,
    )
    industry_room = max(
        Decimal("0"),
        capital * decimal_value(settings.max_industry_percentage) / 100 - industry_invested,
    )
    approved_amount = min(
        capital * desired_percentage / 100,
        capital * decimal_value(settings.max_position_percentage) / 100,
        portfolio_room,
        industry_room,
    )
    risk_amount = capital * decimal_value(settings.max_risk_per_trade) / 100
    risk_shares = (risk_amount / per_share_risk).to_integral_value(rounding=ROUND_DOWN)
    capital_shares = (approved_amount / entry_price).to_integral_value(rounding=ROUND_DOWN)
    final_quantity = int(max(Decimal("0"), min(risk_shares, capital_shares)))
    final_amount = entry_price * final_quantity
    final_percentage = final_amount / capital * 100 if capital else Decimal("0")
    initial_percentage = final_percentage * decimal_value(settings.initial_entry_ratio) / 100
    first_percentage = final_percentage * decimal_value(settings.first_add_on_ratio) / 100
    second_percentage = final_percentage * decimal_value(settings.second_add_on_ratio) / 100
    initial_amount = capital * initial_percentage / 100
    initial_quantity = int((initial_amount / entry_price).to_integral_value(rounding=ROUND_DOWN))
    estimated_risk = per_share_risk * initial_quantity
    return {
        "quantity": final_quantity,
        "target_percentage": percent(final_percentage),
        "initial_percentage": percent(initial_percentage),
        "first_percentage": percent(first_percentage),
        "second_percentage": percent(second_percentage),
        "initial_amount": money(entry_price * initial_quantity),
        "initial_quantity": initial_quantity,
        "estimated_risk": money(estimated_risk),
        "reason": "依單筆風險、單檔上限、產業曝險與可用資金取最小值",
    }


def monitor_payload(item: AIStockMonitor) -> dict[str, Any]:
    return {
        "id": item.id, "symbol": item.symbol, "stockName": item.stock_name,
        "market": item.market, "industry": item.industry,
        "strategyName": item.strategy_name,
        "secondaryStrategies": json.loads(item.secondary_strategies_json),
        "signalId": item.signal_id, "monitorStatus": item.monitor_status,
        "totalScore": decimal_json(item.total_score),
        "strategyFit": decimal_json(item.strategy_fit),
        "marketFit": decimal_json(item.market_fit),
        "healthScore": decimal_json(item.health_score),
        "currentPrice": decimal_json(item.current_price),
        "entryMin": decimal_json(item.entry_min), "entryMax": decimal_json(item.entry_max),
        "stopLoss": decimal_json(item.stop_loss), "target1": decimal_json(item.target_1),
        "target2": decimal_json(item.target_2), "riskRewardRatio": decimal_json(item.risk_reward_ratio),
        "targetAllocationPercentage": decimal_json(item.target_allocation_percentage),
        "initialAllocationPercentage": decimal_json(item.initial_allocation_percentage),
        "firstAddOnPercentage": decimal_json(item.first_add_on_percentage),
        "secondAddOnPercentage": decimal_json(item.second_add_on_percentage),
        "suggestedInitialAmount": decimal_json(item.suggested_initial_amount),
        "suggestedInitialQuantity": item.suggested_initial_quantity,
        "estimatedRiskAmount": decimal_json(item.estimated_risk_amount),
        "reasons": json.loads(item.reasons_json), "warnings": json.loads(item.warnings_json),
        "quoteSource": item.quote_source, "quoteTimestamp": item.quote_timestamp.isoformat(),
        "createdAt": item.created_at.isoformat(), "updatedAt": item.updated_at.isoformat(),
        "expiredAt": item.expired_at.isoformat(),
    }


def position_payload(
    item: AIStockPosition,
    monitor: AIStockMonitor | None = None,
) -> dict[str, Any]:
    return {
        "id": item.id, "monitorId": item.monitor_id, "symbol": item.symbol,
        "stockName": item.stock_name, "industry": item.industry, "direction": item.direction,
        "strategyName": item.strategy_name, "entryPrice": decimal_json(item.entry_price),
        "averageCost": decimal_json(item.average_cost), "originalQuantity": item.original_quantity,
        "remainingQuantity": item.remaining_quantity, "entryTime": item.entry_time.isoformat(),
        "stopLoss": decimal_json(item.stop_loss), "target1": decimal_json(item.target_1),
        "target2": decimal_json(item.target_2), "trailingStop": decimal_json(item.trailing_stop),
        "currentPrice": decimal_json(item.current_price), "highestPrice": decimal_json(item.highest_price),
        "lowestPrice": decimal_json(item.lowest_price),
        "maxUnrealizedProfit": decimal_json(item.max_unrealized_profit),
        "maxUnrealizedLoss": decimal_json(item.max_unrealized_loss),
        "realizedProfit": decimal_json(item.realized_profit),
        "unrealizedProfit": decimal_json(item.unrealized_profit),
        "returnPercentage": decimal_json(item.return_percentage),
        "healthScore": decimal_json(item.health_score), "latestAction": item.latest_action,
        "positionStatus": item.position_status, "overnightStatus": item.overnight_status,
        "targetAllocationPercentage": decimal_json(item.target_allocation_percentage),
        "initialAllocationPercentage": decimal_json(item.initial_allocation_percentage),
        "currentAllocationPercentage": decimal_json(item.current_allocation_percentage),
        "investedAmount": decimal_json(item.invested_amount),
        "availableAddOnAmount": decimal_json(item.available_add_on_amount),
        "addOnCount": item.add_on_count, "estimatedRiskAmount": decimal_json(item.estimated_risk_amount),
        "industryExposurePercentage": decimal_json(item.industry_exposure_percentage),
        "lineExitNotifications": item.line_exit_notifications, "addOnEnabled": item.add_on_enabled,
        "closedAt": item.closed_at.isoformat() if item.closed_at else None,
        "exitPrice": decimal_json(item.exit_price), "exitReason": item.exit_reason,
        "createdAt": item.created_at.isoformat(), "updatedAt": item.updated_at.isoformat(),
        "quoteSource": monitor.quote_source if monitor else "未提供",
        "quoteTimestamp": monitor.quote_timestamp.isoformat() if monitor else None,
    }


def monitor_entry_failures(
    monitor: AIStockMonitor,
    quote: "OfficialStockQuote",
    now: datetime | None = None,
) -> list[str]:
    current = now or datetime.now(UTC)
    failures: list[str] = []
    quote_time = datetime.fromisoformat(quote.quote_timestamp)
    current_price = decimal_value(quote.price)
    if not quote.is_realtime or not quote_is_fresh(quote_time, current):
        failures.append("行情時間過期或不是盤中即時報價")
    if decimal_value(monitor.total_score) < 75:
        failures.append("條件符合分數低於 75")
    if decimal_value(monitor.strategy_fit) < 75:
        failures.append("策略適配度低於 75%")
    if decimal_value(monitor.market_fit) < 55:
        failures.append("大盤適配度不足")
    if decimal_value(monitor.health_score) < 70:
        failures.append("健康度低於 70")
    if decimal_value(monitor.risk_reward_ratio) < Decimal("1.5"):
        failures.append("風險報酬比低於 1：1.5")
    if quote.volume < 500_000:
        failures.append("成交量不足")
    if current_price * quote.volume < Decimal("50000000"):
        failures.append("成交金額不足")
    if quote.best_bid is None or quote.best_ask is None:
        failures.append("缺少即時買賣價差")
    else:
        spread = (decimal_value(quote.best_ask) - decimal_value(quote.best_bid)) / current_price * 100
        if spread < 0 or spread > Decimal(".5"):
            failures.append("買賣價差過大")
    if current_price > decimal_value(monitor.entry_max):
        failures.append("現價高於建議進場區上緣，禁止追價")
    elif current_price < decimal_value(monitor.entry_min):
        failures.append("現價尚未進入建議進場區")
    if decimal_value(monitor.stop_loss) >= current_price:
        failures.append("停損價格不合理")
    if decimal_value(monitor.target_1) <= current_price:
        failures.append("第一目標價格不合理")
    if monitor.suggested_initial_quantity <= 0:
        failures.append("資金配置或單筆風險不合格")
    if monitor.expired_at <= current:
        failures.append("訊號已失效")
    return failures


def add_on_payload(item: AIStockAddOn) -> dict[str, Any]:
    return {
        "id": item.id, "positionId": item.position_id, "addOnNumber": item.add_on_number,
        "suggestedPriceMin": decimal_json(item.suggested_price_min),
        "suggestedPriceMax": decimal_json(item.suggested_price_max),
        "suggestedPercentage": decimal_json(item.suggested_percentage),
        "suggestedAmount": decimal_json(item.suggested_amount),
        "suggestedQuantity": item.suggested_quantity,
        "actualPrice": decimal_json(item.actual_price), "actualQuantity": item.actual_quantity,
        "previousAverageCost": decimal_json(item.previous_average_cost),
        "newAverageCost": decimal_json(item.new_average_cost),
        "previousStopLoss": decimal_json(item.previous_stop_loss),
        "newStopLoss": decimal_json(item.new_stop_loss),
        "status": item.status, "signalId": item.signal_id,
        "suggestedAt": item.suggested_at.isoformat(),
        "confirmedAt": item.confirmed_at.isoformat() if item.confirmed_at else None,
    }


def sync_recommendations(
    db: Session,
    user_id: str,
    items: list[AIRecommendationSyncItem],
    now: datetime | None = None,
) -> list[AIStockMonitor]:
    current = now or datetime.now(UTC)
    settings = get_portfolio_settings(db, user_id)
    positions = active_positions(db, user_id)
    current_invested = sum((decimal_value(item.invested_amount) for item in positions), Decimal("0"))
    active_ids: set[str] = set()
    for candidate in items[:5]:
        if (
            not is_ai_related_theme_symbol(candidate.symbol)
            or candidate.total_score < 75
            or candidate.strategy_fit < 75
            or candidate.market_fit < 55
            or candidate.risk_reward_ratio < Decimal("1.5")
            or candidate.quote_source not in {"TWSE MIS", "TWSE OpenAPI", "TPEx OpenAPI"}
            or not quote_is_fresh(candidate.quote_timestamp, current)
        ):
            continue
        active_ids.add(candidate.signal_id)
        stored = db.scalar(select(AIStockMonitor).where(
            AIStockMonitor.user_id == user_id,
            AIStockMonitor.signal_id == candidate.signal_id,
        ))
        industry_invested = sum(
            (decimal_value(item.invested_amount) for item in positions if item.industry == candidate.industry),
            Decimal("0"),
        )
        allocation = calculate_position_allocation(
            settings,
            entry_price=candidate.current_price,
            stop_loss=candidate.stop_loss,
            score=candidate.total_score,
            strategy_fit=candidate.strategy_fit,
            health_score=candidate.health_score,
            current_invested=current_invested,
            industry_invested=industry_invested,
        )
        values = {
            "symbol": candidate.symbol, "stock_name": candidate.stock_name,
            "market": candidate.market, "industry": candidate.industry,
            "strategy_name": candidate.strategy_name,
            "secondary_strategies_json": json.dumps(candidate.secondary_strategies, ensure_ascii=False),
            "total_score": candidate.total_score, "strategy_fit": candidate.strategy_fit,
            "market_fit": candidate.market_fit, "health_score": candidate.health_score,
            "current_price": candidate.current_price, "entry_min": candidate.entry_min,
            "entry_max": candidate.entry_max, "stop_loss": candidate.stop_loss,
            "target_1": candidate.target_1, "target_2": candidate.target_2,
            "risk_reward_ratio": candidate.risk_reward_ratio,
            "target_allocation_percentage": allocation.get("target_percentage", Decimal("0")),
            "initial_allocation_percentage": allocation.get("initial_percentage", Decimal("0")),
            "first_add_on_percentage": allocation.get("first_percentage", Decimal("0")),
            "second_add_on_percentage": allocation.get("second_percentage", Decimal("0")),
            "suggested_initial_amount": allocation.get("initial_amount", Decimal("0")),
            "suggested_initial_quantity": allocation.get("initial_quantity", 0),
            "estimated_risk_amount": allocation.get("estimated_risk", Decimal("0")),
            "reasons_json": json.dumps(candidate.reasons, ensure_ascii=False),
            "warnings_json": json.dumps(candidate.warnings, ensure_ascii=False),
            "quote_source": candidate.quote_source, "quote_timestamp": candidate.quote_timestamp,
            "updated_at": current, "expired_at": candidate.expired_at,
        }
        if stored is None:
            stored = AIStockMonitor(
                user_id=user_id, signal_id=candidate.signal_id,
                monitor_status="monitoring", created_at=current, **values,
            )
            db.add(stored)
        elif stored.monitor_status not in {"position", "ended", "ignored"}:
            for key, value in values.items():
                setattr(stored, key, value)
    existing = db.scalars(select(AIStockMonitor).where(
        AIStockMonitor.user_id == user_id,
        AIStockMonitor.monitor_status.in_(ACTIVE_MONITOR_STATUSES),
    )).all()
    for monitor in existing:
        if monitor.signal_id not in active_ids:
            monitor.monitor_status = "expired"
            monitor.updated_at = current
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
    return list(db.scalars(select(AIStockMonitor).where(
        AIStockMonitor.user_id == user_id,
        AIStockMonitor.monitor_status.in_(ACTIVE_MONITOR_STATUSES),
    ).order_by(AIStockMonitor.total_score.desc()).limit(5)).all())


def confirm_entry(
    db: Session,
    user_id: str,
    monitor_id: int,
    *,
    entry_price: Decimal,
    quantity: int,
    entry_time: datetime,
    custom_stop_loss: Decimal | None,
    line_exit_notifications: bool,
    add_on_enabled: bool,
) -> AIStockPosition:
    monitor = db.scalar(select(AIStockMonitor).where(
        AIStockMonitor.id == monitor_id, AIStockMonitor.user_id == user_id,
    ))
    if monitor is None:
        raise LookupError("AI監控項目不存在")
    if monitor.monitor_status != "buy_confirmed":
        raise ValueError("尚未形成買進確認，不可建立持倉")
    existing = db.scalar(select(AIStockPosition).where(
        AIStockPosition.monitor_id == monitor.id,
        AIStockPosition.position_status.in_(ACTIVE_POSITION_STATUSES),
    ))
    if existing:
        return existing
    settings = get_portfolio_settings(db, user_id)
    capital = decimal_value(settings.total_capital)
    invested = money(entry_price * quantity)
    stop = custom_stop_loss or decimal_value(monitor.stop_loss)
    if stop >= entry_price:
        raise ValueError("停損價格必須低於實際買進價格")
    estimated_risk = money(max(Decimal("0"), entry_price - stop) * quantity)
    positions = active_positions(db, user_id)
    total_invested = sum((decimal_value(value.invested_amount) for value in positions), Decimal("0"))
    industry_invested = sum(
        (decimal_value(value.invested_amount) for value in positions if value.industry == monitor.industry),
        Decimal("0"),
    )
    total_risk = sum((decimal_value(value.estimated_risk_amount) for value in positions), Decimal("0"))
    risk_flags = position_risk_failures(
        settings,
        invested_amount=invested,
        estimated_risk=estimated_risk,
        total_invested_after=total_invested + invested,
        industry_invested_after=industry_invested + invested,
        total_risk_after=total_risk + estimated_risk,
    )
    item = AIStockPosition(
        user_id=user_id, monitor_id=monitor.id, symbol=monitor.symbol,
        stock_name=monitor.stock_name, industry=monitor.industry, direction="long",
        strategy_name=monitor.strategy_name, entry_price=entry_price, average_cost=entry_price,
        original_quantity=quantity, remaining_quantity=quantity, entry_time=entry_time,
        stop_loss=stop, target_1=monitor.target_1, target_2=monitor.target_2,
        current_price=entry_price, highest_price=entry_price, lowest_price=entry_price,
        health_score=monitor.health_score,
        latest_action="資金風險超標" if risk_flags else "持有中",
        position_status="holding",
        target_allocation_percentage=monitor.target_allocation_percentage,
        initial_allocation_percentage=monitor.initial_allocation_percentage,
        current_allocation_percentage=percent(invested / capital * 100 if capital else Decimal("0")),
        invested_amount=invested,
        available_add_on_amount=money(max(Decimal("0"), capital * monitor.target_allocation_percentage / 100 - invested)),
        estimated_risk_amount=estimated_risk, line_exit_notifications=line_exit_notifications,
        add_on_enabled=add_on_enabled and settings.allow_add_on and not risk_flags,
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
    )
    monitor.monitor_status = "position"
    monitor.updated_at = datetime.now(UTC)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def position_risk_failures(
    settings: PortfolioSettings,
    *,
    invested_amount: Decimal,
    estimated_risk: Decimal,
    total_invested_after: Decimal,
    industry_invested_after: Decimal,
    total_risk_after: Decimal,
) -> list[str]:
    capital = decimal_value(settings.total_capital)
    if capital <= 0:
        return ["可投入總資金設定錯誤"]
    failures: list[str] = []
    if invested_amount > capital * decimal_value(settings.max_position_percentage) / 100:
        failures.append("超過單檔最大資金占比")
    if total_invested_after > capital * decimal_value(settings.max_total_exposure) / 100:
        failures.append("超過整體持倉上限")
    if industry_invested_after > capital * decimal_value(settings.max_industry_percentage) / 100:
        failures.append("超過單一產業最大占比")
    if estimated_risk > capital * decimal_value(settings.max_risk_per_trade) / 100:
        failures.append("超過單筆最大可承受損失")
    if total_risk_after > capital * decimal_value(settings.max_portfolio_risk) / 100:
        failures.append("超過整體持倉最大風險")
    return failures


def evaluate_position_action(
    position: AIStockPosition,
    current_price: Decimal,
    *,
    quote_valid: bool,
) -> tuple[str, list[str]]:
    if not quote_valid:
        return "資料異常", ["行情時間過期或來源異常，暫停產生買賣指令"]
    if current_price <= decimal_value(position.stop_loss):
        return "立即停損", ["現價跌破使用者確認的硬性停損"]
    if position.trailing_stop is not None and current_price <= decimal_value(position.trailing_stop):
        return "建議全部賣出", ["觸發移動停利"]
    if current_price >= decimal_value(position.target_2):
        return "建議全部賣出", ["到達第二目標價"]
    if current_price >= decimal_value(position.target_1):
        return "建議減碼 50%", ["到達第一目標價"]
    return "續抱", ["價格仍位於停損與目標區間內"]


def update_position_quote(
    position: AIStockPosition,
    current_price: Decimal,
    *,
    quote_valid: bool,
    now: datetime | None = None,
) -> tuple[str, list[str]]:
    current = now or datetime.now(UTC)
    position.current_price = current_price
    position.highest_price = max(decimal_value(position.highest_price), current_price)
    position.lowest_price = min(decimal_value(position.lowest_price), current_price)
    pnl = money((current_price - decimal_value(position.average_cost)) * position.remaining_quantity)
    position.unrealized_profit = pnl
    position.return_percentage = percent(
        (current_price - decimal_value(position.average_cost)) / decimal_value(position.average_cost) * 100
    )
    position.max_unrealized_profit = max(decimal_value(position.max_unrealized_profit), pnl)
    position.max_unrealized_loss = min(decimal_value(position.max_unrealized_loss), pnl)
    if current_price >= decimal_value(position.average_cost) * Decimal("1.05"):
        raised = price(current_price * Decimal(".95"))
        position.trailing_stop = max(decimal_value(position.trailing_stop or 0), raised)
    action, reasons = evaluate_position_action(position, current_price, quote_valid=quote_valid)
    position.latest_action = action
    if action == "資料異常":
        position.position_status = "data_abnormal"
    elif action == "立即停損":
        position.position_status = "stop_loss"
    elif action == "建議全部賣出":
        position.position_status = "sell_all"
    elif action.startswith("建議減碼"):
        position.position_status = "reduce"
    elif position.overnight_status:
        position.position_status = "overnight"
    else:
        position.position_status = "continue_holding"
    position.updated_at = current
    return action, reasons


def create_alert(
    db: Session,
    *,
    user_id: str,
    monitor_id: int | None,
    position_id: int | None,
    signal_id: str,
    alert_type: str,
    alert_level: str,
    action: str,
    current_price: Decimal,
    reasons: Iterable[str],
) -> AIStockAlert | None:
    alert = AIStockAlert(
        user_id=user_id, monitor_id=monitor_id, position_id=position_id,
        signal_id=signal_id, alert_type=alert_type, alert_level=alert_level,
        action=action, price=current_price, reason="；".join(reasons),
        line_push_status="pending", created_at=datetime.now(UTC),
    )
    db.add(alert)
    try:
        db.commit()
        db.refresh(alert)
        return alert
    except IntegrityError:
        db.rollback()
        return None


def suggest_add_on(
    db: Session,
    position: AIStockPosition,
    settings: PortfolioSettings,
) -> AIStockAddOn | None:
    if (
        not settings.allow_add_on
        or not position.add_on_enabled
        or position.add_on_count >= settings.maximum_add_on_count
        or decimal_value(position.current_price) <= decimal_value(position.entry_price)
        or decimal_value(position.health_score) < (Decimal("75") if position.add_on_count == 0 else Decimal("80"))
        or position.latest_action != "續抱"
        or decimal_value(position.available_add_on_amount) <= 0
    ):
        return None
    stage = position.add_on_count + 1
    threshold = decimal_value(position.entry_price) * (Decimal("1.03") if stage == 1 else Decimal("1.06"))
    if decimal_value(position.current_price) < threshold:
        return None
    existing = db.scalar(select(AIStockAddOn).where(
        AIStockAddOn.position_id == position.id, AIStockAddOn.add_on_number == stage,
    ))
    if existing:
        return existing if existing.status == "suggested" else None
    ratio = settings.first_add_on_ratio if stage == 1 else settings.second_add_on_ratio
    suggested_percentage = decimal_value(position.target_allocation_percentage) * decimal_value(ratio) / 100
    capital = decimal_value(settings.total_capital)
    positions = active_positions(db, position.user_id)
    total_invested = sum((decimal_value(item.invested_amount) for item in positions), Decimal("0"))
    industry_invested = sum(
        (decimal_value(item.invested_amount) for item in positions if item.industry == position.industry),
        Decimal("0"),
    )
    position_room = max(
        Decimal("0"),
        capital * decimal_value(settings.max_position_percentage) / 100
        - decimal_value(position.invested_amount),
    )
    portfolio_room = max(
        Decimal("0"),
        capital * decimal_value(settings.max_total_exposure) / 100 - total_invested,
    )
    industry_room = max(
        Decimal("0"),
        capital * decimal_value(settings.max_industry_percentage) / 100 - industry_invested,
    )
    suggested_amount = money(min(
        capital * suggested_percentage / 100,
        decimal_value(position.available_add_on_amount),
        position_room,
        portfolio_room,
        industry_room,
    ))
    quantity = int((suggested_amount / decimal_value(position.current_price)).to_integral_value(rounding=ROUND_DOWN))
    if quantity <= 0:
        return None
    new_stop = max(decimal_value(position.stop_loss), decimal_value(position.average_cost))
    new_quantity = position.remaining_quantity + quantity
    new_average = (
        decimal_value(position.average_cost) * position.remaining_quantity
        + decimal_value(position.current_price) * quantity
    ) / new_quantity
    new_risk = money(max(Decimal("0"), new_average - new_stop) * new_quantity)
    other_risk = sum(
        (decimal_value(item.estimated_risk_amount) for item in positions if item.id != position.id),
        Decimal("0"),
    )
    if position_risk_failures(
        settings,
        invested_amount=decimal_value(position.invested_amount) + suggested_amount,
        estimated_risk=new_risk,
        total_invested_after=total_invested + suggested_amount,
        industry_invested_after=industry_invested + suggested_amount,
        total_risk_after=other_risk + new_risk,
    ):
        return None
    item = AIStockAddOn(
        position_id=position.id, add_on_number=stage,
        suggested_price_min=price(decimal_value(position.current_price) * Decimal(".995")),
        suggested_price_max=price(decimal_value(position.current_price) * Decimal("1.005")),
        suggested_percentage=percent(suggested_percentage), suggested_amount=suggested_amount,
        suggested_quantity=quantity, previous_average_cost=position.average_cost,
        previous_stop_loss=position.stop_loss, new_stop_loss=price(new_stop),
        status="suggested", signal_id=f"{position.symbol}-addon-{stage}-{datetime.now(TAIPEI).date().isoformat()}",
        suggested_at=datetime.now(UTC), created_at=datetime.now(UTC),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    position.latest_action = f"第{stage}次加碼確認"
    position.position_status = "add_on_confirmed"
    db.commit()
    return item


def confirm_add_on(
    db: Session,
    user_id: str,
    position_id: int,
    *,
    actual_price: Decimal,
    actual_quantity: int,
    add_on_time: datetime,
    fee: Decimal,
    accept_new_stop_loss: bool,
) -> AIStockPosition:
    position = db.scalar(select(AIStockPosition).where(
        AIStockPosition.id == position_id, AIStockPosition.user_id == user_id,
    ))
    if position is None:
        raise LookupError("持倉不存在")
    add_on = db.scalar(select(AIStockAddOn).where(
        AIStockAddOn.position_id == position.id,
        AIStockAddOn.status == "suggested",
    ).order_by(AIStockAddOn.add_on_number).limit(1))
    if add_on is None:
        raise ValueError("目前沒有待確認的加碼建議")
    if actual_quantity > add_on.suggested_quantity:
        raise ValueError("實際加碼股數不可超過本次風控核准數量")
    settings = get_portfolio_settings(db, user_id)
    if settings.prohibit_averaging_down and actual_price < decimal_value(position.entry_price):
        raise ValueError("已啟用禁止虧損攤平，實際加碼價格不得低於初始進場價")
    previous_quantity = position.remaining_quantity
    new_quantity = previous_quantity + actual_quantity
    new_cost = price(
        (
            decimal_value(position.average_cost) * previous_quantity
            + actual_price * actual_quantity
            + fee
        )
        / new_quantity
    )
    new_invested = money(
        decimal_value(position.invested_amount) + actual_price * actual_quantity + fee
    )
    proposed_stop = (
        max(decimal_value(position.stop_loss), decimal_value(add_on.new_stop_loss))
        if accept_new_stop_loss else decimal_value(position.stop_loss)
    )
    new_risk = money(max(Decimal("0"), new_cost - proposed_stop) * new_quantity)
    positions = active_positions(db, user_id)
    other_invested = sum(
        (decimal_value(item.invested_amount) for item in positions if item.id != position.id),
        Decimal("0"),
    )
    industry_other = sum(
        (
            decimal_value(item.invested_amount)
            for item in positions
            if item.id != position.id and item.industry == position.industry
        ),
        Decimal("0"),
    )
    other_risk = sum(
        (decimal_value(item.estimated_risk_amount) for item in positions if item.id != position.id),
        Decimal("0"),
    )
    risk_flags = position_risk_failures(
        settings,
        invested_amount=new_invested,
        estimated_risk=new_risk,
        total_invested_after=other_invested + new_invested,
        industry_invested_after=industry_other + new_invested,
        total_risk_after=other_risk + new_risk,
    )
    if risk_flags:
        raise ValueError(f"加碼後風險超標：{'、'.join(risk_flags)}")
    add_on.actual_price = actual_price
    add_on.actual_quantity = actual_quantity
    add_on.new_average_cost = new_cost
    add_on.confirmed_at = add_on_time
    add_on.status = "confirmed"
    position.remaining_quantity = new_quantity
    position.average_cost = new_cost
    position.invested_amount = new_invested
    position.add_on_count += 1
    if accept_new_stop_loss:
        position.stop_loss = proposed_stop
    capital = decimal_value(settings.total_capital)
    position.current_allocation_percentage = percent(new_invested / capital * 100)
    position.available_add_on_amount = money(max(
        Decimal("0"),
        capital * decimal_value(position.target_allocation_percentage) / 100 - new_invested,
    ))
    position.estimated_risk_amount = new_risk
    position.industry_exposure_percentage = percent((industry_other + new_invested) / capital * 100)
    position.latest_action = "持有中"
    position.position_status = "holding"
    position.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(position)
    return position


def partial_exit(
    db: Session,
    user_id: str,
    position_id: int,
    *,
    quantity: int,
    exit_price: Decimal,
    exit_time: datetime,
    fee: Decimal,
    tax: Decimal,
) -> AIStockPosition:
    position = db.scalar(select(AIStockPosition).where(
        AIStockPosition.id == position_id, AIStockPosition.user_id == user_id,
    ))
    if position is None:
        raise LookupError("持倉不存在")
    if quantity >= position.remaining_quantity:
        raise ValueError("部分賣出數量必須小於剩餘股數")
    realized = money((exit_price - decimal_value(position.average_cost)) * quantity - fee - tax)
    db.add(AIStockPartialExit(
        position_id=position.id, quantity=quantity, exit_price=exit_price,
        exit_time=exit_time, fee=fee, tax=tax, realized_profit=realized,
        created_at=datetime.now(UTC),
    ))
    position.remaining_quantity -= quantity
    position.realized_profit = money(decimal_value(position.realized_profit) + realized)
    position.invested_amount = money(decimal_value(position.average_cost) * position.remaining_quantity)
    settings = get_portfolio_settings(db, user_id)
    capital = decimal_value(settings.total_capital)
    position.current_allocation_percentage = percent(
        decimal_value(position.invested_amount) / capital * 100 if capital else Decimal("0")
    )
    position.estimated_risk_amount = money(
        max(Decimal("0"), decimal_value(position.average_cost) - decimal_value(position.stop_loss))
        * position.remaining_quantity
    )
    position.available_add_on_amount = money(max(
        Decimal("0"),
        capital * decimal_value(position.target_allocation_percentage) / 100
        - decimal_value(position.invested_amount),
    ))
    position.latest_action = "部分賣出後繼續監控"
    position.position_status = "holding"
    position.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(position)
    return position


def close_position(
    db: Session,
    user_id: str,
    position_id: int,
    *,
    quantity: int,
    exit_price: Decimal,
    exit_time: datetime,
    fee: Decimal,
    tax: Decimal,
    reason: str,
) -> AIStockPosition:
    position = db.scalar(select(AIStockPosition).where(
        AIStockPosition.id == position_id, AIStockPosition.user_id == user_id,
    ))
    if position is None:
        raise LookupError("持倉不存在")
    if quantity != position.remaining_quantity:
        raise ValueError("全部賣出數量必須等於剩餘股數")
    realized = money((exit_price - decimal_value(position.average_cost)) * quantity - fee - tax)
    position.realized_profit = money(decimal_value(position.realized_profit) + realized)
    position.remaining_quantity = 0
    position.unrealized_profit = Decimal("0")
    position.invested_amount = Decimal("0")
    position.current_allocation_percentage = Decimal("0")
    position.available_add_on_amount = Decimal("0")
    position.estimated_risk_amount = Decimal("0")
    position.position_status = "closed"
    position.latest_action = "已全部賣出"
    position.closed_at = exit_time
    position.exit_price = exit_price
    position.exit_reason = reason
    position.updated_at = datetime.now(UTC)
    monitor = db.get(AIStockMonitor, position.monitor_id)
    if monitor:
        monitor.monitor_status = "ended"
        monitor.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(position)
    return position
