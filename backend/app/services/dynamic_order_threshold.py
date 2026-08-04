from dataclasses import dataclass
from datetime import time
from decimal import ROUND_CEILING
import math

from .chip_flow_types import NormalizedTradeTick, TradeSession


CONTINUOUS_START = time(9, 0)
CONTINUOUS_END = time(13, 30)


@dataclass(frozen=True, slots=True)
class DynamicOrderThreshold:
    amount: int
    mode: str
    percentile: float
    sample_count: int


class DynamicOrderThresholdCalculator:
    def __init__(
        self,
        floor_amount: int,
        percentile: float = 0.99,
        min_samples: int = 100,
        enabled: bool = True,
    ):
        if floor_amount <= 0:
            raise ValueError("large-order floor must be positive")
        if not 0 < percentile <= 1:
            raise ValueError("percentile must be between 0 and 1")
        if min_samples < 1:
            raise ValueError("minimum sample count must be positive")
        self.floor_amount = floor_amount
        self.percentile = percentile
        self.min_samples = min_samples
        self.enabled = enabled

    def calculate(
        self,
        ticks: list[NormalizedTradeTick],
    ) -> DynamicOrderThreshold:
        amounts = sorted(
            tick.price * tick.volume_shares
            for tick in ticks
            if (
                tick.session == TradeSession.REGULAR
                and CONTINUOUS_START < tick.timestamp.time().replace(tzinfo=None) < CONTINUOUS_END
                and tick.price > 0
                and tick.volume_shares > 0
            )
        )
        if not self.enabled or len(amounts) < self.min_samples:
            return DynamicOrderThreshold(
                amount=self.floor_amount,
                mode="fixed_floor",
                percentile=self.percentile,
                sample_count=len(amounts),
            )
        index = min(len(amounts) - 1, max(0, math.ceil(len(amounts) * self.percentile) - 1))
        percentile_amount = int(
            amounts[index].to_integral_value(rounding=ROUND_CEILING)
        )
        return DynamicOrderThreshold(
            amount=max(self.floor_amount, percentile_amount),
            mode="dynamic_percentile",
            percentile=self.percentile,
            sample_count=len(amounts),
        )
