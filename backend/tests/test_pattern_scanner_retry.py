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
