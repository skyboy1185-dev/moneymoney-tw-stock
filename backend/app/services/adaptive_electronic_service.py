from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
import json
from typing import Any, Sequence

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..adaptive_schemas import AdaptiveScanPayload, AdaptiveStockInput
from ..models import (
    AdaptiveSignal,
    AdaptiveStockCandidate,
    AdaptiveStockMonitoring,
    ElectronicIndustryMapping,
    ElectronicIndustryStrength,
    MarketRegime,
)
from .adaptive_parameters import load_parameters
from .adaptive_performance_service import update_adaptive_paper_trades
from .adaptive_entry_window import adaptive_entry_window_open
from .adaptive_strategies import STRATEGIES, CrashRecoveryStrategy, StrategyScore
from .electronic_industry_strength_service import rank_industries
from .electronic_stock_universe_service import common_filter_failures
from .market_regime_service import RegimeEvaluation, evaluate_market_regime, intraday_regime_override
from .risk_management_service import allocation_percent


AUTOMATION_USER_ID = "system-adaptive-electronic"
STRATEGY_NAMES = {
    "CRASH": "崩盤防守模式",
    "RECOVERY": "崩盤後止跌復甦選股",
    "RANGE": "區間盤整選股",
    "BREAKOUT": "多頭突破選股",
    "UNCERTAIN": "盤勢不明・降低訊號",
}
STATUS_LABELS = {
    "market_risk_high": "市場風險過高",
    "can_enter": "可以進場",
    "next_day_watch": "隔日觀察",
    "waiting_confirmation": "等待確認",
    "waiting_retest": "等待回測",
    "near_support": "接近支撐",
    "breakout_watch": "突破觀察",
    "waiting_stop": "等待止跌",
    "signal_invalid": "訊號失效",
}


def _decimal(value: float | int) -> Decimal:
    return Decimal(str(round(float(value), 4)))


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _candidate_levels(stock: AdaptiveStockInput, strategy: str) -> dict[str, float]:
    price = stock.price
    atr = stock.atr14 or price * .025
    breakout = stock.range_high or price
    if strategy == "CRASH":
        entry_low, entry_high = price * .995, price * 1.005
        stop = min(price * 1.08, price + atr * 1.8)
        risk = max(.01, stop - entry_low)
        return {
            "entry_low": round(entry_low, 2), "entry_high": round(entry_high, 2),
            "breakout": round(stock.range_low or price, 2), "stop": round(stop, 2),
            "target1": round(max(.01, entry_low - risk * 1.5), 2),
            "target2": round(max(.01, entry_low - risk * 2.5), 2),
        }
    if strategy == "RANGE":
        entry_low = max(.01, stock.range_low or price * .98)
        entry_high = min(price * 1.01, entry_low * 1.02)
        structural = entry_low * .975
        atr_stop = entry_high - atr * 1.5
        stop = max(structural, atr_stop)
    elif strategy == "RECOVERY":
        entry_low, entry_high = price * .995, price * 1.005
        stop = max(price * .92, price - atr * 2)
    elif strategy == "BREAKOUT":
        entry_low, entry_high = breakout, breakout * 1.02
        stop = max(breakout * .96, entry_high - atr * 2)
    else:
        entry_low = entry_high = price
        stop = max(price * .92, price - atr * 2)
    risk = max(.01, entry_high - stop)
    return {
        "entry_low": round(entry_low, 2), "entry_high": round(entry_high, 2),
        "breakout": round(breakout, 2), "stop": round(stop, 2),
        "target1": round(entry_high + risk * 1.5, 2),
        "target2": round(entry_high + risk * 2.5, 2),
    }


