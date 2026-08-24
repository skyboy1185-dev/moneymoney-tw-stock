from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from math import sqrt
from typing import Iterable, Literal


PatternStatus = Literal[
    "FORMING", "NEAR_BREAKOUT", "INTRADAY_BREAKOUT", "CONFIRMED_BREAKOUT",
    "FAILED_BREAKOUT", "INVALIDATED",
]
PatternType = Literal[
    "HEAD_SHOULDERS_BOTTOM", "DOUBLE_BOTTOM", "ROUNDED_BOTTOM", "CUP_HANDLE", "ASCENDING_TRIANGLE",
]


@dataclass(frozen=True, slots=True)
class Candle:
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    turnover: float


@dataclass(frozen=True, slots=True)
class Pivot:
    index: int
    trade_date: date
    price: float
    kind: Literal["HIGH", "LOW"]
    confirmed_index: int
    confirmed_date: date


@dataclass(slots=True)
class PatternResult:
    pattern_type: PatternType
    pattern_status: PatternStatus
    score: float
    start_date: date
    confirmed_at: datetime | None
    pivot_confirmed_date: date
    neckline_price: float
    breakout_price: float
    current_price: float
    target_price: float
    invalidation_price: float
    stop_loss_price: float
    entry_price_low: float
    entry_price_high: float
    add_price: float
    take_profit_1: float
    take_profit_2: float
    trailing_stop_price: float
    volume_ratio: float
    distance_to_breakout_pct: float
    risk_reward_ratio: float
    completion_pct: float
    action: str
    action_label: str
    suggested_position_pct: float
    key_points: list[dict] = field(default_factory=list)
    score_breakdown: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    missing_conditions: list[str] = field(default_factory=list)
    risk_warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def _average(values: Iterable[float]) -> float:
    rows = list(values)
    return sum(rows) / len(rows) if rows else 0.0


def _atr(candles: list[Candle], period: int = 14) -> float:
    if len(candles) < 2:
        return 0.0
    rows = candles[-period:]
    start = len(candles) - len(rows)
    values = []
    for offset, row in enumerate(rows):
        index = start + offset
        previous = candles[index - 1].close if index else row.close
        values.append(max(row.high - row.low, abs(row.high - previous), abs(row.low - previous)))
    return _average(values)


def find_pivots(
    candles: list[Candle],
    *,
    window: int = 5,
    minimum_swing_pct: float = 3.0,
) -> list[Pivot]:
    """Confirm pivots only after `window` later bars have existed.

    `confirmed_date` is deliberately different from the turning-point date. This
    makes the function safe for live scans and historical replay without
    back-filling a signal to a price that was not knowable at the time.
    """
    if len(candles) < window * 2 + 1:
        return []
    atr_pct = (_atr(candles) / candles[-1].close * 100) if candles[-1].close else 0
    threshold = max(minimum_swing_pct, min(8.0, atr_pct * 1.25)) / 100
    raw: list[Pivot] = []
    for index in range(window, len(candles) - window):
        row = candles[index]
        segment = candles[index - window:index + window + 1]
        if row.high == max(item.high for item in segment):
            raw.append(Pivot(index, row.trade_date, row.high, "HIGH", index + window, candles[index + window].trade_date))
        if row.low == min(item.low for item in segment):
            raw.append(Pivot(index, row.trade_date, row.low, "LOW", index + window, candles[index + window].trade_date))
    raw.sort(key=lambda item: (item.index, item.kind))
    representative: list[Pivot] = []
    for pivot in raw:
        if not representative:
            representative.append(pivot)
            continue
        previous = representative[-1]
        if pivot.kind == previous.kind:
            more_extreme = pivot.price > previous.price if pivot.kind == "HIGH" else pivot.price < previous.price
            if more_extreme:
                representative[-1] = pivot
            continue
        move = abs(pivot.price / previous.price - 1) if previous.price else 0
        if move < threshold:
            continue
        representative.append(pivot)
    return representative


def _line_price(first: Pivot, second: Pivot, at_index: int) -> float:
    if second.index == first.index:
        return second.price
    return first.price + (second.price - first.price) * (at_index - first.index) / (second.index - first.index)


