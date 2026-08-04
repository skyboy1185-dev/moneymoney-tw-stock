from decimal import Decimal

from .chip_flow_types import NormalizedTradeTick, TradeDirection


class TradeDirectionClassifier:
    def classify(
        self,
        tick: NormalizedTradeTick,
        previous_price: Decimal | None = None,
        previous_direction: TradeDirection = TradeDirection.UNKNOWN,
    ) -> TradeDirection:
        if tick.source_side == TradeDirection.BUY:
            return TradeDirection.BUY
        if tick.source_side == TradeDirection.SELL:
            return TradeDirection.SELL

        if tick.ask_price is not None and tick.price >= tick.ask_price:
            return TradeDirection.BUY
        if tick.bid_price is not None and tick.price <= tick.bid_price:
            return TradeDirection.SELL

        reference_price = tick.previous_price
        if reference_price is None:
            reference_price = previous_price
        if reference_price is None:
            return TradeDirection.UNKNOWN
        if tick.price > reference_price:
            return TradeDirection.BUY
        if tick.price < reference_price:
            return TradeDirection.SELL
        if previous_direction in (TradeDirection.BUY, TradeDirection.SELL):
            return previous_direction
        return TradeDirection.UNKNOWN
