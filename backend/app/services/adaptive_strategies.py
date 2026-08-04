from __future__ import annotations

from dataclasses import dataclass

from ..adaptive_schemas import AdaptiveStockInput


@dataclass(frozen=True)
class StrategyScore:
    total: float
    components: dict[str, float]
    reasons: tuple[str, ...]
    risks: tuple[str, ...]
    status: str
    false_breakout_risk: float = 0


def _points(items: list[tuple[bool, float, str]]) -> tuple[float, list[str]]:
    score = sum(weight for passed, weight, _ in items if passed)
    reasons = [reason for passed, _, reason in items if passed]
    return score, reasons


def _present_positive(value: float | None) -> bool:
    return value is not None and value > 0


class CrashRecoveryStrategy:
    name = "崩盤後止跌復甦選股"

    def evaluate(self, stock: AdaptiveStockInput, parameters: dict[str, float]) -> StrategyScore:
        relative, relative_reasons = _points([
            (stock.relative_strength_market >= parameters["recovery.relative_strength_minimum"], 8, "近 20 日相對加權指數抗跌"),
            (stock.relative_strength_electronic >= parameters["recovery.relative_strength_minimum"], 6, "近 20 日相對電子類股抗跌"),
            (stock.return_20d > stock.market_return_20d, 4, "個股跌幅小於大盤"),
            (stock.ma60 is not None and stock.price >= stock.ma60 * .97, 4, "股價位於季線附近或之上"),
            (stock.industry_rank_percentile <= .30, 3, "電子次產業強度排名前 30%"),
        ])
        reversal, reversal_reasons = _points([
            (stock.ma5 is not None and stock.price > stock.ma5, 4, "股價重新站上 5 日線"),
            ((stock.ma5_slope or -1) >= 0, 3, "5 日線走平轉上"),
            (stock.macd_histogram_rising is True, 3, "MACD 柱狀體縮短或翻紅"),
            (stock.rsi14 is not None and 30 <= stock.rsi14 <= 55, 3, "RSI 從低檔回升區間"),
            (stock.bottom_reversal_candle, 3, "出現底部止跌 K 線"),
            (stock.higher_low, 2, "低點墊高"),
            (stock.breakout_20d, 2, "突破止跌平台高點"),
        ])
        volume, volume_reasons = _points([
            (stock.volume_ratio_5d is not None and .8 <= stock.volume_ratio_5d <= 1.8, 5, "止跌量能結構正常"),
            (stock.down_volume_less_than_up, 5, "下跌量小於上漲量"),
            (stock.volume_ratio_20d is not None and stock.volume_ratio_20d >= 1.3 and stock.return_1d > 0, 5, "轉強時成交量放大"),
        ])
        chip, chip_reasons = _points([
            (_present_positive(stock.foreign_net_5d), 4, "外資近五日買超"),
            (_present_positive(stock.trust_net_5d), 3, "投信近五日買超"),
            (_present_positive(stock.holder_400_change), 3, "400 張以上大戶持股增加"),
            (_present_positive(stock.holder_1000_change), 3, "1,000 張以上大戶持股增加"),
            (stock.retail_holder_change is not None and stock.retail_holder_change < 0, 2, "散戶持股比例下降"),
        ])
        fundamental, fundamental_reasons = _points([
            (_present_positive(stock.revenue_yoy), 4, "月營收年增為正"),
            (_present_positive(stock.revenue_3m_yoy), 4, "近三月累計營收成長"),
            (_present_positive(stock.latest_eps), 4, "最近一季 EPS 為正"),
            (_present_positive(stock.trailing_eps), 3, "最近四季 EPS 為正"),
        ])
        industry = min(10, stock.industry_strength_score / 10)
        components = {
            "相對抗跌強度": min(25, relative), "止跌型態": min(20, reversal),
            "成交量結構": min(15, volume), "法人與大戶籌碼": min(15, chip),
            "營收與基本面": min(15, fundamental), "電子次產業強度": industry,
        }
        total = round(sum(components.values()), 2)
        risks = []
        if stock.fundamental_risk: risks.append("基本面資料顯示重大風險")
        if stock.margin_change is not None and stock.margin_change > 10: risks.append("反彈初期融資快速增加")
        trigger_count = sum([
            stock.breakout_20d,
            (stock.volume_ratio_20d or 0) >= 1.3,
            (stock.close_location or 0) >= .75,
            (stock.upper_shadow_ratio or 1) <= .35,
            stock.same_industry_strong_count >= 2,
        ])
        status = "可以進場" if total >= 80 and trigger_count >= 4 else "等待確認"
        return StrategyScore(total, components, tuple(relative_reasons + reversal_reasons + volume_reasons + chip_reasons + fundamental_reasons), tuple(risks), status)


