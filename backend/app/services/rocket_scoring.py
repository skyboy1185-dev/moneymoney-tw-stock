from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..adaptive_schemas import AdaptiveMarketMetrics, AdaptiveScanPayload, AdaptiveStockInput
from .electronic_industry_strength_service import IndustryStrengthResult, rank_industries


RocketStatus = Literal[
    "watch", "waiting", "can_enter", "strong_breakout", "pullback",
    "can_add", "reduce", "exit", "overheated",
]


@dataclass(frozen=True, slots=True)
class RocketRegimeResult:
    key: str
    label: str
    score: float
    exposure_pct: float
    strategy_label: str
    reasons: tuple[str, ...]
    indicators: dict[str, float | bool | None]


@dataclass(frozen=True, slots=True)
class RocketPick:
    stock: AdaptiveStockInput
    sector_rank: int
    pattern_type: str
    status: RocketStatus
    rocket_score: float
    chase_risk_score: float
    components: dict[str, float | None]
    data_availability_pct: float
    breakout_price: float
    stop_loss_price: float
    target_price_1: float
    target_price_2: float
    risk_reward_ratio: float
    reasons: tuple[str, ...]
    missing_data: tuple[str, ...]


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _scaled(value: float, low: float, high: float, maximum: float) -> float:
    if high <= low:
        return 0.0
    return _clamp((value - low) / (high - low) * maximum, 0, maximum)


def classify_rocket_market(market: AdaptiveMarketMetrics) -> RocketRegimeResult:
    breadth = market.advance_ratio or 50
    volume = market.volume_ratio_20d or 1
    reasons: list[str] = []
    bullish = 0
    bearish = 0
    if market.taiex_above_ma5 is True: bullish += 1; reasons.append("加權指數站上 5 日線")
    if market.taiex_above_ma20 is True: bullish += 2; reasons.append("加權指數站上 20 日線")
    if market.taiex_above_ma60 is True: bullish += 1
    if (market.ma20_slope or 0) > 0: bullish += 2; reasons.append("20 日均線向上")
    if breadth >= 60: bullish += 2; reasons.append("市場上漲家數占優")
    if volume >= 1.05: bullish += 1; reasons.append("市場成交量高於 20 日均量")
    if market.taiex_breakout_20d is True: bullish += 2; reasons.append("加權指數突破 20 日高點")
    if market.taiex_above_ma20 is False: bearish += 2
    if market.taiex_above_ma60 is False: bearish += 2
    if (market.ma20_slope or 0) < 0: bearish += 2
    if breadth < 35: bearish += 2; reasons.append("市場下跌家數明顯占優")
    if (market.taiex_return_5d or 0) <= -7 or (market.limit_down_count or 0) >= 30:
        bearish += 5; reasons.append("市場出現急跌或大量跌停")

    if bearish >= 7:
        key, label, exposure, strategy = "bear", "🔴 空頭／崩跌", 0.0, "停止新倉，等待恐慌止跌"
    elif bearish >= 4:
        key, label, exposure, strategy = "weak", "🟠 弱勢盤", 30.0, "只做逆勢創高與族群龍頭"
    elif bullish >= 9 and breadth >= 60:
        key, label, exposure, strategy = "strong_bull", "🔥 強勢多頭", 90.0, "突破型＋強勢回踩"
    elif bullish >= 6:
        key, label, exposure, strategy = "bull", "🟢 多頭", 80.0, "突破型＋族群領先"
    else:
        key, label, exposure, strategy = "range", "🟡 震盪盤", 60.0, "強勢回踩＋箱型突破＋籌碼潛伏"
    score = round(_clamp(50 + bullish * 5 - bearish * 7, 0, 100), 2)
    return RocketRegimeResult(
        key, label, score, exposure, strategy, tuple(reasons[:8] or ["市場條件混合，採保守曝險"]),
        {
            "taiexReturn1d": market.taiex_return_1d, "taiexReturn5d": market.taiex_return_5d,
            "advanceRatio": market.advance_ratio, "volumeRatio20d": market.volume_ratio_20d,
            "aboveMa5": market.taiex_above_ma5, "aboveMa20": market.taiex_above_ma20,
            "aboveMa60": market.taiex_above_ma60, "ma20Slope": market.ma20_slope,
        },
    )


