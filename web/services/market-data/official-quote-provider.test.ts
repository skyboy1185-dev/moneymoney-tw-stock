import { describe, expect, it } from "vitest";
import { mergeOfficialQuote } from "./official-quote-provider";
import { MockStockDataProvider } from "@/services/stock-service";
import type { StockQuote } from "@/lib/types";

describe("mergeOfficialQuote", () => {
  it("使用官方最新價與昨收銜接展示歷史，重新計算指標", async () => {
    const payload = await new MockStockDataProvider().getStock("2330");
    expect(payload).not.toBeNull();
    const quote: StockQuote = {
      symbol: "2330", name: "台積電", date: "2026-07-24", time: "13:30:00",
      open: 2355, high: 2365, low: 2345, price: 2350, previousClose: 2405,
      change: -55, changePercent: -2.2869, volume: 21_505_000,
      source: "TWSE MIS", isRealtime: false,
    };
    const merged = mergeOfficialQuote(payload!, quote);
    expect(merged.prices.at(-1)).toMatchObject({
      date: "2026-07-24", open: 2355, high: 2365, low: 2345, close: 2350, volume: 21_505_000,
    });
    expect(merged.prices.at(-2)?.close).toBe(2405);
    expect(merged.indicators).toHaveLength(merged.prices.length);
    expect(merged.dataMode).toBe("official_quote_demo_history");
  });
});
