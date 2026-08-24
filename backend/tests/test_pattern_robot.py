from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.database import get_db
from app.models import (
    PatternDetection, PatternFill, PatternOrder, PatternPosition, PatternRobotRun,
    PatternSignal, PatternTradeCycle, PatternTradeMessage,
)
from app.pattern_schemas import PatternScanPayload, PatternStockInput
from app.services.pattern_detection import (
    Candle, PatternResult, Pivot, _detect_ascending_triangle, _detect_cup_handle,
    _detect_double_bottom, _detect_head_shoulders, _detect_rounded_bottom,
    _status, find_pivots, risk_sized_quantity,
)
from app.services.pattern_robot_service import (
    ensure_pattern_settings, manual_position_trade, performance, process_pattern_scan, update_settings,
)
from app.routers import pattern_robot as pattern_router


START = date(2025, 1, 2)


def candles(values: list[float], volumes: list[float] | None = None) -> list[Candle]:
    return [
        Candle(
            START + timedelta(days=index), value * .997, value * 1.006, value * .994, value,
            (volumes or [1_000_000] * len(values))[index], value * (volumes or [1_000_000] * len(values))[index],
        )
        for index, value in enumerate(values)
    ]


def pivot(rows: list[Candle], index: int, price: float, kind: str, window: int = 3) -> Pivot:
    return Pivot(index, rows[index].trade_date, price, kind, index + window, rows[index + window].trade_date)


def context(**values):
    return {"close_complete": True, "market_regime": "bull", "vwap": None, **values}


def test_standard_head_shoulders_bottom_is_detected():
    rows = candles([110] * 70 + [118])
    points = [pivot(rows, 10, 100, "LOW"), pivot(rows, 20, 112, "HIGH"), pivot(rows, 30, 90, "LOW"), pivot(rows, 40, 113, "HIGH"), pivot(rows, 50, 102, "LOW")]
    result = _detect_head_shoulders(rows, points, **context())
    assert result and result.pattern_type == "HEAD_SHOULDERS_BOTTOM"
    assert result.pattern_status == "CONFIRMED_BREAKOUT"


@pytest.mark.parametrize("left,right", [(100, 109), (109, 100)])
def test_head_shoulders_rejects_shoulders_over_eight_percent(left, right):
    rows = candles([110] * 71)
    points = [pivot(rows, 10, left, "LOW"), pivot(rows, 20, 112, "HIGH"), pivot(rows, 30, 88, "LOW"), pivot(rows, 40, 113, "HIGH"), pivot(rows, 50, right, "LOW")]
    assert _detect_head_shoulders(rows, points, **context()) is None


def test_head_shoulders_rejects_head_not_five_percent_lower():
    rows = candles([110] * 71)
    points = [pivot(rows, 10, 100, "LOW"), pivot(rows, 20, 112, "HIGH"), pivot(rows, 30, 97, "LOW"), pivot(rows, 40, 113, "HIGH"), pivot(rows, 50, 101, "LOW")]
    assert _detect_head_shoulders(rows, points, **context()) is None


def test_double_bottom_standard_pattern():
    rows = candles([108] * 70 + [115])
    points = [pivot(rows, 10, 100, "LOW"), pivot(rows, 25, 112, "HIGH"), pivot(rows, 40, 102, "LOW")]
    result = _detect_double_bottom(rows, points, **context())
    assert result and result.pattern_type == "DOUBLE_BOTTOM"


@pytest.mark.parametrize("second", [94, 106])
def test_double_bottom_rejects_price_difference_over_five_percent(second):
    rows = candles([108] * 71)
    points = [pivot(rows, 10, 100, "LOW"), pivot(rows, 25, 112, "HIGH"), pivot(rows, 40, second, "LOW")]
    assert _detect_double_bottom(rows, points, **context()) is None


def test_double_bottom_rejects_spacing_under_ten_days():
    rows = candles([108] * 71)
    points = [pivot(rows, 20, 100, "LOW"), pivot(rows, 24, 112, "HIGH"), pivot(rows, 29, 101, "LOW")]
    assert _detect_double_bottom(rows, points, **context()) is None


