from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.day_trading_v2_models import DayTradeV2CalendarHoliday, DayTradeV2RuntimeState
from app.routers import day_trading_v2 as router
from app.services.day_trading_v2_quotes import QUOTE_NOTICES, fresh_quote, quote_health, update_quote_notice
from app.services.official_market_data import OfficialStockQuote


NOW = datetime(2026, 9, 8, 2, 0, tzinfo=UTC)


def quote(stamp=NOW, source="TWSE MIS", realtime=True):
    return OfficialStockQuote("2330", "台積電", 100, 99, 99, 101, 98, 1000000, 1, 1,
                              stamp.isoformat() if isinstance(stamp, datetime) else stamp, source, realtime,
                              best_bid=99.9, best_ask=100.1,
                              book_timestamp=stamp.isoformat() if isinstance(stamp, datetime) else stamp)


@pytest.mark.parametrize("stamp,source,realtime,expected", [
    (NOW, "TWSE MIS", True, True), (NOW - timedelta(seconds=15), "TWSE MIS", True, True),
    (NOW - timedelta(seconds=16), "TWSE MIS", True, False),
    (NOW + timedelta(seconds=1), "TWSE MIS", True, False),
    (NOW - timedelta(days=1), "TWSE MIS", True, False),
    ("", "TWSE MIS", True, False), (NOW, "CSV", True, False), (NOW, "TWSE MIS", False, False),
])
def test_only_verified_same_day_nonfuture_quote_is_eligible(stamp, source, realtime, expected):
    assert fresh_quote(quote(stamp, source, realtime), NOW, 15) is expected


def test_receipt_is_not_market_freshness_and_partial_staleness_is_explicit():
    state = quote_health({"2330": quote(), "2317": quote(NOW - timedelta(seconds=16))}, NOW, 15,
                         {"lastReceivedAt": NOW.isoformat(), "overCapacity": True})
    assert state["freshCount"] == state["staleCount"] == 1
    assert state["trackedCount"] == 2 and state["overCapacity"] is True
    assert state["lastReceivedAt"] == NOW.isoformat()
    missing = quote_health({"2330": quote()}, NOW, 15, {"trackedCount": 3})
    assert missing["trackedCount"] == 3 and missing["freshCount"] == 1 and missing["staleCount"] == 2


def test_reference_or_future_timestamp_cannot_mask_last_reliable_quote(monkeypatch):
    stale = quote(NOW - timedelta(seconds=20))
    quotes = {"2330": stale, "ref": quote(), "future": quote(NOW + timedelta(seconds=1))}
    quotes["ref"] = quote(source="CSV", realtime=False)
    monkeypatch.setattr(router.day_trading_engine, "official_quotes_snapshot", lambda: quotes)
    monkeypatch.setattr(router.day_trading_engine, "market_regime", lambda: {})
    monkeypatch.setattr(router.day_trading_engine, "signals", lambda: [])
    observed = router._quote_observation(NOW, router.merged_config())
    assert observed["latestQuote"] == NOW - timedelta(seconds=20)
    assert observed["fresh"] is False


@pytest.mark.parametrize("error", list(QUOTE_NOTICES) + ["策略執行例外", "行情中斷或延遲，已禁止建立新部位；另一個錯誤"])
def test_quote_recovery_clears_only_exact_known_notices(error):
    runtime = SimpleNamespace(latest_error=error)
    update_quote_notice(runtime, healthy=True, active=True)
    assert runtime.latest_error == ("" if error in QUOTE_NOTICES else error)
    runtime.latest_error = "策略執行例外"
    update_quote_notice(runtime, healthy=False, active=True)
    assert runtime.latest_error == "策略執行例外"


@pytest.fixture
def runtime_db(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(router, "_now", lambda: NOW)
    monkeypatch.setattr(router.day_trading_engine, "official_quotes_snapshot", lambda symbols=None: {"2330": quote()})
    with sessionmaker(bind=engine, expire_on_commit=False)() as db:
        router._ensure_defaults(db, "quote-user")
        runtime = DayTradeV2RuntimeState(user_id="quote-user", mode="PAPER", trading_date=NOW.date(),
                                        status="RUNNING", phase="SCANNING", heartbeat_at=NOW,
                                        scanning=True, receiving_quotes=True, order_allowed=True, last_quote_at=NOW)
        db.add(runtime)
        db.commit()
        yield db, runtime
    engine.dispose()


def test_quote_failure_does_not_become_execution_alert_or_halt(runtime_db):
    db, runtime = runtime_db
    runtime.last_quote_at = NOW - timedelta(seconds=16)
    runtime.receiving_quotes = runtime.order_allowed = False
    runtime.latest_error = next(iter(QUOTE_NOTICES))
    db.commit()
    state = router._runtime_dict(runtime, router.merged_config())
    assert state["status"] == "RUNNING" and state["running"] is True
    assert state["quoteStale"] is True and state["dataStatus"] == "stale"
    assert state["executionError"] == ""
    assert router._dashboard(db, "quote-user")["systemStatus"] == "NORMAL"


@pytest.mark.parametrize("raw", ["STOPPED", "EMERGENCY_STOP", "RISK_HALTED"])
def test_resume_does_not_unlock_explicit_stop_or_risk(runtime_db, raw):
    db, runtime = runtime_db
    runtime.status = raw
    db.commit()
    with pytest.raises(HTTPException, match="409"):
        router._set_runtime_status(db, "quote-user", "RESUME_TRADING", "RUNNING")
    assert runtime.status == raw


def test_holiday_blocks_automatic_entry_even_with_fresh_quote(runtime_db):
    db, _runtime = runtime_db
    db.add(DayTradeV2CalendarHoliday(holiday_date=NOW.date(), name="休市"))
    db.commit()
    with pytest.raises(HTTPException) as exc:
        router._verify_automatic_entry_quote(db, "quote-user", "2330", router.merged_config(), NOW)
    assert "非交易時段" in exc.value.detail


def test_expired_quote_is_rechecked_before_automatic_entry(runtime_db):
    db, _runtime = runtime_db
    router._verify_automatic_entry_quote(db, "quote-user", "2330", router.merged_config(), NOW)
    with pytest.raises(HTTPException) as exc:
        router._verify_automatic_entry_quote(db, "quote-user", "2330", router.merged_config(), NOW + timedelta(seconds=16))
    assert "該股票即時行情逾時" in exc.value.detail
