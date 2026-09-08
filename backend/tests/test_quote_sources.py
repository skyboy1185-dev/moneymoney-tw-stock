from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from app.services.day_trading import MockDayTradingEngine
from app.services.official_market_data import OfficialStockQuote, StockQuoteRequest, parse_mis_quote
from app.services.quote_quality import trusted_quote, trusted_snapshot, positive
from app.services.day_trading_v2_quotes import quote_health

NOW = datetime.fromisoformat("2026-09-08T10:30:00+08:00")


@pytest.fixture(autouse=True)
def fixed_engine_clock(monkeypatch):
    monkeypatch.setattr(MockDayTradingEngine, "_now", lambda self: NOW + timedelta(minutes=2))


def quote(**changes):
    base = OfficialStockQuote("2330", "test", 100, 99, 99, 101, 98, 1000, 1, 1,
                              NOW.isoformat(), "FUGLE", True, best_bid=99.9, best_ask=100.1,
                              book_timestamp=NOW.isoformat())
    return replace(base, **changes)


@pytest.mark.parametrize("source", ["TWSE MIS", "FUGLE"])
def test_allowed_source_has_identical_age_and_book_contract(source):
    q = quote(source=source)
    assert trusted_quote(q, NOW + timedelta(seconds=15), 15, require_book=True)
    assert not trusted_quote(q, NOW + timedelta(seconds=16), 15, require_book=True)


@pytest.mark.parametrize("changes", [
    {"source": "unknown"}, {"source": "TWSE MIS 五檔參考價"},
    {"quote_kind": "reference"}, {"session": "afterhours"},
    {"is_trial": True}, {"is_halted": True}, {"price": float("nan")},
    {"price": float("inf")}, {"price": 0}, {"volume": -1},
    {"high": float("nan")}, {"change_percent": float("inf")},
    {"quote_timestamp": (NOW + timedelta(seconds=1)).isoformat()},
    {"quote_timestamp": (NOW - timedelta(days=1)).isoformat()},
    {"quote_timestamp": "2026-09-08T10:30:00"},
])
def test_untrusted_quote_is_rejected(changes):
    assert not trusted_quote(quote(**changes), NOW, 15)


@pytest.mark.parametrize("changes", [
    {"book_timestamp": None}, {"book_timestamp": (NOW - timedelta(seconds=16)).isoformat()},
    {"book_timestamp": (NOW + timedelta(seconds=1)).isoformat()},
    {"best_bid": 101}, {"best_ask": None},
])
def test_fresh_trade_cannot_refresh_invalid_or_stale_book(changes):
    q = quote(**changes)
    assert trusted_quote(q, NOW, 15)
    assert not trusted_quote(q, NOW, 15, require_book=True)


@pytest.mark.parametrize("changes", [
    {"source": "TWSE MIS"}, {"volume_unit": "lots"},
    {"continuity": "reconnected"}, {"volume": 10},
])
def test_source_unit_reconnect_and_volume_reset_require_fresh_warmup(changes, monkeypatch):
    engine = MockDayTradingEngine()
    monkeypatch.setattr(engine, "_now", lambda: NOW + timedelta(minutes=2))
    for minute in range(65):
        engine.update_official_quotes({"2330": quote(
            quote_timestamp=(NOW - timedelta(minutes=65-minute)).isoformat(), volume=minute * 100)})
    assert len(engine.minute_bars_for("2330")) == 65
    engine.update_official_quotes({"2330": quote(**changes)})
    assert len(engine._quote_history["2330"]) == 1
    bars = engine.minute_bars_for("2330")
    assert len(bars) == 1 and bars[0]["volume"] == 0
    assert not engine._live_metrics(engine._quote_history["2330"])["qualified"]


def test_v2_history_preserves_provenance_and_book_age():
    engine = MockDayTradingEngine()
    q = quote(continuity="connection-1", received_at=(NOW + timedelta(seconds=1)).isoformat())
    engine.update_official_quotes({"2330": q})
    snapshot = engine.export_official_quote_history(NOW)
    assert snapshot["version"] == 2
    restored = MockDayTradingEngine()
    assert restored.restore_official_quote_history(snapshot, NOW) == 1
    assert restored.official_quotes_snapshot()["2330"] == q