class RangeTradingStrategy:
    name = "區間盤整選股"

    def evaluate(self, stock: AdaptiveStockInput, parameters: dict[str, float]) -> StrategyScore:
        amplitude = stock.range_amplitude or 999
        structure, structure_reasons = _points([
            (parameters["range.minimum_amplitude"] <= amplitude <= parameters["range.maximum_amplitude"], 10, "近 20～40 日形成合理箱型"),
            (stock.higher_low, 6, "箱型低點逐漸墊高"),
            (stock.range_position is not None and stock.range_position <= .55, 5, "股價位於箱型中間偏下"),
            ((stock.ma20_slope or 0) >= -.1, 4, "月線走平或微幅向上"),
        ])
        support = max(0, 20 * (1 - min(1, max(0, stock.range_position or 1))))
        contraction, contraction_reasons = _points([
            (stock.volume_contracting, 5, "整理期間成交量收斂"),
            (stock.bollinger_width_percentile is not None and stock.bollinger_width_percentile <= 35, 5, "布林通道位於近 60 日低檔"),
            (stock.atr20_ratio is not None and stock.atr20_ratio <= 3, 5, "波動率維持收斂"),
        ])
        volume, volume_reasons = _points([
            (stock.volume_contracting, 7, "整理期間量縮"),
            (stock.down_volume_less_than_up, 5, "下跌量小於上漲量"),
            ((stock.volume_ratio_20d or 0) < 1.8, 3, "沒有爆量滯漲"),
        ])
        chip, chip_reasons = _points([
            (stock.foreign_net_5d is not None and stock.foreign_net_5d >= 0, 4, "外資未連續大量賣超"),
            (stock.trust_net_5d is not None and stock.trust_net_5d >= 0, 3, "投信籌碼穩定"),
            (stock.holder_400_change is not None and stock.holder_400_change >= 0, 3, "400 張以上大戶穩定或增加"),
            (stock.holder_1000_change is not None and stock.holder_1000_change >= 0, 3, "1,000 張以上大戶穩定或增加"),
            (stock.margin_change is not None and stock.margin_change <= 5, 2, "融資未異常暴增"),
        ])
        has_fundamental_data = stock.revenue_yoy is not None or stock.latest_eps is not None
        fundamental = min(10, sum([
            3 if _present_positive(stock.revenue_yoy) else 0,
            3 if _present_positive(stock.latest_eps) else 0,
            2 if has_fundamental_data and not stock.fundamental_risk else 0,
            2 if stock.industry_strength_score >= 50 else 0,
        ]))
        components = {
            "箱型完整度": min(25, structure), "接近支撐程度": min(20, support),
            "波動收斂": min(15, contraction), "成交量縮減": min(15, volume),
            "籌碼穩定度": min(15, chip), "基本面與產業題材": fundamental,
        }
        total = round(sum(components.values()), 2)
        close_to_support = stock.range_position is not None and stock.range_position <= .2
        entry = close_to_support and stock.bottom_reversal_candle and (stock.rsi14 is None or 35 <= stock.rsi14 <= 50)
        status = "可以進場" if total >= 80 and entry else "接近支撐" if close_to_support else "等待確認"
        risks = [] if amplitude <= parameters["range.maximum_amplitude"] else ["箱型振幅過大"]
        return StrategyScore(total, components, tuple(structure_reasons + contraction_reasons + volume_reasons + chip_reasons), tuple(risks), status)