def _key(name: str, pivot: Pivot) -> dict:
    return {
        "name": name, "date": pivot.trade_date.isoformat(), "price": round(pivot.price, 4),
        "confirmedDate": pivot.confirmed_date.isoformat(),
    }


def _status(
    candles: list[Candle], neckline: float, invalidation: float, atr: float,
    *, close_complete: bool, previously_confirmed: bool = False,
) -> PatternStatus:
    current = candles[-1].close
    if current < invalidation:
        return "INVALIDATED"
    if previously_confirmed and current < neckline * .99:
        return "FAILED_BREAKOUT"
    threshold = max(neckline * .005, atr * .25)
    if current >= neckline + threshold:
        return "CONFIRMED_BREAKOUT" if close_complete else "INTRADAY_BREAKOUT"
    distance = (neckline / current - 1) * 100 if current else 999
    return "NEAR_BREAKOUT" if distance <= 3 else "FORMING"


def _breakout_confirmation_date(candles: list[Candle], neckline: float, atr: float) -> date:
    """Return the latest actual cross above the threshold, not the later scan date."""
    breakout_level = neckline + max(neckline * .005, atr * .25)
    for index in range(len(candles) - 1, 0, -1):
        if candles[index].close >= breakout_level and candles[index - 1].close < breakout_level:
            return candles[index].trade_date
    return candles[0].trade_date


def _score_and_action(
    *, structure: float, symmetry: float, duration: float, volume_ratio: float,
    status: PatternStatus, market_regime: str, risk_reward: float, current: float,
    breakout: float, vwap: float | None, close_complete: bool,
) -> tuple[float, dict[str, float], str, str, list[str], list[str]]:
    volume = 20 if volume_ratio >= 1.3 else max(4, min(18, volume_ratio / 1.3 * 18))
    breakout_score = {
        "CONFIRMED_BREAKOUT": 15, "INTRADAY_BREAKOUT": 12, "NEAR_BREAKOUT": 8,
        "FORMING": 4, "FAILED_BREAKOUT": 0, "INVALIDATED": 0,
    }[status]
    market = 10 if market_regime in {"bull", "strong_bull"} else 7 if market_regime == "neutral" else 3 if market_regime == "bear" else 0
    parts = {
        "structure": min(30, structure), "symmetry": min(15, symmetry),
        "duration": min(10, duration), "volume": volume,
        "breakout": breakout_score, "marketSector": market,
    }
    score = min(100.0, round(sum(parts.values()), 2))
    missing: list[str] = []
    warnings: list[str] = []
    action, label = "WATCH", "觀察"
    bearish = market_regime in {"bear", "strong_bear"}
    if status == "FORMING":
        missing.append("尚未接近或突破頸線／壓力線")
    elif status == "NEAR_BREAKOUT":
        action, label = ("NO_TRADE", "禁止進場") if bearish else ("PREPARE", "準備突破")
        missing.append("等待收盤突破及成交量確認")
    elif status == "INTRADAY_BREAKOUT":
        eligible = score >= 80 and volume_ratio >= 1.3 and risk_reward >= 2 and not bearish and (vwap is None or current >= vwap)
        action, label = ("PROBE_BUY", "小量試單") if eligible else ("WATCH", "盤中觀察")
        missing.append("盤中暫時突破，尚未收盤確認")
        if not eligible:
            missing.append("試單分數、量能、VWAP、大盤或風險報酬條件未全部通過")
    elif status == "CONFIRMED_BREAKOUT":
        chase = (current / breakout - 1) * 100 if breakout else 999
        if risk_reward < 2:
            action, label = "NO_TRADE", "風險報酬不足"
            warnings.append("目前價格風險報酬比不足，不建議追價")
        elif chase > 5:
            action, label = "WATCH", "漲幅過大，等待回測"
            warnings.append("現價高於突破價5%以上，不追價")
        elif bearish:
            action, label = "NO_TRADE", "大盤偏空，禁止新倉"
        elif volume_ratio >= 1.3 and score >= 70 and close_complete:
            action, label = "BUY", "正式建立部位"
        else:
            action, label = "WATCH", "等待量價確認"
            missing.append("突破量未達20日均量1.3倍")
    elif status in {"FAILED_BREAKOUT", "INVALIDATED"}:
        action, label = ("STOP_LOSS", "停損") if status == "FAILED_BREAKOUT" else ("EXIT", "取消／出場")
        warnings.append("型態結構已破壞，禁止攤平並取消未成交買單")
    return score, parts, action, label, missing, warnings


