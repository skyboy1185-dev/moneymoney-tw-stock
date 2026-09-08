import asyncio
import threading
from types import SimpleNamespace
from typing import Any

import app.services.day_trading_automation as automation_module


class FakeSession:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def __enter__(self) -> "FakeSession":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def commit(self) -> None:
        self.events.append("commit")


def test_web_entry_is_persisted_when_line_delivery_fails(monkeypatch: Any) -> None:
    events: list[str] = []
    recommendation = {
        "id": "2330-long",
        "symbol": "2330",
        "direction": "long",
        "status": "confirmed",
        "action": "buy",
        "expiresAt": (automation_module.datetime.now(automation_module.UTC) + automation_module.timedelta(minutes=5)).isoformat(),
        "isOfficialRecommendation": True,
        "momentumUniverseMember": True,
        "dataStatus": "normal",
        "dataMode": "official",
        "quoteIsRealtime": True,
        "quoteTimestamp": automation_module.datetime.now(automation_module.UTC).isoformat(),
        "confidenceScore": 90,
        "confirmationScore": 90,
        "healthScore": 90,
        "riskRewardRatio": 2,
        "spreadPercentage": 0.1,
        "volume": automation_module.TradingScheduleConfig().minimum_volume,
        "turnover": automation_module.TradingScheduleConfig().minimum_turnover,
        "stopDistancePercent": 1,
        "largeOrderDataAvailable": True,
        "largeOrderContinuousBuy": True,
        "largeOrderContinuousSell": False,
        "tradingEligible": True,
        "marketAlignment": 80,
    }
    monkeypatch.setattr(automation_module, "SessionLocal", lambda: FakeSession(events))
    monkeypatch.setattr(
        automation_module.day_trading_restrictions,
        "filter_candidates",
        lambda items: items,
    )
    monkeypatch.setattr(
        automation_module,
        "record_official_recommendations",
        lambda _db, _items, **_kwargs: events.append("web-entry"),
    )
    monkeypatch.setattr(
        automation_module,
        "ensure_positions_for_official_recommendations",
        lambda _db, _items, **_kwargs: [],
    )

    async def fail_line(_items: list[dict[str, Any]]) -> int:
        events.append("line-entry")
        raise RuntimeError("LINE unavailable")

    monkeypatch.setattr(
        automation_module.line_notification_dispatcher,
        "send_recommendations",
        fail_line,
    )

    sent = asyncio.run(
        automation_module.DayTradingAutomationSupervisor()._send_recommendations_and_track(
            [recommendation],
            automation_module.TradingScheduleConfig(),
            {"formalSignalsAllowed": True, "formalLongSignalsAllowed": True, "phase": "scanning", "statusMessage": ""},
            automation_module.datetime.now(automation_module.UTC),
        )
    )

    assert sent == 0
    assert events == ["web-entry", "commit", "line-entry"]


def test_web_exit_is_persisted_before_line_delivery(monkeypatch: Any) -> None:
    events: list[str] = []
    event = {
        "type": "emergency_exit",
        "level": "emergency",
        "action": "立即全部回補",
        "reason": "突破停損價",
        "price": 101.0,
        "position": {"symbol": "2317"},
        "_positionId": 7,
        "_terminal": True,
    }
    monkeypatch.setattr(automation_module, "SessionLocal", lambda: FakeSession(events))
    monkeypatch.setattr(
        automation_module,
        "pending_automatic_position_events",
        lambda *_args, **_kwargs: [event],
    )
    monkeypatch.setattr(
        automation_module,
        "finalize_automatic_position_event",
        lambda _db, _event: events.append("web-exit"),
    )

    async def fail_line(_event: dict[str, Any]) -> int:
        events.append("line-exit")
        raise RuntimeError("LINE unavailable")

    monkeypatch.setattr(
        automation_module.line_notification_dispatcher,
        "send_position_event",
        fail_line,
    )

    evaluated, sent = asyncio.run(
        automation_module.DayTradingAutomationSupervisor()._monitor_automatic_positions(
            data_status="normal",
            phase="scanning",
        )
    )

    assert (evaluated, sent) == (1, 0)
    assert events == ["commit", "web-exit", "commit", "line-exit"]