def _short_score(stock: AdaptiveStockInput, regime: str) -> StrategyScore:
    market_score = {"CRASH": 20, "RANGE": 14, "UNCERTAIN": 12, "RECOVERY": 8, "BREAKOUT": -25}.get(regime, 0)
    trend_items = [
        (stock.price < (stock.ma5 or stock.price * 2), 5, "price_below_ma5"),
        (stock.price < (stock.ma20 or stock.price * 2), 8, "price_below_ma20"),
        (stock.ma20_slope is not None and stock.ma20_slope < 0, 6, "ma20_slope_down"),
        (stock.ma60 is not None and stock.price < stock.ma60, 5, "price_below_ma60"),
        (stock.higher_low is False, 4, "no_higher_low"),
    ]
    momentum_items = [
        (stock.return_1d < 0, 6, "intraday_weak"),
        (stock.return_3d < 0, 6, "three_day_weak"),
        (stock.relative_strength_market <= -3, 8, "underperform_market_3pct"),
        (stock.relative_strength_market <= -6, 6, "underperform_market_6pct"),
        (stock.relative_strength_electronic <= -3, 5, "underperform_electronic"),
        ((stock.rsi14 or 100) < 45, 4, "rsi_weak"),
    ]
    volume_items = [
        ((stock.volume_ratio_20d or 0) >= 1.2 and stock.return_1d < 0, 8, "down_on_volume"),
        (stock.down_volume_less_than_up is False, 5, "down_volume_dominates"),
        ((stock.close_location or 1) <= .35, 4, "close_near_low"),
        ((stock.upper_shadow_ratio or 0) >= .45, 4, "upper_shadow_supply"),
    ]
    sector_items = [
        (stock.industry_strength_score <= 40, 8, "weak_sector"),
        (stock.industry_rank_percentile >= .65, 5, "sector_lagging_rank"),
        (stock.same_industry_strong_count <= 1, 3, "few_strong_peers"),
    ]
    def points(items: Sequence[tuple[bool, int | float, str]]) -> tuple[float, list[str]]:
        return sum(weight for passed, weight, _ in items if passed), [reason for passed, _, reason in items if passed]
    trend, trend_reasons = points(trend_items)
    momentum, momentum_reasons = points(momentum_items)
    volume, volume_reasons = points(volume_items)
    sector, sector_reasons = points(sector_items)
    components = {
        "market_short": max(0, market_score),
        "trend_short": min(28, trend),
        "momentum_short": min(35, momentum),
        "volume_short": min(18, volume),
        "sector_short": min(19, sector),
    }
    total = round(max(0, min(100, sum(components.values()) + min(6, max(0, -stock.gap_percent)))), 2)
    reasons = tuple([*trend_reasons, *momentum_reasons, *volume_reasons, *sector_reasons])
    risks: list[str] = []
    if regime == "BREAKOUT":
        risks.append("strong_bull_blocks_short")
    if stock.price >= (stock.ma20 or stock.price * 2) and regime != "CRASH":
        risks.append("not_below_major_average")
    if (stock.volume_ratio_20d or 0) < .8:
        risks.append("volume_too_light_for_short")
    status = "can_enter" if total >= 80 and not risks[:1] else "breakout_watch"
    return StrategyScore(total, components, reasons, tuple(risks), status, 0)


def _health(stock: AdaptiveStockInput, regime: str) -> tuple[float, dict[str, float], list[str]]:
    trend = min(20, sum([
        7 if stock.ma20 is not None and stock.price >= stock.ma20 else 0,
        5 if stock.ma60 is not None and stock.price >= stock.ma60 else 0,
        4 if (stock.ma20_slope or -1) >= 0 else 0,
        4 if stock.higher_low else 0,
    ]))
    momentum = min(15, sum([5 if stock.return_5d > 0 else 0, 5 if (stock.rsi14 or 0) >= 45 else 0, 5 if stock.macd_histogram_rising else 0]))
    volume = min(15, sum([6 if stock.down_volume_less_than_up else 0, 5 if (stock.volume_ratio_20d or 0) >= 1 else 0, 4 if stock.volume_contracting else 0]))
    chip_values = [stock.foreign_net_5d, stock.trust_net_5d, stock.holder_400_change, stock.holder_1000_change]
    chip = min(15, sum(3.75 for value in chip_values if value is not None and value >= 0))
    industry = min(15, stock.industry_strength_score * .15)
    market = 10 if regime in {"RECOVERY", "RANGE", "BREAKOUT"} else 0 if regime == "CRASH" else 4
    fundamental_values = [stock.revenue_yoy, stock.latest_eps, stock.trailing_eps]
    fundamental = min(10, sum(3 for value in fundamental_values if value is not None and value > 0) + (1 if not stock.fundamental_risk else 0))
    breakdown = {"趨勢": trend, "動能": momentum, "成交量": volume, "籌碼": chip, "電子次產業強度": industry, "大盤配合度": market, "基本面": fundamental}
    missing = []
    if all(value is None for value in chip_values): missing.append("法人／大戶籌碼資料不足，籌碼分數不加分")
    if all(value is None for value in fundamental_values): missing.append("基本面資料不足，基本面分數不加分")
    return round(sum(breakdown.values()), 2), breakdown, missing


