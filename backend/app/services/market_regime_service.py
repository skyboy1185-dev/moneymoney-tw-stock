from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..adaptive_schemas import AdaptiveMarketMetrics


EXPOSURE = {
    "CRASH": (0, 20),
    "RECOVERY": (20, 40),
    "RANGE": (40, 60),
    "BREAKOUT": (60, 80),
    "UNCERTAIN": (20, 40),
}


@dataclass(frozen=True)
class RegimeEvaluation:
    regime: str
    provisional_regime: str
    confidence: float
    reasons: tuple[str, ...]
    scores: dict[str, float]
    exposure_min: float
    exposure_max: float
    immediate_crash: bool = False


def _yes(value: bool | None) -> bool:
    return value is True


def intraday_regime_override(market: AdaptiveMarketMetrics, base_regime: str) -> str:
    """Use live market pressure to override slower daily regime confirmation.

    The daily regime model intentionally waits for confirmation. Day trading
    cannot wait that long: a broad intraday squeeze should immediately block
    shorts, and a broad intraday selloff should immediately block longs.
    """
    taiex_1d = market.taiex_return_1d
    electronic_1d = market.electronic_return_1d
    advance = market.advance_ratio

    bullish_votes = 0
    if taiex_1d is not None and taiex_1d >= 0.6:
        bullish_votes += 1
    if electronic_1d is not None and electronic_1d >= 0.8:
        bullish_votes += 1
    if advance is not None and advance >= 58:
        bullish_votes += 1
    if _yes(market.taiex_above_ma5):
        bullish_votes += 1
    if _yes(market.up_volume_expanding):
        bullish_votes += 1

    bearish_votes = 0
    if taiex_1d is not None and taiex_1d <= -0.6:
        bearish_votes += 1
    if electronic_1d is not None and electronic_1d <= -0.8:
        bearish_votes += 1
    if advance is not None and advance <= 35:
        bearish_votes += 1
    if market.limit_down_count is not None and market.limit_down_count >= 10:
        bearish_votes += 1
    if _yes(market.taiex_new_low):
        bearish_votes += 1

    if bullish_votes >= 3:
        return "BREAKOUT"
    if bullish_votes >= 2 and base_regime not in {"BREAKOUT", "RECOVERY"}:
        return "RECOVERY"
    if bearish_votes >= 2:
        return "CRASH"
    return base_regime