def test_quote_requests_are_deduped_before_refresh(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        automation_module.day_trading_restrictions,
        "is_disposed",
        lambda _symbol: False,
    )
    monkeypatch.setattr(
        automation_module.day_trading_restrictions,
        "market_restrictions_available",
        lambda _market: True,
    )
    stocks = [
        SimpleNamespace(symbol="2330", name="台積電", market="上市"),
        SimpleNamespace(symbol="2330", name="台積電 duplicate", market="上市"),
        SimpleNamespace(symbol="2354", name="鴻準", market="上市"),
    ]

    requests = automation_module._quote_requests_for_stocks(stocks)

    assert [request.symbol for request in requests] == ["2330", "2354"]


def test_baseline_quote_slice_rotates_without_refreshing_full_universe() -> None:
    supervisor = automation_module.DayTradingAutomationSupervisor()
    stocks = tuple(
        SimpleNamespace(symbol=str(1000 + index), name=f"Stock {index}", market="上市")
        for index in range(automation_module.BASELINE_QUOTE_BATCH_SIZE + 5)
    )

    first = supervisor._baseline_quote_slice(stocks)
    second = supervisor._baseline_quote_slice(stocks)

    assert len(first) == automation_module.BASELINE_QUOTE_BATCH_SIZE
    assert len(second) == automation_module.BASELINE_QUOTE_BATCH_SIZE
    assert first[0].symbol == "1000"
    assert second[0].symbol == str(1000 + automation_module.BASELINE_QUOTE_BATCH_SIZE)


def test_quote_target_refresh_uses_threads_and_preserves_last_inputs_on_database_failure(monkeypatch: Any) -> None:
    from app.services.day_trading_quote_targets import PriorityCandidate, QuoteTargetInputs
    from app.services.theme_stock_universe import ThemeStock
    from decimal import Decimal

    supervisor = automation_module.DayTradingAutomationSupervisor()
    main_thread = threading.get_ident()
    threads = []
    updates = []
    retained = []
    original = QuoteTargetInputs(frozenset({"9901"}), (PriorityCandidate("9902", Decimal(90)),), 1, 1)

    def load(_now):
        threads.append(threading.get_ident())
        return original

    def cache(*_args, **_kwargs):
        threads.append(threading.get_ident())

    monkeypatch.setattr(supervisor, "_read_quote_inputs", load)
    monkeypatch.setattr(automation_module.day_trading_cache, "put", cache)
    monkeypatch.setattr(automation_module, "day_trading_quote_pump", SimpleNamespace(
        state={"overCapacity": False}, diagnostics=lambda: {},
        update_targets=lambda *args, **kwargs: updates.append((args, kwargs)),
    ))
    monkeypatch.setattr(automation_module.electronic_chip_flow_alert_monitor, "stock_universe_snapshot", lambda: (
        ThemeStock("9901", "held", "上櫃", "", ()), ThemeStock("9902", "candidate", "上市", "", ()),
    ))
    monkeypatch.setattr(automation_module.electronic_chip_flow_alert_monitor, "high_frequency_symbols_snapshot", lambda _now: ())
    monkeypatch.setattr(automation_module.electronic_chip_flow_alert_monitor, "set_day_trading_priority_symbols", lambda _symbols: None)
    monkeypatch.setattr(automation_module.day_trading_engine, "set_quote_tracking_symbols", lambda symbols: retained.append(set(symbols)))
    monkeypatch.setattr(automation_module.day_trading_engine, "set_stock_universe", lambda _stocks: None)
    now = automation_module.datetime(2026, 9, 8, 2, tzinfo=automation_module.UTC)
    asyncio.run(supervisor._refresh_quote_targets(now))
    assert threads and all(thread != main_thread for thread in threads)
    assert retained[-1] == {"9901", "9902"}

    def failed(_now):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(supervisor, "_read_quote_inputs", failed)
    monkeypatch.setattr(automation_module.electronic_chip_flow_alert_monitor, "stock_universe_snapshot", lambda: ())
    asyncio.run(supervisor._refresh_quote_targets(now))
    assert supervisor._quote_inputs is original
    assert [item.symbol for item in updates[-1][0][0]] == ["t00", "9901", "9902"]
    assert updates[-1][0][0][1].market == "上櫃"
    assert supervisor._quote_target_state["targetRefreshError"] == "database unavailable"


