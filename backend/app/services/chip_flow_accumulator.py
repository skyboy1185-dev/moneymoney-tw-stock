from collections import OrderedDict
from datetime import date, datetime
import logging

from .chip_flow_types import (
    ChipFlowSnapshotData,
    ChipFlowTotals,
    NormalizedTradeTick,
    OrderSize,
    TradeDirection,
)
from .order_size_classifier import OrderSizeClassifier
from .trade_direction_classifier import TradeDirectionClassifier


logger = logging.getLogger(__name__)


class ChipFlowAccumulator:
    def __init__(
        self,
        stock_id: str,
        trade_date: date,
        direction_classifier: TradeDirectionClassifier,
        size_classifier: OrderSizeClassifier,
    ):
        self.stock_id = stock_id
        self.trade_date = trade_date
        self.direction_classifier = direction_classifier
        self.size_classifier = size_classifier
        self.totals = ChipFlowTotals()
        self.snapshots: OrderedDict[datetime, ChipFlowSnapshotData] = OrderedDict()
        self.seen_trade_ids: set[str] = set()
        self.previous_price = None
        self.previous_direction = TradeDirection.UNKNOWN

    def _reset(self, trade_date: date) -> None:
        self.trade_date = trade_date
        self.totals = ChipFlowTotals()
        self.snapshots.clear()
        self.seen_trade_ids.clear()
        self.previous_price = None
        self.previous_direction = TradeDirection.UNKNOWN

    def process(self, tick: NormalizedTradeTick) -> bool:
        if tick.stock_id != self.stock_id:
            raise ValueError("tick stock does not match accumulator")
        if tick.trade_date != self.trade_date:
            self._reset(tick.trade_date)
        if tick.id in self.seen_trade_ids:
            return False
        self.seen_trade_ids.add(tick.id)
        if (
            not tick.id
            or tick.price <= 0
            or tick.volume_shares <= 0
            or tick.timestamp.date() != tick.trade_date
        ):
            logger.warning(
                "skipping invalid chip-flow tick",
                extra={
                    "stock_id": tick.stock_id,
                    "trade_id": tick.id,
                    "trade_date": tick.trade_date.isoformat(),
                    "timestamp": tick.timestamp.isoformat(),
                    "price": str(tick.price),
                    "volume_shares": tick.volume_shares,
                },
            )
            return False

        direction = self.direction_classifier.classify(
            tick,
            previous_price=self.previous_price,
            previous_direction=self.previous_direction,
        )
        size = self.size_classifier.classify(tick)
        if direction == TradeDirection.UNKNOWN:
            self.totals.unknown_shares += tick.volume_shares
        else:
            prefix = size.value.lower()
            side = "buy" if direction == TradeDirection.BUY else "sell"
            field = f"{prefix}_{side}_shares"
            setattr(self.totals, field, getattr(self.totals, field) + tick.volume_shares)
            self.previous_direction = direction

        self.previous_price = tick.price
        minute = tick.timestamp.replace(second=0, microsecond=0)
        self.snapshots[minute] = ChipFlowSnapshotData(
            stock_id=self.stock_id,
            trade_date=self.trade_date,
            snapshot_time=minute,
            totals=self.totals.copy(),
            updated_at=tick.timestamp,
        )
        return True

    @property
    def latest(self) -> ChipFlowSnapshotData | None:
        return next(reversed(self.snapshots.values()), None) if self.snapshots else None
