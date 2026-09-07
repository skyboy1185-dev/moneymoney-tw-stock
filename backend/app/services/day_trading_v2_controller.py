from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from decimal import Decimal
from typing import Iterable, Mapping, Sequence

from .day_trading_v2 import STRATEGIES, dec


REGIME_STRONG = "A_STRONG_TREND"
REGIME_MILD = "B_MILD_BULL"
REGIME_RANGE = "C_RANGE"
REGIME_WEAK = "D_WEAK"
REGIME_CRASH = "E_CRASH"
REGIME_UNKNOWN = "UNKNOWN"

REGIME_LABELS = {
    REGIME_STRONG: "強勢趨勢盤",
    REGIME_MILD: "溫和多頭盤",
    REGIME_RANGE: "區間震盪盤",
    REGIME_WEAK: "弱勢盤",
    REGIME_CRASH: "急跌或異常盤",
    REGIME_UNKNOWN: "資料不足",
}

STRATEGY_WINDOWS = {
    "OPENING_RANGE_BREAKOUT": (time(9, 15), time(11, 0)),
    "VWAP_TREND_PULLBACK": (time(9, 5), time(13, 20)),
    "VOLUME_HIGH_BREAKOUT": (time(9, 5), time(13, 20)),
    "FALSE_BREAKDOWN_REVERSAL": (time(9, 15), time(12, 30)),
    "AFTERNOON_STRENGTH_BREAKOUT": (time(12, 30), time(13, 20)),
}

STRATEGY_WINDOW_CONFIG = {
    "OPENING_RANGE_BREAKOUT": ("openingStrategyStart", "openingStrategyEnd"),
    "VWAP_TREND_PULLBACK": ("vwapStrategyStart", "vwapStrategyEnd"),
    "VOLUME_HIGH_BREAKOUT": ("volumeStrategyStart", "volumeStrategyEnd"),
    "FALSE_BREAKDOWN_REVERSAL": ("reversalStrategyStart", "reversalStrategyEnd"),
    "AFTERNOON_STRENGTH_BREAKOUT": ("afternoonStrategyStart", "afternoonStrategyEnd"),
}

REGIME_ADJUSTMENTS = {
    REGIME_STRONG: {
        "OPENING_RANGE_BREAKOUT": 10, "VWAP_TREND_PULLBACK": 8,
        "VOLUME_HIGH_BREAKOUT": 8, "FALSE_BREAKDOWN_REVERSAL": -10,
        "AFTERNOON_STRENGTH_BREAKOUT": 5,
    },
    REGIME_MILD: {
        "OPENING_RANGE_BREAKOUT": 5, "VWAP_TREND_PULLBACK": 10,
        "VOLUME_HIGH_BREAKOUT": 7, "FALSE_BREAKDOWN_REVERSAL": -10,
        "AFTERNOON_STRENGTH_BREAKOUT": 3,
    },
    REGIME_RANGE: {
        "OPENING_RANGE_BREAKOUT": -15, "VWAP_TREND_PULLBACK": 8,
        "VOLUME_HIGH_BREAKOUT": -15, "FALSE_BREAKDOWN_REVERSAL": 8,
        "AFTERNOON_STRENGTH_BREAKOUT": -5,
    },
    REGIME_WEAK: {
        "OPENING_RANGE_BREAKOUT": -100, "VWAP_TREND_PULLBACK": -15,
        "VOLUME_HIGH_BREAKOUT": -10, "FALSE_BREAKDOWN_REVERSAL": -100,
        "AFTERNOON_STRENGTH_BREAKOUT": -20,
    },
    REGIME_CRASH: {strategy_id: -100 for strategy_id, _, _ in STRATEGIES},
    REGIME_UNKNOWN: {strategy_id: -100 for strategy_id, _, _ in STRATEGIES},
}


@dataclass(frozen=True)
class MarketInputs:
    index_price: Decimal
    index_vwap: Decimal
    trend_1m_pct: Decimal
    trend_5m_pct: Decimal
    breadth_pct: Decimal
    relative_volume: Decimal
    strong_sector_count: int = 0
    weak_sector_count: int = 0
    vwap_crosses_30m: int = 0
    quote_coverage_pct: Decimal = Decimal("100")
    data_normal: bool = True
    spread_expansion_ratio: Decimal = Decimal("1")

    @property
    def vwap_deviation_pct(self) -> Decimal:
        if not self.index_vwap:
            return Decimal("0")
        return (self.index_price - self.index_vwap) / self.index_vwap * Decimal("100")