def _finish(
    pattern_type: PatternType, candles: list[Candle], points: list[tuple[str, Pivot]],
    neckline: float, target: float, invalidation: float, structure: float, symmetry: float,
    duration_score: float, reasons: list[str], *, close_complete: bool, market_regime: str,
    vwap: float | None, previously_confirmed: bool = False,
) -> PatternResult:
    current = candles[-1].close
    atr = _atr(candles)
    average_volume = _average(row.volume for row in candles[-21:-1]) or 1
    volume_ratio = candles[-1].volume / average_volume
    status = _status(candles, neckline, invalidation, atr, close_complete=close_complete, previously_confirmed=previously_confirmed)
    stop = max(invalidation * .995, min(neckline * .975, current - 1.5 * atr))
    if stop >= current:
        stop = min(invalidation * .995, current * .97)
    risk = max(.01, current - stop)
    reward = max(0, target - current)
    rr = reward / risk
    score, breakdown, action, label, missing, warnings = _score_and_action(
        structure=structure, symmetry=symmetry, duration=duration_score, volume_ratio=volume_ratio,
        status=status, market_regime=market_regime, risk_reward=rr, current=current,
        breakout=neckline, vwap=vwap, close_complete=close_complete,
    )
    if (current - stop) / current > .08:
        warnings.append("停損距離超過8%，應降低部位或不進場")
        if action in {"BUY", "PROBE_BUY"}:
            action, label = "NO_TRADE", "停損過遠"
    distance = (neckline / current - 1) * 100 if current else 0
    completion = 100 if status == "CONFIRMED_BREAKOUT" else 92 if status == "INTRADAY_BREAKOUT" else max(55, 100 - max(0, distance) * 10)
    position_pct = 20 if score >= 85 else 15 if score >= 70 else 0
    if action == "PROBE_BUY":
        position_pct *= .35
    return PatternResult(
        pattern_type=pattern_type, pattern_status=status, score=score,
        start_date=points[0][1].trade_date,
        confirmed_at=datetime.combine(_breakout_confirmation_date(candles, neckline, atr), datetime.min.time()) if status == "CONFIRMED_BREAKOUT" else None,
        pivot_confirmed_date=max(item.confirmed_date for _, item in points), neckline_price=round(neckline, 4),
        breakout_price=round(neckline, 4), current_price=round(current, 4), target_price=round(target, 4),
        invalidation_price=round(invalidation, 4), stop_loss_price=round(stop, 4),
        entry_price_low=round(neckline, 4), entry_price_high=round(neckline * 1.03, 4),
        add_price=round(neckline * 1.01, 4), take_profit_1=round(current + risk, 4),
        take_profit_2=round(current + 2 * risk, 4), trailing_stop_price=round(max(stop, current - 2 * atr), 4),
        volume_ratio=round(volume_ratio, 4), distance_to_breakout_pct=round(distance, 4),
        risk_reward_ratio=round(rr, 4), completion_pct=round(completion, 2), action=action,
        action_label=label, suggested_position_pct=round(position_pct, 2),
        key_points=[_key(name, item) for name, item in points], score_breakdown=breakdown,
        reasons=reasons, missing_conditions=missing, risk_warnings=warnings,
    )


