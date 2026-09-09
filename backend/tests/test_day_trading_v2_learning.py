from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.day_trading_v2_models import DayTradeV2LearningDay, DayTradeV2LearningRun
from app.services import day_trading_v2_learning as learning
from app.services.day_trading_v2 import TAIPEI
from app.services.day_trading_v2_controller import REGIME_MILD


START = datetime(2026, 9, 9, 9, tzinfo=TAIPEI)


def data():
    rows = [{"timestamp": (START+timedelta(minutes=i)).isoformat(), "open": 100,
             "high": 101, "low": 99, "close": 100, "volume": 1000} for i in range(270)]
    return {"symbols": ["2330"], "bars": {"2330": rows}, "regimes": {r["timestamp"]: REGIME_MILD for r in rows}, "sectors": {}}


def test_capture_deduplicates_and_cannot_backfill_regimes():
    payload = {"bars": {}, "regimes": {}}
    engine = SimpleNamespace(minute_bars_for=lambda *a, **kw: data()["bars"]["2330"][:2])
    for _ in range(2):
        learning.capture_minute(payload, now=START+timedelta(minutes=2), symbols=["2330"], market_engine=engine, regime=REGIME_MILD)
    assert len(payload["regimes"]) == 1
    assert len(payload["bars"]["2330"]) == 1
    learning.capture_minute(payload, now=START+timedelta(minutes=10), symbols=["2330"], market_engine=engine, regime=REGIME_MILD)
    assert len(payload["regimes"]) == 1


def test_missing_session_does_not_count_as_training():
    assert learning.quality_reason({"regimes": {}})
    assert learning.quality_reason(data()) == ""


def test_validation_can_only_reject_and_oos_stays_frozen():
    state = learning.new_state({}, START)
    chosen = state["candidates"][0]
    state.update(phase="VALIDATION", days=9, routes={REGIME_MILD: chosen})
    learning.advance(state, {"trials": [], "trades": [], "stressTrades": []})
    assert state["phase"] == "OOS"
    assert state["routes"] == {}  # no runner-up or profitable-looking substitution
    frozen = deepcopy(state["candidates"])
    for _ in range(10):
        learning.advance(state, {"trials": [], "trades": [], "stressTrades": []})
    assert state["cycle"] == 2 and state["phase"] == "TRAINING"
    assert state["candidates"] == frozen
    assert not state["lastEvaluation"]["qualifiedRoutes"]
    assert state["lastEvaluation"]["automaticallyActivated"] is False


def test_daily_runner_uses_frozen_costs_and_doubled_slippage():
    state = learning.new_state({"slippageBps": "7"}, START)
    state["phase"] = "OOS"
    calls = []
    def runner(datasets, **kwargs):
        calls.append(kwargs)
        return {"trades": [], "summary": {"netPnl": "0", "tradeCount": 0}}
    learning.evaluate_day(state, data(), runner=runner)
    assert [str(c["config"]["slippageBps"]) for c in calls] == ["7", "14"]
    assert all(c["strategy_policy"] == {} for c in calls)
    assert state["days"] == 1


def test_worker_persists_once_and_pause_keeps_parameters(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(learning, "SessionLocal", sessions)
    calls = []
    def evaluate(state, payload):
        calls.append(1)
        state["days"] += 1
        return {"phase": "TRAINING", "minutes": 270}
    monkeypatch.setattr(learning, "evaluate_day", evaluate)
    with sessions() as db:
        learning.set_enabled(db, "test", True, START)
        db.add(DayTradeV2LearningDay(id="day", user_id="test", trading_date=START.date(), status="COLLECTING", data_json=learning.dumps(data()), result_json="{}"))
        db.commit()
    for _ in range(2):
        learning.process_learning_cycle(START+timedelta(hours=5))
    assert len(calls) == 1
    with sessions() as db:
        assert db.scalar(select(DayTradeV2LearningDay)).status == "COMPLETED"
        row = db.get(DayTradeV2LearningRun, "test")
        before = row.state_json
        learning.set_enabled(db, "test", False, START)
        learning.set_enabled(db, "test", True, START)
        assert row.state_json == before