def _pattern(stock: AdaptiveStockInput) -> tuple[str, float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    amplitude = stock.range_amplitude or 999
    distance = stock.distance_to_high_percent if stock.distance_to_high_percent is not None else 99
    if stock.breakout_20d and amplitude <= 18:
        return "平台突破", 15.0, ["突破 20 日整理平台"]
    if stock.breakout_60d:
        return "族群龍頭", 15.0, ["突破 60 日高點"]
    if stock.higher_low and distance <= 2.5 and amplitude <= 15:
        score = 13.0; reasons.append("高低點收斂且接近壓力")
        return "三角收斂", score, reasons
    if stock.volume_contracting and amplitude <= 12 and distance <= 4:
        score = 12.0; reasons.append("波動與成交量同步壓縮")
        return "波動壓縮", score, reasons
    if stock.ma10 and stock.price >= stock.ma10 and (stock.price / stock.ma10 - 1) * 100 <= 3 and stock.higher_low:
        score = 12.0; reasons.append("回踩 MA10 未破且低點墊高")
        return "強勢回踩", score, reasons
    if stock.industry_rank_percentile <= .2 and stock.relative_strength_market > 3:
        return "族群龍頭", 10.0, ["位於強勢族群且相對大盤領先"]
    score = _scaled(5 - distance, 0, 5, 8)
    return "接近突破", score, ["仍在等待整理區突破"]


def chase_risk_score(stock: AdaptiveStockInput) -> tuple[float, tuple[str, ...]]:
    score = 0.0
    reasons: list[str] = []
    checks = (
        (stock.return_1d > 8, 18, "單日漲幅超過 8%"),
        (stock.return_3d > 18, 16, "近 3 日漲幅超過 18%"),
        (stock.return_5d > 25, 15, "近 5 日漲幅超過 25%"),
        (bool(stock.ma20) and stock.price / stock.ma20 > 1.15, 14, "距 MA20 超過 15%"),
        (bool(stock.ma10) and stock.price / stock.ma10 > 1.10, 10, "距 MA10 超過 10%"),
        (stock.gap_percent > 7, 10, "今日跳空超過 7%"),
        (stock.consecutive_strong_up_days >= 3, 12, "連續 3 日大漲"),
        (stock.consecutive_long_bullish_days >= 2, 9, "連續 2 根長紅 K"),
        (stock.is_highest_volume_20d, 8, "爆 20 日最大量"),
        ((stock.upper_shadow_ratio or 0) > .5, 12, "長上影大於實體風險"),
        ((stock.rsi14 or 0) > 85, 12, "RSI 超過 85"),
    )
    for matched, points, reason in checks:
        if matched:
            score += points; reasons.append(reason)
    return round(_clamp(score, 0, 100), 2), tuple(reasons)


def _eligible(stock: AdaptiveStockInput) -> bool:
    current_turnover = stock.price * stock.volume_shares
    return (
        stock.price > 10
        and current_turnover >= 300_000_000
        and stock.average_turnover_20d >= 100_000_000
        and stock.average_volume_20d_shares > 0
        and stock.has_recent_trade
        and stock.data_completeness >= .70
        and not any((stock.is_full_delivery, stock.is_alternate_trading, stock.is_disposed,
                     stock.is_suspended, stock.is_delisted, stock.abnormal_trading))
    )


def score_rocket_stock(
    stock: AdaptiveStockInput,
    regime: RocketRegimeResult,
    sector: IndustryStrengthResult | None,
    *,
    enforce_initial_filter: bool = True,
) -> RocketPick | None:
    if enforce_initial_filter and not _eligible(stock):
        return None
    if not stock.has_recent_trade or stock.price <= 0 or any((stock.is_suspended, stock.is_delisted)):
        return None
    sector_rank = sector.rank if sector else 99
    sector_points = {1: 20, 2: 18, 3: 16, 4: 14, 5: 12}.get(sector_rank, _scaled(stock.industry_strength_score, 0, 100, 10))
    momentum = 0.0
    momentum += 3 if stock.ma5 and stock.price > stock.ma5 else 0
    momentum += 3 if stock.ma10 and stock.price > stock.ma10 else 0
    momentum += 3 if stock.ma20 and stock.price > stock.ma20 else 0
    momentum += 3 if stock.ma5 and stock.ma10 and stock.ma5 > stock.ma10 else 0
    momentum += 3 if stock.ma10 and stock.ma20 and stock.ma10 > stock.ma20 else 0
    momentum += 2 if (stock.ma20_slope or 0) > 0 else 0
    momentum += 2 if (stock.distance_to_high_percent or 99) <= 3 else 0
    momentum += 1 if stock.breakout_60d else 0
    momentum = min(20.0, momentum)

    volume_ratio = stock.volume_ratio_20d or 0
    volume = _scaled(volume_ratio, 1.0, 2.0, 13)
    if 2 <= volume_ratio <= 3:
        volume = 15
    elif volume_ratio > 4:
        volume = 9
    if volume_ratio > 2 and (stock.upper_shadow_ratio or 0) > .45:
        volume = max(0, volume - 6)

    pattern_type, pattern_points, pattern_reasons = _pattern(stock)
    chip_values = [stock.holder_400_change, stock.holder_1000_change, stock.retail_holder_change]
    if all(value is None for value in chip_values):
        chip: float | None = None
    else:
        chip = 5.0
        chip += _scaled(stock.holder_400_change or 0, -.5, 1.5, 5)
        chip += _scaled(stock.holder_1000_change or 0, -.5, 1.0, 4)
        chip += 1 if (stock.retail_holder_change or 0) < 0 else 0
        chip = min(15.0, chip)

    if stock.foreign_net_5d is None and stock.trust_net_5d is None:
        institutional: float | None = None
    else:
        institutional = 2.0
        institutional += 4 if (stock.trust_net_5d or 0) > 0 else 0
        institutional += 3 if (stock.foreign_net_5d or 0) > 0 else 0
        institutional += 1 if (stock.trust_net_5d or 0) > 0 and (stock.foreign_net_5d or 0) > 0 else 0
        institutional = min(10.0, institutional)

    if pattern_points < 12 and chip is not None and chip >= 12:
        pattern_type = "大戶吸籌"
        pattern_reasons = ["大戶持股增加，籌碼集中度改善"]
        pattern_points = max(pattern_points, 10)
    elif pattern_points < 12 and institutional is not None and institutional >= 8:
        pattern_type = "法人攻擊"
        pattern_reasons = ["外資與投信買盤同步增強"]
        pattern_points = max(pattern_points, 10)

    atr_pct = stock.atr20_ratio or 0
    quality = 5 - _scaled(atr_pct, 4, 10, 3) - _scaled(abs(stock.gap_percent), 4, 10, 2)
    quality = round(max(0, quality), 2)
    components: dict[str, float | None] = {
        "族群強度": round(sector_points, 2), "價格動能": round(momentum, 2),
        "成交量": round(volume, 2), "突破型態": round(pattern_points, 2),
        "籌碼強度": None if chip is None else round(chip, 2),
        "法人": None if institutional is None else round(institutional, 2),
        "風險品質": quality,
    }
    maximums = {"族群強度": 20, "價格動能": 20, "成交量": 15, "突破型態": 15, "籌碼強度": 15, "法人": 10, "風險品質": 5}
    available_max = sum(maximums[key] for key, value in components.items() if value is not None)
    earned = sum(value for value in components.values() if value is not None)
    rocket_score = round(earned / available_max * 100, 2) if available_max else 0
    availability = round(available_max, 2)
    chase, chase_reasons = chase_risk_score(stock)

    breakout = max(stock.range_high or stock.price, stock.price if stock.breakout_20d else 0)
    atr = stock.atr14 or stock.price * .025
    support_candidates = [breakout - 1.8 * atr]
    if stock.ma10 and stock.ma10 < breakout: support_candidates.append(stock.ma10 * .985)
    if stock.range_low and stock.range_low < breakout: support_candidates.append(stock.range_low * .995)
    stop = max(support_candidates)
    stop = min(stop, breakout * .985)
    expected_up_pct = _clamp(6 + sector_points / 20 * 4 + pattern_points / 15 * 4, 6, 14)
    target1 = breakout * (1 + expected_up_pct / 100)
    target2 = breakout * (1 + expected_up_pct * 1.6 / 100)
    risk = max(.01, breakout - stop)
    rr = round((target1 - breakout) / risk, 2)

    confirmed = stock.price >= breakout and volume_ratio >= 1.5 and rr >= 1.8
    if chase > 75:
        status: RocketStatus = "overheated"
    elif rocket_score >= 90 and confirmed and sector_rank <= 5 and (stock.breakout_20d or stock.breakout_60d):
        status = "strong_breakout"
    elif rocket_score >= 85 and chase < 60 and confirmed:
        status = "can_enter"
    elif pattern_type == "強勢回踩" and rocket_score >= 80:
        status = "pullback"
    elif rocket_score >= 80:
        status = "waiting"
    else:
        status = "watch"
    missing: list[str] = []
    if chip is None: missing.append("大戶資料暫無")
    if institutional is None: missing.append("法人資料暫無")
    reasons = [
        f"族群排名第 {sector_rank}" if sector_rank < 99 else "族群尚未進入前段",
        *pattern_reasons,
        f"量比 {volume_ratio:.2f} 倍",
    ]
    if chase_reasons:
        reasons.append(f"追高風險：{'、'.join(chase_reasons[:2])}")
    return RocketPick(
        stock, sector_rank, pattern_type, status, rocket_score, chase, components,
        availability, round(breakout, 4), round(stop, 4), round(target1, 4), round(target2, 4), rr,
        tuple(reasons), tuple(missing),
    )


def rank_rocket_candidates(payload: AdaptiveScanPayload) -> tuple[RocketRegimeResult, list[IndustryStrengthResult], list[RocketPick]]:
    regime = classify_rocket_market(payload.market)
    sectors = rank_industries(payload.industries)
    sector_map = {item.sub_industry: item for item in sectors}
    picks = [
        pick for stock in payload.stocks
        if (pick := score_rocket_stock(stock, regime, sector_map.get(stock.sub_industry))) is not None
    ]
    picks.sort(key=lambda item: (item.rocket_score, -item.chase_risk_score, item.stock.average_turnover_20d), reverse=True)
    return regime, sectors, picks
