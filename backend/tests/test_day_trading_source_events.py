import copy
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.day_trading_v2_models import DayTradeV2Notification, DayTradeV2RuntimeState, DayTradeV2Setting
from app.services import day_trading_source_events as module
from app.services.day_trading_source_events import observe_quote_source
from app.services.day_trading_v2_automation import EMAIL_EVENT_TYPES


NOW = datetime(2026, 9, 8, 1, 30, tzinfo=UTC)


class Cache:
    mode = "memory"

    def __init__(self):
        self.values = {}
        self.fail_advance = False

    def get(self, key):
        return copy.deepcopy(self.values.get(key))

    def put(self, key, value, ttl):
        if self.fail_advance and value["previous"] == "backup":
            raise ConnectionError("cache unavailable after database commit")
        self.values[key] = copy.deepcopy(value)


def diagnostic(state="primary"):
    return {"enabled": True, "sourceHealth": {"isLeader": True},
            "providerMode": "degraded" if state == "backup" else "primary",
            "activeSource": {"primary": "FUGLE_WS", "backup": "TWSE_MIS", "dead": "NONE"}[state],
            "activeSourceCounts": {"primary": {"FUGLE_WS": 10, "FUGLE_REST": 1},
                                   "backup": {"TWSE_MIS": 10, "FUGLE_REST": 1}, "dead": {}}[state],
            "indexSource": "FUGLE_REST" if state != "dead" else None}


@pytest.fixture
def harness(monkeypatch):
    monkeypatch.setattr(module, "get_settings", lambda: SimpleNamespace(app_env="development"))
    monkeypatch.setattr(module, "_is_current_leader", lambda: True)
    engine = create_engine("sqlite://")
    for model in (DayTradeV2Setting, DayTradeV2RuntimeState, DayTradeV2Notification):
        model.__table__.create(engine)
    sessions = sessionmaker(engine)
    cache = Cache()
    with sessions() as db:
        for user, mode, runtime_mode, status, day in [
            ("running", "PAPER", "PAPER", "RUNNING", NOW.date()),
            ("paused", "PAPER", "PAPER", "PAUSED", NOW.date()),
            ("stopped", "PAPER", "PAPER", "STOPPED", NOW.date()),
            ("live", "LIVE", "LIVE", "RUNNING", NOW.date()),
            ("old", "PAPER", "PAPER", "RUNNING", NOW.date() - timedelta(days=1)),
        ]:
            db.add(DayTradeV2Setting(user_id=user, trade_mode=mode))
            db.add(DayTradeV2RuntimeState(user_id=user, mode=runtime_mode, status=status, trading_date=day))
        db.add(DayTradeV2RuntimeState(user_id="no-setting", mode="PAPER", status="RUNNING", trading_date=NOW.date()))
        db.commit()

    def observe(state, seconds=0, **kwargs):
        return observe_quote_source(diagnostic(state) if isinstance(state, str) else state,
                                    NOW + timedelta(seconds=seconds), session_factory=sessions,
                                    cache=cache, **kwargs)

    yield SimpleNamespace(observe=observe, cache=cache, sessions=sessions, engine=engine)
    engine.dispose()


def notifications(harness):
    with harness.sessions() as db:
        return list(db.scalars(select(DayTradeV2Notification).order_by(DayTradeV2Notification.id)))


def test_initial_baseline_silent_and_ten_seconds_stable_switch_only_existing_active_paper(harness):
    assert harness.observe("primary") == 0
    assert harness.observe("backup", 5) == 0
    assert harness.observe("backup", 14) == 0
    assert harness.observe("backup", 15) == 2
    assert harness.observe("backup", 20) == 0
    rows = notifications(harness)
    assert {r.user_id for r in rows} == {"running", "paused"}
    assert {r.event_type for r in rows} == {"QUOTE_SOURCE_SWITCHED"}
    assert all(r.mode == "PAPER" for r in rows)
    assert all("風控" in r.message for r in rows)
    with harness.sessions() as db:
        assert db.scalar(select(func.count()).select_from(DayTradeV2Setting)) == 5


def test_flapping_resets_pending_and_recovery_uses_its_own_transition(harness):
    harness.observe("primary")
    harness.observe("backup", 5)
    harness.observe("primary", 10)
    harness.observe("backup", 11)
    assert harness.observe("backup", 20) == 0
    assert harness.observe("backup", 21) == 2
    harness.observe("primary", 25)
    assert harness.observe("primary", 35) == 2
    rows = notifications(harness)
    assert [r.event_type for r in rows] == ["QUOTE_SOURCE_SWITCHED"] * 2 + ["QUOTE_SOURCE_RECOVERED"] * 2
    assert len({r.event_id for r in rows}) == 2


