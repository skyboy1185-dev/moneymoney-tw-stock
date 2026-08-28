export const MARKET_STREAM_STALE_MS = 45_000;
export const MARKET_ACTIVE_FALLBACK_POLL_MS = 30_000;
export const MARKET_IDLE_FALLBACK_POLL_MS = 60_000;
export const FUTURES_QUOTE_DELAY_MS = 90_000;

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

export function parseTaipeiQuoteTimeMs(value: string | null | undefined): number | null {
  if (!value) return null;
  const normalized = value.trim();
  const taipeiMatch = normalized.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?$/);
  const timestamp = taipeiMatch
    ? Date.parse(`${taipeiMatch[1]}-${taipeiMatch[2]}-${taipeiMatch[3]}T${taipeiMatch[4]}:${taipeiMatch[5]}:${taipeiMatch[6] ?? "00"}+08:00`)
    : Date.parse(normalized);
  return Number.isFinite(timestamp) ? timestamp : null;
}

export function isFuturesQuoteDelayed({
  now,
  quoteAt,
  updatedAt,
}: {
  now: number;
  quoteAt?: string | null;
  updatedAt?: string | null;
}) {
  const quoteTime = parseTaipeiQuoteTimeMs(quoteAt) ?? parseTaipeiQuoteTimeMs(updatedAt);
  if (quoteTime == null) return true;
  return now - quoteTime > FUTURES_QUOTE_DELAY_MS;
}