def test_rounded_bottom_quadratic_fit_and_moving_average_turn():
    values = [80 + .01 * (index - 60) ** 2 for index in range(120)]
    rows = candles(values)
    result = _detect_rounded_bottom(rows, [], **context())
    assert result and result.pattern_type == "ROUNDED_BOTTOM"
    assert any("R²" in reason for reason in result.reasons)


def test_rounded_bottom_rejects_sharp_v_reversal():
    values = [100 + abs(index - 60) * 3.5 for index in range(120)]
    assert _detect_rounded_bottom(candles(values), [], **context()) is None


def cup_rows(handle_price: float = 96, handle_volume: float = 400_000):
    values = [80 + index * (22 / 39) for index in range(40)]
    values += [102 - (index / 30) * 22 for index in range(1, 31)]
    values += [80 + (index / 35) * 21 for index in range(1, 36)]
    values += [101 - (index / 7) * (101 - handle_price) for index in range(1, 8)]
    values += [handle_price + (index / 8) * (103 - handle_price) for index in range(1, 9)]
    values += [103] * (140 - len(values))
    volumes = [1_000_000] * len(values)
    rows = candles(values, volumes)
    points = [pivot(rows, 40, 102, "HIGH"), pivot(rows, 70, 80, "LOW"), pivot(rows, 105, 101, "HIGH"), pivot(rows, 112, handle_price, "LOW")]
    for index in range(106, 114):
        row = rows[index]
        rows[index] = Candle(row.trade_date, row.open, row.high, row.low, row.close, handle_volume, row.close * handle_volume)
    return rows, points


def test_cup_handle_standard_pattern():
    rows, points = cup_rows()
    rows[-1] = Candle(rows[-1].trade_date, 102, 104, 101, 103, 2_000_000, 206_000_000)
    result = _detect_cup_handle(rows, points, **context())
    assert result and result.pattern_type == "CUP_HANDLE"


def test_cup_handle_rejects_handle_deeper_than_half_cup():
    rows, points = cup_rows(handle_price=89)
    assert _detect_cup_handle(rows, points, **context()) is None


def test_cup_handle_rejects_expanding_handle_volume():
    rows, points = cup_rows(handle_volume=2_000_000)
    assert _detect_cup_handle(rows, points, **context()) is None


def test_ascending_triangle_standard_pattern():
    rows = candles([108] * 100 + [116])
    points = [pivot(rows, 20, 115, "HIGH"), pivot(rows, 30, 100, "LOW"), pivot(rows, 45, 116, "HIGH"), pivot(rows, 55, 104, "LOW"), pivot(rows, 70, 114.5, "HIGH"), pivot(rows, 80, 108, "LOW")]
    result = _detect_ascending_triangle(rows, points, **context())
    assert result and result.pattern_type == "ASCENDING_TRIANGLE"


def test_ascending_triangle_rejects_non_rising_lows():
    rows = candles([108] * 101)
    points = [pivot(rows, 20, 115, "HIGH"), pivot(rows, 30, 100, "LOW"), pivot(rows, 45, 116, "HIGH"), pivot(rows, 55, 99, "LOW")]
    assert _detect_ascending_triangle(rows, points, **context()) is None


def test_triangle_rejects_highs_more_than_three_percent_apart():
    rows = candles([108] * 101)
    points = [pivot(rows, 20, 115, "HIGH"), pivot(rows, 30, 100, "LOW"), pivot(rows, 45, 119, "HIGH"), pivot(rows, 55, 104, "LOW")]
    assert _detect_ascending_triangle(rows, points, **context()) is None


def test_pivot_confirmation_date_waits_for_later_bars():
    rows = candles([100, 101, 103, 108, 104, 102, 99, 95, 99, 102, 105, 103, 100])
    points = find_pivots(rows, window=3, minimum_swing_pct=3)
    assert points
    assert all(item.confirmed_index == item.index + 3 for item in points)
    assert all(item.confirmed_date > item.trade_date for item in points)


def test_future_bars_are_never_used_to_confirm_last_window():
    rows = candles([100] * 20 + [90])
    points = find_pivots(rows, window=3, minimum_swing_pct=3)
    assert all(item.index < len(rows) - 3 for item in points)
    assert not any(item.index == len(rows) - 1 for item in points)