def test_v1_missing_source_is_never_promoted_to_mis():
    engine = MockDayTradingEngine()
    engine.update_official_quotes({"2330": quote(source="TWSE MIS")})
    snapshot = engine.export_official_quote_history(NOW)
    snapshot["version"] = 1
    row = snapshot["symbols"]["2330"]
    row["samples"] = [sample[:15] for sample in row["samples"]]
    restored = MockDayTradingEngine()
    assert restored.restore_official_quote_history(snapshot, NOW) == 1
    assert restored.official_quotes_snapshot()["2330"].source == "TWSE MIS"
    del row["source"]
    assert MockDayTradingEngine().restore_official_quote_history(snapshot, NOW) == 0


def test_history_restore_rejects_future_and_does_not_merge_sources():
    engine = MockDayTradingEngine()
    engine.update_official_quotes({"2330": quote(source="TWSE MIS")})
    snapshot = engine.export_official_quote_history(NOW)
    sample = snapshot["symbols"]["2330"]["samples"][0].copy()
    sample[0] = (NOW + timedelta(minutes=1)).isoformat()
    sample[15] = "FUGLE"
    snapshot["symbols"]["2330"]["samples"].append(sample)
    restored = MockDayTradingEngine()
    assert restored.restore_official_quote_history(snapshot, NOW) == 1
    assert restored.official_quotes_snapshot()["2330"].source == "TWSE MIS"
    assert restored.restore_official_quote_history(snapshot, NOW + timedelta(minutes=2)) == 1
    assert restored.official_quotes_snapshot()["2330"].source == "FUGLE"


def test_mis_future_timestamp_is_not_realtime():
    q = parse_mis_quote({"y": "99", "z": "100", "d": "20260908", "t": "10:30:01"},
                        StockQuoteRequest("2330", "test", "上市"), now=NOW)
    assert q is not None and not q.is_realtime


def test_future_ingestion_cannot_poison_latest_quote(monkeypatch):
    engine = MockDayTradingEngine()
    monkeypatch.setattr(engine, "_now", lambda: NOW)
    engine.update_official_quotes({"2330": quote(quote_timestamp=(NOW + timedelta(seconds=1)).isoformat())})
    assert not engine.official_quotes_snapshot()
    engine.update_official_quotes({"2330": quote()})
    assert engine.official_quotes_snapshot()["2330"].quote_timestamp == NOW.isoformat()


def test_session_change_discards_prior_regular_segment():
    engine = MockDayTradingEngine()
    engine.update_official_quotes({"2330": quote()})
    engine.update_official_quotes({"2330": quote(session="afterhours")})
    assert not engine._quote_history.get("2330")
    engine.update_official_quotes({"2330": quote(quote_timestamp=(NOW + timedelta(seconds=15)).isoformat())})
    assert len(engine._quote_history["2330"]) == 1


def test_public_source_diagnostics_allowlist_and_shadow_label():
    diagnostics = {"providerMode": "shadow", "activeSource": "TWSE_MIS", "ready": False,
                   "entitlementReason": "secret-adapter-error", "sourceSwitchCount": 2,
                   "sourceHealth": {"wsConnected": True, "acknowledgedCount": 30,
                                    "wsSubscriptionLimit": 300, "reconnectCount": 1,
                                    "apiKey": "secret", "lastError": "secret"}}
    result = quote_health({}, NOW, 15, diagnostics)
    assert result["providerMode"] == "shadow" and result["ready"] is False
    assert "尚未啟用" in result["entitlementReason"]
    assert result["subscriptionCount"] == 30 and result["subscriptionLimit"] == 300
    assert result["reconnectAttempts"] == 1 and result["sourceSwitchCount"] == 2
    assert "secret" not in str(result)
    assert "providerMode" not in quote_health({}, NOW, 15)


@pytest.mark.parametrize("ready", [True, False])
def test_shadow_publish_flag_is_independent_of_real_entitlement(ready):
    result = quote_health({}, NOW, 15, {"providerMode": "shadow", "ready": False,
                                       "entitlementReady": ready})
    assert result["ready"] is False and result["entitlementReady"] is ready
    assert "尚未啟用" in result["entitlementReason"]