@dataclass(frozen=True)
class MarketRegimeResult:
    proposed: str
    effective: str
    confidence: Decimal
    reasons: tuple[str, ...]
    data_blocked: bool = False


def classify_market(inputs: MarketInputs, config: Mapping[str, object] | None = None) -> MarketRegimeResult:
    cfg = config or {}
    coverage_min = dec(cfg.get("regimeMinCoveragePct", "80"))
    crash_1m = dec(cfg.get("regimeCrash1mPct", "-0.5"))
    crash_5m = dec(cfg.get("regimeCrash5mPct", "-1.0"))
    crash_breadth = dec(cfg.get("regimeCrashBreadthPct", "20"))
    crash_spread = dec(cfg.get("regimeCrashSpreadRatio", "2"))
    deviation = inputs.vwap_deviation_pct

    if not inputs.data_normal or inputs.quote_coverage_pct < coverage_min:
        reasons = ("行情來源異常" if not inputs.data_normal else "行情覆蓋率不足",)
        return MarketRegimeResult(REGIME_CRASH, REGIME_CRASH, Decimal("100"), reasons, True)
    crash = (
        inputs.trend_1m_pct <= crash_1m
        or (inputs.trend_5m_pct <= crash_5m and inputs.breadth_pct <= Decimal("25"))
        or (inputs.breadth_pct <= crash_breadth and inputs.trend_5m_pct <= Decimal("-0.6"))
        or inputs.spread_expansion_ratio >= crash_spread
    )
    if crash:
        return MarketRegimeResult(
            REGIME_CRASH, REGIME_CRASH, Decimal("95"),
            ("指數短線急跌或市場交易品質異常", "禁止建立新部位"),
        )

    strong_checks = (
        deviation >= dec(cfg.get("regimeStrongVwapDeviationPct", "0.15")),
        inputs.trend_5m_pct >= dec(cfg.get("regimeStrongTrend5mPct", "0.30")),
        inputs.breadth_pct >= dec(cfg.get("regimeStrongBreadthPct", "60")),
        inputs.relative_volume >= dec(cfg.get("regimeStrongRelativeVolume", "1.05")),
        inputs.strong_sector_count >= int(cfg.get("regimeStrongSectorCount", 3)),
    )
    weak_checks = (
        deviation <= dec(cfg.get("regimeWeakVwapDeviationPct", "-0.15")),
        inputs.trend_5m_pct <= dec(cfg.get("regimeWeakTrend5mPct", "-0.25")),
        inputs.breadth_pct <= dec(cfg.get("regimeWeakBreadthPct", "40")),
        inputs.weak_sector_count > inputs.strong_sector_count,
    )
    mild_checks = (
        deviation >= dec(cfg.get("regimeMildVwapDeviationPct", "-0.05")),
        inputs.trend_5m_pct >= dec(cfg.get("regimeMildTrend5mPct", "0")),
        inputs.breadth_pct >= dec(cfg.get("regimeMildBreadthPct", "52")),
        inputs.relative_volume >= dec(cfg.get("regimeMildRelativeVolume", "0.9")),
    )
    range_checks = (
        abs(deviation) <= Decimal("0.15"), abs(inputs.trend_5m_pct) <= Decimal("0.25"),
        Decimal("42") <= inputs.breadth_pct <= Decimal("58"), inputs.vwap_crosses_30m >= 3,
    )
    if sum(strong_checks) >= 4:
        state, matched = REGIME_STRONG, sum(strong_checks)
    elif sum(weak_checks) >= 3:
        state, matched = REGIME_WEAK, sum(weak_checks)
    elif sum(mild_checks) >= 3:
        state, matched = REGIME_MILD, sum(mild_checks)
    else:
        state, matched = REGIME_RANGE, max(sum(range_checks), 2)
    confidence = min(Decimal("95"), Decimal("55") + Decimal(matched * 8))
    reasons = (
        f"指數相對VWAP {deviation:+.2f}%",
        f"5分鐘趨勢 {inputs.trend_5m_pct:+.2f}%",
        f"上漲家數比例 {inputs.breadth_pct:.1f}%",
        f"相對量 {inputs.relative_volume:.2f}",
    )
    return MarketRegimeResult(state, state, confidence, reasons)


