import asyncio
from contextlib import suppress
import importlib.util
from pathlib import Path

from app.services import day_trading_quote_pump as module
from app.services.day_trading_quote_pump import DayTradingQuotePump
from app.services.official_market_data import StockQuoteRequest


def requests(count):
    return [StockQuoteRequest(str(1000+i), "test", "上市") for i in range(count)]


def test_yahoo_priority_every_turn_and_baseline_rotates_without_starvation():
    tick = [0.0]
    pool = requests(300)
    pump = DayTradingQuotePump(provider=object(), monotonic=lambda: tick[0])
    pump.update_targets(pool[:30], pool[30:], mandatory_symbols={pool[0].symbol})
    seen = set()
    for _ in range(6):
        assert pump.yahoo_targets("priority") == pool[:30]
        batch = pump.yahoo_targets("baseline")
        assert len(batch) <= 50
        assert not {r.symbol for r in batch} & {r.symbol for r in pool[:30]}
        seen.update(r.symbol for r in batch)
        tick[0] += 5
    assert seen == {r.symbol for r in pool[30:]}
    assert pump.state["freeQuoteBaselineSweepSeconds"] == 30
    promoted = pool[90]
    pump.update_targets([promoted, *pool[:30]], pool[30:], mandatory_symbols={promoted.symbol})
    assert promoted.symbol in pump.priority_symbols()
    assert promoted not in pump.yahoo_targets("baseline")
    pump.update_targets([], [], enabled=False)
    assert pump.yahoo_targets("priority") == pump.yahoo_targets("baseline") == []


def test_mandatory_holdings_are_not_truncated_to_priority_soft_cap():
    pool = requests(75)
    pump = DayTradingQuotePump(provider=object())
    pump.update_targets(pool[:60], pool[60:], mandatory_symbols={r.symbol for r in pool[:60]})
    assert pump.yahoo_targets("priority") == pool[:60]
    assert pump.state["mandatoryOverCapacity"] is True


def test_slow_baseline_does_not_delay_next_priority_refresh(monkeypatch):
    from app.services import yahoo_tw_live_quotes as yahoo

    async def scenario():
        pool = requests(90)
        pump = DayTradingQuotePump(provider=object(), publisher=lambda quotes: None)
        pump.update_targets(pool[:30], pool[30:])
        monkeypatch.setattr(module, "FREE_QUOTE_INTERVAL_SECONDS", .01)
        priority = {r.symbol for r in pool[:30]}
        counts = {"priority": 0, "baseline": 0, "active": 0, "maximum": 0}
        twice = asyncio.Event()
        blocked = asyncio.Event()

        async def fetch(_client, batch):
            counts["active"] += 1
            counts["maximum"] = max(counts["maximum"], counts["active"])
            try:
                if batch[0].symbol in priority:
                    counts["priority"] += 1
                    if counts["priority"] >= 2:
                        twice.set()
                else:
                    counts["baseline"] += 1
                    await blocked.wait()
                return {}
            finally:
                counts["active"] -= 1

        monkeypatch.setattr(yahoo, "fetch_batch", fetch)
        task = asyncio.create_task(pump._run_yahoo())
        try:
            await asyncio.wait_for(twice.wait(), 2)
            assert counts["baseline"] == 1 and counts["maximum"] <= 2
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        assert counts["active"] == 0

    asyncio.run(scenario())


def test_local_relay_preserves_holdings_and_rotates_only_general_pool():
    path = Path(__file__).resolve().parents[2] / "tools" / "quote_relay.py"
    spec = importlib.util.spec_from_file_location("quote_relay_test", path)
    relay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(relay)
    pool = [{"symbol": str(i), "priority": i < 30} for i in range(300)]
    attempts, seen = {}, set()
    for tick in range(6):
        batches = relay.scheduled_batches(pool, attempts, tick * 5)
        assert batches[0] == pool[:30]
        assert all(len(b) <= 50 for b in batches)
        seen.update(r["symbol"] for batch in batches[1:] for r in batch)
    assert seen == {r["symbol"] for r in pool[30:]}
    pool[100]["priority"] = True
    batches = relay.scheduled_batches(pool, attempts, 30)
    assert pool[100] in batches[0] and pool[100] not in batches[-1]
    assert "100" not in attempts