@pytest.mark.parametrize(
    "current,close_complete,previous,status",
    [(105,False,False,"INTRADAY_BREAKOUT"),(105,True,False,"CONFIRMED_BREAKOUT"),(100.2,True,False,"NEAR_BREAKOUT"),(90,True,False,"INVALIDATED"),(98,True,True,"FAILED_BREAKOUT")],
)
def test_pattern_status_state_machine(current, close_complete, previous, status):
    rows = candles([100] * 69 + [current])
    assert _status(rows, 100, 92, 1, close_complete=close_complete, previously_confirmed=previous) == status


@pytest.mark.parametrize(
    "equity,cash,entry,stop,risk_pct,max_pct,expected",
    [(1_000_000,1_000_000,100,95,.5,20,1000),(1_000_000,50_000,100,95,.5,20,500),(1_000_000,1_000_000,100,100,.5,20,0),(0,1_000_000,100,95,.5,20,0)],
)
def test_risk_sized_quantity(equity,cash,entry,stop,risk_pct,max_pct,expected):
    assert risk_sized_quantity(equity=equity,cash=cash,entry_price=entry,stop_loss_price=stop,risk_per_trade_pct=risk_pct,max_position_pct=max_pct) == expected


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()


def scan_payload(mode: str = "PAPER_LIVE") -> PatternScanPayload:
    rows = [{"date": START + timedelta(days=index), "open":100, "high":112, "low":98, "close":110, "volume":1_000_000, "turnover":110_000_000} for index in range(180)]
    return PatternScanPayload(
        trade_date=rows[-1]["date"], generated_at=datetime.combine(rows[-1]["date"], datetime.min.time(), UTC) + timedelta(hours=6), is_trading_day=True,
        market_regime="bull", stocks=[PatternStockInput(
            stock_code="2330", stock_name="台積電", market_type="上市", sector_name="24",
            listing_date=date(1994, 9, 5), current_price=110, current_volume=1_000_000,
            current_turnover=110_000_000, vwap=108, quote_time=datetime(2025, 7, 1, 5, tzinfo=UTC),
            quote_realtime=False, quote_source="TWSE", close_complete=True,
            adjusted_prices=rows, actual_prices=rows,
        )], sources=["TWSE"], source_status={"history":"ok"},
    )


def bullish_result(action: str = "BUY") -> PatternResult:
    return PatternResult(
        pattern_type="DOUBLE_BOTTOM", pattern_status="CONFIRMED_BREAKOUT", score=90,
        start_date=START, confirmed_at=datetime(2025,7,1,tzinfo=UTC), pivot_confirmed_date=START+timedelta(days=100),
        neckline_price=108, breakout_price=108, current_price=110, target_price=130,
        invalidation_price=98, stop_loss_price=100, entry_price_low=108, entry_price_high=111,
        add_price=109, take_profit_1=120, take_profit_2=125, trailing_stop_price=102,
        volume_ratio=1.5, distance_to_breakout_pct=-1.8, risk_reward_ratio=2,
        completion_pct=100, action=action, action_label="正式建立部位", suggested_position_pct=20,
        key_points=[], score_breakdown={"structure":30}, reasons=["測試型態"],
    )


def test_same_day_scan_run_and_detection_are_idempotent(db, monkeypatch):
    monkeypatch.setattr("app.services.pattern_robot_service.detect_patterns", lambda *args, **kwargs: [bullish_result()])
    first = process_pattern_scan(db, scan_payload())
    second = process_pattern_scan(db, scan_payload(), force=True)
    assert first["status"] == second["status"] == "completed"
    assert db.scalar(select(func.count(PatternRobotRun.id))) == 1
    assert db.scalar(select(func.count(PatternDetection.id))) == 1
    assert db.scalar(select(func.count(PatternSignal.id)).where(PatternSignal.signal_type == "BUY")) == 1


