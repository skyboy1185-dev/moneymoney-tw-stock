from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
import json
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    AdaptivePaperTrade,
    AdaptiveStockCandidate,
    MarketRegime,
    SuperAIDaytradeNotification,
    SuperAIDaytradeSetting,
)


SYSTEM_NAME = "超強AI當沖系統"
SOURCE = "SUPER_AI_DAYTRADE"
TAIPEI = ZoneInfo("Asia/Taipei")
MONEY = Decimal("0.01")
LONG_MAX_STOP_DISTANCE_PCT = Decimal("8.0")
SHORT_MAX_STOP_DISTANCE_PCT = Decimal("1.8")
A_PLUS_MAX_STOP_DISTANCE_PCT = Decimal("8.0")
DEFAULT_MAX_STOP_DISTANCE_PCT = Decimal("1.0")
MIN_CONFIGURABLE_STOP_DISTANCE_PCT = Decimal("0.3")
MAX_CONFIGURABLE_STOP_DISTANCE_PCT = Decimal("10.0")
INTRADAY_BULL_BREAKOUT_BONUS = Decimal("8")
PRECISION_MIN_AI_SCORE = Decimal("60")
PRECISION_MIN_TOTAL_SCORE = Decimal("58")
PRECISION_PROBE_MIN_TOTAL_SCORE = Decimal("55")
PRECISION_PROBE_MIN_HEALTH_SCORE = Decimal("55")
PRECISION_MIN_HEALTH_SCORE = Decimal("55")
PRECISION_MIN_INDUSTRY_STRENGTH = Decimal("20")
PRECISION_MIN_RISK_REWARD = Decimal("2.0")
PRECISION_MAX_STOP_DISTANCE_PCT = Decimal("8.0")
PRECISION_MAX_NEW_TRADES_PER_DAY = 2
PRECISION_MAX_PROBE_TRADES_PER_DAY = 1
PRECISION_MAX_DAILY_LOSS_PCT = Decimal("0.3")
PRECISION_RISK_PER_TRADE_PCT = Decimal("0.15")
PRECISION_MAX_FALSE_BREAKOUT_RISK = Decimal("35")

MARKET_WEIGHTS: dict[str, dict[str, float | str]] = {
    "BREAKOUT": {"label": "強多", "long": 100, "short": 0},
    "RECOVERY": {"label": "偏多", "long": 80, "short": 20},
    "RANGE": {"label": "盤整", "long": 50, "short": 50},
    "UNCERTAIN": {"label": "盤整", "long": 50, "short": 50},
    "CRASH": {"label": "強空", "long": 10, "short": 90},
}

TRADE_EMAIL_CATEGORIES = {
    "BUY", "SHORT", "ADD", "REDUCE", "STOP_LOSS", "TAKE_PROFIT", "EXIT", "RISK", "ERROR",
}

MARKET_WEIGHTS["RECOVERY"].update({"long": 100, "short": 0})


