import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from app.services import pattern_robot_automation as automation


@pytest.mark.parametrize('failure', ['disconnect', '503', 'proxy503'])
def test_transient_page_failure_recovers(monkeypatch, failure):
    calls = []
    def handler(request):
        calls.append(request.url)
        if len(calls) == 1:
            if failure == 'disconnect':
                raise httpx.RemoteProtocolError('incomplete chunked read')
            if failure == 'proxy503':
                return httpx.Response(200, json={'error': 'unavailable', 'statusCode': 503})
            return httpx.Response(503)
        return httpx.Response(200, json={'ok': True})
    monkeypatch.setattr(automation.asyncio, 'sleep', AsyncMock())
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await automation._fetch_scanner_page(client, 'https://scanner.test/?page=2', {})
            assert result.json() == {'ok': True}
    asyncio.run(run())
    assert len(calls) == 2 and calls[0] == calls[1]


@pytest.mark.parametrize('status,attempts', [(401, 1), (503, 3)])
def test_permanent_or_exhausted_errors_propagate(monkeypatch, status, attempts):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status)
    monkeypatch.setattr(automation.asyncio, 'sleep', AsyncMock())
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await automation._fetch_scanner_page(client, 'https://scanner.test/', {})
    asyncio.run(run())
    assert len(calls) == attempts


def test_cancellation_is_not_retried(monkeypatch):
    client = AsyncMock()
    client.get.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(automation._fetch_scanner_page(client, 'https://scanner.test/', {}))
    assert client.get.await_count == 1


def test_hung_scan_cancels_before_retry_and_never_processes_partial_data(monkeypatch):
    from unittest.mock import MagicMock
    async def scenario():
        finished = asyncio.Event()
        async def hung(progress):
            try:
                await asyncio.Event().wait()
            finally:
                finished.set()
        wait_for = asyncio.wait_for
        async def bounded(awaitable, timeout):
            return await wait_for(awaitable, timeout=.01)
        monkeypatch.setattr(automation.asyncio, 'wait_for', bounded)
        monkeypatch.setattr(automation, '_is_trading_day', lambda day: True)
        monkeypatch.setattr(automation, 'fetch_pattern_scan_payload', hung)
        process = MagicMock(return_value={'completed': True})
        monkeypatch.setattr(automation, 'process_pattern_scan', process)
        monkeypatch.setattr(automation, 'SessionLocal', MagicMock())
        worker = automation.PatternRobotAutomation()
        with pytest.raises(TimeoutError):
            await worker.run_once(force=True)
        assert finished.is_set() and not worker._run_lock.locked()
        process.assert_not_called()
        assert worker.state['status'] == 'error'
        monkeypatch.setattr(automation, 'fetch_pattern_scan_payload', AsyncMock(return_value=object()))
        await worker.run_once(force=True)
        process.assert_called_once()
        assert worker.state['status'] == 'running'
        assert worker.state['lastError'] is None
    asyncio.run(scenario())