def _status_code(label: str) -> str:
    return next((key for key, value in STATUS_LABELS.items() if value == label), "waiting_confirmation")


def _display_candidate_status(status: str, trade_date, at: datetime) -> str:
    if status == "can_enter" and not adaptive_entry_window_open(at, True, trade_date):
        return "next_day_watch"
    return status


def _selection_strategy(evaluation: RegimeEvaluation, payload: AdaptiveScanPayload) -> str:
    """Keep screening in UNCERTAIN mode, but never promote it to an entry signal."""
    if evaluation.regime != "UNCERTAIN":
        return evaluation.regime
    market = payload.market
    if (
        (market.electronic_return_20d is not None and market.electronic_return_20d <= -8)
        or market.electronic_above_ma60 is False
    ):
        return "RECOVERY"
    if (
        market.taiex_above_ma20 is True
        and (market.advance_ratio or 0) >= 55
    ):
        return "BREAKOUT"
    if market.taiex_return_20d is not None and abs(market.taiex_return_20d) <= 5:
        return "RANGE"
    return "RECOVERY"


def _active_trading_strategy(evaluation: RegimeEvaluation, payload: AdaptiveScanPayload, trading_regime: str) -> str:
    """Use the intraday override for live day-trading when it is decisive."""
    if payload.market.market_open and trading_regime in {"BREAKOUT", "RECOVERY", "RANGE", "CRASH"}:
        return trading_regime
    return _selection_strategy(evaluation, payload)


def _persist_regime(
    db: Session,
    payload: AdaptiveScanPayload,
    evaluation: RegimeEvaluation,
    previous: MarketRegime | None,
) -> MarketRegime:
    current = db.scalar(select(MarketRegime).where(MarketRegime.trade_date == payload.market.trade_date))
    prior = db.scalar(select(MarketRegime).where(
        MarketRegime.trade_date < payload.market.trade_date,
    ).order_by(MarketRegime.trade_date.desc()).limit(1))
    switched = previous is None or previous.regime != evaluation.regime
    confirmation = (
        (prior.confirmation_days + 1)
        if prior and prior.provisional_regime == evaluation.provisional_regime
        else 1
    )
    values = {
        "regime": evaluation.regime, "provisional_regime": evaluation.provisional_regime,
        "regime_score": _decimal(evaluation.confidence), "taiex_score": _decimal(evaluation.scores["taiex"]),
        "otc_score": _decimal(evaluation.scores["otc"]), "electronic_index_score": _decimal(evaluation.scores["electronic"]),
        "breadth_score": _decimal(evaluation.scores["breadth"]), "volume_score": _decimal(evaluation.scores["volume"]),
        "institutional_score": _decimal(evaluation.scores["institutional"]), "volatility_score": _decimal(evaluation.scores["volatility"]),
        "confirmation_days": confirmation, "recommended_exposure_min": _decimal(evaluation.exposure_min),
        "recommended_exposure_max": _decimal(evaluation.exposure_max), "trigger_reasons": _json(evaluation.reasons),
        "indicators_json": _json(payload.market.model_dump(mode="json")),
        "source_status_json": _json(payload.market.source_status), "missing_fields_json": _json(payload.market.missing_fields),
        "evaluated_at": payload.market.updated_at, "is_current": True,
        "switched_at": payload.market.updated_at if switched else previous.switched_at if previous else payload.market.updated_at,
    }
    db.execute(delete(MarketRegime).where(MarketRegime.is_current.is_(True), MarketRegime.trade_date != payload.market.trade_date))
    if current is None:
        current = MarketRegime(trade_date=payload.market.trade_date, **values)
        db.add(current)
    else:
        for key, value in values.items(): setattr(current, key, value)
    return current


