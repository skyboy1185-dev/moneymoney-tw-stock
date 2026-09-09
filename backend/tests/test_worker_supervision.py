import asyncio

from app.services.worker_supervision import supervise


def test_failed_worker_restarts_after_cleanup_without_overlap():
    async def scenario():
        state, events = {}, []
        stopped = False
        async def worker():
            nonlocal stopped
            events.append('start')
            try:
                if len(events) == 1:
                    raise RuntimeError('injected failure')
                stopped = True
            finally:
                events.append('cleanup')
        await supervise(worker, state, stopping=lambda: stopped, retry_seconds=0)
        assert events == ['start', 'cleanup', 'start', 'cleanup']
        assert state['restartCount'] == 1
    asyncio.run(scenario())


def test_explicit_stop_cancels_without_respawn():
    async def scenario():
        started = asyncio.Event()
        state = {}
        async def worker():
            started.set()
            await asyncio.Event().wait()
        task = asyncio.create_task(supervise(worker, state, retry_seconds=0))
        await started.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert not state.get('restartCount')
    asyncio.run(scenario())


def test_unexpected_return_restarts_but_requested_stop_does_not():
    async def scenario():
        state = {}
        calls = 0
        async def worker():
            nonlocal calls
            calls += 1
        await supervise(worker, state, stopping=lambda: calls == 2, retry_seconds=0)
        assert calls == 2 and state['restartCount'] == 1
    asyncio.run(scenario())
