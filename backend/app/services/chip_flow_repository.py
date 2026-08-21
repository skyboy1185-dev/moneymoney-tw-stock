from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import ChipFlowSnapshot
from .chip_flow_types import ChipFlowSnapshotData


def _utc_minute_key(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


class ChipFlowRepository:
    def __init__(self, db: Session):
        self.db = db

    def upsert_many(self, snapshots: list[ChipFlowSnapshotData]) -> None:
        if not snapshots:
            return
        stock_id = snapshots[0].stock_id
        trade_date = snapshots[0].trade_date
        if any(
            item.stock_id != stock_id or item.trade_date != trade_date
            for item in snapshots
        ):
            raise ValueError("upsert_many snapshots must share one stock and trade date")
        existing = {
            _utc_minute_key(item.snapshot_time): item
            for item in self.db.scalars(
                select(ChipFlowSnapshot).where(
                    ChipFlowSnapshot.trade_date == trade_date,
                    ChipFlowSnapshot.stock_id == stock_id,
                    ChipFlowSnapshot.snapshot_time.in_([
                        snapshot.snapshot_time for snapshot in snapshots
                    ]),
                )
            )
        }
        for snapshot in snapshots:
            item = existing.get(_utc_minute_key(snapshot.snapshot_time))
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
        """Synchronize a reconstructed series without rewriting the whole trading day."""
        if not snapshots:
            return
        stock_id = snapshots[0].stock_id
        trade_date = snapshots[0].trade_date
        if any(
            item.stock_id != stock_id or item.trade_date != trade_date
            for item in snapshots
        ):
            raise ValueError("replace_day snapshots must share one stock and trade date")

        snapshot_times = [item.snapshot_time for item in snapshots]
        self.db.execute(
            delete(ChipFlowSnapshot).where(
                ChipFlowSnapshot.stock_id == stock_id,
                ChipFlowSnapshot.trade_date == trade_date,
                ChipFlowSnapshot.snapshot_time.not_in(snapshot_times),
            )
        )

        latest_stored = self.db.scalar(
            select(ChipFlowSnapshot.snapshot_time)
            .where(
                ChipFlowSnapshot.stock_id == stock_id,
                ChipFlowSnapshot.trade_date == trade_date,
            )
            .order_by(ChipFlowSnapshot.snapshot_time.desc())
            .limit(1)
        )
        if latest_stored is None:
            candidates = snapshots
        else:
            cutoff = _utc_minute_key(latest_stored - timedelta(minutes=10))
            candidates = [
                item for item in snapshots
                if _utc_minute_key(item.snapshot_time) >= cutoff
            ]
        self.upsert_many(candidates)

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

    def list_many_for_day(
        self,
        stock_ids: list[str],
        trade_date: date,
    ) -> dict[str, list[ChipFlowSnapshot]]:
        """Load the market-wide ticker snapshot set with one database query."""
        grouped = {stock_id: [] for stock_id in stock_ids}
        if not stock_ids:
            return grouped
        rows = self.db.scalars(
            select(ChipFlowSnapshot)
            .where(
                ChipFlowSnapshot.stock_id.in_(stock_ids),
                ChipFlowSnapshot.trade_date == trade_date,
            )
            .order_by(ChipFlowSnapshot.stock_id, ChipFlowSnapshot.snapshot_time)
        )
        for row in rows:
            grouped.setdefault(row.stock_id, []).append(row)
        return grouped