def process_adaptive_scan(db: Session, payload: AdaptiveScanPayload) -> dict[str, Any]:
    parameters = load_parameters(db)
    previous = db.scalar(select(MarketRegime).order_by(MarketRegime.trade_date.desc()).limit(1))
    previous_for_confirmation = previous if previous and previous.trade_date < payload.market.trade_date else None
    evaluation = evaluate_market_regime(
        payload.market, parameters,
        previous_regime=previous_for_confirmation.regime if previous_for_confirmation else previous.regime if previous else None,
        previous_provisional=previous_for_confirmation.provisional_regime if previous_for_confirmation else None,
        previous_confirmation_days=previous_for_confirmation.confirmation_days if previous_for_confirmation else 0,
    )
    regime_row = _persist_regime(db, payload, evaluation, previous)
    trading_regime = intraday_regime_override(payload.market, evaluation.regime)
    entry_window_open = adaptive_entry_window_open(
        payload.market.updated_at,
        payload.market.market_open,
        payload.market.trade_date,
    )

    strengths = rank_industries(payload.industries)
    strength_map = {item.sub_industry: item for item in strengths}
    for item in strengths:
        stored = db.scalar(select(ElectronicIndustryStrength).where(
            ElectronicIndustryStrength.trade_date == payload.market.trade_date,
            ElectronicIndustryStrength.sub_industry == item.sub_industry,
        ))
        source = next(value for value in payload.industries if value.sub_industry == item.sub_industry)
        values = {
            "return_1d": source.return_1d, "return_3d": source.return_3d,
            "return_5d": source.return_5d, "return_20d": source.return_20d,
            "advance_ratio": source.advance_ratio, "new_high_ratio": source.new_high_ratio,
            "volume_growth": source.volume_growth, "foreign_net_buy": source.foreign_net_buy,
            "investment_trust_net_buy": source.investment_trust_net_buy,
            "large_holder_change": source.large_holder_change, "strength_score": _decimal(item.score),
            "strength_rank": item.rank, "continuation_days": item.continuation_days,
            "score_breakdown_json": _json(item.breakdown), "updated_at": payload.market.updated_at,
        }
        if stored is None:
            db.add(ElectronicIndustryStrength(trade_date=payload.market.trade_date, sub_industry=item.sub_industry, **values))
        else:
            for key, value in values.items(): setattr(stored, key, value)

    scored: list[tuple[AdaptiveStockInput, str, StrategyScore, float, dict[str, float], list[str]]] = []
    selection_strategy = _active_trading_strategy(evaluation, payload, trading_regime)
    for stock in payload.stocks:
        mapping = db.scalar(select(ElectronicIndustryMapping).where(ElectronicIndustryMapping.stock_code == stock.stock_code))
        mapping_values = {
            "stock_name": stock.stock_name, "market_type": stock.market_type,
            "industry_code": stock.industry_code, "main_industry": stock.main_industry,
            "sub_industry": stock.sub_industry, "listing_date": stock.listing_date,
            "is_electronic": stock.is_electronic, "is_enabled": True,
            "source": "TWSE/TPEx 公司基本資料", "updated_at": payload.market.updated_at,
        }
        if mapping is None:
            db.add(ElectronicIndustryMapping(stock_code=stock.stock_code, **mapping_values))
        else:
            for key, value in mapping_values.items(): setattr(mapping, key, value)
        failures = common_filter_failures(stock, parameters, payload.market.trade_date)
        if failures:
            continue
        strength = strength_map.get(stock.sub_industry)
        if strength:
            stock.industry_strength_score = strength.score
            stock.industry_rank_percentile = strength.rank / max(1, len(strengths))
        strategy_key = selection_strategy
        if strategy_key == "CRASH":
            result = CrashRecoveryStrategy().evaluate(stock, parameters)
            result = StrategyScore(result.total, result.components, result.reasons, result.risks, "市場風險過高", result.false_breakout_risk)
            minimum = 60
        else:
            strategy = STRATEGIES[strategy_key]
            result = strategy.evaluate(stock, parameters)
            minimum = parameters[f"{strategy_key.lower()}.observation_score"]
        if trading_regime == "UNCERTAIN":
            result = StrategyScore(
                result.total,
                result.components,
                (*result.reasons, "市場狀態尚未確認，僅列入盤中候選監控"),
                result.risks,
                "等待確認",
                result.false_breakout_risk,
            )
        health, health_breakdown, missing = _health(stock, evaluation.regime)
        if result.total >= minimum:
            scored.append((stock, strategy_key, result, health, health_breakdown, missing))
        if trading_regime not in {"BREAKOUT", "RECOVERY"}:
            short_result = _short_score(stock, trading_regime)
            short_minimum = 55 if trading_regime == "CRASH" else 62 if trading_regime in {"RANGE", "UNCERTAIN"} else 70
            if short_result.total >= short_minimum:
                scored.append((stock, "CRASH", short_result, health, health_breakdown, missing))

    scored.sort(key=lambda row: (row[2].total, row[3], row[0].industry_strength_score), reverse=True)
    maximum = int(parameters["monitor.maximum_candidates"])
    db.execute(delete(AdaptiveStockCandidate).where(AdaptiveStockCandidate.trade_date == payload.market.trade_date))
    candidates: list[AdaptiveStockCandidate] = []
    deduped: list[tuple[AdaptiveStockInput, str, StrategyScore, float, dict[str, float], list[str]]] = []
    seen_strategy_symbols: set[tuple[str, str]] = set()
    for item in scored:
        key = (item[0].stock_code, item[1])
        if key in seen_strategy_symbols:
            continue
        seen_strategy_symbols.add(key)
        deduped.append(item)
        if len(deduped) >= maximum:
            break
    for rank, (stock, candidate_strategy, result, health, health_breakdown, missing) in enumerate(deduped, 1):
        levels = _candidate_levels(stock, candidate_strategy)
        status = _status_code(result.status)
        # A MIS five-level reference price is official market data and may be
        # shown for observation, but it is not an executed trade price.
        if (
            (stock.quote_source != "TWSE MIS" and stock.quote_source.startswith("TWSE MIS"))
            or stock.quote_source.startswith("Yahoo Finance")
        ):
            status = "waiting_confirmation"
        if candidate_strategy == "CRASH":
            status = "can_enter" if result.total >= 80 and entry_window_open else "breakout_watch"
        elif evaluation.regime == "CRASH": status = "market_risk_high"
        elif not entry_window_open: status = "next_day_watch"
        if result.status == "可以進場" and health < parameters.get("recovery.entry_health_minimum", 75):
            status = "waiting_confirmation" if entry_window_open else "next_day_watch"
        if candidate_strategy == "CRASH":
            status = "can_enter" if result.total >= 80 and entry_window_open else "breakout_watch"
        selected_reasons = list(result.reasons[:12])
        if status == "next_day_watch":
            selected_reasons.append("已超過 12:00 新進場截止時間，隔日開盤後必須重新確認")
        candidate = AdaptiveStockCandidate(
            trade_date=payload.market.trade_date, stock_code=stock.stock_code,
            stock_name=stock.stock_name, market_type=stock.market_type,
            main_industry=stock.main_industry, sub_industry=stock.sub_industry,
            strategy_type=candidate_strategy, total_score=_decimal(result.total),
            technical_score=_decimal(sum(value for key, value in result.components.items() if key not in {"法人與大戶籌碼", "營收與基本面", "電子次產業強度", "基本面與產業題材"})),
            chip_score=_decimal(result.components.get("法人與大戶籌碼", result.components.get("籌碼穩定度", 0))),
            fundamental_score=_decimal(result.components.get("營收與基本面", result.components.get("基本面與營收", result.components.get("基本面與產業題材", 0)))),
            industry_score=_decimal(result.components.get("電子次產業強度", stock.industry_strength_score / 10)),
            market_score=_decimal(evaluation.confidence / 10), health_score=_decimal(health),
            current_price=_decimal(stock.price), entry_price_low=_decimal(levels["entry_low"]),
            entry_price_high=_decimal(levels["entry_high"]), breakout_price=_decimal(levels["breakout"]),
            stop_loss_price=_decimal(levels["stop"]), target_price_1=_decimal(levels["target1"]),
            target_price_2=_decimal(levels["target2"]), allocation_percent=_decimal(allocation_percent(trading_regime, result.total)),
            relative_strength=_decimal(stock.relative_strength_market),
            volume_status="量縮整理" if stock.volume_contracting else "量能放大" if (stock.volume_ratio_20d or 0) >= 1.5 else "量能正常",
            foreign_net_buy=stock.foreign_net_5d, investment_trust_net_buy=stock.trust_net_5d,
            holder_400_change=stock.holder_400_change, holder_1000_change=stock.holder_1000_change,
            retail_holder_change=stock.retail_holder_change, margin_change=stock.margin_change,
            industry_strength=_decimal(stock.industry_strength_score), false_breakout_risk=_decimal(result.false_breakout_risk),
            candidate_status=status, rank=rank,
            score_breakdown_json=_json({**result.components, "健康度": health_breakdown}),
            selected_reasons=_json(selected_reasons), risk_reasons=_json(result.risks[:10]),
            missing_data_json=_json(missing), quote_source=stock.quote_source,
            quote_timestamp=stock.quote_timestamp, created_at=payload.market.updated_at,
            updated_at=payload.market.updated_at,
        )
        db.add(candidate)
        candidates.append(candidate)
    db.flush()

    priority = int(parameters["monitor.priority_candidates"])
    for candidate in candidates[:priority]:
        monitor = db.scalar(select(AdaptiveStockMonitoring).where(
            AdaptiveStockMonitoring.user_id == AUTOMATION_USER_ID,
            AdaptiveStockMonitoring.stock_code == candidate.stock_code,
        ))
        if monitor is None:
            monitor = AdaptiveStockMonitoring(
                user_id=AUTOMATION_USER_ID, stock_code=candidate.stock_code,
                stock_name=candidate.stock_name, strategy_type=candidate.strategy_type,
                added_date=payload.market.trade_date, trigger_price=candidate.breakout_price,
                entry_price=None, stop_loss_price=candidate.stop_loss_price,
                target_price_1=candidate.target_price_1, target_price_2=candidate.target_price_2,
                allocation_percent=candidate.allocation_percent, health_score=candidate.health_score,
                monitor_status="monitoring", updated_at=payload.market.updated_at,
            )
            db.add(monitor)
        else:
            monitor.strategy_type = candidate.strategy_type
            monitor.trigger_price = candidate.breakout_price
            monitor.stop_loss_price = candidate.stop_loss_price
            monitor.target_price_1 = candidate.target_price_1
            monitor.target_price_2 = candidate.target_price_2
            monitor.allocation_percent = candidate.allocation_percent
            monitor.health_score = candidate.health_score
            monitor.monitor_status = "monitoring"
            monitor.updated_at = payload.market.updated_at

    signals: list[AdaptiveSignal] = []
    if previous and previous.regime != evaluation.regime:
        signal = AdaptiveSignal(
            signal_key=f"regime:{payload.market.trade_date}:{evaluation.regime}",
            signal_type="market_regime_changed", action=f"市場切換為 {evaluation.regime}",
            strategy_type=evaluation.regime, reasons_json=_json(evaluation.reasons[:8]),
            line_push_status="pending", created_at=payload.market.updated_at,
        )
        if db.scalar(select(AdaptiveSignal.id).where(AdaptiveSignal.signal_key == signal.signal_key)) is None:
            db.add(signal); signals.append(signal)
    for candidate in candidates[:priority]:
        signal_type = (
            "entry_confirmed"
            if candidate.candidate_status == "can_enter" and entry_window_open
            else "next_day_watch"
            if candidate.candidate_status == "next_day_watch"
            else "new_top5"
        )
        key = f"adaptive:{payload.market.trade_date}:{candidate.stock_code}:{signal_type}"
        if db.scalar(select(AdaptiveSignal.id).where(AdaptiveSignal.signal_key == key)) is not None:
            continue
        signal = AdaptiveSignal(
            signal_key=key, stock_code=candidate.stock_code, stock_name=candidate.stock_name,
            signal_type=signal_type,
            action=(
                "符合進場條件"
                if signal_type == "entry_confirmed"
                else "收盤後列入隔日觀察"
                if signal_type == "next_day_watch"
                else "進入前 5 名監控"
            ),
            strategy_type=candidate.strategy_type, price=candidate.current_price,
            health_score=candidate.health_score, reasons_json=candidate.selected_reasons,
            line_push_status="pending", created_at=payload.market.updated_at,
        )
        db.add(signal); signals.append(signal)
    db.flush()
    signals.extend(update_adaptive_paper_trades(
        db,
        payload,
        candidates,
        signals,
        trading_regime,
    ))
    db.commit()
    return {
        "regime": regime_payload(regime_row),
        "candidateCount": len(candidates), "priorityCount": min(priority, len(candidates)),
        "signalIds": [item.signal_key for item in signals],
        "dataSources": payload.data_sources,
    }


