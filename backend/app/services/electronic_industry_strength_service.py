from __future__ import annotations

from dataclasses import dataclass

from ..adaptive_schemas import AdaptiveIndustryInput


@dataclass(frozen=True)
class IndustryStrengthResult:
    sub_industry: str
    score: float
    rank: int
    continuation_days: int
    breakdown: dict[str, float]


def _scaled(value: float | None, low: float, high: float, weight: float) -> float:
    if value is None or high <= low:
        return 0
    return max(0, min(weight, (value - low) / (high - low) * weight))


def rank_industries(items: list[AdaptiveIndustryInput]) -> list[IndustryStrengthResult]:
    scored: list[tuple[AdaptiveIndustryInput, dict[str, float], float]] = []
    for item in items:
        momentum = (
            _scaled(item.return_1d, -3, 5, 6)
            + _scaled(item.return_3d, -5, 10, 7)
            + _scaled(item.return_5d, -8, 15, 7)
            + _scaled(item.return_20d, -15, 30, 10)
        )
        volume = _scaled(item.volume_growth, -30, 100, 20)
        breadth = _scaled(item.advance_ratio, 20, 80, 10) + _scaled(item.new_high_ratio, 0, 30, 5)
        institutional = _scaled(item.foreign_net_buy, -10_000, 10_000, 8) + _scaled(item.investment_trust_net_buy, -5_000, 5_000, 7)
        holders = _scaled(item.large_holder_change, -2, 2, 10)
        continuation = min(10, max(0, item.continuation_days * 2.5))
        breakdown = {
            "價格動能": round(momentum, 2), "成交量與成交金額": round(volume, 2),
            "市場寬度": round(breadth, 2), "法人籌碼": round(institutional, 2),
            "大戶籌碼": round(holders, 2), "強勢延續性": round(continuation, 2),
        }
        scored.append((item, breakdown, round(sum(breakdown.values()), 2)))
    scored.sort(key=lambda row: row[2], reverse=True)
    return [
        IndustryStrengthResult(item.sub_industry, score, index + 1, item.continuation_days, breakdown)
        for index, (item, breakdown, score) in enumerate(scored)
    ]
