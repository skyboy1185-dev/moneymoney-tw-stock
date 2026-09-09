import { afterEach, expect, it, vi } from "vitest";

vi.mock("@/services/backend-client", () => ({ backendJson: vi.fn() }));
vi.mock("@/services/market-data/official-history-provider", () => ({ getOfficialRecentHistory: vi.fn() }));
import { patternHistory } from "./pattern-robot-scan-service";

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

it("reuses completed bars while excluding today's price from the cache", async () => {
  vi.useFakeTimers();
  const now = new Date("2026-09-09T03:00:00Z");
  vi.setSystemTime(now);
  const timestamps = Array.from({ length: 200 }, (_, i) => now.getTime() / 1000 - (199 - i) * 86400);
  const prices = Array(200).fill(100);
  const fetcher = vi.fn(async () => new Response(JSON.stringify({ chart: { result: [{
    timestamp: timestamps, indicators: { quote: [{ open: prices, high: prices, low: prices,
      close: prices, volume: prices }], adjclose: [{ adjclose: prices }] },
  }] } })));
  vi.stubGlobal("fetch", fetcher);
  const company = { symbol: "3532", name: "test", market: "上市" as const, sector: "test", listingDate: null };
  const first = await patternHistory(company);
  const second = await patternHistory(company);
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(first).toBe(second);
  expect(first.actual).toHaveLength(199);
  expect(first.actual.every(row => row.date < "2026-09-09")).toBe(true);
  expect(first.adjusted.every(row => row.date < "2026-09-09")).toBe(true);
});