def test_priority_scheduler_continues_during_a_slow_signal_scan(monkeypatch: Any) -> None:
    supervisor = automation_module.DayTradingAutomationSupervisor()
    release = threading.Event()

    async def exercise():
        ticks = 0
        refreshed = asyncio.Event()

        async def refresh(_now):
            nonlocal ticks
            ticks += 1
            if ticks >= 2:
                refreshed.set()

        monkeypatch.setattr(supervisor, "_refresh_quote_targets", refresh)
        monkeypatch.setattr(automation_module, "PRIORITY_QUOTE_REFRESH_SECONDS", 0.01)
        scan = asyncio.create_task(asyncio.to_thread(release.wait, 2))
        target_task = asyncio.create_task(supervisor._run_quote_targets())
        try:
            await asyncio.wait_for(refreshed.wait(), timeout=1)
            assert not scan.done()
        finally:
            release.set()
            target_task.cancel()
            await asyncio.gather(target_task, scan, return_exceptions=True)

    asyncio.run(exercise())


def test_stop_cleans_up_target_scheduler_and_pump_after_supervisor_failure(monkeypatch: Any) -> None:
    supervisor = automation_module.DayTradingAutomationSupervisor()
    calls = []

    async def stop_pump():
        calls.append("pump-stopped")

    monkeypatch.setattr(automation_module, "day_trading_quote_pump", SimpleNamespace(stop=stop_pump))

    async def exercise():
        async def failed():
            raise RuntimeError("scanner failed")

        supervisor._task = asyncio.create_task(failed())
        target = supervisor._quote_target_task = asyncio.create_task(asyncio.sleep(100))
        await asyncio.sleep(0)
        await supervisor.stop()
        assert target.cancelled()
        assert supervisor._task is supervisor._quote_target_task is None

    asyncio.run(exercise())
    assert calls == ["pump-stopped"]


def test_target_overflow_reaches_real_pump_and_v2_runtime_quote_health(monkeypatch: Any) -> None:
    from decimal import Decimal
    from app.services.day_trading_quote_pump import DayTradingQuotePump
    from app.services.day_trading_quote_targets import PriorityCandidate, QuoteTargetInputs
    from app.services.day_trading_v2_quotes import quote_health
    from app.services.theme_stock_universe import ThemeStock

    supervisor = automation_module.DayTradingAutomationSupervisor()
    pump = DayTradingQuotePump()
    existing = tuple(str(9000 + index) for index in range(28))
    inputs = QuoteTargetInputs(frozenset({"9900"}), (
        PriorityCandidate("9901", Decimal(90)), PriorityCandidate("9902", Decimal(80)),
    ))
    universe = tuple(ThemeStock(symbol, "test", "上市", "", ()) for symbol in (*existing, "9900", "9901", "9902"))
    monkeypatch.setattr(automation_module, "day_trading_quote_pump", pump)
    monkeypatch.setattr(supervisor, "_read_quote_inputs", lambda _now: inputs)
    monkeypatch.setattr(automation_module.day_trading_cache, "put", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(automation_module.electronic_chip_flow_alert_monitor, "stock_universe_snapshot", lambda: universe)
    monkeypatch.setattr(automation_module.electronic_chip_flow_alert_monitor, "high_frequency_symbols_snapshot", lambda _now: existing)
    monkeypatch.setattr(automation_module.electronic_chip_flow_alert_monitor, "set_day_trading_priority_symbols", lambda _symbols: None)
    monkeypatch.setattr(automation_module.day_trading_engine, "set_quote_tracking_symbols", lambda _symbols: None)
    monkeypatch.setattr(automation_module.day_trading_engine, "set_stock_universe", lambda _stocks: None)
    now = automation_module.datetime(2026, 9, 8, 2, tzinfo=automation_module.UTC)

    asyncio.run(supervisor._refresh_quote_targets(now))
    diagnostics = pump.diagnostics()
    assert diagnostics["prioritySymbols"] == ["t00", "9900", *existing]
    assert diagnostics["priorityCount"] == 30
    assert diagnostics["baselineCount"] == 2
    assert diagnostics["priorityOverflowCount"] == 2
    assert diagnostics["overCapacity"] is True
    assert quote_health({}, now, 15, diagnostics)["overCapacity"] is True

    inputs = QuoteTargetInputs(frozenset({"9900"}), ())
    asyncio.run(supervisor._refresh_quote_targets(now))
    assert pump.diagnostics()["overCapacity"] is False
    assert quote_health({}, now, 15, pump.diagnostics())["overCapacity"] is False
