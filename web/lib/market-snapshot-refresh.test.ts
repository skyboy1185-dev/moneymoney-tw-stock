import { describe, expect, it } from "vitest";

import {
  MARKET_ACTIVE_FALLBACK_POLL_MS,
  FUTURES_QUOTE_DELAY_MS,
  MARKET_IDLE_FALLBACK_POLL_MS,
  MARKET_STREAM_STALE_MS,
  futuresFlashDirection,
  isFuturesQuoteDelayed,
  marketSnapshotFallbackPollMs,
  parseTaipeiQuoteTimeMs,
  shouldFallbackRefreshMarketSnapshot,
} from "./market-snapshot-refresh";

describe("market snapshot refresh helpers", () => {
  it("falls back to refresh when the market stream is stale during night futures", () => {
    expect(shouldFallbackRefreshMarketSnapshot({
      now: MARKET_STREAM_STALE_MS + 1,
      lastEventAt: 0,
      hasSnapshot: true,
      marketOpen: false,
      futuresMarketOpen: true,
    })).toBe(true);
  });

  it("does not force fallback while the stream is still fresh", () => {
    expect(shouldFallbackRefreshMarketSnapshot({
      now: MARKET_STREAM_STALE_MS - 1,
      lastEventAt: 0,
      hasSnapshot: true,
      marketOpen: false,
      futuresMarketOpen: true,
    })).toBe(false);
  });

  it("uses active polling for futures sessions and idle polling after all sessions close", () => {
    expect(marketSnapshotFallbackPollMs({ marketOpen: false, futuresMarketOpen: true }))
      .toBe(MARKET_ACTIVE_FALLBACK_POLL_MS);
    expect(marketSnapshotFallbackPollMs({ marketOpen: false, futuresMarketOpen: false }))
      .toBe(MARKET_IDLE_FALLBACK_POLL_MS);
  });

  it("reports futures price flash direction only when the value changes", () => {
    expect(futuresFlashDirection(46_473, 46_500)).toBe("up");
    expect(futuresFlashDirection(46_473, 46_462)).toBe("down");
    expect(futuresFlashDirection(46_473, 46_473)).toBe("");
  });

  it("parses quote timestamps as Taipei time", () => {
    expect(parseTaipeiQuoteTimeMs("2026-08-28 17:33"))
      .toBe(Date.parse("2026-08-28T17:33:00+08:00"));
  });

  it("marks futures quotes delayed after ninety seconds", () => {
    const quoteAt = "2026-08-28 17:33";
    const now = Date.parse("2026-08-28T17:33:00+08:00") + FUTURES_QUOTE_DELAY_MS + 1;

    expect(isFuturesQuoteDelayed({ now, quoteAt })).toBe(true);
    expect(isFuturesQuoteDelayed({ now: now - 2_000, quoteAt })).toBe(false);
  });
});
