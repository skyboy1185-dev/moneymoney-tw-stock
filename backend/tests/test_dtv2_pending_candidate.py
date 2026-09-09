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