def regime_payload(item: MarketRegime) -> dict[str, Any]:
    return {
        "tradeDate": item.trade_date.isoformat(), "regime": item.regime,
        "regimeLabel": STRATEGY_NAMES[item.regime], "provisionalRegime": item.provisional_regime,
        "confidence": float(item.regime_score),
        "activeStrategy": "盤勢不明・持續選股監控" if item.regime == "UNCERTAIN" else STRATEGY_NAMES[item.regime],
        "exposureMin": float(item.recommended_exposure_min), "exposureMax": float(item.recommended_exposure_max),
        "reasons": _loads(item.trigger_reasons, []), "indicators": _loads(item.indicators_json, {}),
        "sourceStatus": _loads(item.source_status_json, {}), "missingFields": _loads(item.missing_fields_json, []),
        "confirmationDays": item.confirmation_days,
        "lastSwitchedAt": item.switched_at.isoformat() if item.switched_at else None,
        "updatedAt": item.evaluated_at.isoformat(),
    }


def _normalize_entry_cutoff_reasons(values: list[Any]) -> list[Any]:
    normalized: list[Any] = []
    for value in values:
        if isinstance(value, str):
            normalized.append(value.replace("13:20 新進場截止時間", "12:00 新進場截止時間"))
        else:
            normalized.append(value)
    return normalized


