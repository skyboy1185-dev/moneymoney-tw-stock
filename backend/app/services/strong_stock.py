from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
import json
from typing import Iterable
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..adaptive_schemas import AdaptiveIndustryInput, AdaptiveMarketMetrics, AdaptiveScanPayload, AdaptiveStockInput
from ..strong_stock_models import (
    StrongStockAccount, StrongStockAuditEvent, StrongStockDataRun, StrongStockEquitySnapshot,
    StrongStockIndustryRanking, StrongStockMarketRegime, StrongStockNotification, StrongStockOrder,
    StrongStockPosition, StrongStockRanking, StrongStockSetting, StrongStockStrategyVersion,
    StrongStockTrade,
)


STRATEGY_ID = "STRONG_STOCK"
STRATEGY_VERSION = "1.0.0"
ZERO = Decimal("0")
CENT = Decimal("0.01")

DEFAULT_CONFIG: dict[str, object] = {
    "initialCapital": "3000000", "paperAutoTrade": True, "minimumPrice": "10",
    "minimumAverageTurnover20d": "100000000", "minimumDataCompleteness": "0.85",
    "minimumWatchScore": "70", "minimumEntryScore": "80", "minimumRiskReward": "2",
    "maximumDailyRisePct": "6", "maximumDistanceMa20Pct": "12",
    "minimumBreakoutVolumeRatio": "1.5", "maximumPositionCapital": "450000",
    "initialPositionCapital": "300000", "maximumPositionPct": "15",
    "maximumIndustryPct": "30", "maximumOpenPositions": 8, "targetMinimumPositions": 5,
    "riskPerTrade": "15000", "commissionRate": "0.001425", "commissionDiscount": "0.2",
    "minimumCommission": "20", "taxRate": "0.003", "slippageBps": "5",
    "allowOddLots": True, "firstEntryPct": 30, "confirmationAddPct": 30,
    "trendAddPct": 40, "maximumHoldingDays": 60, "minimumHoldingDays": 5,
    "strongExposurePct": "80", "mildExposurePct": "60", "rangeExposurePct": "35",
    "weakExposurePct": "10", "dataReadyStartTime": "14:30:00",
    "dataReadyEndTime": "22:00:00", "dataReadyPollMinutes": 15,
    "benchmarkEtf": "0050", "emailEnabled": True,
}


REGIME_LABELS = {
    "STRONG_BULL": "強勢多頭", "MILD_BULL": "溫和多頭",
    "RANGE": "區間震盪", "WEAK": "弱勢或空頭", "DATA_INSUFFICIENT": "資料不足",
}


