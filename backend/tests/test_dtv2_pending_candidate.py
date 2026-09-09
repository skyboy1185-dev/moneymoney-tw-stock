from datetime import UTC, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.day_trading_v2_models import DayTradeV2CandidateState
from app.routers.day_trading_v2 import _pending_candidate


def test_repeated_symbol_reuses_unflushed_candidate():
    engine = create_engine('sqlite://')
    DayTradeV2CandidateState.__table__.create(engine)
    now = datetime.now(UTC)
    with Session(engine, autoflush=False) as db:
        for strategy in ['first', 'second']:
            row = _pending_candidate(db, 'test-user', now.date(), '6239') or db.scalar(
                select(DayTradeV2CandidateState).where(DayTradeV2CandidateState.symbol == '6239'))
            if row is None:
                row = DayTradeV2CandidateState(user_id='test-user', mode='PAPER',
                    trading_date=now.date(), symbol='6239', scanned_at=now)
                db.add(row)
            row.strategy_id = strategy
        assert _pending_candidate(db, 'other-user', now.date(), '6239') is None
        db.commit()
        rows = list(db.scalars(select(DayTradeV2CandidateState)))
        assert len(rows) == 1
        assert rows[0].strategy_id == 'second'


def test_repeated_skip_reason_counts_pending_and_persisted_rows():
    from app.day_trading_v2_models import DayTradeV2SkipStat
    from app.routers.day_trading_v2 import _record_skip
    engine = create_engine('sqlite://')
    DayTradeV2SkipStat.__table__.create(engine)
    today = datetime.now(UTC).date()
    with Session(engine, autoflush=False) as db:
        for _ in range(5):
            _record_skip(db, 'test-user', 'PAPER', today, 'same reason')
        _record_skip(db, 'other-user', 'PAPER', today, 'same reason')
        db.commit()
        _record_skip(db, 'test-user', 'PAPER', today, 'same reason')
        db.commit()
        rows = list(db.scalars(select(DayTradeV2SkipStat)))
        assert len(rows) == 2
        assert {r.user_id: r.occurrence_count for r in rows} == {'test-user': 6, 'other-user': 1}