class BreakoutStrategy:
    name = "多頭突破選股"

    def evaluate(self, stock: AdaptiveStockInput, parameters: dict[str, float]) -> StrategyScore:
        volume_price, vp_reasons = _points([
            (stock.breakout_20d or stock.breakout_60d, 8, "收盤突破近 20 日或 60 日高點"),
            (stock.breakout_percent >= parameters["breakout.minimum_breakout_percent"], 4, "突破幅度超過門檻"),
            ((stock.volume_ratio_20d or 0) >= parameters["breakout.minimum_volume_ratio"], 7, "突破量大於 20 日均量 1.5 倍"),
            ((stock.close_location or 0) >= .75, 3, "收盤位於當日振幅上方 25%"),
            ((stock.upper_shadow_ratio or 1) <= .35, 3, "突破 K 棒上影線短"),
        ])
        pattern, pattern_reasons = _points([
            (stock.distance_to_high_percent is not None and stock.distance_to_high_percent <= 3, 6, "突破前距離前高小於 3%"),
            (stock.volume_contracting, 5, "整理期間量縮"),
            (stock.range_amplitude is not None and stock.range_amplitude <= 18, 5, "平台整理振幅受控"),
            (stock.bollinger_width_percentile is not None and stock.bollinger_width_percentile <= 45, 4, "突破前波動收斂"),
        ])
        trend, trend_reasons = _points([
            (stock.ma20 is not None and stock.price > stock.ma20, 5, "股價位於月線之上"),
            (stock.ma60 is not None and stock.price > stock.ma60, 4, "股價位於季線之上"),
            ((stock.ma20_slope or -1) > 0, 3, "月線向上"),
            ((stock.ma60_slope or -1) >= 0, 3, "季線走平或向上"),
        ])
        relative, relative_reasons = _points([
            (stock.relative_strength_market > 0, 7, "相對強弱優於加權指數"),
            (stock.relative_strength_electronic > 0, 5, "相對強弱優於電子類指數"),
            (stock.industry_rank_percentile <= .30, 3, "電子次產業排名前 30%"),
        ])
        industry = min(10, stock.industry_strength_score / 10)
        chip, chip_reasons = _points([
            (_present_positive(stock.foreign_net_5d), 3, "外資近五日買超"),
            (_present_positive(stock.trust_net_5d), 2, "投信近五日買超"),
            (_present_positive(stock.holder_400_change), 2, "400 張以上大戶增加"),
            (_present_positive(stock.holder_1000_change), 2, "1,000 張以上大戶增加"),
            (stock.retail_holder_change is not None and stock.retail_holder_change <= 0, 1, "散戶持股未快速增加"),
        ])
        has_fundamental_data = stock.revenue_yoy is not None or stock.latest_eps is not None
        fundamental = min(5, sum([
            2 if _present_positive(stock.revenue_yoy) else 0,
            2 if _present_positive(stock.latest_eps) else 0,
            1 if has_fundamental_data and not stock.fundamental_risk else 0,
        ]))
        false_risk = 0
        risks: list[str] = []
        penalties = [
            ((stock.volume_ratio_20d or 0) < 1.2, 15, "突破量不足"),
            ((stock.upper_shadow_ratio or 0) > .5, 15, "爆量長上影"),
            (stock.same_industry_strong_count < 2, 10, "電子次產業僅單一股票轉強"),
            ((stock.margin_change or 0) > 10, 10, "融資快速增加"),
            (stock.ma20 is not None and (stock.price / stock.ma20 - 1) * 100 > parameters["breakout.maximum_distance_ma20"], 15, "股價距離月線過遠"),
            (stock.return_5d > 15, 15, "近 5 日漲幅過大"),
        ]
        for failed, penalty, reason in penalties:
            if failed:
                false_risk += penalty
                risks.append(reason)
        components = {
            "突破量價": min(25, volume_price), "整理型態": min(20, pattern),
            "趨勢方向": min(15, trend), "相對強度": min(15, relative),
            "電子次產業同步性": industry, "法人與大戶籌碼": min(10, chip),
            "基本面與營收": fundamental,
        }
        total = round(max(0, sum(components.values()) - false_risk * .25), 2)
        effective = sum([
            stock.breakout_20d or stock.breakout_60d,
            stock.breakout_percent >= 1,
            (stock.volume_ratio_20d or 0) >= 1.5,
            (stock.close_location or 0) >= .75,
            (stock.upper_shadow_ratio or 1) <= .35,
            stock.same_industry_strong_count >= 2,
        ]) >= 5
        status = "可以進場" if total >= parameters["breakout.direct_entry_score"] and effective else "等待回測" if total >= 75 else "突破觀察"
        return StrategyScore(total, components, tuple(vp_reasons + pattern_reasons + trend_reasons + relative_reasons + chip_reasons), tuple(risks), status, min(100, false_risk))


STRATEGIES = {
    "RECOVERY": CrashRecoveryStrategy(),
    "RANGE": RangeTradingStrategy(),
    "BREAKOUT": BreakoutStrategy(),
}
