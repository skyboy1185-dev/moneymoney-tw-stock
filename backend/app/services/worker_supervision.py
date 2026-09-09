"""Restart failed async loops only after the previous invocation has ended."""
import asyncio
from datetime import UTC, datetime
import logging

logger = logging.getLogger(__name__)


async def supervise(worker, state, *, stopping=lambda: False, retry_seconds=5):
    while not stopping():
        try:
            await worker()
            if stopping():
                return
            reason = "Worker exited unexpectedly"
        except asyncio.CancelledError:
            raise  # Explicit stop/shutdown must never respawn the worker.
        except Exception as error:
            reason = type(error).__name__
            logger.exception("Background worker exited; restarting")
        state.update(status="recovering", lastError=reason,
                     restartCount=state.get("restartCount", 0) + 1,
                     lastRestartAt=datetime.now(UTC).isoformat())
        await asyncio.sleep(retry_seconds)