@pytest.mark.parametrize("changes", [{"d": None}, {"d": "20260999"}, {"v": "NaN"}, {"h": "Infinity"}])
def test_mis_cannot_invent_date_or_accept_nonfinite_numbers(changes):
    row = {"y": "99", "z": "100", "d": "20260908", "t": "10:30:00", **changes}
    assert parse_mis_quote(row, StockQuoteRequest("2330", "test", "上市"), now=NOW) is None


@pytest.mark.parametrize("flag", [True, False])
def test_boolean_is_not_a_numeric_quote_value(flag):
    assert not positive(flag)
    for field in ("price", "volume", "change", "change_percent", "high"):
        assert not trusted_quote(quote(**{field: flag}), NOW, 15)


@pytest.mark.parametrize("field", ["is_halted", "is_trial"])
def test_negative_observation_blocks_old_cached_trade_and_restart_restore(field):
    engine = MockDayTradingEngine()
    engine.update_official_quotes({"2330": quote()})
    old_snapshot = engine.export_official_quote_history(NOW)
    event = NOW + timedelta(seconds=5)
    invalid = quote(**{field: True}, quote_timestamp=(NOW - timedelta(seconds=1)).isoformat(),
                    book_timestamp=event.isoformat(), received_at=event.isoformat(), is_realtime=False)
    engine.update_official_quotes({"2330": invalid})
    assert not trusted_quote(engine.official_quotes_snapshot()["2330"], event, 15)
    assert not engine._quote_history.get("2330")
    engine.update_official_quotes({"2330": quote(source="TWSE MIS")})
    assert engine.official_quotes_snapshot()["2330"] == invalid
    engine.restore_official_quote_history(old_snapshot, event)
    assert not engine._quote_history.get("2330")
    blocked = engine.export_official_quote_history(event)
    assert blocked["blockedSymbols"] == {"2330": event.isoformat()}
    restored = MockDayTradingEngine()
    restored.restore_official_quote_history(blocked, event)
    restored.restore_official_quote_history(old_snapshot, event)
    assert not restored.official_quotes_snapshot()
    newer = quote(quote_timestamp=(event + timedelta(seconds=1)).isoformat(), volume=1001)
    restored.update_official_quotes({"2330": newer})
    assert restored.official_quotes_snapshot()["2330"] == newer
    assert len(restored._quote_history["2330"]) == 1


def test_partial_halt_with_old_book_blocks_rest_trade_before_status_receipt():
    engine = MockDayTradingEngine()
    engine.update_official_quotes({"2330": quote()})
    rest = quote(quote_timestamp=(NOW + timedelta(seconds=3)).isoformat(), source="TWSE MIS")
    engine.update_official_quotes({"2330": rest})
    halted = quote(is_halted=True, is_realtime=False, received_at=(NOW + timedelta(seconds=5)).isoformat())
    engine.update_official_quotes({"2330": halted})
    assert engine.official_quotes_snapshot()["2330"] == halted
    engine.update_official_quotes({"2330": rest})
    assert engine.official_quotes_snapshot()["2330"] == halted
    assert engine._quote_blocks["2330"] == NOW + timedelta(seconds=5)


@pytest.mark.parametrize("source", ["TWSE MIS", "FUGLE"])
def test_delayed_snapshot_remains_available_but_cannot_authorize_entry(source):
    stale = quote(source=source, is_realtime=False, quote_timestamp=(NOW - timedelta(seconds=390)).isoformat())
    assert trusted_snapshot(stale, NOW)
    assert not trusted_quote(stale, NOW, 15, require_book=True)
    for changes in ({"is_halted": True}, {"is_trial": True}, {"source": "unknown"},
                    {"quote_timestamp": (NOW + timedelta(seconds=1)).isoformat()},
                    {"quote_timestamp": (NOW - timedelta(days=1)).isoformat()}):
        assert not trusted_snapshot(replace(stale, **changes), NOW)


def test_expired_realtime_flag_preserves_previously_verified_history():
    engine = MockDayTradingEngine()
    engine.update_official_quotes({"2330": quote()})
    engine.update_official_quotes({"2330": quote(is_realtime=False)})
    assert len(engine._quote_history["2330"]) == 1
    assert not trusted_quote(engine.official_quotes_snapshot()["2330"], NOW, 15)
    engine.update_official_quotes({"2330": quote(quote_timestamp=(NOW + timedelta(seconds=15)).isoformat())})
    assert len(engine._quote_history["2330"]) == 2