def _detect_head_shoulders(
    candles: list[Candle], pivots: list[Pivot], **context,
) -> PatternResult | None:
    for start in range(len(pivots) - 5, -1, -1):
        seq = pivots[start:start + 5]
        if [p.kind for p in seq] != ["LOW", "HIGH", "LOW", "HIGH", "LOW"]:
            continue
        left, neck1, head, neck2, right = seq
        span = right.index - left.index
        shoulders_diff = abs(left.price / right.price - 1)
        neck_diff = abs(neck1.price / neck2.price - 1)
        if not (20 <= span <= 120 and head.price <= min(left.price, right.price) * .95 and shoulders_diff <= .08 and neck_diff <= .08 and right.price > head.price):
            continue
        neckline = _line_price(neck1, neck2, len(candles) - 1)
        target = neckline + (neckline - head.price)
        return _finish(
            "HEAD_SHOULDERS_BOTTOM", candles,
            [("左肩", left), ("第一頸線", neck1), ("頭部", head), ("第二頸線", neck2), ("右肩", right)],
            neckline, target, right.price * .985, 29, max(3, 15 - shoulders_diff * 100), 10,
            ["頭部低於雙肩至少5%", "左右肩及頸線差異均在8%內", "以兩個頸線高點計算動態頸線"], **context,
        )
    return None


def _detect_double_bottom(candles: list[Candle], pivots: list[Pivot], **context) -> PatternResult | None:
    for start in range(len(pivots) - 3, -1, -1):
        first, neck, second = pivots[start:start + 3]
        if [first.kind, neck.kind, second.kind] != ["LOW", "HIGH", "LOW"]:
            continue
        span = second.index - first.index
        bottom_diff = abs(first.price / second.price - 1)
        if not (10 <= span <= 60 and bottom_diff <= .05 and second.price >= first.price * .97):
            continue
        average_bottom = (first.price + second.price) / 2
        second_vol = _average(row.volume for row in candles[max(0, second.index - 2):second.index + 3])
        first_vol = _average(row.volume for row in candles[max(0, first.index - 2):first.index + 3])
        return _finish(
            "DOUBLE_BOTTOM", candles, [("第一底", first), ("中央反彈高點", neck), ("第二底", second)],
            neck.price, neck.price + (neck.price - average_bottom), second.price * .985,
            28, max(5, 15 - bottom_diff * 150), 10,
            ["兩個底部價差在5%內", "中央反彈高點作為頸線"] + (["第二底量能小於第一底"] if second_vol <= first_vol else []), **context,
        )
    return None


def _quadratic_fit(values: list[float]) -> tuple[float, float]:
    count = len(values)
    if count < 10:
        return 0, 0
    xs = [(i - (count - 1) / 2) / max(1, count - 1) for i in range(count)]
    s0, s2, s4 = count, sum(x*x for x in xs), sum(x**4 for x in xs)
    sy, sxy, sx2y = sum(values), sum(x*y for x, y in zip(xs, values)), sum(x*x*y for x, y in zip(xs, values))
    # Symmetric x values make odd/even terms independent.
    determinant = s0 * s4 - s2 * s2
    if abs(determinant) < 1e-12 or s2 == 0:
        return 0, 0
    a = (s0 * sx2y - s2 * sy) / determinant
    b = sxy / s2
    c = (sy - a * s2) / s0
    predicted = [a*x*x + b*x + c for x in xs]
    mean = sy / count
    total = sum((y - mean) ** 2 for y in values)
    residual = sum((y - p) ** 2 for y, p in zip(values, predicted))
    return a, max(0, 1 - residual / total) if total else 0


