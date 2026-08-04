from decimal import Decimal

from .chip_flow_types import NormalizedTradeTick, OrderSize


class OrderSizeClassifier:
    def __init__(self, large_order_amount: int, small_order_amount: int):
        if large_order_amount <= small_order_amount:
            raise ValueError("large order threshold must exceed small order threshold")
        self.large_order_amount = Decimal(large_order_amount)
        self.small_order_amount = Decimal(small_order_amount)

    def classify(self, tick: NormalizedTradeTick) -> OrderSize:
        trade_amount = tick.price * tick.volume_shares
        if trade_amount >= self.large_order_amount:
            return OrderSize.LARGE
        if trade_amount < self.small_order_amount:
            return OrderSize.SMALL
        return OrderSize.MEDIUM
