from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.day_trading_v2_models import DayTradeV2CalendarHoliday, DayTradeV2RuntimeState, DayTradeV2Setting
from app.models import LongTermPortfolioRun, PatternRobotRun
from app.routers.robot_health import router
from app.services import robot_health as health
from app.strong_stock_models import StrongStockAccount, StrongStockDataRun, StrongStockSetting


NOW = datetime(2026, 9, 8, 2, 0, tzinfo=UTC)  # Tuesday 10:00 in Taipei.


def settings(holidays=""):
    return SimpleNamespace(twse_holidays=holidays, rocket_radar_enabled=True,
                           pattern_robot_scan_interval_seconds=180, rocket_radar_scan_interval_seconds=60)


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(health, "get_settings", settings)
    monkeypatch.setattr(health, "day_trading_quote_pump", SimpleNamespace(diagnostics=lambda: {}))
    for name in ("limit_up_ai_automation", "pattern_robot_automation", "rocket_radar_automation", "long_term_selection_automation", "strong_stock_automation"):
        # Only observational state is available: invoking any worker method fails.
        monkeypatch.setattr(health, name, SimpleNamespace(state={
            "status": "running", "lastRunAt": NOW.isoformat(), "lastSuccessAt": NOW.isoformat(),
            "lastResult": {"userId": "private-other-user", "queued": {"private-other-user": 5}},
        }))
    with sessions() as session:
        yield session
    engine.dispose()


def by_id(payload):
    return {item["id"]: item for item in payload["items"]}


def add_runtime(db, uid="health-user", mode="PAPER", **values):
    if db.get(DayTradeV2Setting, uid) is None:
        db.add(DayTradeV2Setting(user_id=uid, trade_mode=mode))
    defaults = dict(user_id=uid, mode=mode, trading_date=NOW.date(), status="RUNNING", phase="SCANNING",
                    scanning=True, heartbeat_at=NOW, last_quote_at=NOW, last_scan_at=NOW)
    defaults.update(values)
    db.add(DayTradeV2RuntimeState(**defaults))
    db.commit()


def test_get_is_read_only_scoped_and_has_six_current_bots(db):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: db
    statements = []
    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)
    event.listen(db.bind, "before_cursor_execute", capture)
    try:
        response = TestClient(app).get("/api/v1/robot-health", headers={"x-user-id": "health-user"})
    finally:
        event.remove(db.bind, "before_cursor_execute", capture)
    assert response.status_code == 200
    assert len(statements) <= 15
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
    payload = response.json()
    assert list(by_id(payload)) == ["day-trading-v2", "limit-up-ai", "pattern-robot", "rocket-radar", "long-term", "strong-stocks"]
    assert "private-other-user" not in response.text
    assert by_id(payload)["day-trading-v2"]["execution"]["status"] == "unknown"
    assert by_id(payload)["strong-stocks"]["execution"]["status"] == "unknown"
    assert db.scalar(select(func.count()).select_from(DayTradeV2Setting)) == 0
    assert db.scalar(select(func.count()).select_from(DayTradeV2RuntimeState)) == 0
    assert db.scalar(select(func.count()).select_from(StrongStockSetting)) == 0
    assert TestClient(app).get("/api/v1/robot-health").status_code == 422


def test_pending_changes_are_not_autoflushed(db):
    db.add(DayTradeV2Setting(user_id="pending-user"))
    health.robot_health_summary(db, "other-user", now=NOW)
    assert len(db.new) == 1
    with db.no_autoflush:
        assert db.scalar(select(func.count()).select_from(DayTradeV2Setting)) == 0


@pytest.mark.parametrize("entitled", [True, False])
def test_provider_summary_is_observational_sanitized_and_not_execution_failure(db, monkeypatch, entitled):
    add_runtime(db)
    diagnostics = {"providerMode": "shadow", "activeSource": "TWSE_MIS", "ready": False,
                   "entitlementReady": entitled, "entitlementReason": "private-api-key",
                   "sourceHealth": {"wsConnected": True, "acknowledgedCount": 5, "wsSubscriptionLimit": 300,
                                    "lastError": "private-secret", "headers": {"api-key": "private-key"},
                                    "subscriptionSymbols": ["private-symbol"]}}
    monkeypatch.setattr(health, "day_trading_quote_pump", SimpleNamespace(diagnostics=lambda: diagnostics))
    statements = []
    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)
    event.listen(db.bind, "before_cursor_execute", capture)
    try:
        item = by_id(health.robot_health_summary(db, "health-user", now=NOW))["day-trading-v2"]
    finally:
        event.remove(db.bind, "before_cursor_execute", capture)
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in statements)
    assert item["execution"]["status"] == "running"
    assert item["data"]["status"] == "current"
    assert "尚未啟用" in item["data"]["reason"]
    assert ("額度尚未就緒" in item["data"]["reason"]) is (not entitled)
    assert item["data"]["provider"]["entitlementReady"] is entitled
    assert "private" not in str(item)


