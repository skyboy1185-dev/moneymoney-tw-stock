from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum


class TradeDirection(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    UNKNOWN = "UNKNOWN"


class OrderSize(StrEnum):
    LARGE = "LARGE"
    MEDIUM = "MEDIUM"
    SMALL = "SMALL"


class TradeSession(StrEnum):
    REGULAR = "REGULAR"
    ODD_LOT = "ODD_LOT"


@dataclass(frozen=True, slots=True)
class NormalizedTradeTick:
    id: str
    stock_id: str
    trade_date: date
    timestamp: datetime
    price: Decimal
    volume_shares: int
    bid_price: Decimal | None = None
    ask_price: Decimal | None = None
    previous_price: Decimal | None = None
    source_side: TradeDirection | None = None
    session: TradeSession = TradeSession.REGULAR


@dataclass(slots=True)
class ChipFlowTotals:
    large_buy_shares: int = 0
    large_sell_shares: int = 0
    medium_buy_shares: int = 0
    medium_sell_shares: int = 0
    small_buy_shares: int = 0
    small_sell_shares: int = 0
    unknown_shares: int = 0

    @property
    def large_net_shares(self) -> int:
        return self.large_buy_shares - self.large_sell_shares

    @property
    def medium_net_shares(self) -> int:
        return self.medium_buy_shares - self.medium_sell_shares

    @property
    def small_net_shares(self) -> int:
        return self.small_buy_shares - self.small_sell_shares

    @property
    def classified_shares(self) -> int:
        return (
            self.large_buy_shares
            + self.large_sell_shares
            + self.medium_buy_shares
            + self.medium_sell_shares
            + self.small_buy_shares
            + self.small_sell_shares
        )

    @property
    def retail_control_ratio(self) -> float | None:
        if self.classified_shares == 0:
            return None
        small_total = self.small_buy_shares + self.small_sell_shares
        return small_total / self.classified_shares * 100

    def copy(self) -> "ChipFlowTotals":
        return replace(self)


@dataclass(frozen=True, slots=True)
class ChipFlowSnapshotData:
    stock_id: str
    trade_date: date
    snapshot_time: datetime
    totals: ChipFlowTotals
    updated_at: datetime

    @property
    def large_buy_shares(self) -> int:
        return self.totals.large_buy_shares

    @property
    def large_sell_shares(self) -> int:
        return self.totals.large_sell_shares

    @property
    def large_net_shares(self) -> int:
        return self.totals.large_net_shares

    @property
    def small_buy_shares(self) -> int:
        return self.totals.small_buy_shares

    @property
    def small_sell_shares(self) -> int:
        return self.totals.small_sell_shares

    @property
    def small_net_shares(self) -> int:
        return self.totals.small_net_shares