def evaluate_market_regime(
    market: AdaptiveMarketMetrics,
    parameters: dict[str, float],
    *,
    previous_regime: str | None = None,
    previous_provisional: str | None = None,
    previous_confirmation_days: int = 0,
) -> RegimeEvaluation:
    if not market.official_data:
        return RegimeEvaluation(
            "UNCERTAIN", "UNCERTAIN", 0, ("市場資料不是官方來源，禁止產生正式訊號",),
            {"taiex": 0.0, "otc": 0.0, "electronic": 0.0, "breadth": 0.0, "volume": 0.0, "institutional": 0.0, "volatility": 0.0},
            *EXPOSURE["UNCERTAIN"],
        )

    crash: list[str] = []
    if market.taiex_above_ma60 is False: crash.append("加權指數低於季線")
    if market.electronic_above_ma60 is False: crash.append("電子類指數低於季線")
    if market.ma20_slope is not None and market.ma20_slope < 0: crash.append("指數 20 日均線向下")
    if market.taiex_return_5d is not None and market.taiex_return_5d <= parameters["regime.crash_return_5d"]: crash.append("加權指數近 5 日急跌")
    if market.taiex_return_20d is not None and market.taiex_return_20d <= parameters["regime.crash_return_20d"]: crash.append("加權指數近 20 日跌幅過大")
    if market.atr20_ratio is not None and market.atr20_ratio > parameters["regime.crash_atr_ratio"]: crash.append("指數波動率進入高風險區")
    if market.advance_ratio is not None and market.advance_ratio < parameters["regime.crash_advance_ratio"]: crash.append("市場上漲家數比例低於 25%")
    if market.foreign_net_5d is not None and market.foreign_net_5d < 0: crash.append("外資近 5 日賣超")
    if (market.electronic_long_black_days or 0) >= 2: crash.append("電子類股連續帶量長黑")
    if market.new_low_ratio_change is not None and market.new_low_ratio_change >= 10: crash.append("創 20 日新低家數快速增加")
    if _yes(market.taiex_new_low): crash.append("指數跌破前波重要低點")
    immediate = any(
        value is not None and value <= parameters["regime.immediate_crash_daily_return"]
        for value in (market.taiex_return_1d, market.otc_return_1d, market.electronic_return_1d)
    ) or (market.limit_down_count or 0) >= 30

    recovery: list[str] = []
    if market.taiex_new_low is False: recovery.append("加權指數不再創新低")
    if market.electronic_new_low is False: recovery.append("電子類指數不再創新低")
    if _yes(market.higher_low): recovery.append("市場低點墊高")
    if _yes(market.taiex_above_ma5): recovery.append("指數重新站上 5 日均線")
    if market.ma5_slope is not None and market.ma5_slope >= 0: recovery.append("5 日均線轉為走平或向上")
    if market.advance_ratio_2d is not None and market.advance_ratio_2d > 55: recovery.append("上漲家數連續兩日超過 55%")
    if _yes(market.panic_volume_contracted): recovery.append("恐慌爆量後成交量收斂")
    if _yes(market.up_volume_expanding): recovery.append("指數上漲時成交量放大")
    if _yes(market.foreign_selling_shrinking): recovery.append("外資賣超縮小或轉買")
    if (market.sector_continuation_days or 0) >= 2: recovery.append("電子強勢族群至少延續兩日")
    if _yes(market.otc_relative_strength): recovery.append("櫃買指數相對轉強")
    if market.new_low_ratio_change is not None and market.new_low_ratio_change < 0: recovery.append("創 20 日新低家數下降")
    if market.new_high_20d_ratio is not None and market.new_high_20d_ratio > 5: recovery.append("創 20 日新高家數開始增加")

    breakout: list[str] = []
    if _yes(market.taiex_above_ma20) and _yes(market.taiex_above_ma60): breakout.append("加權指數站上月線與季線")
    if _yes(market.electronic_above_ma20) and _yes(market.electronic_above_ma60): breakout.append("電子類指數站上月線與季線")
    if market.ma20_slope is not None and market.ma20_slope > 0: breakout.append("月線向上")
    if _yes(market.taiex_breakout_20d) or _yes(market.taiex_breakout_60d): breakout.append("指數突破近期高點")
    if market.volume_ratio_20d is not None and market.volume_ratio_20d >= parameters["regime.breakout_volume_ratio"]: breakout.append("突破成交量放大")
    if market.advance_ratio is not None and market.advance_ratio >= parameters["regime.breakout_advance_ratio"]: breakout.append("上漲家數比例超過 60%")
    if market.new_high_20d_ratio is not None and market.new_high_20d_ratio >= 10: breakout.append("創 20 日新高家數增加")
    if market.foreign_net_5d is not None and market.foreign_net_5d > 0: breakout.append("外資近 5 日買超")
    if (market.sector_continuation_days or 0) >= 2: breakout.append("電子領漲族群具延續性")
    if _yes(market.otc_relative_strength): breakout.append("櫃買與加權同步轉強")

    range_reasons: list[str] = []
    if market.taiex_return_20d is not None and abs(market.taiex_return_20d) <= 5: range_reasons.append("加權指數近 20 日維持區間")
    if market.ma20_slope is not None and abs(market.ma20_slope) <= .15: range_reasons.append("月線斜率接近水平")
    if market.adx14 is not None and market.adx14 <= parameters["regime.range_adx_max"]: range_reasons.append("ADX 顯示趨勢強度偏低")
    if market.volume_ratio_20d is not None and market.volume_ratio_20d < 1: range_reasons.append("成交量低於 20 日均量")
    if market.advance_ratio is not None and 40 <= market.advance_ratio <= 60: range_reasons.append("市場漲跌家數接近")
    if market.bollinger_width_percentile is not None and market.bollinger_width_percentile <= 35: range_reasons.append("指數布林通道寬度收斂")
    if not _yes(market.taiex_breakout_20d) and market.taiex_new_low is False: range_reasons.append("近 20 日未明確創高或破底")

    if immediate or len(crash) >= parameters["regime.crash_minimum_conditions"]:
        provisional = "CRASH"
        reasons = (["單日市場急跌，立即切換防守模式"] if immediate else []) + crash
    elif len(breakout) >= parameters["regime.breakout_minimum_conditions"]:
        provisional, reasons = "BREAKOUT", breakout
    elif len(recovery) >= parameters["regime.recovery_minimum_conditions"]:
        provisional, reasons = "RECOVERY", recovery
    elif len(range_reasons) >= parameters["regime.range_minimum_conditions"]:
        provisional, reasons = "RANGE", range_reasons
    else:
        provisional, reasons = "UNCERTAIN", ["盤勢條件未形成一致方向，降低訊號強度"]

    confirmation = previous_confirmation_days + 1 if provisional == previous_provisional else 1
    required = int(parameters["regime.confirmation_days"])
    if provisional == "CRASH":
        regime = "CRASH"
    elif previous_regime == "CRASH" and provisional == "RECOVERY" and confirmation < required:
        regime = "CRASH"
        reasons = [*reasons, f"復甦條件需連續 {required} 個交易日，維持防守"]
    elif previous_regime and provisional != previous_regime and confirmation < required:
        regime = "UNCERTAIN"
        reasons = [*reasons, f"新盤勢尚未連續確認 {required} 個交易日"]
    else:
        regime = provisional

    available = 1 - min(1, len(market.missing_fields) / 12)
    condition_count = len(reasons)
    confidence = round(min(100, max(20, condition_count * 12.5)) * available, 2)
    exposure = EXPOSURE[regime]
    scores = {
        "taiex": float(min(100, (len(crash) + len(breakout)) * 10)),
        "otc": 70.0 if market.otc_close is not None else 0.0,
        "electronic": 70.0 if market.electronic_close is not None else 0.0,
        "breadth": 100.0 if market.advance_ratio is not None else 0.0,
        "volume": 100.0 if market.volume_ratio_20d is not None else 0.0,
        "institutional": 100.0 if market.foreign_net_5d is not None else 0.0,
        "volatility": 100.0 if market.atr20_ratio is not None else 0.0,
    }
    return RegimeEvaluation(
        regime, provisional, confidence, tuple(reasons), scores,
        exposure[0], exposure[1], immediate,
    )