def test_provider_diagnostic_failure_does_not_initialize_or_break_health(db, monkeypatch):
    add_runtime(db)
    def fail():
        raise RuntimeError("private-key")
    monkeypatch.setattr(health, "day_trading_quote_pump", SimpleNamespace(diagnostics=fail))
    item = by_id(health.robot_health_summary(db, "health-user", now=NOW))["day-trading-v2"]
    assert item["data"]["status"] == "current"
    assert "private" not in str(item)


def test_no_selected_provider_data_does_not_leave_recent_runtime_quote_green(db, monkeypatch):
    add_runtime(db)
    monkeypatch.setattr(health, "day_trading_quote_pump", SimpleNamespace(diagnostics=lambda: {
        "providerMode": "primary", "activeSource": "NONE", "ready": True, "entitlementReady": True}))
    item = by_id(health.robot_health_summary(db, "health-user", now=NOW))["day-trading-v2"]
    assert item["execution"]["status"] == "running"
    assert item["data"]["status"] == "stale"


def test_user_and_mode_isolation(db):
    add_runtime(db, "other-user", latest_error="private account error")
    add_runtime(db, mode="BACKTEST", latest_error="private backtest error")
    db.get(DayTradeV2Setting, "health-user").trade_mode = "PAPER"
    db.add(StrongStockSetting(user_id="other-user", paper_enabled=False))
    db.add(StrongStockAccount(user_id="other-user", trading_paused=True))
    db.commit()
    payload = health.robot_health_summary(db, "health-user", now=NOW)
    assert "private" not in str(payload)
    items = by_id(payload)
    assert items["day-trading-v2"]["mode"]["tradeMode"] == "PAPER"
    assert items["day-trading-v2"]["execution"]["status"] == "unknown"
    assert items["strong-stocks"]["execution"]["enabled"] is None


def test_active_stale_quotes_are_distinct_from_healthy_execution(db):
    add_runtime(db, last_quote_at=NOW - timedelta(minutes=2))
    item = by_id(health.robot_health_summary(db, "health-user", now=NOW))["day-trading-v2"]
    assert item["execution"]["status"] == "running"
    assert item["data"]["status"] == "stale"
    row = db.scalar(select(DayTradeV2RuntimeState))
    row.heartbeat_at = NOW - timedelta(minutes=2)
    db.commit()
    item = by_id(health.robot_health_summary(db, "health-user", now=NOW))["day-trading-v2"]
    assert item["execution"]["status"] == "error"
    assert "心跳" in item["execution"]["error"]


@pytest.mark.parametrize("notice", sorted(health.V2_QUOTE_NOTICES))
def test_completed_v2_quote_notice_is_historical_data_information(db, notice):
    closed = NOW.replace(hour=8)
    add_runtime(db, status="COMPLETED", phase="COMPLETED", scanning=False,
                heartbeat_at=closed, last_quote_at=NOW, latest_error=notice)
    item = by_id(health.robot_health_summary(db, "health-user", now=closed))["day-trading-v2"]
    assert item["execution"]["status"] == "waiting"
    assert item["execution"]["error"] is None
    assert item["data"]["status"] == "waiting"
    assert notice in item["data"]["reason"]


@pytest.mark.parametrize("notice", sorted(health.V2_QUOTE_NOTICES))
@pytest.mark.parametrize("quote,expected", [(None, "unavailable"), (NOW - timedelta(minutes=2), "stale"), (NOW, "stale")])
def test_active_quote_notice_is_data_status_without_hiding_execution(db, notice, quote, expected):
    add_runtime(db, last_quote_at=quote, latest_error=notice)
    item = by_id(health.robot_health_summary(db, "health-user", now=NOW))["day-trading-v2"]
    assert item["execution"]["status"] == "running"
    assert item["execution"]["error"] is None
    assert item["data"]["status"] == expected
    assert item["data"]["reason"] == notice


@pytest.mark.parametrize("status,error", [
    ("ALERT", "行情中斷或延遲，已禁止建立新部位"),
    ("EMERGENCY_STOP", "行情來源尚未就緒，開盤後會持續重試並禁止新交易"),
    ("COMPLETED", "RuntimeError: strategy execution failed"),
    ("COMPLETED", "行情中斷或延遲，已禁止建立新部位；另一個執行例外"),
])
def test_explicit_v2_execution_failures_remain_errors_after_close(db, status, error):
    closed = NOW.replace(hour=8)
    add_runtime(db, status=status, latest_error=error, heartbeat_at=closed)
    item = by_id(health.robot_health_summary(db, "health-user", now=closed))["day-trading-v2"]
    assert item["execution"]["status"] == "error"
    if error not in health.V2_QUOTE_NOTICES:
        assert item["execution"]["error"] == error


def test_quote_notice_does_not_hide_missing_execution_heartbeat(db):
    add_runtime(db, latest_error="行情中斷或延遲，已禁止建立新部位", heartbeat_at=None)
    item = by_id(health.robot_health_summary(db, "health-user", now=NOW))["day-trading-v2"]
    assert item["execution"]["status"] == "error"
    assert item["execution"]["error"] == "執行心跳逾時"


