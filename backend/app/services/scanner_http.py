"""Bounded retries for read-only scanner responses."""
import asyncio
import logging
import httpx

logger = logging.getLogger(__name__)


async def fetch_scanner_response(client, url, headers):
    """Retry only transient read failures; never retry writes or invalid payloads."""
    for attempt in range(3):
        try:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            # The scanner proxy streams keepalive whitespace with HTTP 200;
            # its upstream failure is reported in the JSON envelope.
            payload = response.json()
            if isinstance(payload, dict) and payload.get("error") and payload.get("statusCode") in {408, 429, 500, 502, 503, 504}:
                raise httpx.RemoteProtocolError("Scanner upstream temporarily unavailable")
            return response
        except (httpx.TransportError, httpx.HTTPStatusError) as error:
            if isinstance(error, httpx.HTTPStatusError) and error.response.status_code not in {408, 429, 500, 502, 503, 504}:
                raise
            if attempt == 2:
                raise
            logger.warning("Scanner request retry %s: %s", attempt + 1, type(error).__name__)
            await asyncio.sleep(2 ** (attempt + 1))