def test_buy_fill_updates_cash_position_order_fill_in_one_commit(db, monkeypatch):
    monkeypatch.setattr("app.services.pattern_robot_service.detect_patterns", lambda *args, **kwargs: [bullish_result()])
    process_pattern_scan(db, scan_payload())
    settings = ensure_pattern_settings(db)
    position = db.scalar(select(PatternPosition))
    assert position and position.quantity > 0
    assert float(settings.cash) < 1_000_000
    assert db.scalar(select(func.count(PatternOrder.id))) == 1
    assert db.scalar(select(func.count(PatternFill.id))) == 1
    assert db.scalar(select(func.count(PatternTradeCycle.id))) == 1


def test_alert_only_mode_never_creates_paper_position(db, monkeypatch):
    settings = ensure_pattern_settings(db); settings.robot_mode = "ALERT_ONLY"; db.commit()
    monkeypatch.setattr("app.services.pattern_robot_service.detect_patterns", lambda *args, **kwargs: [bullish_result()])
    process_pattern_scan(db, scan_payload())
    assert db.scalar(select(func.count(PatternPosition.id))) == 0
    assert db.scalar(select(func.count(PatternSignal.id))) == 1


def test_strong_bear_blocks_new_long_position(db, monkeypatch):
    payload = scan_payload(); payload.market_regime = "strong_bear"
    monkeypatch.setattr("app.services.pattern_robot_service.detect_patterns", lambda *args, **kwargs: [bullish_result()])
    process_pattern_scan(db, payload)
    assert db.scalar(select(func.count(PatternPosition.id))) == 0


def test_non_trading_day_never_scans_even_when_force_requested(db):
    payload = scan_payload(); payload.is_trading_day = False
    result = process_pattern_scan(db, payload, force=True)
    assert result["status"] == "skipped_non_trading_day"
    assert db.scalar(select(func.count(PatternRobotRun.id))) == 0


def test_backend_rejects_non_ai_stocks_even_if_scanner_sends_them(db, monkeypatch):
    payload = scan_payload()
    payload.stocks[0].stock_code = "2603"
    payload.stocks[0].stock_name = "長榮"
    monkeypatch.setattr("app.services.pattern_robot_service.detect_patterns", lambda *args, **kwargs: [bullish_result()])
    result = process_pattern_scan(db, payload)
    assert result["scannedCount"] == 0
    assert db.scalar(select(func.count(PatternDetection.id))) == 0


def test_manual_sell_cannot_exceed_holdings(db, monkeypatch):
    monkeypatch.setattr("app.services.pattern_robot_service.detect_patterns", lambda *args, **kwargs: [bullish_result()])
    process_pattern_scan(db, scan_payload())
    position = db.scalar(select(PatternPosition))
    with pytest.raises(ValueError, match="不得超過"):
        manual_position_trade(db, position.id, action="REDUCE", quantity=position.quantity + 1, price=111, reason="test", at=datetime.now(UTC))


def test_open_position_is_not_counted_as_completed_win(db, monkeypatch):
    monkeypatch.setattr("app.services.pattern_robot_service.detect_patterns", lambda *args, **kwargs: [bullish_result()])
    process_pattern_scan(db, scan_payload())
    metrics = performance(db)
    assert metrics["completedTrades"] == 0
    assert metrics["winRate"] == 0


def test_scan_completed_message_is_not_duplicated(db, monkeypatch):
    monkeypatch.setattr("app.services.pattern_robot_service.detect_patterns", lambda *args, **kwargs: [bullish_result()])
    process_pattern_scan(db, scan_payload())
    process_pattern_scan(db, scan_payload(), force=True)
    assert db.scalar(select(func.count(PatternTradeMessage.id)).where(PatternTradeMessage.message_type == "SCAN_COMPLETED")) == 1
    message = db.scalar(select(PatternTradeMessage).where(PatternTradeMessage.message_type == "SCAN_COMPLETED"))
    assert "AI 核心與延伸供應鏈" in message.message
    assert message.is_read is False


