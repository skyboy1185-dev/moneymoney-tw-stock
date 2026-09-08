from dataclasses import replace
from datetime import datetime, timedelta
import pytest

from app.services.day_trading import MockDayTradingEngine
from app.services.official_market_data import OfficialStockQuote
from app.services.theme_stock_universe import ThemeStock


START = datetime.fromisoformat("2026-09-08T09:00:00+08:00")


@pytest.fixture(autouse=True)
def fixed_engine_clock(monkeypatch):
    monkeypatch.setattr(MockDayTradingEngine, "_now", lambda self: START + timedelta(days=1))


def quote(seconds: int, symbol: str = "2330") -> OfficialStockQuote:
    return OfficialStockQuote(
        symbol=symbol, name="test", price=100 + seconds / 10000,
        previous_close=99, open=100, high=105, low=99,
        volume=1000 + seconds, change=1, change_percent=1,
        quote_timestamp=(START + timedelta(seconds=seconds)).isoformat(),
        source="TWSE MIS", is_realtime=True,
    )


def test_five_second_updates_accumulate_twenty_completed_minutes(monkeypatch):
    engine = MockDayTradingEngine()
    for second in range(0, 1201, 5):
        engine.update_official_quotes({"2330": quote(second)})
    monkeypatch.setattr(engine, "_now", lambda: START + timedelta(minutes=20))
    bars = engine.minute_bars_for("2330")
    assert len(engine._quote_history["2330"]) == 81
    assert len(bars) == 20
    assert bars[0]["timestamp"] == START.isoformat()
    assert bars[-1]["timestamp"] == (START + timedelta(minutes=19)).isoformat()
    assert all(row["volume"] >= 0 for row in bars)


def test_fixed_bucket_replaces_only_same_bucket_and_rejects_time_regression():
    engine = MockDayTradingEngine()
    for second in (10, 14, 15, 20, 30, 25):
        engine.update_official_quotes({"2330": quote(second)})
    assert [row.quote_timestamp for row in engine._quote_history["2330"]] == [
        quote(second).quote_timestamp for second in (14, 20, 30)
    ]
    assert engine.official_quotes_snapshot(["2330"])["2330"] == quote(30)


def test_full_session_and_restart_keep_opening_minutes(monkeypatch):
    engine = MockDayTradingEngine()
    for second in range(0, 270 * 60 + 1, 5):
        engine.update_official_quotes({"2330": quote(second)})
    end = START + timedelta(minutes=270)
    monkeypatch.setattr(engine, "_now", lambda: end)
    assert len(engine._quote_history["2330"]) == 1081
    before = engine.minute_bars_for("2330")
    assert len(before) == 270
    assert before[0]["timestamp"] == START.isoformat()
    snapshot = engine.export_official_quote_history(end)
    assert snapshot["version"] == 2
    assert len(snapshot["symbols"]["2330"]["samples"]) == 271
    restored = MockDayTradingEngine()
    restored.restore_official_quote_history(snapshot, end)
    monkeypatch.setattr(restored, "_now", lambda: end)
    after = restored.minute_bars_for("2330")
    assert [bar["timestamp"] for bar in after] == [bar["timestamp"] for bar in before]


def test_next_day_clears_history_and_yesterday_snapshot_is_not_restored(monkeypatch):
    engine = MockDayTradingEngine()
    engine.update_official_quotes({"2330": quote(0)})
    snapshot = engine.export_official_quote_history(START)
    next_day = START + timedelta(days=1)
    engine.update_official_quotes({"2330": replace(quote(0), quote_timestamp=next_day.isoformat())})
    assert len(engine._quote_history["2330"]) == 1
    assert engine.restore_official_quote_history(snapshot, next_day) == 0
    monkeypatch.setattr(engine, "_now", lambda: next_day)
    assert engine.minute_bars_for("2330") == []


def test_tracked_position_outside_selection_pool_retains_quotes_and_history():
    engine = MockDayTradingEngine()
    engine.update_official_quotes({"2330": quote(0), "2454": quote(0, "2454")})
    engine.set_quote_tracking_symbols(["2330"])
    universe = tuple(ThemeStock(str(8000 + index), "test", "上市", "", ()) for index in range(200))
    engine.set_stock_universe(universe)
    assert "2330" not in engine.stock_universe_symbols
    assert "2330" in engine.official_quotes_snapshot()
    assert "2330" in engine._quote_history
    assert "2454" not in engine.official_quotes_snapshot()
    snapshot = engine.official_quotes_snapshot()
    snapshot.clear()
    assert "2330" in engine.official_quotes_snapshot()
    engine.set_quote_tracking_symbols([])
    engine.set_stock_universe(universe)
    assert "2330" not in engine.official_quotes_snapshot()
