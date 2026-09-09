from datetime import UTC, datetime, timedelta
from dataclasses import replace
import hashlib
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import market_data as router
from app.services.day_trading_quote_pump import DayTradingQuotePump
from app.services.official_market_data import StockQuoteRequest, OfficialStockQuote


def test_relay_requires_configured_secret(tmp_path, monkeypatch):
    path = tmp_path / "digest"
    monkeypatch.setattr(router, "RELAY_DIGEST_PATH", path)
    with pytest.raises(HTTPException) as error:
        router.relay_authorized("Bearer anything")
    assert error.value.status_code == 503
    path.write_text(hashlib.sha256(b"secret").hexdigest())
    with pytest.raises(HTTPException) as error:
        router.relay_authorized("Bearer wrong")
    assert error.value.status_code == 401
    router.relay_authorized("Bearer secret")


def test_relay_uses_exchange_time_and_rejects_unknown_stale_future(monkeypatch):
    from app.services import day_trading_quote_pump as module
    now = datetime(2026, 9, 9, 1, 10, 0, tzinfo=UTC)
    monkeypatch.setattr(router, "datetime", SimpleNamespace(now=lambda tz: now))
    received = []
    pump = SimpleNamespace(relay_targets=lambda: [StockQuoteRequest("2330", "test", "上市")],
                           ingest_mis_relay=lambda quotes: received.append(quotes) or len(quotes))
    monkeypatch.setattr(module, "day_trading_quote_pump", pump)
    monkeypatch.setattr(router, "_relay_requests", pump.relay_targets)
    raw = {"c": "2330", "d": "20260909", "t": "09:09:55", "z": "100", "y": "99", "v": "100"}
    for changes in ({}, {"t": "09:09:00"}, {"t": "09:10:01"}, {"c": "9999"}):
        router.relay_quotes(router.RelayBatch(rows=[{**raw, **changes}]))
    assert [len(rows) for rows in received] == [1, 0, 0, 0]
    assert received[0]["2330"].quote_timestamp == "2026-09-09T09:09:55+08:00"


def test_relay_publication_cannot_regress_or_repeat_trade():
    now = datetime(2026, 9, 9, 1, 10, tzinfo=UTC)
    published = []
    pump = DayTradingQuotePump(provider=object(), publisher=published.append, utcnow=lambda: now)
    pump.update_targets([StockQuoteRequest("2330", "test", "上市")], [])
    quote = OfficialStockQuote("2330", "test", 100, 99, 99, 101, 98, 1000, 1, 1,
                               now.isoformat(), "TWSE MIS", True)
    pump.ingest_mis_relay({"2330": quote})
    pump.ingest_mis_relay({"2330": quote})
    pump.ingest_mis_relay({"2330": replace(quote, quote_timestamp=(now-timedelta(seconds=5)).isoformat(), price=90)})
    assert len(published) == 1
    assert published[0]["2330"].price == 100