def apply_regime_hysteresis(
    proposed: MarketRegimeResult, *, current: str | None,
    recent_proposals: Sequence[str] = (), recovery_cycles: int = 3, switch_cycles: int = 2,
) -> MarketRegimeResult:
    if proposed.proposed == REGIME_CRASH:
        return proposed
    if not current or current == REGIME_UNKNOWN:
        return proposed
    needed = recovery_cycles if current == REGIME_CRASH else switch_cycles
    tail = [*recent_proposals, proposed.proposed][-needed:]
    if len(tail) >= needed and len(set(tail)) == 1:
        return proposed
    return MarketRegimeResult(
        proposed.proposed, current, max(Decimal("0"), proposed.confidence - Decimal("15")),
        (*proposed.reasons, f"等待{needed}次連續判定後切換"), proposed.data_blocked,
    )


@dataclass(frozen=True)
class ControllerCandidateInput:
    key: str
    symbol: str
    stock_name: str
    sector: str
    strategy_id: str
    strategy_version: str
    signal_time: datetime
    raw_score: Decimal
    entry_price: Decimal
    stop_price: Decimal
    target_price: Decimal
    risk_reward: Decimal
    sector_strength: Decimal = Decimal("50")
    health_status: str = "NORMAL"
    liquidity_score: Decimal = Decimal("70")
    vwap_deviation_pct: Decimal = Decimal("0")
    oos_profit_factor: Decimal = Decimal("0")
    health_score: Decimal = Decimal("50")
    market_stabilized: bool = False
    correlation: Decimal | None = None
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class RankedCandidate:
    candidate: ControllerCandidateInput
    final_score: Decimal
    regime_adjustment: Decimal
    sector_adjustment: Decimal
    health_adjustment: Decimal
    liquidity_adjustment: Decimal
    concentration_penalty: Decimal
    chase_penalty: Decimal
    allowed: bool
    blocked_reasons: tuple[str, ...] = ()
    rank: int | None = None
    score_details: Mapping[str, str] = field(default_factory=dict)


def strategy_time_allowed(
    strategy_id: str, local_time: time, config: Mapping[str, object] | None = None,
) -> bool:
    start, end = STRATEGY_WINDOWS[strategy_id]
    cfg = config or {}
    start_key, end_key = STRATEGY_WINDOW_CONFIG[strategy_id]
    if start_key in cfg:
        start = time.fromisoformat(str(cfg[start_key]))
    if end_key in cfg:
        end = time.fromisoformat(str(cfg[end_key]))
    return start <= local_time < end