def dec(value: object, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _json(value: str, fallback):
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def merged_config(raw: dict[str, object] | None = None) -> dict[str, object]:
    return {**DEFAULT_CONFIG, **(raw or {})}


def ensure_defaults(db: Session, user_id: str) -> tuple[StrongStockSetting, StrongStockAccount, dict[str, object]]:
    setting = db.get(StrongStockSetting, user_id)
    if setting is None:
        setting = StrongStockSetting(user_id=user_id, config_json=json.dumps(DEFAULT_CONFIG, ensure_ascii=False))
        db.add(setting)
    account = db.get(StrongStockAccount, user_id)
    if account is None:
        account = StrongStockAccount(user_id=user_id)
        db.add(account)
    version = db.scalar(select(StrongStockStrategyVersion).where(
        StrongStockStrategyVersion.strategy_id == STRATEGY_ID,
        StrongStockStrategyVersion.version == STRATEGY_VERSION,
    ))
    if version is None:
        version = StrongStockStrategyVersion(
            strategy_id=STRATEGY_ID, version=STRATEGY_VERSION,
            parameters_json=json.dumps(DEFAULT_CONFIG, ensure_ascii=False),
            data_requirements_json=json.dumps({
                "prices": "point-in-time daily OHLCV", "fundamentals": "published_at required",
                "universe": "historical listed and delisted", "corporateActions": "effective dates required",
            }, ensure_ascii=False), activated_at=datetime.now(UTC),
        )
        db.add(version)
    db.commit()
    return setting, account, merged_config(_json(setting.config_json, {}))


@dataclass(frozen=True)
class MarketDecision:
    regime: str
    confidence: Decimal
    exposure_pct: Decimal
    reasons: tuple[str, ...]


def classify_market(market: AdaptiveMarketMetrics, config: dict[str, object]) -> MarketDecision:
    required = (market.taiex_above_ma20, market.taiex_above_ma60, market.ma20_slope, market.ma60_slope, market.advance_ratio)
    if sum(value is not None for value in required) < 4 or not market.official_data:
        return MarketDecision("DATA_INSUFFICIENT", Decimal("0"), Decimal("0"), ("大盤日線或市場廣度資料不足",))
    reasons: list[str] = []
    advance = dec(market.advance_ratio)
    ma20_up = dec(market.ma20_slope) > 0
    ma60_up = dec(market.ma60_slope) >= 0
    new_highs = dec(market.new_high_20d_ratio)
    if market.taiex_above_ma20 and market.taiex_above_ma60 and ma20_up and ma60_up and advance >= Decimal("55"):
        reasons.extend(("加權指數站上MA20與MA60", "MA20與MA60斜率向上", "上漲家數明顯較多"))
        if new_highs >= 10:
            reasons.append("創20日新高股票比例增加")
        return MarketDecision("STRONG_BULL", Decimal("85"), dec(config["strongExposurePct"]), tuple(reasons))
    if market.taiex_above_ma60 and dec(market.ma20_slope) >= 0 and advance >= Decimal("48"):
        return MarketDecision("MILD_BULL", Decimal("72"), dec(config["mildExposurePct"]), ("加權指數位於MA60之上", "市場廣度中性偏多"))
    if not market.taiex_above_ma60 and dec(market.ma20_slope) < 0 and dec(market.ma60_slope) < 0 and advance < Decimal("45"):
        return MarketDecision("WEAK", Decimal("84"), dec(config["weakExposurePct"]), ("加權指數跌破MA60", "中期均線轉弱", "下跌家數較多"))
    return MarketDecision("RANGE", Decimal("65"), dec(config["rangeExposurePct"]), ("指數與市場廣度缺乏一致方向", "提高進場門檻並保留現金"))


def industry_score(row: AdaptiveIndustryInput) -> tuple[Decimal, dict[str, object]]:
    values: dict[str, object] = {
        "return5": max(-10.0, min(15.0, float(row.return_5d or 0))),
        "return20": max(-20.0, min(30.0, float(row.return_20d or 0))),
        "relativeMarket": max(-20.0, min(25.0, float(row.relative_taiex or 0))),
        "breadth": max(0.0, min(100.0, float(row.advance_ratio or 0))),
        "newHigh": max(0.0, min(100.0, float(row.new_high_ratio or 0))),
        "volume": max(0.0, min(3.0, float(row.volume_growth or 0))),
    }
    return5 = float(str(values["return5"])); return20 = float(str(values["return20"]))
    relative_market = float(str(values["relativeMarket"])); breadth = float(str(values["breadth"]))
    new_high = float(str(values["newHigh"])); volume_growth = float(str(values["volume"]))
    score = 45 + return5 * .7 + return20 * .55 + relative_market * .8
    score += (breadth - 50) * .25 + new_high * .1 + min(10, volume_growth * 4)
    if row.continuation_days >= 3:
        score += 5
    return dec(round(max(0, min(100, score)), 2)), values


def percentile_scores(stocks: Iterable[AdaptiveStockInput]) -> dict[str, Decimal]:
    rows = sorted(stocks, key=lambda stock: (getattr(stock, "return_60d", stock.return_20d), stock.return_20d, stock.return_5d))
    denominator = max(1, len(rows) - 1)
    return {stock.stock_code: dec(round(index / denominator * 100, 2)) for index, stock in enumerate(rows)}


@dataclass(frozen=True)
class CandidateScore:
    symbol: str
    total: Decimal
    relative: Decimal
    trend: Decimal
    industry: Decimal
    volume_chip: Decimal
    fundamental: Decimal
    valuation_risk: Decimal
    completeness: Decimal
    entry_type: str
    status: str
    entry_low: Decimal
    entry_high: Decimal
    breakout_price: Decimal
    pullback_price: Decimal
    stop_price: Decimal
    add_price: Decimal
    risk_reward: Decimal
    suggested_capital: Decimal
    reasons: tuple[str, ...]
    blocked_reasons: tuple[str, ...]
    details: dict[str, object]


def score_stock(
    stock: AdaptiveStockInput, rs_percentile: Decimal, sector_score: Decimal,
    market: MarketDecision, config: dict[str, object], sector_top20: bool,
) -> CandidateScore:
    reasons: list[str] = []
    blocked: list[str] = []
    price = dec(stock.price)
    ma20 = dec(stock.ma20)
    ma60 = dec(stock.ma60)
    atr = dec(stock.atr14 or price * Decimal("0.04"))

    relative = min(Decimal("25"), max(ZERO, rs_percentile / Decimal("4")))
    trend_checks = [price > ma20 > 0, ma20 > ma60 > 0, dec(stock.ma20_slope) > 0, dec(stock.ma60_slope) >= 0]
    trend = Decimal(sum(trend_checks)) * Decimal("4")
    trend += Decimal("2") if stock.breakout_20d else ZERO
    trend += Decimal("2") if stock.breakout_60d else ZERO
    trend = min(Decimal("20"), trend)
    industry_component = min(Decimal("15"), sector_score * Decimal("0.15"))
    volume = Decimal("5")
    volume += min(Decimal("5"), max(ZERO, dec(stock.volume_ratio_20d) - Decimal("1")) * Decimal("5"))
    volume += Decimal("2") if stock.volume_contracting else ZERO
    volume += Decimal("1.5") if (stock.foreign_net_5d or 0) > 0 else ZERO
    volume += Decimal("1.5") if (stock.trust_net_5d or 0) > 0 else ZERO
    volume = min(Decimal("15"), volume)
    fundamental_fields = [stock.revenue_yoy, stock.revenue_3m_yoy, stock.trailing_eps, stock.gross_margin_change, stock.operating_margin_change]
    fundamental_coverage = Decimal(sum(value is not None for value in fundamental_fields)) / Decimal(len(fundamental_fields))
    fundamental = Decimal("4")
    fundamental += Decimal("3") if (stock.revenue_yoy or 0) > 0 else ZERO
    fundamental += Decimal("3") if (stock.revenue_3m_yoy or 0) > 0 else ZERO
    fundamental += Decimal("3") if (stock.trailing_eps or 0) > 0 else ZERO
    fundamental += Decimal("2") if not stock.fundamental_risk else Decimal("-4")
    fundamental = min(Decimal("15"), max(ZERO, fundamental))
    valuation = Decimal("6")
    volatility = dec(stock.atr20_ratio)
    if volatility and volatility <= Decimal("5"):
        valuation += Decimal("2")
    if stock.distance_to_high_percent is not None and stock.distance_to_high_percent <= 15:
        valuation += Decimal("2")
    valuation = min(Decimal("10"), valuation)

    completeness = dec(stock.data_completeness) * Decimal("0.70") + fundamental_coverage * Decimal("0.30")
    total = min(Decimal("100"), relative + trend + industry_component + volume + fundamental + valuation)
    if rs_percentile >= 80:
        reasons.append("60日相對強度位於市場前20%")
    if sector_top20:
        reasons.append("所屬產業位於強勢排名前20%")
    if price > ma20 > ma60 > 0:
        reasons.append("收盤價、MA20與MA60維持多頭排列")
    if stock.breakout_20d or stock.breakout_60d:
        reasons.append("股價突破中期平台")

    if stock.is_full_delivery or stock.is_alternate_trading or stock.is_disposed or stock.is_suspended or stock.is_delisted or stock.abnormal_trading:
        blocked.append("股票位於排除名單")
    if price < dec(config["minimumPrice"]):
        blocked.append("股價低於最低門檻")
    if dec(stock.average_turnover_20d) < dec(config["minimumAverageTurnover20d"]):
        blocked.append("最近20日平均成交金額不足1億元")
    if (stock.trailing_eps is None) or (stock.trailing_eps <= 0):
        blocked.append("最近四季EPS缺少或為負")
    if completeness < dec(config["minimumDataCompleteness"]):
        blocked.append("基本面或歷史資料完整度不足")
    if rs_percentile < 80:
        blocked.append("60日相對強度未達市場前20%")
    if stock.distance_to_high_percent is not None and stock.distance_to_high_percent > 15:
        blocked.append("距離52週高點超過15%")
    if not sector_top20:
        blocked.append("所屬產業未進入前20%")
    if market.regime in {"WEAK", "DATA_INSUFFICIENT"}:
        blocked.append("大盤環境不允許增加一般部位")

    daily_rise = dec(stock.return_1d)
    distance_ma20 = (price / ma20 - 1) * 100 if ma20 > 0 else Decimal("999")
    if daily_rise > dec(config["maximumDailyRisePct"]):
        blocked.append("單日漲幅超過禁止追高門檻")
    if distance_ma20 > dec(config["maximumDistanceMa20Pct"]):
        blocked.append("股價偏離MA20過遠")
    if (stock.upper_shadow_ratio or 0) >= .45 and (stock.volume_ratio_20d or 0) >= 2:
        blocked.append("出現爆量長上影線")

    breakout = bool((stock.breakout_20d or stock.breakout_60d) and dec(stock.volume_ratio_20d) >= dec(config["minimumBreakoutVolumeRatio"]))
    pullback = bool(price > ma60 > 0 and ma20 > ma60 and stock.volume_contracting and (stock.bottom_reversal_candle or stock.higher_low))
    entry_type = "BREAKOUT" if breakout else "PULLBACK" if pullback else "WATCH"
    if entry_type == "WATCH":
        blocked.append("尚未出現合格突破或回踩買點")
    stop_candidates = [value for value in (ma20, ma60, price - atr * 2) if value > 0 and value < price]
    stop = max(stop_candidates) if stop_candidates else price * Decimal("0.92")
    risk = max(Decimal("0.01"), price - stop)
    target = price + risk * Decimal("2")
    rr = (target - price) / risk
    if rr < dec(config["minimumRiskReward"]):
        blocked.append("預估風險報酬比不足1比2")
    if total < dec(config["minimumEntryScore"]):
        blocked.append("綜合評分未達進場門檻")
    status = "ENTRY_READY" if not blocked else "WATCH" if total >= dec(config["minimumWatchScore"]) else "TRACKING"
    if completeness < dec(config["minimumDataCompleteness"]):
        status = "DATA_INSUFFICIENT"
    capital = min(dec(config["initialPositionCapital"]), dec(config["maximumPositionCapital"]))
    details = {
        "relativeStrengthPercentile": str(rs_percentile), "distanceMa20Pct": str(round(distance_ma20, 4)),
        "fundamentalCoverage": str(round(fundamental_coverage, 4)), "marketRegime": market.regime,
        "targetPrice": str(money(target)), "componentWeights": {"relative": 25, "trend": 20, "industry": 15, "volumeChip": 15, "fundamental": 15, "valuationRisk": 10},
    }
    return CandidateScore(
        stock.stock_code, money(total), money(relative), money(trend), money(industry_component),
        money(volume), money(fundamental), money(valuation), completeness.quantize(Decimal("0.0001")),
        entry_type, status, money(price * Decimal("0.99")), money(price * Decimal("1.01")),
        money(max(price, dec(stock.range_high or price))), money(ma20 or price), money(stop),
        money(price + risk), money(rr), money(capital), tuple(reasons), tuple(dict.fromkeys(blocked)), details,
    )


def calculate_position_quantity(capital: Decimal, entry: Decimal, stop: Decimal, risk_limit: Decimal, *, allow_odd_lots: bool = True) -> int:
    if entry <= 0 or stop <= 0 or stop >= entry or capital <= 0 or risk_limit <= 0:
        return 0
    shares = min(int((risk_limit / (entry - stop)).to_integral_value(rounding=ROUND_DOWN)), int((capital / entry).to_integral_value(rounding=ROUND_DOWN)))
    return shares if allow_odd_lots else shares // 1000 * 1000


def commission(notional: Decimal, config: dict[str, object]) -> Decimal:
    return money(max(dec(config["minimumCommission"]), notional * dec(config["commissionRate"]) * dec(config["commissionDiscount"])))


def scan_and_persist(db: Session, payload: AdaptiveScanPayload, at: datetime, config_override: dict[str, object] | None = None) -> dict[str, object]:
    config = merged_config(config_override)
    decision = classify_market(payload.market, config)
    trade_date = payload.market.trade_date
    db.execute(delete(StrongStockMarketRegime).where(StrongStockMarketRegime.trade_date == trade_date))
    db.execute(delete(StrongStockIndustryRanking).where(StrongStockIndustryRanking.trade_date == trade_date))
    db.execute(delete(StrongStockRanking).where(StrongStockRanking.trade_date == trade_date))
    db.add(StrongStockMarketRegime(
        trade_date=trade_date, regime=decision.regime, label=REGIME_LABELS[decision.regime], confidence=decision.confidence,
        suggested_exposure_pct=decision.exposure_pct, reasons_json=json.dumps(decision.reasons, ensure_ascii=False),
        source_snapshot_json=payload.market.model_dump_json(), calculated_at=at,
    ))
    scored_industries = [(row, *industry_score(row)) for row in payload.industries]
    scored_industries.sort(key=lambda item: item[1], reverse=True)
    industry_map: dict[str, tuple[Decimal, int, bool]] = {}
    top_count = max(1, (len(scored_industries) + 4) // 5)
    for index, (row, score, details) in enumerate(scored_industries, start=1):
        industry_map[row.sub_industry] = (score, index, index <= top_count)
        db.add(StrongStockIndustryRanking(
            trade_date=trade_date, industry=row.sub_industry, score=score, rank=index,
            percentile=dec(round((len(scored_industries) - index + 1) / max(1, len(scored_industries)) * 100, 2)),
            member_count=0, details_json=json.dumps(details, ensure_ascii=False), calculated_at=at,
        ))
    eligible = [stock for stock in payload.stocks if stock.has_recent_trade and not stock.is_delisted]
    percentiles = percentile_scores(eligible)
    scored: list[tuple[AdaptiveStockInput, CandidateScore]] = []
    for stock in eligible:
        sector_score, _rank, sector_top = industry_map.get(stock.sub_industry, (dec(stock.industry_strength_score), 999, False))
        scored.append((stock, score_stock(stock, percentiles.get(stock.stock_code, ZERO), sector_score, decision, config, sector_top)))
    scored.sort(key=lambda item: item[1].total, reverse=True)
    for rank, (stock, result) in enumerate(scored, start=1):
        db.add(StrongStockRanking(
            trade_date=trade_date, symbol=stock.stock_code, name=stock.stock_name, market=stock.market_type,
            industry=stock.sub_industry, rank=rank, total_score=result.total,
            relative_strength_score=result.relative, trend_score=result.trend, industry_score=result.industry,
            volume_chip_score=result.volume_chip, fundamental_score=result.fundamental,
            valuation_risk_score=result.valuation_risk, data_completeness=result.completeness,
            close_price=dec(stock.price), high_52w_distance_pct=dec(stock.distance_to_high_percent) if stock.distance_to_high_percent is not None else None,
            entry_low=result.entry_low, entry_high=result.entry_high, breakout_price=result.breakout_price,
            pullback_price=result.pullback_price, stop_price=result.stop_price, add_price=result.add_price,
            risk_reward=result.risk_reward, suggested_capital=result.suggested_capital, status=result.status,
            entry_type=result.entry_type, reasons_json=json.dumps(result.reasons, ensure_ascii=False),
            blocked_reasons_json=json.dumps(result.blocked_reasons, ensure_ascii=False),
            score_details_json=json.dumps(result.details, ensure_ascii=False),
            source_snapshot_json=stock.model_dump_json(), strategy_version=STRATEGY_VERSION, calculated_at=at,
        ))
    db.add(StrongStockDataRun(
        id=str(uuid4()), trade_date=trade_date, status="COMPLETED" if scored else "DATA_INSUFFICIENT",
        source_json=json.dumps(payload.data_sources, ensure_ascii=False),
        missing_json=json.dumps(payload.market.missing_fields, ensure_ascii=False), completed_at=at,
    ))
    db.commit()
    return {"tradeDate": trade_date.isoformat(), "regime": decision.regime, "ranked": len(scored), "entryReady": sum(result.status == "ENTRY_READY" for _, result in scored)}


def queue_paper_orders(db: Session, user_id: str, signal_date: date) -> int:
    setting, account, config = ensure_defaults(db, user_id)
    if not setting.paper_enabled or account.trading_paused or not bool(config["paperAutoTrade"]):
        return 0
    existing_positions = list(db.scalars(select(StrongStockPosition).where(StrongStockPosition.user_id == user_id, StrongStockPosition.status == "OPEN")).all())
    pending_orders = list(db.scalars(select(StrongStockOrder).where(
        StrongStockOrder.user_id == user_id, StrongStockOrder.status == "PENDING",
    )).all())
    pending_symbols = {order.symbol for order in pending_orders}
    capacity = max(0, int(str(config["maximumOpenPositions"])) - len(existing_positions) - len(pending_orders))
    if not capacity:
        return 0
    exposure_limit = dec(config["initialCapital"])
    regime = db.scalar(select(StrongStockMarketRegime).where(StrongStockMarketRegime.trade_date == signal_date))
    if regime:
        exposure_limit *= dec(regime.suggested_exposure_pct) / 100
    invested = sum((position.invested_capital for position in existing_positions), ZERO)
    reserved = sum((order.limit_price * order.quantity for order in pending_orders), ZERO)
    available_exposure = max(ZERO, exposure_limit - invested - reserved)
    available_cash = max(ZERO, account.cash - reserved)
    rows = list(db.scalars(select(StrongStockRanking).where(
        StrongStockRanking.trade_date == signal_date, StrongStockRanking.status == "ENTRY_READY",
    ).order_by(StrongStockRanking.rank).limit(capacity * 3)).all())
    queued = 0
    next_date = signal_date + timedelta(days=1)
    while next_date.weekday() >= 5:
        next_date += timedelta(days=1)
    for row in rows:
        if queued >= capacity or available_exposure <= 0:
            break
        if any(position.symbol == row.symbol for position in existing_positions) or row.symbol in pending_symbols:
            continue
        sector_used = sum((position.invested_capital for position in existing_positions if position.industry == row.industry), ZERO)
        sector_limit = dec(config["initialCapital"]) * dec(config["maximumIndustryPct"]) / 100
        if sector_used >= sector_limit:
            continue
        signal_key = f"{user_id}:{signal_date}:{row.symbol}:{row.entry_type}:{STRATEGY_VERSION}"
        if db.scalar(select(StrongStockOrder.id).where(StrongStockOrder.signal_key == signal_key)):
            continue
        initial_capital = min(row.suggested_capital * dec(config["firstEntryPct"]) / 100, available_exposure, available_cash)
        quantity = calculate_position_quantity(initial_capital, row.close_price, row.stop_price or ZERO, dec(config["riskPerTrade"]), allow_odd_lots=bool(config["allowOddLots"]))
        if quantity <= 0:
            continue
        db.add(StrongStockOrder(
            id=str(uuid4()), user_id=user_id, signal_key=signal_key, symbol=row.symbol, name=row.name,
            limit_price=row.entry_high or row.close_price, quantity=quantity, entry_type=row.entry_type,
            strategy_version=row.strategy_version, reason="、".join(_json(row.reasons_json, [])), valid_date=next_date,
        ))
        available_exposure -= row.close_price * quantity
        available_cash -= row.close_price * quantity
        pending_symbols.add(row.symbol)
        queued += 1
    if queued:
        notify(db, user_id, f"orders:{signal_date}", "SIGNALS_READY", "【強勢股策略｜隔日模擬委託】", f"已建立 {queued} 筆隔日有效的模擬委託。")
    db.commit()
    return queued


def fill_pending_orders(db: Session, user_id: str, prices: dict[str, Decimal], at: datetime) -> dict[str, int]:
    _setting, account, config = ensure_defaults(db, user_id)
    today = at.date()
    filled = expired = 0
    orders = list(db.scalars(select(StrongStockOrder).where(StrongStockOrder.user_id == user_id, StrongStockOrder.status == "PENDING")).all())
    for order in orders:
        if order.valid_date < today:
            order.status = "EXPIRED"; order.completed_at = at; expired += 1; continue
        if order.valid_date != today or order.symbol not in prices or prices[order.symbol] > order.limit_price:
            continue
        price = prices[order.symbol]
        notional = money(price * order.quantity)
        fee = commission(notional, config)
        slippage = money(notional * dec(config["slippageBps"]) / Decimal("10000"))
        if notional + fee + slippage > account.cash:
            order.status = "REJECTED_INSUFFICIENT_CASH"; order.completed_at = at; continue
        ranking = db.scalar(select(StrongStockRanking).where(StrongStockRanking.symbol == order.symbol).order_by(StrongStockRanking.trade_date.desc()).limit(1))
        if ranking is None or ranking.stop_price is None:
            order.status = "REJECTED_DATA"; order.completed_at = at; continue
        position = db.scalar(select(StrongStockPosition).where(StrongStockPosition.user_id == user_id, StrongStockPosition.symbol == order.symbol, StrongStockPosition.status == "OPEN"))
        if position is not None:
            order.status = "REJECTED_DUPLICATE_POSITION"; order.completed_at = at; continue
        position = StrongStockPosition(
            id=str(uuid4()), user_id=user_id, symbol=order.symbol, name=order.name,
            industry=ranking.industry, quantity=order.quantity, average_cost=price, current_price=price,
            initial_stop=ranking.stop_price, trailing_stop=ranking.stop_price, next_add_price=ranking.add_price,
            invested_capital=notional, initial_risk=money((price - ranking.stop_price) * order.quantity),
            current_score=ranking.total_score, entry_type=order.entry_type, strategy_version=order.strategy_version,
            tranches_json=json.dumps([{"pct": order.tranche_pct, "quantity": order.quantity, "price": str(price), "filledAt": at.isoformat()}]),
            reasons_json=ranking.reasons_json, entry_at=at,
        )
        db.add(position)
        account.cash = money(account.cash - notional - fee - slippage)
        order.filled_quantity = order.quantity; order.status = "FILLED"; order.completed_at = at; filled += 1
        notify(db, user_id, f"fill:{order.id}", "PAPER_BUY_FILLED", "【強勢股策略｜模擬買進成交】", f"{order.symbol} {order.name}，{order.quantity}股，成交價 {price} 元。")
    db.commit()
    return {"filled": filled, "expired": expired}


def close_position(db: Session, position: StrongStockPosition, price: Decimal, at: datetime, reason: str, quantity: int | None = None) -> StrongStockTrade:
    _setting, account, config = ensure_defaults(db, position.user_id)
    sell_quantity = min(position.quantity, quantity or position.quantity)
    entry_notional = money(position.average_cost * sell_quantity)
    exit_notional = money(price * sell_quantity)
    buy_fee = commission(entry_notional, config)
    sell_fee = commission(exit_notional, config)
    tax = money(exit_notional * dec(config["taxRate"]))
    slippage = money((entry_notional + exit_notional) * dec(config["slippageBps"]) / Decimal("10000"))
    gross = money((price - position.average_cost) * sell_quantity)
    net = money(gross - buy_fee - sell_fee - tax - slippage)
    trade = StrongStockTrade(
        id=str(uuid4()), user_id=position.user_id, position_id=position.id, symbol=position.symbol,
        name=position.name, industry=position.industry, entry_type=position.entry_type,
        quantity=sell_quantity, entry_price=position.average_cost, exit_price=price,
        entry_at=position.entry_at, exit_at=at, gross_pnl=gross, buy_fee=buy_fee, sell_fee=sell_fee,
        tax=tax, slippage=slippage, net_pnl=net,
        return_pct=money(net / entry_notional * 100) if entry_notional else ZERO,
        strategy_version=position.strategy_version, entry_reason="、".join(_json(position.reasons_json, [])), exit_reason=reason,
    )
    db.add(trade)
    account.cash = money(account.cash + exit_notional - sell_fee - tax - slippage / 2)
    account.realized_pnl = money(account.realized_pnl + net)
    position.quantity -= sell_quantity
    position.invested_capital = money(position.average_cost * position.quantity)
    if position.quantity == 0:
        position.status = "CLOSED"; position.exit_at = at
    notify(db, position.user_id, f"sell:{trade.id}", "PAPER_SELL_FILLED", "【強勢股策略｜模擬賣出成交】", f"{position.symbol} {sell_quantity}股，淨損益 {net:+} 元；{reason}")
    db.commit()
    return trade


def monitor_positions(db: Session, user_id: str, prices: dict[str, Decimal], at: datetime) -> dict[str, int]:
    positions = list(db.scalars(select(StrongStockPosition).where(StrongStockPosition.user_id == user_id, StrongStockPosition.status == "OPEN")).all())
    _setting, account, config = ensure_defaults(db, user_id)
    closed = reduced = added = 0
    for position in positions:
        price = prices.get(position.symbol)
        if price is None:
            continue
        position.current_price = price
        latest_ranking = db.scalar(select(StrongStockRanking).where(
            StrongStockRanking.symbol == position.symbol,
        ).order_by(StrongStockRanking.trade_date.desc()).limit(1))
        if latest_ranking:
            position.current_score = latest_ranking.total_score
            if latest_ranking.stop_price is not None and latest_ranking.stop_price < price:
                position.trailing_stop = max(position.trailing_stop, latest_ranking.stop_price)
        initial_r_per_share = position.average_cost - position.initial_stop
        if price <= position.trailing_stop:
            close_position(db, position, price, at, "觸及技術停損或移動停利")
            closed += 1
        elif initial_r_per_share > 0 and price >= position.average_cost + initial_r_per_share * 2 and position.quantity >= 3 and "2R_REDUCED" not in _json(position.warnings_json, []):
            warnings = _json(position.warnings_json, []) + ["2R_REDUCED"]
            position.warnings_json = json.dumps(warnings, ensure_ascii=False)
            close_position(db, position, price, at, "達到2R，減碼三分之一", max(1, position.quantity // 3))
            position.trailing_stop = max(position.trailing_stop, position.average_cost)
            reduced += 1
        elif position.next_add_price is not None and price >= position.next_add_price and price > position.average_cost:
            tranches = _json(position.tranches_json, [])
            invested_pct = sum(int(row.get("pct", 0)) for row in tranches if isinstance(row, dict))
            add_pct = 30 if invested_pct < 60 else 40 if invested_pct < 100 else 0
            ranking = latest_ranking
            if add_pct and ranking and ranking.total_score >= dec(config["minimumEntryScore"]):
                full_capital = min(dec(config["initialPositionCapital"]), dec(config["maximumPositionCapital"]))
                total_invested = sum((item.invested_capital for item in positions if item.status == "OPEN"), ZERO)
                sector_invested = sum((item.invested_capital for item in positions if item.status == "OPEN" and item.industry == position.industry), ZERO)
                regime = db.scalar(select(StrongStockMarketRegime).order_by(StrongStockMarketRegime.trade_date.desc()).limit(1))
                exposure_limit = dec(config["initialCapital"]) * (dec(regime.suggested_exposure_pct) / 100 if regime else Decimal("1"))
                stock_limit = min(dec(config["maximumPositionCapital"]), dec(config["initialCapital"]) * dec(config["maximumPositionPct"]) / 100)
                sector_limit = dec(config["initialCapital"]) * dec(config["maximumIndustryPct"]) / 100
                add_capital = min(
                    full_capital * add_pct / 100, account.cash,
                    max(ZERO, exposure_limit - total_invested),
                    max(ZERO, stock_limit - position.invested_capital),
                    max(ZERO, sector_limit - sector_invested),
                )
                add_quantity = int((add_capital / price).to_integral_value(rounding=ROUND_DOWN))
                if not bool(config["allowOddLots"]):
                    add_quantity = add_quantity // 1000 * 1000
                combined_risk = (price - position.initial_stop) * add_quantity + position.initial_risk
                if add_quantity > 0 and combined_risk <= dec(config["riskPerTrade"]):
                    notional = money(price * add_quantity); fee = commission(notional, config)
                    slippage = money(notional * dec(config["slippageBps"]) / Decimal("10000"))
                    if notional + fee + slippage <= account.cash:
                        old_quantity = position.quantity
                        position.quantity += add_quantity
                        position.average_cost = money((position.average_cost * old_quantity + price * add_quantity) / position.quantity)
                        position.invested_capital = money(position.average_cost * position.quantity)
                        position.initial_risk = money(combined_risk)
                        tranches.append({"pct": add_pct, "quantity": add_quantity, "price": str(price), "filledAt": at.isoformat()})
                        position.tranches_json = json.dumps(tranches, ensure_ascii=False)
                        position.next_add_price = money(price + max(Decimal("0.01"), price - position.initial_stop)) if invested_pct + add_pct < 100 else None
                        account.cash = money(account.cash - notional - fee - slippage)
                        order = StrongStockOrder(
                            id=str(uuid4()), user_id=user_id,
                            signal_key=f"add:{position.id}:{invested_pct + add_pct}", symbol=position.symbol,
                            name=position.name, limit_price=price, quantity=add_quantity, filled_quantity=add_quantity,
                            status="FILLED", entry_type="ADD", tranche_pct=add_pct,
                            strategy_version=position.strategy_version, reason="原始理由仍成立且突破加碼位置",
                            valid_date=at.date(), completed_at=at,
                        )
                        db.add(order)
                        notify(db, user_id, f"fill:{order.id}", "PAPER_ADD_FILLED", "【強勢股策略｜模擬加碼成交】", f"{position.symbol} 加碼 {add_quantity}股，成交價 {price} 元。")
                        added += 1
    db.commit()
    return {"closed": closed, "reduced": reduced, "added": added}


def notify(db: Session, user_id: str, event_key: str, event_type: str, title: str, message: str, priority: str = "NORMAL") -> None:
    if db.scalar(select(StrongStockNotification.id).where(StrongStockNotification.user_id == user_id, StrongStockNotification.event_key == event_key)):
        return
    db.add(StrongStockNotification(user_id=user_id, event_key=event_key, event_type=event_type, title=title, message=message, priority=priority))


def audit(db: Session, user_id: str, action: str, entity_type: str = "SYSTEM", entity_id: str = "", details: dict[str, object] | None = None) -> None:
    db.add(StrongStockAuditEvent(user_id=user_id, action=action, entity_type=entity_type, entity_id=entity_id, details_json=json.dumps(details or {}, ensure_ascii=False, default=str)))


def performance(db: Session, user_id: str) -> dict[str, object]:
    account = db.get(StrongStockAccount, user_id)
    if account is None:
        return {}
    positions = list(db.scalars(select(StrongStockPosition).where(StrongStockPosition.user_id == user_id, StrongStockPosition.status == "OPEN")).all())
    trades = list(db.scalars(select(StrongStockTrade).where(StrongStockTrade.user_id == user_id).order_by(StrongStockTrade.exit_at)).all())
    market_value = sum((position.current_price * position.quantity for position in positions), ZERO)
    invested = sum((position.average_cost * position.quantity for position in positions), ZERO)
    unrealized = money(market_value - invested)
    equity = money(account.cash + market_value)
    wins = [trade for trade in trades if trade.net_pnl > 0]
    losses = [trade for trade in trades if trade.net_pnl < 0]
    gross_profit = sum((trade.net_pnl for trade in wins), ZERO)
    gross_loss = abs(sum((trade.net_pnl for trade in losses), ZERO))
    peak = dec(account.initial_capital)
    max_drawdown = ZERO
    running = dec(account.initial_capital)
    for trade in trades:
        running += trade.net_pnl; peak = max(peak, running); max_drawdown = max(max_drawdown, peak - running)
    return {
        "initialCapital": str(account.initial_capital), "cash": str(account.cash), "marketValue": str(money(market_value)),
        "totalEquity": str(equity), "realizedPnl": str(account.realized_pnl), "unrealizedPnl": str(unrealized),
        "totalReturnPct": str(money((equity / account.initial_capital - 1) * 100)), "tradeCount": len(trades),
        "winCount": len(wins), "lossCount": len(losses), "winRate": str(money(dec(len(wins)) / len(trades) * 100)) if trades else None,
        "profitFactor": str(money(gross_profit / gross_loss)) if gross_loss else None,
        "maximumDrawdown": str(money(max_drawdown)), "maximumDrawdownPct": str(money(max_drawdown / peak * 100)) if peak else "0",
        "openCount": len(positions), "investedCapital": str(money(invested)),
    }


def strategy_health(db: Session, user_id: str, *, apply_guard: bool = False) -> dict[str, object]:
    trades = list(db.scalars(select(StrongStockTrade).where(StrongStockTrade.user_id == user_id).order_by(StrongStockTrade.exit_at.desc()).limit(50)).all())
    def window(rows: list[StrongStockTrade]) -> dict[str, object]:
        wins = [row for row in rows if row.net_pnl > 0]; losses = [row for row in rows if row.net_pnl < 0]
        gain = sum((row.net_pnl for row in wins), ZERO); loss = abs(sum((row.net_pnl for row in losses), ZERO))
        net = sum((row.net_pnl for row in rows), ZERO)
        return {"tradeCount": len(rows), "netPnl": str(money(net)), "winRate": str(money(dec(len(wins)) / len(rows) * 100)) if rows else None, "profitFactor": str(money(gain / loss)) if loss else None, "expectancy": str(money(net / len(rows))) if rows else None}
    recent20 = window(trades[:20]); recent50 = window(trades[:50])
    reasons: list[str] = []
    status = "SAMPLE_INSUFFICIENT" if len(trades) < 20 else "NORMAL"
    if len(trades) >= 20:
        if dec(recent20["netPnl"]) < 0: reasons.append("最近20筆淨損益為負")
        if recent20["profitFactor"] is not None and dec(recent20["profitFactor"]) < 1: reasons.append("最近20筆獲利因子低於1")
        if dec(recent20["expectancy"]) < 0: reasons.append("最近20筆平均期望值為負")
        if reasons: status = "ALERT"
    if apply_guard and status == "ALERT":
        account = db.get(StrongStockAccount, user_id)
        if account:
            account.trading_paused = True
        notify(db, user_id, f"health-alert:{datetime.now(UTC).date()}", "PERFORMANCE_ALERT", "【強勢股策略｜績效警戒】", "；".join(reasons) + "。已暫停新增模擬交易，既有持倉仍持續風控。", "HIGH")
        audit(db, user_id, "PERFORMANCE_GUARD_APPLIED", details={"reasons": reasons})
        db.commit()
    return {"status": status, "reasons": reasons or (["已完成交易未滿20筆"] if len(trades) < 20 else []), "recent20": recent20, "recent50": recent50, "automaticRiskIncrease": False, "candidateMayTradeLive": False}


def snapshot_equity(db: Session, user_id: str, trade_date: date) -> None:
    metrics = performance(db, user_id)
    if not metrics:
        return
    previous = db.scalar(select(StrongStockEquitySnapshot).where(StrongStockEquitySnapshot.user_id == user_id).order_by(StrongStockEquitySnapshot.trade_date.desc()).limit(1))
    total = dec(metrics["totalEquity"])
    daily = total - (previous.total_equity if previous else dec(metrics["initialCapital"]))
    peak = db.scalar(select(func.max(StrongStockEquitySnapshot.total_equity)).where(StrongStockEquitySnapshot.user_id == user_id)) or dec(metrics["initialCapital"])
    drawdown = (dec(peak) - total) / dec(peak) * 100 if dec(peak) else ZERO
    row = db.scalar(select(StrongStockEquitySnapshot).where(StrongStockEquitySnapshot.user_id == user_id, StrongStockEquitySnapshot.trade_date == trade_date))
    if row is None:
        row = StrongStockEquitySnapshot(user_id=user_id, trade_date=trade_date, cash=dec(metrics["cash"]), market_value=dec(metrics["marketValue"]), total_equity=total, daily_pnl=daily, drawdown_pct=money(drawdown))
        db.add(row)
    else:
        row.cash=dec(metrics["cash"]); row.market_value=dec(metrics["marketValue"]); row.total_equity=total; row.daily_pnl=daily; row.drawdown_pct=money(drawdown)
    db.commit()


def ranking_payload(row: StrongStockRanking) -> dict[str, object]:
    return {
        "id": row.id, "tradeDate": row.trade_date, "rank": row.rank, "symbol": row.symbol, "name": row.name,
        "market": row.market, "industry": row.industry, "totalScore": str(row.total_score),
        "relativeStrengthScore": str(row.relative_strength_score), "trendScore": str(row.trend_score),
        "industryScore": str(row.industry_score), "volumeChipScore": str(row.volume_chip_score),
        "fundamentalScore": str(row.fundamental_score), "valuationRiskScore": str(row.valuation_risk_score),
        "dataCompleteness": str(row.data_completeness), "closePrice": str(row.close_price),
        "high52wDistancePct": str(row.high_52w_distance_pct) if row.high_52w_distance_pct is not None else None,
        "entryLow": str(row.entry_low) if row.entry_low is not None else None, "entryHigh": str(row.entry_high) if row.entry_high is not None else None,
        "breakoutPrice": str(row.breakout_price) if row.breakout_price is not None else None,
        "pullbackPrice": str(row.pullback_price) if row.pullback_price is not None else None,
        "stopPrice": str(row.stop_price) if row.stop_price is not None else None, "addPrice": str(row.add_price) if row.add_price is not None else None,
        "riskReward": str(row.risk_reward) if row.risk_reward is not None else None, "suggestedCapital": str(row.suggested_capital),
        "status": row.status, "entryType": row.entry_type, "reasons": _json(row.reasons_json, []),
        "blockedReasons": _json(row.blocked_reasons_json, []), "scoreDetails": _json(row.score_details_json, {}),
        "strategyVersion": row.strategy_version, "calculatedAt": row.calculated_at,
    }
