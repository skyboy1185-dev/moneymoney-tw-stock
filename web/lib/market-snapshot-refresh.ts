export const MARKET_STREAM_STALE_MS = 45_000;
export const MARKET_ACTIVE_FALLBACK_POLL_MS = 30_000;
export const MARKET_IDLE_FALLBACK_POLL_MS = 60_000;

export type FuturesFlashDirection = "up" | "down" | "";

export function shouldFallbackRefreshMarketSnapshot({
  now,
  lastEventAt,
  hasSnapshot,
  marketOpen,
  futuresMarketOpen,
}: {
  now: number;
  lastEventAt: number;
  hasSnapshot: boolean;
  marketOpen: boolean;
  futuresMarketOpen: boolean;
}) {
  const elapsed = now - lastEventAt;
  if (!hasSnapshot) return elapsed >= MARKET_STREAM_STALE_MS;
  if (marketOpen || futuresMarketOpen) return elapsed >= MARKET_STREAM_STALE_MS;
  return elapsed >= MARKET_IDLE_FALLBACK_POLL_MS;
}

export function marketSnapshotFallbackPollMs({
  marketOpen,
  futuresMarketOpen,
}: {
  marketOpen: boolean;
  futuresMarketOpen: boolean;
}) {
  return marketOpen || futuresMarketOpen ? MARKET_ACTIVE_FALLBACK_POLL_MS : MARKET_IDLE_FALLBACK_POLL_MS;
}

export function futuresFlashDirection(previous: number | null | undefined, next: number | null | undefined): FuturesFlashDirection {
  if (previous == null || next == null) return "";
  if (!Number.isFinite(previous) || !Number.isFinite(next)) return "";
  if (next > previous) return "up";
  if (next < previous) return "down";
  return "";
}