def _money(value: Decimal | float | int) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _loads(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def discount_label(value: Decimal) -> str:
    tenths = value * Decimal("10")
    return f"{int(tenths)}折" if tenths == tenths.to_integral_value() else f"{float(tenths):.1f}折"


def ensure_settings(db: Session, at: datetime | None = None) -> SuperAIDaytradeSetting:
    row = db.get(SuperAIDaytradeSetting, 1)
    if row is None:
        row = SuperAIDaytradeSetting(id=1, updated_at=at or datetime.now(UTC))
        db.add(row)
        db.flush()
    return row


def settings_payload(row: SuperAIDaytradeSetting) -> dict[str, Any]:
    return {
        "systemName": SYSTEM_NAME,
        "enabled": row.enabled,
        "tradingMode": row.trading_mode,
        "maxCapital": float(row.max_capital),
        "availableCapital": float(row.available_capital),
        "riskPerTradePct": float(row.risk_per_trade_pct),
        "dailyMaxLossPct": float(row.daily_max_loss_pct),
        "weeklyDrawdownPct": float(row.weekly_drawdown_pct),
        "minAiScoreToTrade": float(row.min_ai_score_to_trade),
        "minAiScoreToWatch": float(row.min_ai_score_to_watch),
        "minRiskReward": float(row.min_risk_reward),
        "maxPositions": row.max_positions,
        "maxPositionPct": float(row.max_position_pct),
        "maxStopDistancePct": float(row.max_stop_distance_pct),
        "commissionDiscount": float(row.commission_discount),
        "commissionDiscountLabel": discount_label(Decimal(row.commission_discount)),
        "emailEnabled": row.email_enabled,
        "emailBuyEnabled": row.email_buy_enabled,
        "emailSellEnabled": row.email_sell_enabled,
        "emailAddEnabled": row.email_add_enabled,
        "emailStopLossEnabled": row.email_stop_loss_enabled,
        "emailTakeProfitEnabled": row.email_take_profit_enabled,
        "emailRiskEnabled": row.email_risk_enabled,
        "emailDailySummaryEnabled": row.email_daily_summary_enabled,
        "emailErrorEnabled": row.email_error_enabled,
        "stopNewTrades": row.stop_new_trades,
        "stopReason": row.stop_reason,
        "consecutiveStopLosses": row.consecutive_stop_losses,
        "strategyMode": "BALANCED_BREAKOUT",
        "strategyModeLabel": "平衡突破＋試單模式",
        "precisionPolicy": {
            "longOnly": True,
            "allowedRegimes": ["BREAKOUT", "RECOVERY"],
            "allowedStrategies": ["BREAKOUT"],
            "minAiScore": float(PRECISION_MIN_AI_SCORE),
            "minTotalScore": float(PRECISION_MIN_TOTAL_SCORE),
            "minHealthScore": float(PRECISION_MIN_HEALTH_SCORE),
            "minIndustryStrength": float(PRECISION_MIN_INDUSTRY_STRENGTH),
            "minRiskReward": float(PRECISION_MIN_RISK_REWARD),
            "maxStopDistancePct": float(PRECISION_MAX_STOP_DISTANCE_PCT),
            "maxNewTradesPerDay": PRECISION_MAX_NEW_TRADES_PER_DAY,
            "maxProbeTradesPerDay": PRECISION_MAX_PROBE_TRADES_PER_DAY,
            "probeMinTotalScore": float(PRECISION_PROBE_MIN_TOTAL_SCORE),
            "probeMinHealthScore": float(PRECISION_PROBE_MIN_HEALTH_SCORE),
            "maxDailyLossPct": float(PRECISION_MAX_DAILY_LOSS_PCT),
            "riskPerTradePct": float(PRECISION_RISK_PER_TRADE_PCT),
            "requiresRealtimeBreakoutProxy": True,
        },
        "settingsVersion": row.settings_version,
        "updatedAt": row.updated_at.isoformat(),
    }


def update_settings(db: Session, values: dict[str, Any], user_id: str, at: datetime) -> SuperAIDaytradeSetting:
    row = ensure_settings(db, at)
    mapping = {
        "enabled": "enabled",
        "tradingMode": "trading_mode",
        "maxCapital": "max_capital",
        "availableCapital": "available_capital",
        "riskPerTradePct": "risk_per_trade_pct",
        "dailyMaxLossPct": "daily_max_loss_pct",
        "weeklyDrawdownPct": "weekly_drawdown_pct",
        "minAiScoreToTrade": "min_ai_score_to_trade",
        "minAiScoreToWatch": "min_ai_score_to_watch",
        "minRiskReward": "min_risk_reward",
        "maxPositions": "max_positions",
        "maxPositionPct": "max_position_pct",
        "maxStopDistancePct": "max_stop_distance_pct",
        "commissionDiscount": "commission_discount",
        "emailEnabled": "email_enabled",
        "emailBuyEnabled": "email_buy_enabled",
        "emailSellEnabled": "email_sell_enabled",
        "emailAddEnabled": "email_add_enabled",
        "emailStopLossEnabled": "email_stop_loss_enabled",
        "emailTakeProfitEnabled": "email_take_profit_enabled",
        "emailRiskEnabled": "email_risk_enabled",
        "emailDailySummaryEnabled": "email_daily_summary_enabled",
        "emailErrorEnabled": "email_error_enabled",
        "stopNewTrades": "stop_new_trades",
        "stopReason": "stop_reason",
    }
    decimal_fields = {
        "maxCapital", "availableCapital", "riskPerTradePct", "dailyMaxLossPct",
        "weeklyDrawdownPct", "minAiScoreToTrade", "minAiScoreToWatch",
        "minRiskReward", "maxPositionPct", "maxStopDistancePct", "commissionDiscount",
    }
    for source, target in mapping.items():
        if source not in values or values[source] is None:
            if source == "stopReason" and source in values:
                row.stop_reason = None
            continue
        value = values[source]
        if source == "maxCapital":
            value = min(5_000_000, max(100_000, float(value)))
        if source == "commissionDiscount":
            value = min(1, max(0, float(value)))
        if source == "maxStopDistancePct":
            value = min(
                float(MAX_CONFIGURABLE_STOP_DISTANCE_PCT),
                max(float(MIN_CONFIGURABLE_STOP_DISTANCE_PCT), float(value)),
            )
        if source in decimal_fields:
            setattr(row, target, Decimal(str(value)))
        else:
            setattr(row, target, value)
    row.system_name = SYSTEM_NAME
    row.settings_version += 1
    row.updated_by = user_id
    row.updated_at = at
    db.flush()
    return row


def market_state(regime: str) -> dict[str, Any]:
    row = MARKET_WEIGHTS.get(regime, MARKET_WEIGHTS["UNCERTAIN"])
    return {
        "regime": regime,
        "label": row["label"],
        "longWeight": row["long"],
        "shortWeight": row["short"],
    }


def trade_side_for(regime: str, candidate: AdaptiveStockCandidate) -> str:
    if regime == "CRASH":
        return "SHORT"
    if regime in {"BREAKOUT", "RECOVERY"}:
        return "LONG"
    if candidate.strategy_type == "CRASH":
        severe_weak = (
            float(candidate.relative_strength) <= -3
            or float(candidate.industry_strength) <= 45
            or candidate.candidate_status in {"market_risk_high", "breakout_watch", "can_enter"}
        )
        if severe_weak:
            return "SHORT"
    weak = (
        float(candidate.relative_strength) < -3
        or float(candidate.industry_strength) < 35
        or candidate.candidate_status == "market_risk_high"
    )
    if regime in {"RANGE", "UNCERTAIN"} and weak:
        return "SHORT"
    return "LONG"


def levels_for_side(candidate: AdaptiveStockCandidate, side: str) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    entry = Decimal(candidate.current_price)
    if side == "SHORT":
        stop = max(entry * Decimal("1.015"), entry + abs(entry - Decimal(candidate.stop_loss_price)))
        risk = max(Decimal("0.01"), stop - entry)
        target1 = entry - risk * Decimal("1.5")
        target2 = entry - risk * Decimal("2.5")
        return entry, _money(stop), _money(max(Decimal("0.01"), target1)), _money(max(Decimal("0.01"), target2))
    stop = Decimal(candidate.stop_loss_price)
    risk = max(Decimal("0.01"), entry - stop)
    return entry, stop, Decimal(candidate.target_price_1), Decimal(candidate.target_price_2)


def risk_reward(entry: Decimal, stop: Decimal, target2: Decimal, side: str) -> Decimal:
    if side == "SHORT":
        risk = max(Decimal("0.01"), stop - entry)
        reward = max(Decimal("0"), entry - target2)
    else:
        risk = max(Decimal("0.01"), entry - stop)
        reward = max(Decimal("0"), target2 - entry)
    return (reward / risk).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def stop_distance_pct(entry: Decimal, stop: Decimal) -> Decimal:
    if entry <= 0:
        return Decimal("999")
    return (abs(entry - stop) / entry * Decimal("100")).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _configured_stop_distance_pct(value: Decimal | float | int | None) -> Decimal:
    if value is None:
        return DEFAULT_MAX_STOP_DISTANCE_PCT
    parsed = Decimal(str(value))
    return min(MAX_CONFIGURABLE_STOP_DISTANCE_PCT, max(MIN_CONFIGURABLE_STOP_DISTANCE_PCT, parsed))


def max_stop_distance_pct(side: str, score: Decimal, configured: Decimal | float | int | None = None) -> Decimal:
    if score >= Decimal("90"):
        base = A_PLUS_MAX_STOP_DISTANCE_PCT
    elif side == "SHORT":
        base = SHORT_MAX_STOP_DISTANCE_PCT
    else:
        base = LONG_MAX_STOP_DISTANCE_PCT
    return min(base, _configured_stop_distance_pct(configured)) if configured is not None else base


def cap_stop_distance(entry: Decimal, stop: Decimal, side: str, max_stop_pct: Decimal) -> tuple[Decimal, bool]:
    if stop_distance_pct(entry, stop) <= max_stop_pct:
        return stop, False
    if side == "SHORT":
        return _money(entry * (Decimal("1") + max_stop_pct / Decimal("100"))), True
    return _money(entry * (Decimal("1") - max_stop_pct / Decimal("100"))), True


def _intraday_bull_breakout_bonus(candidate: AdaptiveStockCandidate, regime: str, side: str) -> Decimal:
    if (
        side == "LONG"
        and regime == "BREAKOUT"
        and candidate.strategy_type == "BREAKOUT"
        and Decimal(candidate.total_score) >= PRECISION_MIN_TOTAL_SCORE
        and Decimal(candidate.health_score) >= PRECISION_MIN_HEALTH_SCORE
        and Decimal(candidate.industry_strength) >= PRECISION_MIN_INDUSTRY_STRENGTH
        and Decimal(candidate.current_price) >= Decimal(candidate.breakout_price)
        and str(candidate.quote_source).startswith("TWSE MIS")
    ):
        return INTRADAY_BULL_BREAKOUT_BONUS / Decimal("2")
    return Decimal("0")


def ai_score(candidate: AdaptiveStockCandidate, regime: str, side: str = "LONG") -> Decimal:
    if side == "SHORT":
        base = Decimal(candidate.total_score) * Decimal("0.80")
        market_bonus = (
            Decimal("14") if regime == "CRASH"
            else Decimal("7") if regime in {"RANGE", "UNCERTAIN"}
            else Decimal("4") if regime == "RECOVERY"
            else Decimal("-30")
        )
        weakness_bonus = min(Decimal("12"), max(Decimal("0"), -Decimal(candidate.relative_strength)) * Decimal("1.5"))
        if Decimal(candidate.industry_strength) <= Decimal("40"):
            weakness_bonus += Decimal("4")
        score = max(Decimal("0"), min(Decimal("100"), base + market_bonus + weakness_bonus))
        return score.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    base = Decimal(candidate.total_score) * Decimal("0.60") + Decimal(candidate.health_score) * Decimal("0.40")
    market_bonus = Decimal("10") if regime in {"BREAKOUT", "RECOVERY"} else Decimal("4") if regime in {"RANGE", "UNCERTAIN"} else Decimal("0")
    momentum_bonus = _intraday_bull_breakout_bonus(candidate, regime, side)
    score = max(Decimal("0"), min(Decimal("100"), base + market_bonus + momentum_bonus))
    return score.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def decision_reasons(candidate: AdaptiveStockCandidate, regime: str, side: str, rr: Decimal) -> list[str]:
    reasons = [
        f"market={market_state(regime)['label']}",
        f"side={side}",
        f"strategy={candidate.strategy_type}",
        f"score={float(candidate.total_score):.1f}",
        f"health={float(candidate.health_score):.1f}",
        f"sectorStrength={float(candidate.industry_strength):.1f}",
        f"riskReward={float(rr):.2f}",
    ]
    selected = _loads(candidate.selected_reasons, [])
    return reasons + [str(item) for item in selected[:6]]


def today_bounds(at: datetime) -> tuple[datetime, datetime]:
    local = at.astimezone(TAIPEI)
    start = datetime.combine(local.date(), time.min, TAIPEI).astimezone(UTC)
    end = start + timedelta(days=1)
    return start, end


def risk_status(db: Session, settings: SuperAIDaytradeSetting, at: datetime) -> dict[str, Any]:
    start, end = today_bounds(at)
    closed = list(db.scalars(select(AdaptivePaperTrade).where(
        AdaptivePaperTrade.entry_time >= start,
        AdaptivePaperTrade.entry_time < end,
        AdaptivePaperTrade.status == "closed",
    )).all())
    open_trades = list(db.scalars(select(AdaptivePaperTrade).where(
        AdaptivePaperTrade.status == "open",
    )).all())
    realized_pnl = sum((trade.net_profit for trade in closed), Decimal("0"))
    unrealized_pnl = sum((trade.unrealized_profit for trade in open_trades), Decimal("0"))
    today_pnl = realized_pnl + unrealized_pnl
    effective_daily_loss_pct = min(Decimal(settings.daily_max_loss_pct), PRECISION_MAX_DAILY_LOSS_PCT)
    daily_limit = Decimal(settings.max_capital) * effective_daily_loss_pct / Decimal("100")
    stop_losses = [trade for trade in closed if trade.net_profit < 0 and "STOP" in (trade.exit_reason or "").upper()]
    opened_today = int(db.scalar(select(func.count(AdaptivePaperTrade.id)).where(
        AdaptivePaperTrade.entry_time >= start,
        AdaptivePaperTrade.entry_time < end,
    )) or 0)
    opened_probe_today = int(db.scalar(select(func.count(AdaptivePaperTrade.id)).where(
        AdaptivePaperTrade.entry_time >= start,
        AdaptivePaperTrade.entry_time < end,
        AdaptivePaperTrade.entry_reason.like("%probe_entry%"),
    )) or 0)
    stop_new = (
        settings.stop_new_trades
        or today_pnl <= -daily_limit
        or settings.consecutive_stop_losses >= 3
        or len(stop_losses) >= 1
        or opened_today >= PRECISION_MAX_NEW_TRADES_PER_DAY
    )
    return {
        "todayPnl": float(_money(today_pnl)),
        "dailyMaxLoss": float(_money(daily_limit)),
        "openTrades": len(open_trades),
        "openedTradesToday": opened_today,
        "openedProbeTradesToday": opened_probe_today,
        "maxNewTradesPerDay": PRECISION_MAX_NEW_TRADES_PER_DAY,
        "maxProbeTradesPerDay": PRECISION_MAX_PROBE_TRADES_PER_DAY,
        "stopNewTrades": bool(stop_new),
        "stopReason": settings.stop_reason
            or ("daily_max_loss" if today_pnl <= -daily_limit else "first_stop_loss" if len(stop_losses) >= 1 else "daily_trade_limit" if opened_today >= PRECISION_MAX_NEW_TRADES_PER_DAY else "consecutive_stop_losses" if settings.consecutive_stop_losses >= 3 else None),
        "consecutiveStopLosses": max(settings.consecutive_stop_losses, len(stop_losses)),
    }


def sized_quantity(
    settings: SuperAIDaytradeSetting,
    *,
    entry: Decimal,
    stop: Decimal,
    side: str,
    open_market_value: Decimal,
) -> tuple[int, Decimal, Decimal]:
    risk_per_share = (stop - entry) if side == "SHORT" else (entry - stop)
    risk_per_share = max(Decimal("0.01"), risk_per_share)
    effective_risk_pct = min(Decimal(settings.risk_per_trade_pct), PRECISION_RISK_PER_TRADE_PCT)
    risk_amount = Decimal(settings.max_capital) * effective_risk_pct / Decimal("100")
    capital_limit = min(
        Decimal(settings.available_capital),
        Decimal(settings.max_capital) * Decimal(settings.max_position_pct) / Decimal("100"),
    )
    risk_shares = int(risk_amount / risk_per_share)
    capital_shares = int(capital_limit / entry)
    quantity = max(0, min(risk_shares, capital_shares))
    quantity = (quantity // 1000) * 1000
    if quantity == 0 and min(risk_shares, capital_shares) >= 100:
        quantity = (min(risk_shares, capital_shares) // 100) * 100
    return quantity, _money(risk_amount), open_market_value + entry * quantity


def trading_gate(
    db: Session,
    settings: SuperAIDaytradeSetting,
    candidate: AdaptiveStockCandidate,
    regime: str,
    at: datetime,
) -> dict[str, Any]:
    side = trade_side_for(regime, candidate)
    entry, stop, tp1, tp2 = levels_for_side(candidate, side)
    max_stop_pct = PRECISION_MAX_STOP_DISTANCE_PCT
    stop, stop_distance_capped = cap_stop_distance(entry, stop, side, max_stop_pct)
    rr = risk_reward(entry, stop, tp2, side)
    score = ai_score(candidate, regime, side)
    stop_pct = stop_distance_pct(entry, stop)
    risk = risk_status(db, settings, at)
    open_trades = list(db.scalars(select(AdaptivePaperTrade).where(
        AdaptivePaperTrade.status == "open",
    )).all())
    open_value = sum((trade.last_price * trade.quantity_shares for trade in open_trades), Decimal("0"))
    quantity, risk_amount, projected_value = sized_quantity(
        settings, entry=entry, stop=stop, side=side, open_market_value=open_value,
    )
    failures: list[str] = []
    warnings: list[str] = []
    if not settings.enabled:
        failures.append("system_disabled")
    if settings.trading_mode not in {"PAPER", "LIVE"}:
        failures.append("invalid_trading_mode")
    if risk["stopNewTrades"]:
        failures.append(str(risk["stopReason"] or "risk_stop"))
    if side == "SHORT":
        failures.append("precision_breakout_long_only")
    if regime not in {"BREAKOUT", "RECOVERY"}:
        failures.append("precision_requires_strong_market")
    if candidate.strategy_type != "BREAKOUT":
        failures.append("precision_requires_breakout_strategy")
    if Decimal(candidate.total_score) < PRECISION_MIN_TOTAL_SCORE:
        failures.append("precision_total_score_below_58")
    if Decimal(candidate.health_score) < PRECISION_MIN_HEALTH_SCORE:
        failures.append("precision_health_score_below_55")
    if Decimal(candidate.relative_strength) <= Decimal("0"):
        failures.append("precision_relative_strength_not_positive")
    if Decimal(candidate.industry_strength) < PRECISION_MIN_INDUSTRY_STRENGTH:
        failures.append("precision_industry_strength_below_20")
    if Decimal(candidate.false_breakout_risk) > PRECISION_MAX_FALSE_BREAKOUT_RISK:
        failures.append("precision_false_breakout_risk_too_high")
    if not str(candidate.quote_source).startswith("TWSE MIS"):
        failures.append("precision_vwap_proxy_not_confirmed")
    elif Decimal(candidate.current_price) < Decimal(candidate.breakout_price):
        warnings.append("breakout_price_not_reached")
    if side == "LONG" and regime in {"BREAKOUT", "RECOVERY"}:
        if candidate.strategy_type != "BREAKOUT":
            failures.append("strong_market_requires_breakout_strategy")
        if Decimal(candidate.total_score) < PRECISION_MIN_TOTAL_SCORE:
            failures.append("strong_market_total_score_too_weak")
        if Decimal(candidate.health_score) < PRECISION_MIN_HEALTH_SCORE:
            failures.append("strong_market_health_score_too_weak")
        if Decimal(candidate.relative_strength) <= Decimal("0"):
            failures.append("strong_market_relative_strength_too_weak")
    if len(open_trades) >= settings.max_positions:
        failures.append("max_positions")
    if score < PRECISION_MIN_AI_SCORE:
        failures.append("ai_score_below_trade_threshold")
    if rr < PRECISION_MIN_RISK_REWARD:
        failures.append("risk_reward_below_threshold")
    if stop_pct > max_stop_pct:
        failures.append("stop_distance_too_wide")
    if quantity <= 0:
        failures.append("quantity_zero")
    if candidate.quote_source.startswith("Yahoo Finance"):
        failures.append("delayed_quote")
    if candidate.candidate_status in {"market_risk_high", "signal_invalid"} and side == "LONG":
        failures.append("market_risk_blocks_long")

    entry_mode = "FORMAL"
    probe_failures: list[str] = []
    if failures:
        non_probe_failures = [
            reason for reason in failures
            if reason not in {
                "precision_total_score_below_58",
                "strong_market_total_score_too_weak",
            }
        ]
        if Decimal(candidate.total_score) < PRECISION_PROBE_MIN_TOTAL_SCORE:
            probe_failures.append("probe_total_score_below_55")
        if Decimal(candidate.health_score) < PRECISION_PROBE_MIN_HEALTH_SCORE:
            probe_failures.append("probe_health_score_below_55")
        if int(risk.get("openedProbeTradesToday", 0)) >= PRECISION_MAX_PROBE_TRADES_PER_DAY:
            probe_failures.append("probe_daily_trade_limit")
        if non_probe_failures:
            probe_failures.extend(non_probe_failures)
        if not probe_failures:
            warnings.extend(failures)
            failures = []
            entry_mode = "PROBE"
        else:
            failures = probe_failures

    reasons = decision_reasons(candidate, regime, side, rr)
    reasons.append("balanced_breakout_mode")
    if entry_mode == "PROBE":
        reasons.append("probe_entry")
    if _intraday_bull_breakout_bonus(candidate, regime, side):
        reasons.append(f"intraday_bull_breakout_bonus=+{INTRADAY_BULL_BREAKOUT_BONUS / Decimal('2')}")
    if stop_distance_capped:
        reasons.append(f"stop_distance_capped_to_{float(max_stop_pct):.2f}%")
    return {
        "allowed": not failures,
        "failures": failures,
        "warnings": warnings,
        "entryMode": entry_mode if not failures else "BLOCKED",
        "probeEligible": not failures and entry_mode == "PROBE",
        "side": side,
        "entry": entry,
        "stop": stop,
        "takeProfit1": tp1,
        "takeProfit2": tp2,
        "riskReward": rr,
        "stopDistancePct": stop_pct,
        "maxStopDistancePct": max_stop_pct,
        "aiScore": score,
        "quantity": quantity,
        "riskAmount": risk_amount,
        "projectedMarketValue": _money(projected_value),
        "reasons": reasons,
        "risk": risk,
    }


def record_notification(
    db: Session,
    *,
    category: str,
    level: str,
    title: str,
    message: str,
    dedupe_key: str,
    symbol: str | None = None,
    symbol_name: str | None = None,
    strategy: str | None = None,
    side: str | None = None,
    price: Decimal | None = None,
    quantity: int | None = None,
    stop_loss: Decimal | None = None,
    take_profit_1: Decimal | None = None,
    take_profit_2: Decimal | None = None,
    ai_score_value: Decimal | None = None,
    risk_reward_value: Decimal | None = None,
    at: datetime | None = None,
) -> SuperAIDaytradeNotification | None:
    existing = db.scalar(select(SuperAIDaytradeNotification).where(
        SuperAIDaytradeNotification.source == SOURCE,
        SuperAIDaytradeNotification.dedupe_key == dedupe_key,
    ))
    if existing is not None:
        return None
    row = SuperAIDaytradeNotification(
        source=SOURCE,
        category=category,
        level=level,
        symbol=symbol,
        symbol_name=symbol_name,
        title=title,
        message=message,
        strategy=strategy,
        side=side,
        price=price,
        quantity=quantity,
        stop_loss=stop_loss,
        take_profit_1=take_profit_1,
        take_profit_2=take_profit_2,
        ai_score=ai_score_value,
        risk_reward=risk_reward_value,
        dedupe_key=dedupe_key,
        created_at=at or datetime.now(UTC),
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return None
    return row


def notification_payload(row: SuperAIDaytradeNotification) -> dict[str, Any]:
    return {
        "id": row.id,
        "source": row.source,
        "category": row.category,
        "level": row.level,
        "symbol": row.symbol,
        "symbolName": row.symbol_name,
        "title": row.title,
        "message": row.message,
        "strategy": row.strategy,
        "side": row.side,
        "price": float(row.price) if row.price is not None else None,
        "quantity": row.quantity,
        "stopLoss": float(row.stop_loss) if row.stop_loss is not None else None,
        "takeProfit1": float(row.take_profit_1) if row.take_profit_1 is not None else None,
        "takeProfit2": float(row.take_profit_2) if row.take_profit_2 is not None else None,
        "aiScore": float(row.ai_score) if row.ai_score is not None else None,
        "riskReward": float(row.risk_reward) if row.risk_reward is not None else None,
        "emailSent": row.email_sent,
        "popupShown": row.popup_shown,
        "read": row.is_read,
        "timestamp": row.created_at.isoformat(),
        "stockCode": row.symbol,
        "stockName": row.symbol_name,
        "action": row.category,
        "reasons": [row.message] if row.message else [],
        "healthScore": float(row.ai_score) if row.ai_score is not None else None,
        "createdAt": row.created_at.isoformat(),
    }


def strategy_analytics(db: Session) -> list[dict[str, Any]]:
    rows = list(db.scalars(select(AdaptivePaperTrade).where(
        AdaptivePaperTrade.status == "closed",
    )).all())
    keys = sorted({row.strategy_type for row in rows} | {"OPEN_STRENGTH_BREAKOUT", "VWAP_PULLBACK", "RANGE_BREAKDOWN", "FAKE_BREAKOUT_REVERSAL"})
    result: list[dict[str, Any]] = []
    for key in keys:
        subset = [row for row in rows if row.strategy_type == key]
        wins = [row for row in subset if row.net_profit > 0]
        losses = [row for row in subset if row.net_profit < 0]
        gross_profit = sum((row.net_profit for row in wins), Decimal("0"))
        gross_loss = abs(sum((row.net_profit for row in losses), Decimal("0")))
        pf = float(gross_profit / gross_loss) if gross_loss else (999.0 if gross_profit else 0.0)
        recent = sorted(subset, key=lambda row: row.entry_time)[-30:]
        recent_wins = sum((row.net_profit for row in recent if row.net_profit > 0), Decimal("0"))
        recent_losses = abs(sum((row.net_profit for row in recent if row.net_profit < 0), Decimal("0")))
        recent_pf = float(recent_wins / recent_losses) if recent_losses else (999.0 if recent_wins else 0.0)
        result.append({
            "strategy": key,
            "trades": len(subset),
            "winRate": round(len(wins) / len(subset) * 100, 2) if subset else 0,
            "averageR": round(sum((row.realized_r for row in subset), Decimal("0")) / max(1, len(subset)), 3) if subset else 0,
            "profitFactor": round(pf, 3),
            "recent30ProfitFactor": round(recent_pf, 3),
            "weightStatus": "PAUSED" if len(recent) >= 30 and recent_pf < .8 else "REDUCED" if len(recent) >= 30 and recent_pf < 1 else "ACTIVE",
        })
    return result


def time_bucket_analytics(db: Session) -> list[dict[str, Any]]:
    buckets = [
        ("09:00-09:30", time(9, 0), time(9, 30)),
        ("09:30-10:00", time(9, 30), time(10, 0)),
        ("10:00-11:00", time(10, 0), time(11, 0)),
        ("11:00-12:00", time(11, 0), time(12, 0)),
        ("12:00-13:30", time(12, 0), time(13, 30)),
    ]
    rows = list(db.scalars(select(AdaptivePaperTrade).where(
        AdaptivePaperTrade.status == "closed",
    )).all())
    result = []
    for label, start, end in buckets:
        subset = [
            row for row in rows
            if start <= row.entry_time.astimezone(TAIPEI).time().replace(tzinfo=None) < end
        ]
        wins = [row for row in subset if row.net_profit > 0]
        losses = [row for row in subset if row.net_profit < 0]
        gross_profit = sum((row.net_profit for row in wins), Decimal("0"))
        gross_loss = abs(sum((row.net_profit for row in losses), Decimal("0")))
        result.append({
            "bucket": label,
            "trades": len(subset),
            "winRate": round(len(wins) / len(subset) * 100, 2) if subset else 0,
            "averageR": round(sum((row.realized_r for row in subset), Decimal("0")) / max(1, len(subset)), 3) if subset else 0,
            "profitFactor": round(float(gross_profit / gross_loss), 3) if gross_loss else (999 if gross_profit else 0),
        })
    return result