def _detect_rounded_bottom(candles: list[Candle], pivots: list[Pivot], **context) -> PatternResult | None:
    for length in (120, 90, 60, 150, 180):
        if len(candles) < length:
            continue
        section = candles[-length:]
        closes = [row.close for row in section]
        a, r2 = _quadratic_fit(closes)
        bottom = min(range(length), key=lambda i: closes[i])
        left_slope = _average(closes[:10]) - _average(closes[length // 3:length // 3 + 10])
        right_reference_start = max(0, length * 2 // 3 - 5)
        right_slope = _average(closes[-10:]) - _average(closes[right_reference_start:right_reference_start + 10])
        ma20_now = _average(closes[-20:])
        ma20_before = _average(closes[-25:-5])
        ma60_now = _average(closes[-60:])
        ma60_before = _average(closes[-65:-5]) if len(closes) >= 65 else ma60_now
        v_shape = bottom > 2 and bottom < length - 3 and closes[bottom - 3] / closes[bottom] > 1.08 and closes[bottom + 3] / closes[bottom] > 1.08
        if (a <= 0 or r2 < .55 or left_slope <= 0 or right_slope <= 0 or v_shape
                or ma20_now <= ma20_before or ma60_now < ma60_before * .98
                or not (length * .25 <= bottom <= length * .75)):
            continue
        start_index = len(candles) - length
        neckline_index = max(range(start_index, start_index + max(5, length // 4)), key=lambda i: candles[i].high)
        low_index = start_index + bottom
        right_index = max(low_index + 1, len(candles) - 6)
        points = [
            ("圓弧起點壓力", Pivot(neckline_index, candles[neckline_index].trade_date, candles[neckline_index].high, "HIGH", min(len(candles)-1, neckline_index+5), candles[min(len(candles)-1, neckline_index+5)].trade_date)),
            ("圓弧底", Pivot(low_index, candles[low_index].trade_date, candles[low_index].low, "LOW", min(len(candles)-1, low_index+5), candles[min(len(candles)-1, low_index+5)].trade_date)),
            ("右側轉強", Pivot(right_index, candles[right_index].trade_date, candles[right_index].low, "LOW", len(candles)-1, candles[-1].trade_date)),
        ]
        neckline = candles[neckline_index].high
        target = neckline + (neckline - candles[low_index].low)
        return _finish(
            "ROUNDED_BOTTOM", candles, points, neckline, target, candles[low_index].low * .985,
            min(30, 20 + r2 * 10), min(15, r2 * 18), 10,
            [f"二次曲線係數為正，擬合度R²={r2:.2f}", "前段下降、中段走平、後段上升", "MA20斜率轉正且MA60走平或轉正", "已排除急跌急漲V形"], **context,
        )
    return None


def _detect_cup_handle(candles: list[Candle], pivots: list[Pivot], **context) -> PatternResult | None:
    highs = [p for p in pivots if p.kind == "HIGH"]
    lows = [p for p in pivots if p.kind == "LOW"]
    for right in reversed(highs):
        lefts = [p for p in highs if 20 <= right.index - p.index <= 120]
        for left in reversed(lefts):
            if left.index < 10:
                continue
            prior_low = min(row.low for row in candles[max(0, left.index - 30):left.index])
            if left.price < prior_low * 1.10:
                continue
            bottoms = [p for p in lows if left.index < p.index < right.index]
            if not bottoms:
                continue
            bottom = min(bottoms, key=lambda p: p.price)
            rim = (left.price + right.price) / 2
            depth = (rim - bottom.price) / rim
            if abs(left.price / right.price - 1) > .08 or not (.10 <= depth <= .35):
                continue
            handles = [p for p in lows if right.index < p.index <= min(len(candles)-2, right.index + 20)]
            if not handles:
                continue
            handle = min(handles, key=lambda p: p.price)
            handle_pullback = (right.price - handle.price) / max(.01, rim - bottom.price)
            cup_span = right.index - left.index
            bottom_width = sum(1 for row in candles[bottom.index-2:bottom.index+3] if row.low <= bottom.price * 1.03)
            cup_volume = _average(row.volume for row in candles[max(left.index, right.index - 20):right.index + 1])
            handle_volume = _average(row.volume for row in candles[right.index + 1:handle.index + 1])
            if handle.index - right.index < 3 or handle_pullback > .50 or bottom_width < 2 or handle_volume >= cup_volume:
                continue
            return _finish(
                "CUP_HANDLE", candles, [("左杯緣", left), ("杯底", bottom), ("右杯緣", right), ("柄部低點", handle)],
                max(left.price, right.price), max(left.price, right.price) + (rim - bottom.price), handle.price * .985,
                29, max(4, 15 - abs(left.price / right.price - 1) * 100), 10 if 20 <= cup_span <= 120 else 4,
                ["杯型前已有至少10%上升趨勢", "杯深介於10%至35%", "左右杯緣差異在8%內", "柄部回檔未超過杯深50%且形成至少3日", "柄部成交量低於杯型右側"], **context,
            )
    return None


def _detect_ascending_triangle(candles: list[Candle], pivots: list[Pivot], **context) -> PatternResult | None:
    recent = pivots[-10:]
    highs = [p for p in recent if p.kind == "HIGH"]
    lows = [p for p in recent if p.kind == "LOW"]
    if len(highs) < 2 or len(lows) < 2:
        return None
    for first_high_index in range(len(highs) - 1):
        selected_highs = [p for p in highs[first_high_index:] if abs(p.price / highs[first_high_index].price - 1) <= .03]
        selected_lows = [p for p in lows if selected_highs[0].index <= p.index <= selected_highs[-1].index + 20]
        if len(selected_highs) < 2 or len(selected_lows) < 2:
            continue
        if any(right.price <= left.price for left, right in zip(selected_lows, selected_lows[1:])):
            continue
        span = selected_lows[-1].index - selected_highs[0].index
        if not (10 <= span <= 90):
            continue
        resistance = _average(p.price for p in selected_highs)
        widest = resistance - selected_lows[0].price
        early_volume = _average(row.volume for row in candles[selected_highs[0].index:selected_lows[0].index + 1])
        late_volume = _average(row.volume for row in candles[selected_lows[-1].index:max(selected_lows[-1].index + 1, len(candles) - 1)])
        points = [(f"水平壓力{i+1}", p) for i, p in enumerate(selected_highs[-3:])]
        points += [(f"墊高低點{i+1}", p) for i, p in enumerate(selected_lows[-3:])]
        return _finish(
            "ASCENDING_TRIANGLE", candles, sorted(points, key=lambda item: item[1].index),
            resistance, resistance + widest, selected_lows[-1].price * .985,
            29, max(5, 15 - max(abs(p.price / resistance - 1) for p in selected_highs) * 200), 10,
            ["至少兩個3%內的水平波峰", "至少兩個依序墊高的波谷", "上方壓力水平、下方支撐上揚"] + (["整理期間成交量逐步縮小"] if late_volume <= early_volume else []), **context,
        )
    return None


def detect_patterns(
    candles: list[Candle], *, pivot_window: int = 5, minimum_swing_pct: float = 3,
    close_complete: bool = True, market_regime: str = "neutral", vwap: float | None = None,
) -> list[PatternResult]:
    """Detect the five bullish patterns using only candles available at call time."""
    if len(candles) < 60:
        return []
    candles = sorted(candles, key=lambda row: row.trade_date)[-180:]
    pivots = find_pivots(candles, window=max(3, min(7, pivot_window)), minimum_swing_pct=max(3, minimum_swing_pct))
    context = {"close_complete": close_complete, "market_regime": market_regime, "vwap": vwap}
    detectors = (
        _detect_head_shoulders, _detect_double_bottom, _detect_rounded_bottom,
        _detect_cup_handle, _detect_ascending_triangle,
    )
    results = [result for detector in detectors if (result := detector(candles, pivots, **context)) is not None]
    results.sort(key=lambda item: item.score, reverse=True)
    if len(results) > 1:
        for result in results:
            result.score = min(100, result.score + 5)
            result.score_breakdown["multiplePatternBonus"] = 5
            result.reasons.append("同股同時符合多個型態，可信度加5分（總分上限100）")
    return results


def risk_sized_quantity(
    *, equity: float, cash: float, entry_price: float, stop_loss_price: float,
    risk_per_trade_pct: float = .5, max_position_pct: float = 20,
) -> int:
    per_share_risk = entry_price - stop_loss_price
    if equity <= 0 or cash <= 0 or entry_price <= 0 or per_share_risk <= 0:
        return 0
    risk_quantity = int(equity * risk_per_trade_pct / 100 / per_share_risk)
    budget_quantity = int(min(cash, equity * max_position_pct / 100) / entry_price)
    return max(0, min(risk_quantity, budget_quantity))