def test_forming_pattern_is_saved_but_not_counted_or_notified(db, monkeypatch):
    result = bullish_result("WATCH")
    result.pattern_status = "FORMING"
    monkeypatch.setattr("app.services.pattern_robot_service.detect_patterns", lambda *args, **kwargs: [result])
    outcome = process_pattern_scan(db, scan_payload())
    assert outcome["matchedCount"] == 0
    assert db.scalar(select(func.count(PatternDetection.id))) == 1
    assert db.scalar(select(func.count(PatternTradeMessage.id)).where(PatternTradeMessage.message_type == "WATCH")) == 0


def test_near_breakout_is_counted_and_creates_prepare_reminder(db, monkeypatch):
    result = bullish_result("PREPARE")
    result.pattern_status = "NEAR_BREAKOUT"
    monkeypatch.setattr("app.services.pattern_robot_service.detect_patterns", lambda *args, **kwargs: [result])
    outcome = process_pattern_scan(db, scan_payload())
    assert outcome["matchedCount"] == 1
    reminder = db.scalar(select(PatternTradeMessage).where(PatternTradeMessage.message_type == "PREPARE"))
    assert reminder is not None and "接近突破" in reminder.title


def test_database_uniqueness_rejects_duplicate_signal_version(db, monkeypatch):
    monkeypatch.setattr("app.services.pattern_robot_service.detect_patterns", lambda *args, **kwargs: [bullish_result()])
    process_pattern_scan(db, scan_payload())
    original = db.scalar(select(PatternSignal).limit(1))
    db.add(PatternSignal(
        detection_id=original.detection_id, trade_date=original.trade_date, stock_code=original.stock_code,
        stock_name=original.stock_name, pattern_type=original.pattern_type, signal_type=original.signal_type,
        signal_version=original.signal_version, action=original.action, signal_price=original.signal_price,
        quantity=0, reasons_json="[]", signal_time=datetime.now(UTC),
    ))
    with pytest.raises(IntegrityError):
        db.commit()


@pytest.fixture()
def client(db):
    app = FastAPI()
    app.include_router(pattern_router.router, prefix="/api/v1")
    def override_db():
        yield db
    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as test_client:
        yield test_client


AUTH = {"x-user-id": "test-user-0001"}


def test_status_and_settings_api_restore_database_configuration(client):
    response = client.get("/api/v1/pattern-robot/status")
    assert response.status_code == 200
    assert response.json()["universeScope"] == "AI_CORE_AND_EXTENDED"
    assert response.json()["universeSize"] > 100
    assert response.json()["settings"]["robotMode"] == "SWING"
    changed = client.put("/api/v1/pattern-robot/settings", headers=AUTH, json={"robotMode":"DAY_TRADE","riskPerTradePct":.8})
    assert changed.status_code == 200
    restored = client.get("/api/v1/pattern-robot/settings").json()
    assert restored["robotMode"] == "DAY_TRADE"
    assert restored["riskPerTradePct"] == .8


def test_pattern_universe_contains_ai_extensions_and_excludes_unrelated_stocks(client):
    response = client.get("/api/v1/pattern-robot/universe")
    assert response.status_code == 200
    payload = response.json()
    symbols = {item["stockCode"] for item in payload["items"]}
    assert payload["scope"] == "AI_CORE_AND_EXTENDED"
    assert payload["count"] == len(symbols)
    assert {"2330", "3363", "2449", "2308"} <= symbols
    assert {"2603", "2723"}.isdisjoint(symbols)


def test_mutating_settings_api_requires_user_identity(client):
    response = client.put("/api/v1/pattern-robot/settings", json={"robotMode":"SWING"})
    assert response.status_code == 422


def test_detection_filter_and_stock_detail_apis(client, db, monkeypatch):
    monkeypatch.setattr("app.services.pattern_robot_service.detect_patterns", lambda *args, **kwargs: [bullish_result()])
    process_pattern_scan(db, scan_payload())
    listing = client.get("/api/v1/pattern-robot/detections?pattern=DOUBLE_BOTTOM&minScore=85")
    assert listing.status_code == 200 and listing.json()["total"] == 1
    detail = client.get("/api/v1/pattern-robot/detections/2330")
    assert detail.status_code == 200 and detail.json()["items"][0]["patternLabel"] == "W底／雙重底"