def candidate_payload(item: AdaptiveStockCandidate) -> dict[str, Any]:
    status = _display_candidate_status(item.candidate_status, item.trade_date, datetime.now(UTC))
    current_price = float(item.current_price)
    stop_loss_price = float(item.stop_loss_price)
    stop_distance_pct = (
        abs(current_price - stop_loss_price) / current_price * 100
        if current_price > 0
        else 0
    )
    return {
        "rank": item.rank, "stockCode": item.stock_code, "stockName": item.stock_name,
        "marketType": item.market_type, "mainIndustry": item.main_industry,
        "subIndustry": item.sub_industry, "strategyType": item.strategy_type,
        "strategyName": STRATEGY_NAMES.get(item.strategy_type, item.strategy_type),
        "totalScore": float(item.total_score), "healthScore": float(item.health_score),
        "previousHealthScore": float(item.previous_health_score) if item.previous_health_score is not None else None,
        "currentPrice": float(item.current_price), "entryPriceLow": float(item.entry_price_low),
        "entryPriceHigh": float(item.entry_price_high), "breakoutPrice": float(item.breakout_price),
        "stopLossPrice": stop_loss_price, "stopDistancePct": round(stop_distance_pct, 2),
        "targetPrice1": float(item.target_price_1),
        "targetPrice2": float(item.target_price_2), "allocationPercent": float(item.allocation_percent),
        "relativeStrength": float(item.relative_strength), "volumeStatus": item.volume_status,
        "foreignNetBuy": float(item.foreign_net_buy) if item.foreign_net_buy is not None else None,
        "investmentTrustNetBuy": float(item.investment_trust_net_buy) if item.investment_trust_net_buy is not None else None,
        "holder400Change": float(item.holder_400_change) if item.holder_400_change is not None else None,
        "holder1000Change": float(item.holder_1000_change) if item.holder_1000_change is not None else None,
        "retailHolderChange": float(item.retail_holder_change) if item.retail_holder_change is not None else None,
        "marginChange": float(item.margin_change) if item.margin_change is not None else None,
        "industryStrength": float(item.industry_strength), "falseBreakoutRisk": float(item.false_breakout_risk),
        "status": status, "statusLabel": STATUS_LABELS.get(status, status),
        "scoreBreakdown": _loads(item.score_breakdown_json, {}),
        "selectedReasons": _normalize_entry_cutoff_reasons(_loads(item.selected_reasons, [])),
        "riskReasons": _normalize_entry_cutoff_reasons(_loads(item.risk_reasons, [])),
        "missingData": _loads(item.missing_data_json, []), "quoteSource": item.quote_source,
        "quoteTimestamp": item.quote_timestamp.isoformat(), "updatedAt": item.updated_at.isoformat(),
    }