def score_candidate(
    candidate: ControllerCandidateInput, *, regime: str, local_time: time,
    existing_symbols: set[str] | None = None, sector_positions: int = 0,
    config: Mapping[str, object] | None = None,
) -> RankedCandidate:
    cfg = config or {}
    blocked: list[str] = []
    existing_symbols = existing_symbols or set()
    adjustment = Decimal(REGIME_ADJUSTMENTS.get(regime, REGIME_ADJUSTMENTS[REGIME_UNKNOWN]).get(candidate.strategy_id, -100))
    if adjustment <= -100:
        blocked.append("策略不適合目前盤勢")
    if not strategy_time_allowed(candidate.strategy_id, local_time, cfg):
        blocked.append("不在策略有效時段")
    if candidate.symbol in existing_symbols:
        blocked.append("同股票已有持倉")
    if not candidate.sector:
        blocked.append("缺少產業分類")
    max_sector = int(cfg.get("maxSectorPositions", 2))
    if sector_positions >= max_sector:
        blocked.append("同產業持倉已達上限")
    if candidate.strategy_id == "FALSE_BREAKDOWN_REVERSAL" and regime == REGIME_MILD and candidate.raw_score < Decimal("85"):
        blocked.append("溫和多頭盤僅允許高分反轉訊號")
    if regime == REGIME_WEAK and candidate.strategy_id == "VOLUME_HIGH_BREAKOUT" and candidate.raw_score < Decimal("90"):
        blocked.append("弱勢盤爆量突破分數不足90")
    if regime == REGIME_WEAK and candidate.strategy_id == "VWAP_TREND_PULLBACK" and not candidate.market_stabilized:
        blocked.append("弱勢盤尚未停止下跌")

    sector_adj = Decimal("5") if candidate.sector_strength >= 70 else Decimal("-5") if candidate.sector_strength < 40 else Decimal("0")
    health_adj = Decimal("-10") if candidate.health_status == "ALERT" else Decimal("0")
    if candidate.health_status in {"PAUSED", "DISABLED"}:
        blocked.append("策略已被績效風控暫停")
    liquidity_adj = Decimal("5") if candidate.liquidity_score >= 90 else Decimal("0")
    if candidate.liquidity_score < dec(cfg.get("controllerMinLiquidityScore", "70")):
        blocked.append("流動性不足")
    concentration = Decimal("10") if sector_positions == 1 else Decimal("0")
    if candidate.correlation is not None and candidate.correlation >= Decimal("0.8"):
        concentration += Decimal("5")
    deviation = abs(candidate.vwap_deviation_pct)
    max_deviation = dec(cfg.get("maximumVwapDeviationPct", "1.5"))
    chase = min(Decimal("20"), deviation / max(max_deviation, Decimal("0.01")) * Decimal("20"))
    if deviation > max_deviation:
        blocked.append("股價偏離VWAP過遠")
    if candidate.risk_reward < dec(cfg.get("controllerMinimumRiskReward", "2")):
        blocked.append("風險報酬比不足1比2")
    total = max(Decimal("0"), min(Decimal("100"), candidate.raw_score + adjustment + sector_adj + health_adj + liquidity_adj - concentration - chase))
    if total < dec(cfg.get("controllerMinimumScore", "80")):
        blocked.append("最終分數不足80")
    details = {
        "rawScore": str(candidate.raw_score), "regime": str(adjustment), "sector": str(sector_adj),
        "health": str(health_adj), "liquidity": str(liquidity_adj),
        "concentrationPenalty": str(concentration), "chasePenalty": str(chase.quantize(Decimal("0.01"))),
        "sectorStrength": str(candidate.sector_strength),
        "strategyHealthScore": str(candidate.health_score),
        "oosProfitFactor": str(candidate.oos_profit_factor),
        "correlation": str(candidate.correlation) if candidate.correlation is not None else None,
    }
    return RankedCandidate(
        candidate, total.quantize(Decimal("0.1")), adjustment, sector_adj, health_adj,
        liquidity_adj, concentration, chase.quantize(Decimal("0.01")), not blocked,
        tuple(dict.fromkeys(blocked)), score_details=details,
    )


def rank_candidates(scored: Iterable[RankedCandidate]) -> list[RankedCandidate]:
    ordered = sorted(
        scored,
        key=lambda row: (
            row.allowed, row.final_score, row.candidate.risk_reward,
            row.candidate.oos_profit_factor, row.candidate.health_score,
            row.candidate.liquidity_score,
            -(row.candidate.correlation if row.candidate.correlation is not None else Decimal("0")),
            -row.candidate.signal_time.timestamp(),
        ),
        reverse=True,
    )
    winners: dict[str, RankedCandidate] = {}
    result: list[RankedCandidate] = []
    rank = 0
    for row in ordered:
        blocked = list(row.blocked_reasons)
        if row.candidate.symbol in winners:
            blocked.append("重複股票訊號，較低排名未執行")
        allowed = row.allowed and not blocked
        if allowed:
            winners[row.candidate.symbol] = row
            rank += 1
        result.append(RankedCandidate(
            **{**row.__dict__, "allowed": allowed, "blocked_reasons": tuple(dict.fromkeys(blocked)), "rank": rank if allowed else None}
        ))
    return result


def risk_multiplier_for_regime(regime: str) -> Decimal:
    return Decimal("0.5") if regime == REGIME_WEAK else Decimal("0") if regime in {REGIME_CRASH, REGIME_UNKNOWN} else Decimal("1")