def test_default_detection_api_hides_forming_but_explicit_filter_can_retrieve_it(client, db, monkeypatch):
    result = bullish_result("WATCH")
    result.pattern_status = "FORMING"
    monkeypatch.setattr("app.services.pattern_robot_service.detect_patterns", lambda *args, **kwargs: [result])
    process_pattern_scan(db, scan_payload())
    assert client.get("/api/v1/pattern-robot/detections").json()["total"] == 0
    assert client.get("/api/v1/pattern-robot/detections?status=FORMING").json()["total"] == 1


def test_watchlist_api_also_writes_existing_shared_monitor(client, db, monkeypatch):
    monkeypatch.setattr("app.services.pattern_robot_service.detect_patterns", lambda *args, **kwargs: [bullish_result("WATCH")])
    process_pattern_scan(db, scan_payload())
    detection = db.scalar(select(PatternDetection))
    response = client.post("/api/v1/pattern-robot/watchlist", headers=AUTH, json={"stockCode":"2330","patternType":"DOUBLE_BOTTOM","detectionId":detection.id})
    assert response.status_code == 201
    listing = client.get("/api/v1/pattern-robot/watchlist", headers=AUTH)
    assert listing.status_code == 200 and listing.json()["items"][0]["stockCode"] == "2330"


def test_position_order_signal_trade_and_performance_apis(client, db, monkeypatch):
    monkeypatch.setattr("app.services.pattern_robot_service.detect_patterns", lambda *args, **kwargs: [bullish_result()])
    process_pattern_scan(db, scan_payload())
    assert client.get("/api/v1/pattern-robot/positions").json()["items"]
    assert client.get("/api/v1/pattern-robot/orders").json()["items"][0]["status"] == "FILLED"
    assert client.get("/api/v1/pattern-robot/signals").json()["items"][0]["action"] == "BUY"
    assert client.get("/api/v1/pattern-robot/trades").json()["items"][0]["status"] == "OPEN"
    assert client.get("/api/v1/pattern-robot/performance").json()["completedTrades"] == 0


def test_message_mark_read_and_snooze_use_database_state(client, db, monkeypatch):
    monkeypatch.setattr("app.services.pattern_robot_service.detect_patterns", lambda *args, **kwargs: [bullish_result()])
    process_pattern_scan(db, scan_payload())
    item = db.scalar(select(PatternTradeMessage).where(PatternTradeMessage.message_type == "SCAN_COMPLETED"))
    snoozed = client.post(f"/api/v1/pattern-robot/messages/{item.id}/mark-read?snoozeMinutes=30")
    assert snoozed.status_code == 200 and snoozed.json()["isRead"] is False
    read = client.post(f"/api/v1/pattern-robot/messages/{item.id}/mark-read")
    assert read.status_code == 200 and read.json()["isRead"] is True


def test_message_api_only_shows_current_ai_breakout_notifications(client, db, monkeypatch):
    result = bullish_result("PREPARE")
    result.pattern_status = "NEAR_BREAKOUT"
    monkeypatch.setattr("app.services.pattern_robot_service.detect_patterns", lambda *args, **kwargs: [result])
    process_pattern_scan(db, scan_payload())
    db.add(PatternTradeMessage(
        signal_id=None, message_type="WATCH", message_version=1, stock_code="2603",
        stock_name="長榮", title="舊形成中提醒", message="不應顯示", reasons_json="[]",
        created_at=datetime.now(UTC),
    ))
    db.commit()
    listing = client.get("/api/v1/pattern-robot/messages?pageSize=100").json()["items"]
    assert any(item["messageType"] == "PREPARE" and item["stockCode"] == "2330" for item in listing)
    assert all(item["messageType"] != "WATCH" for item in listing)


def test_equity_curve_and_pattern_performance_apis(client, db, monkeypatch):
    monkeypatch.setattr("app.services.pattern_robot_service.detect_patterns", lambda *args, **kwargs: [bullish_result()])
    process_pattern_scan(db, scan_payload())
    curve = client.get("/api/v1/pattern-robot/equity-curve?period=all")
    patterns = client.get("/api/v1/pattern-robot/performance/by-pattern")
    assert len(curve.json()["items"]) == 1
    assert len(patterns.json()["items"]) == 5


