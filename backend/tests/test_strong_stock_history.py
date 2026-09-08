from datetime import UTC, date, datetime, timedelta
import json

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.adaptive_schemas import AdaptiveMarketMetrics, AdaptiveScanPayload
from app.strong_stock_models import StrongStockScanArchive
from app.services.strong_stock import scan_and_persist
from app.services.strong_stock_history import history_coverage


def test_scan_inputs_are_appended_with_original_timestamp_and_config():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    at = datetime(2026, 9, 7, 7, tzinfo=UTC)
    payload = AdaptiveScanPayload(market=AdaptiveMarketMetrics(trade_date=at.date(), updated_at=at))
    with Session(engine) as db:
        scan_and_persist(db, payload, at, {'minimumEntryScore': '82'})
        scan_and_persist(db, payload, at + timedelta(minutes=1), {'minimumEntryScore': '85'})
        rows = list(db.scalars(select(StrongStockScanArchive).order_by(StrongStockScanArchive.observed_at)))
        assert len(rows) == 2
        assert json.loads(rows[0].config_json)['minimumEntryScore'] == '82'
        assert json.loads(rows[1].config_json)['minimumEntryScore'] == '85'
        assert AdaptiveScanPayload.model_validate_json(rows[0].payload_json) == payload
        assert rows[0].observed_at.replace(tzinfo=UTC) == at
        coverage = history_coverage(db, date(2026, 9, 1), date(2026, 9, 7))
        assert coverage['ready'] is False
        assert coverage['missingArchivedInputDates'] == ['2026-08-31', '2026-09-01', '2026-09-02', '2026-09-03', '2026-09-04']
        # Empty scans preserve evidence but must not manufacture stock coverage.
        assert coverage['firstSnapshotDate'] is None
        assert '2026-09-07' in coverage['missingSnapshotDates']
