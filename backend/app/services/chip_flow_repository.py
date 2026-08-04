from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import ChipFlowSnapshot
from .chip_flow_types import ChipFlowSnapshotData


class ChipFlowRepository:
    def __init__(self, db: Session):
        self.db = db

    def upsert_many(self, snapshots: list[ChipFlowSnapshotData]) -> None:
        for snapshot in snapshots:
            item = self.db.scalar(
                select(ChipFlowSnapshot).where(
                    ChipFlowSnapshot.trade_date == snapshot.trade_date,
                    ChipFlowSnapshot.stock_id == snapshot.stock_id,
                    ChipFlowSnapshot.snapshot_time == snapshot.snapshot_time,
                )
            )
            totals = snapshot.totals
            values = {
                "large_buy_shares": totals.large_buy_shares,
                "large_sell_shares": totals.large_sell_shares,
                "large_net_shares": totals.large_net_shares,
                "medium_buy_shares": totals.medium_buy_shares,
                "medium_sell_shares": totals.medium_sell_shares,
                "medium_net_shares": totals.medium_net_shares,
                "small_buy_shares": totals.small_buy_shares,
                "small_sell_shares": totals.small_sell_shares,
                "small_net_shares": totals.small_net_shares,
                "unknown_shares": totals.unknown_shares,
                "updated_at": snapshot.updated_at,
            }
            if item is None:
                item = ChipFlowSnapshot(
                    trade_date=snapshot.trade_date,
                    stock_id=snapshot.stock_id,
                    snapshot_time=snapshot.snapshot_time,
                    **values,
                )
                self.db.add(item)
            else:
                for field, value in values.items():
                    setattr(item, field, value)
        self.db.commit()

    def replace_day(self, snapshots: list[ChipFlowSnapshotData]) -> None:
        """Atomically replace one stock/day with a complete reconstructed series."""
        if not snapshots:
            return
        stock_id = snapshots[0].stock_id
        trade_date = snapshots[0].trade_date
        if any(
            item.stock_id != stock_id or item.trade_date != trade_date
            for item in snapshots
        ):
            raise ValueError("replace_day snapshots must share one stock and trade date")

        self.db.execute(
            delete(ChipFlowSnapshot).where(
                ChipFlowSnapshot.stock_id == stock_id,
                ChipFlowSnapshot.trade_date == trade_date,
            )
        )
        for snapshot in snapshots:
            totals = snapshot.totals
            self.db.add(ChipFlowSnapshot(
                trade_date=trade_date,
                stock_id=stock_id,
                snapshot_time=snapshot.snapshot_time,
                large_buy_shares=totals.large_buy_shares,
                large_sell_shares=totals.large_sell_shares,
                large_net_shares=totals.large_net_shares,
                medium_buy_shares=totals.medium_buy_shares,
                medium_sell_shares=totals.medium_sell_shares,
                medium_net_shares=totals.medium_net_shares,
                small_buy_shares=totals.small_buy_shares,
                small_sell_shares=totals.small_sell_shares,
                small_net_shares=totals.small_net_shares,
                unknown_shares=totals.unknown_shares,
                updated_at=snapshot.updated_at,
            ))
        self.db.commit()

    def delete_day(self, stock_id: str, trade_date: date) -> None:
        self.db.execute(
            delete(ChipFlowSnapshot).where(
                ChipFlowSnapshot.stock_id == stock_id,
                ChipFlowSnapshot.trade_date == trade_date,
            )
        )
        self.db.commit()

    def list_for_day(self, stock_id: str, trade_date: date) -> list[ChipFlowSnapshot]:
        return list(self.db.scalars(
            select(ChipFlowSnapshot)
            .where(
                ChipFlowSnapshot.stock_id == stock_id,
                ChipFlowSnapshot.trade_date == trade_date,
            )
            .order_by(ChipFlowSnapshot.snapshot_time)
        ))