def test_all_dead_event_is_ui_only_and_recovery_respects_existing_guards(harness):
    harness.observe("backup")
    harness.observe("dead", 5)
    assert harness.observe("dead", 15) == 2
    harness.observe("backup", 20)
    assert harness.observe("backup", 30) == 2
    rows = notifications(harness)
    assert rows[0].event_type == "QUOTE_SOURCE_UNAVAILABLE"
    assert rows[2].event_type == "QUOTE_SOURCE_RECOVERED" and "風控" in rows[2].message
    assert "QUOTE_SOURCE_UNAVAILABLE" not in EMAIL_EVENT_TYPES
    assert {"QUOTE_SOURCE_SWITCHED", "QUOTE_SOURCE_RECOVERED"} <= EMAIL_EVENT_TYPES


@pytest.mark.parametrize("changes", [
    {"enabled": False}, {"sourceHealth": {"isLeader": False}}, {"providerMode": "shadow"},
    {"providerMode": "mis_only"},
])
def test_inactive_shadow_or_nonleader_never_reads_or_advances_state(harness, changes):
    data = diagnostic("backup")
    data.update(changes)
    assert harness.observe(data) == 0
    assert harness.cache.values == {} and notifications(harness) == []


def test_outside_market_hours_does_not_record_state(harness):
    for now in (NOW.replace(hour=0), NOW.replace(hour=6), NOW + timedelta(days=4)):
        assert observe_quote_source(diagnostic(), now, session_factory=harness.sessions, cache=harness.cache) == 0
    assert harness.cache.values == {}


def test_normal_index_rest_is_primary_but_index_alone_is_not_stock_feed_health(harness):
    harness.observe("primary")
    assert harness.observe("primary", 20) == 0
    data = diagnostic()
    data.update(activeSource="FUGLE_REST", activeSourceCounts={"FUGLE_REST": 1})
    harness.observe(data, 25)
    assert harness.observe(data, 35) == 2
    assert notifications(harness)[0].event_type == "QUOTE_SOURCE_UNAVAILABLE"


def test_cache_advance_failure_replays_committed_transition_without_duplicate_events(harness):
    harness.observe("primary")
    harness.observe("backup", 5)
    pending = copy.deepcopy(harness.cache.values)
    harness.cache.fail_advance = True
    with pytest.raises(ConnectionError):
        harness.observe("backup", 15)
    assert len(notifications(harness)) == 2
    assert harness.cache.values == pending
    # A fresh observer process sees durable pendingSince, therefore same event ID.
    harness.cache.fail_advance = False
    assert harness.observe("backup", 20) == 0
    assert len(notifications(harness)) == 2
    assert next(iter(harness.cache.values.values()))["previous"] == "backup"


def test_database_commit_failure_keeps_pending_and_rolls_back_all_users(harness):
    class FailingSession(Session):
        def commit(self):
            raise RuntimeError("database commit failure")

    harness.observe("primary")
    harness.observe("backup", 5)
    pending = copy.deepcopy(harness.cache.values)
    with pytest.raises(RuntimeError, match="commit failure"):
        observe_quote_source(diagnostic("backup"), NOW + timedelta(seconds=15),
                             session_factory=sessionmaker(harness.engine, class_=FailingSession), cache=harness.cache)
    assert harness.cache.values == pending and notifications(harness) == []
    assert harness.observe("backup", 20) == 2


def test_redis_failure_is_not_silently_replaced_with_local_state(harness):
    class BrokenRedis:
        def get(self, key):
            raise ConnectionError("offline")

    harness.cache.mode = "redis"
    harness.cache._redis = BrokenRedis()
    with pytest.raises(ConnectionError):
        harness.observe("primary")
    assert harness.cache.values == {} and notifications(harness) == []


def test_stale_leader_snapshot_does_not_create_or_advance_events(harness, monkeypatch):
    harness.observe("primary")
    harness.observe("backup", 5)
    pending = copy.deepcopy(harness.cache.values)
    monkeypatch.setattr(module, "_is_current_leader", lambda: False)
    assert harness.observe("backup", 15) == 0
    assert harness.cache.values == pending and notifications(harness) == []


def test_lease_loss_during_database_work_rolls_back_before_commit(harness, monkeypatch):
    harness.observe("primary")
    harness.observe("backup", 5)
    pending = copy.deepcopy(harness.cache.values)
    checks = iter([True, True, False])
    monkeypatch.setattr(module, "_is_current_leader", lambda: next(checks))
    assert harness.observe("backup", 15) == 0
    assert harness.cache.values == pending and notifications(harness) == []
