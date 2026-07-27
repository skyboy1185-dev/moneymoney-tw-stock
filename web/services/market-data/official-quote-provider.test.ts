import { describe, expect, it } from "vitest";
import { isQuoteRealtime, mergeOfficialQuote, parseMisStockQuote } from "./official-quote-provider";
import { MockStockDataProvider } from "@/services/stock-service";
import type { StockMeta, StockQuote } from "@/lib/types";

const foxconn: StockMeta = {
  symbol: "2317",
  name: "鴻海",
  market: "上市",
  industry: "其他電子",
  peRatio: null,
  dividendYield: null,
  priceToBook: null,
  eps: null,
  marketCap: null,
};

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

describe("isQuoteRealtime", () => {
  it("requires the same trading date and a recent timestamp", () => {
    const now = new Date("2026-07-21T01:30:30.000Z");
    expect(isQuoteRealtime("2026-07-21", "09:30:00", now)).toBe(true);
    expect(isQuoteRealtime("2026-07-21", "09:20:00", now)).toBe(false);
    expect(isQuoteRealtime("2026-07-20", "09:30:00", now)).toBe(false);
  });

  it("rejects weekends and off-hours", () => {
    expect(isQuoteRealtime("2026-07-25", "09:30:00", new Date("2026-07-25T01:30:30.000Z"))).toBe(false);
    expect(isQuoteRealtime("2026-07-21", "14:00:00", new Date("2026-07-21T06:00:30.000Z"))).toBe(false);
  });
});

describe("parseMisStockQuote", () => {
  it("does not disguise the previous close as a live price when z is empty", () => {
    const quote = parseMisStockQuote({
      c: "2317", n: "鴻海", z: "-", y: "252.5000",
      o: "253.0000", h: "254.5000", l: "248.0000", v: "16523",
      d: "20260727", t: "11:06:40",
      b: "248.0000_247.5000_", a: "248.5000_249.0000_",
    }, foxconn, undefined, new Date("2026-07-27T03:06:45.000Z"));

    expect(quote).toBeNull();
  });

  it("keeps today's last valid trade when a later MIS snapshot has an empty z", () => {
    const previous: StockQuote = {
      symbol: "2317", name: "鴻海", date: "2026-07-27", time: "11:06:35",
      open: 253, high: 254.5, low: 248, price: 248.5, previousClose: 252.5,
      change: -4, changePercent: -1.5842, volume: 16_520_000,
      bestBid: 248, bestAsk: 248.5, source: "TWSE MIS", isRealtime: true,
    };
    const quote = parseMisStockQuote({
      c: "2317", n: "鴻海", z: "-", y: "252.5000",
      o: "253.0000", h: "254.5000", l: "247.5000", v: "16523",
      d: "20260727", t: "11:06:40",
      b: "248.0000_247.5000_", a: "248.5000_249.0000_",
    }, foxconn, previous, new Date("2026-07-27T03:06:45.000Z"));

    expect(quote).toMatchObject({
      price: 248.5,
      previousClose: 252.5,
      change: -4,
      volume: 16_523_000,
      time: "11:06:35",
      isRealtime: true,
    });
  });
});
