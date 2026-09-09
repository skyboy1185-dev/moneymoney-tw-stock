import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.services import rocket_automation as rocket


def test_timeout_cleans_up_and_recovery_processes_once(monkeypatch):
    async def scenario():
        cleaned = asyncio.Event()
        async def hung():
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()
        original_wait = asyncio.wait_for
        async def short_wait(task, timeout):
            return await original_wait(task, .01)
        monkeypatch.setattr(rocket.asyncio, 'wait_for', short_wait)
        monkeypatch.setattr(rocket, 'get_settings', lambda: SimpleNamespace(rocket_radar_enabled=True, rocket_radar_scan_interval_seconds=60))
        monkeypatch.setattr(rocket, 'fetch_rocket_scan_payload', hung)
        process = MagicMock(return_value={'status':'completed'})
        monkeypatch.setattr(rocket, 'process_rocket_scan', process)
        monkeypatch.setattr(rocket, '_dispatch_buy_emails', AsyncMock(return_value=0))
        session = MagicMock()
        session.__enter__.return_value.scalar.return_value = 0
        session.__enter__.return_value.scalars.return_value.all.return_value = []
        monkeypatch.setattr(rocket, 'SessionLocal', lambda: session)
        worker = rocket.RocketRadarAutomation()
        with pytest.raises(TimeoutError):
            await worker.run_once(force=True)
        assert cleaned.is_set() and not worker._run_lock.locked()
        assert worker.state['status'] == 'error'
        assert worker.state['nextRunAt'] > datetime.now(UTC).timestamp()
        assert worker.state['scanDeadlineAt'] is None
        process.assert_not_called()
        payload = SimpleNamespace(market=SimpleNamespace(trade_date=datetime.now(UTC).date()))
        monkeypatch.setattr(rocket, 'fetch_rocket_scan_payload', AsyncMock(return_value=payload))
        await worker.run_once(force=True)
        process.assert_called_once()
        assert worker.state['lastError'] is None
        assert worker.state['lastSuccessAt']
    asyncio.run(scenario())