def test_trade_export_is_utf8_csv_and_does_not_mix_other_robots(client, db, monkeypatch):
    monkeypatch.setattr("app.services.pattern_robot_service.detect_patterns", lambda *args, **kwargs: [bullish_result()])
    process_pattern_scan(db, scan_payload())
    response = client.get("/api/v1/pattern-robot/export")
    assert response.status_code == 200
    assert "pattern-trades-paper_live.csv" in response.headers["content-disposition"]
    assert "2330" in response.text


def test_sell_fill_charges_commission_tax_and_slippage(db, monkeypatch):
    monkeypatch.setattr("app.services.pattern_robot_service.detect_patterns", lambda *args, **kwargs: [bullish_result()])
    process_pattern_scan(db, scan_payload())
    position = db.scalar(select(PatternPosition))
    manual_position_trade(db, position.id, action="EXIT", quantity=position.quantity, price=120, reason="target", at=datetime.now(UTC))
    sell = db.scalar(select(PatternFill).where(PatternFill.side == "SELL"))
    assert sell and sell.fee > 0 and sell.tax > 0 and sell.slippage > 0
    assert db.get(PatternTradeCycle, position.trade_cycle_id).status == "CLOSED"


def test_partial_sales_count_one_trade_cycle_only_after_full_exit(db, monkeypatch):
    monkeypatch.setattr("app.services.pattern_robot_service.detect_patterns", lambda *args, **kwargs: [bullish_result()])
    process_pattern_scan(db, scan_payload())
    position = db.scalar(select(PatternPosition)); original = position.quantity
    manual_position_trade(db, position.id, action="REDUCE", quantity=max(1, original // 3), price=115, reason="tp1", at=datetime.now(UTC))
    assert performance(db)["completedTrades"] == 0
    manual_position_trade(db, position.id, action="EXIT", quantity=position.quantity, price=120, reason="target", at=datetime.now(UTC)+timedelta(seconds=1))
    assert performance(db)["completedTrades"] == 1
    assert db.scalar(select(func.count(PatternTradeCycle.id))) == 1


def test_day_trade_mode_forces_close_after_configured_time(db, monkeypatch):
    settings = ensure_pattern_settings(db); settings.robot_mode = "DAY_TRADE"; settings.day_trade_close_time = "13:20"; db.commit()
    monkeypatch.setattr("app.services.pattern_robot_service.detect_patterns", lambda *args, **kwargs: [bullish_result()])
    payload = scan_payload()
    process_pattern_scan(db, payload)
    process_pattern_scan(db, payload, force=True)
    assert db.scalar(select(PatternPosition)).status == "CLOSED"
    assert db.scalar(select(PatternTradeCycle)).exit_reason == "DAY_TRADE_CLOSE"


def test_database_session_restart_restores_cash_positions_and_processed_signals(db, monkeypatch):
    monkeypatch.setattr("app.services.pattern_robot_service.detect_patterns", lambda *args, **kwargs: [bullish_result()])
    process_pattern_scan(db, scan_payload())
    engine = db.get_bind(); expected_cash = ensure_pattern_settings(db).cash
    db.close()
    restored = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        assert ensure_pattern_settings(restored).cash == expected_cash
        assert restored.scalar(select(PatternPosition).where(PatternPosition.status == "OPEN")) is not None
        assert restored.scalar(select(PatternSignal).where(PatternSignal.processed_at.is_not(None))) is not None
    finally:
        restored.close()


def test_performance_modes_keep_independent_cash_balances(db):
    settings = ensure_pattern_settings(db)
    settings.cash = Decimal("880000")
    settings.paper_live_cash = Decimal("880000")
    db.commit()
    switched = update_settings(db, {"performanceMode": "BACKTEST"}, "tester", datetime.now(UTC))
    assert switched.cash == Decimal("1000000")
    switched.cash = Decimal("910000")
    switched.backtest_cash = Decimal("910000")
    db.commit()
    restored = update_settings(db, {"performanceMode": "PAPER_LIVE"}, "tester", datetime.now(UTC))
    assert restored.cash == Decimal("880000")
    assert restored.backtest_cash == Decimal("910000")