@pytest.mark.parametrize("instant,holidays", [
    (NOW.replace(hour=8), ""),  # after close
    (NOW.replace(day=12), ""),  # Saturday
    (NOW, "2026-09-08"),  # configured exchange holiday
])
def test_inactive_sessions_do_not_flag_old_quotes_or_heartbeats(db, monkeypatch, instant, holidays):
    monkeypatch.setattr(health, "get_settings", lambda: settings(holidays))
    add_runtime(db, trading_date=instant.date(), heartbeat_at=instant - timedelta(hours=2), last_quote_at=instant - timedelta(hours=2))
    item = by_id(health.robot_health_summary(db, "health-user", now=instant))["day-trading-v2"]
    assert item["execution"]["status"] == "waiting"
    assert item["data"]["status"] == "waiting"


def test_explicit_outage_survives_closed_market_and_shared_details_are_sanitized(db):
    health.rocket_radar_automation.state.update(status="error", lastError="private-other-user failed")
    item = by_id(health.robot_health_summary(db, "health-user", now=NOW.replace(hour=8)))["rocket-radar"]
    assert item["execution"]["status"] == "error"
    assert "private" not in str(item)


def test_cached_exchange_holiday_is_respected_without_refreshing_calendar(db):
    db.add(DayTradeV2CalendarHoliday(holiday_date=NOW.date(), name="休市日"))
    db.commit()
    add_runtime(db, heartbeat_at=NOW - timedelta(hours=2), last_quote_at=NOW - timedelta(hours=2))
    payload = health.robot_health_summary(db, "health-user", now=NOW)
    assert payload["market"]["isTradingDay"] is False
    item = by_id(payload)["day-trading-v2"]
    assert item["execution"]["status"] == "waiting"
    assert item["data"]["status"] == "waiting"


def test_shared_success_does_not_claim_fresh_quotes(db):
    items = by_id(health.robot_health_summary(db, "health-user", now=NOW))
    for robot in ("rocket-radar", "limit-up-ai"):
        assert items[robot]["scope"] == "shared"
        assert items[robot]["execution"]["lastSuccessAt"] == NOW.isoformat()
        assert items[robot]["data"]["status"] == "unknown"
        assert items[robot]["data"]["updatedAt"] is None


@pytest.mark.parametrize("last_run", [None, "invalid", (NOW - timedelta(hours=1)).isoformat()])
def test_shared_workers_need_recent_execution_evidence(db, last_run):
    db.add(StrongStockSetting(user_id="health-user"))
    db.add(StrongStockAccount(user_id="health-user"))
    db.commit()
    for worker in (health.limit_up_ai_automation, health.pattern_robot_automation, health.rocket_radar_automation,
                   health.long_term_selection_automation, health.strong_stock_automation):
        worker.state["lastRunAt"] = last_run
    items = by_id(health.robot_health_summary(db, "health-user", now=NOW))
    for robot in ("limit-up-ai", "pattern-robot", "rocket-radar", "long-term", "strong-stocks"):
        assert items[robot]["execution"]["status"] == "unknown"
        assert items[robot]["execution"]["error"] is None
        assert "執行紀錄" in items[robot]["execution"]["reason"]


def test_pattern_old_execution_after_close_is_expected_waiting(db):
    item = by_id(health.robot_health_summary(db, "health-user", now=NOW.replace(hour=8)))["pattern-robot"]
    assert item["execution"]["status"] == "waiting"
    assert item["execution"]["error"] is None


def test_strong_data_insufficient_and_user_pause_are_not_execution_failure(db):
    db.add(StrongStockSetting(user_id="health-user"))
    db.add(StrongStockAccount(user_id="health-user", trading_paused=True))
    db.add(StrongStockDataRun(id="completed", trade_date=date(2026, 9, 7), status="COMPLETED", started_at=NOW - timedelta(days=1), completed_at=NOW - timedelta(days=1)))
    db.add(StrongStockDataRun(id="incomplete", trade_date=NOW.date(), status="DATA_INSUFFICIENT", started_at=NOW))
    db.commit()
    item = by_id(health.robot_health_summary(db, "health-user", now=NOW))["strong-stocks"]
    assert item["execution"]["status"] == "paused"
    assert item["execution"]["error"] is None
    assert item["mode"]["tradeMode"] == "PAPER"
    assert item["data"]["status"] == "waiting"
    assert item["data"]["tradeDate"] == "2026-09-07"


def test_durable_daily_freshness_requires_both_long_term_portfolios(db):
    db.add(PatternRobotRun(trade_date=NOW.date(), status="COMPLETED", started_at=NOW, completed_at=NOW))
    db.add(LongTermPortfolioRun(portfolio_mode="long_only", trade_date=NOW.date(), ran_at=NOW))
    db.commit()
    items = by_id(health.robot_health_summary(db, "health-user", now=NOW))
    assert items["pattern-robot"]["data"]["status"] == "current"
    assert items["long-term"]["data"]["status"] == "unknown"
    db.add(LongTermPortfolioRun(portfolio_mode="focused_long", trade_date=NOW.date(), ran_at=NOW))
    db.commit()
    assert by_id(health.robot_health_summary(db, "health-user", now=NOW))["long-term"]["data"]["status"] == "current"
