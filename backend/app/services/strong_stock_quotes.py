"""Fresh, bounded quotes for the independent strong-stock paper account."""
import asyncio
from datetime import UTC, datetime

import httpx

from .official_market_data import official_market_data_provider
from .quote_quality import trusted_quote, quote_time
from .yahoo_tw_live_quotes import fetch_batch

MAX_QUOTE_AGE_SECONDS = 15


async def load_quotes(requests, *, provider=None, fetcher=fetch_batch, now=None):
    provider = provider or official_market_data_provider
    clock = now or (lambda: datetime.now(UTC))
    requests = list({r.symbol: r for r in requests}.values())
    quotes = provider.cached_quotes(requests)
    missing = [r for r in requests if not trusted_quote(quotes.get(r.symbol), clock(), MAX_QUOTE_AGE_SECONDS)]
    errors = []
    if missing:
        try:
            async with httpx.AsyncClient(timeout=3, headers={"User-Agent": "Mozilla/5.0", "Cache-Control": "no-cache"}) as client:
                async def fetch_missing():
                    for offset in range(0, len(missing), 50):
                        received = await fetcher(client, missing[offset:offset+50])
                        for symbol, quote in received.items():
                            prior = quotes.get(symbol)
                            if prior is None or (quote_time(quote) and (not quote_time(prior) or quote_time(quote) >= quote_time(prior))):
                                quotes[symbol] = quote
                await asyncio.wait_for(fetch_missing(), timeout=6)
        except (httpx.HTTPError, asyncio.TimeoutError, ValueError, TypeError) as exc:
            errors.append(type(exc).__name__)
    # A failed refresh must not discard observations received by the shared pump.
    for symbol, quote in provider.cached_quotes(requests).items():
        prior = quotes.get(symbol)
        if prior is None or (quote_time(quote) and (not quote_time(prior) or quote_time(quote) > quote_time(prior))):
            quotes[symbol] = quote
    current = clock()
    wanted = {r.symbol for r in requests}
    fresh = {s: q for s, q in quotes.items() if s in wanted and trusted_quote(q, current, MAX_QUOTE_AGE_SECONDS)}
    provider.ingest_verified_quotes(fresh)
    return fresh, {"status": "no_targets" if not requests else "current" if len(fresh) == len(requests) else "partial" if fresh else "unavailable",
        "observedAt": current.isoformat(), "requestedCount": len(requests), "freshCount": len(fresh),
        "staleCount": len(requests)-len(fresh), "sources": sorted({q.source for q in fresh.values()}),
        "latestQuoteAt": max((q.quote_timestamp for q in fresh.values()), default=None),
        "errors": errors}
