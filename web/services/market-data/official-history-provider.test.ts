import { describe, expect, it, vi } from "vitest";
import type { StockMeta } from "@/lib/types";
import {
  mergeOfficialHistoryWithQuote,
  parseFinMindHistory,
  parseYahooDailyHistory,
  parseTpexMonthlyHistory,
  parseTwseMonthlyHistory,
  validateOfficialHistoryContinuity,
  getOfficialHistory,
  resetOfficialHistoryCacheForTests,
  stockPayloadFromHistory,
} from "./official-history-provider";

const listed: StockMeta = {
  symbol: "2330", name: "台積電", industry: "半導體", market: "上市",
  peRatio: null, dividendYield: null, priceToBook: null, eps: null, marketCap: null,
};

const otc: StockMeta = {
  ...listed, symbol: "6488", name: "環球晶", market: "上櫃",
};

describe("official historical price parsers", () => {
  it("displays 147 real candles with null annual averages without filling the strict history cache", async () => {
    resetOfficialHistoryCacheForTests();
    const data = Array.from({ length: 147 }, (_, i) => ({
      date: new Date(Date.UTC(2026, 0, 29 + i)).toISOString().slice(0, 10),
      Trading_Volume: 1000, open: 100, max: 102, min: 99, close: 101,
    }));
    const fetcher = vi.fn(async () => Response.json({ status: 200, data }));
    vi.stubGlobal("fetch", fetcher);
    try {
      const meta = { ...otc, symbol: "6907" };
      const prices = await getOfficialHistory(meta, 1);
      const payload = stockPayloadFromHistory(meta, null, prices);
      expect(payload.prices).toHaveLength(147);
      expect(payload.indicators.at(-1)?.ma120).toBe(101);
      expect(payload.indicators.at(-1)?.ma240).toBeNull();
      expect(() => validateOfficialHistoryContinuity(prices)).toThrow("不足");
      // Strict callers must perform their own load, not reuse display-only bars.
      fetcher.mockImplementation(async () => {
        throw new Error("strict source unavailable");
      });
      const pending = getOfficialHistory(meta);
      await Promise.resolve();
      expect(fetcher.mock.calls.length).toBeGreaterThan(1);
      // Let all retries finish immediately while verifying the strict rejection.
      vi.useFakeTimers();
      const rejected = expect(pending).rejects.toThrow();
      await vi.runAllTimersAsync();
      await rejected;
    } finally {
      vi.useRealTimers();
      vi.unstubAllGlobals();
      resetOfficialHistoryCacheForTests();
    }
  });
  it("uses validated Yahoo history when FinMind fails, without monthly fanout", async () => {
    resetOfficialHistoryCacheForTests();
    const timestamp = Array.from({ length: 250 }, (_, i) => Date.UTC(2025, 0, 1 + i) / 1000);
    const values = (n: number) => timestamp.map(() => n);
    const fetcher = vi.fn(async (url: string) => {
      if (url.includes("finmindtrade")) return new Response("unavailable", { status: 503 });
      if (url.includes("range=5y")) return Response.json({ chart: { result: [{
        timestamp, indicators: { quote: [{ open: values(100), high: values(102), low: values(99), close: values(101), volume: values(1000) }] },
      }] } });
      throw new Error("Unexpected monthly request");
    });
    vi.stubGlobal("fetch", fetcher);
    try {
      const prices = await getOfficialHistory(listed);
      expect(prices).toHaveLength(250);
      expect(prices[0].close).toBe(101);
      expect(fetcher).toHaveBeenCalledTimes(2);
      expect(stockPayloadFromHistory(listed, null, prices).dataQuality?.historySource).toContain("Yahoo");
    } finally {
      vi.unstubAllGlobals();
      resetOfficialHistoryCacheForTests();
    }
  });
  it("parses FinMind market history without synthetic scaling", () => {
    const prices = parseFinMindHistory({
      status: 200,
      data: [{
        date: "2026-07-24", Trading_Volume: 24_810_509,
        open: 2355, max: 2365, min: 2345, close: 2350,
      }],
    }, listed);
    expect(prices).toEqual([{
      symbol: "2330", name: "台積電", date: "2026-07-24",
      open: 2355, high: 2365, low: 2345, close: 2350, volume: 24_810_509,
    }]);
  });

  it("parses Yahoo daily candles used by the broad-market scanner fallback", () => {
    const prices = parseYahooDailyHistory({
      chart: { result: [{
        timestamp: [1_785_984_600],
        indicators: { quote: [{
          open: [1_400], high: [1_420], low: [1_390], close: [1_415], volume: [25_000_000],
        }] },
      }] },
    }, listed);
    expect(prices).toEqual([{
      symbol: "2330", name: listed.name, date: "2026-08-06",
      open: 1_400, high: 1_420, low: 1_390, close: 1_415, volume: 25_000_000,
    }]);
  });

  it("parses TWSE share volume without changing its unit", () => {
    const prices = parseTwseMonthlyHistory({
      data: [["115/06/01", "60,942,792", "144,105,259,583", "2,355.00", "2,415.00", "2,350.00", "2,355.00"]],
    }, listed);
    expect(prices).toEqual([{
      symbol: "2330", name: "台積電", date: "2026-06-01",
      open: 2355, high: 2415, low: 2350, close: 2355, volume: 60_942_792,
    }]);
  });

  it("converts TPEx volume from lots to shares", () => {
    const prices = parseTpexMonthlyHistory({
      tables: [{
        data: [["115/06/01", "4,452", "4,333,718", "1,015.00", "1,040.00", "936.00", "950.00", "-65.00", "6,880"]],
      }],
    }, otc);
    expect(prices[0]).toMatchObject({
      symbol: "6488", date: "2026-06-01", close: 950, volume: 4_452_000,
    });
  });

  it("rejects incomplete and impossible candles", () => {
    const prices = parseTwseMonthlyHistory({
      data: [
        ["115/06/01", "100", "1000", "--", "--", "--", "--"],
        ["115/06/02", "100", "1000", "100", "90", "95", "100"],
      ],
    }, listed);
    expect(prices).toEqual([]);
  });

  it("keeps final exchange volume after close and only merges live MIS candles", () => {
    const history = [{
      symbol: "2330", name: "台積電", date: "2026-07-24",
      open: 2355, high: 2365, low: 2345, close: 2350, volume: 24_810_509,
    }];
    const closingQuote = {
      symbol: "2330", name: "台積電", date: "2026-07-24", time: "13:30:00",
      open: 2355, high: 2365, low: 2345, price: 2350, previousClose: 2405,
      change: -55, changePercent: -2.29, volume: 21_505_000,
      source: "TWSE MIS" as const, isRealtime: false,
    };
    expect(mergeOfficialHistoryWithQuote(history, listed, closingQuote)[0].volume).toBe(24_810_509);
    expect(mergeOfficialHistoryWithQuote(history, listed, { ...closingQuote, isRealtime: true })[0].volume)
      .toBe(21_505_000);
  });

  it("shows a delayed quote for a newer trading date without treating it as live", () => {
    const history = [{
      symbol: "2330", name: "台積電", date: "2026-07-24",
      open: 2355, high: 2365, low: 2345, close: 2350, volume: 24_810_509,
    }];
    const delayedQuote = {
      symbol: "2330", name: "台積電", date: "2026-07-27", time: "11:48:40",
      open: 2350, high: 2370, low: 2330, price: 2340, previousClose: 2350,
      change: -10, changePercent: -.43, volume: 18_500_000,
      source: "Yahoo Finance 準即時" as const, isRealtime: false,
    };
    expect(mergeOfficialHistoryWithQuote(history, listed, delayedQuote).at(-1)).toMatchObject({
      date: "2026-07-27", close: 2340, volume: 18_500_000,
    });
  });

  it("rejects histories with too few rows or a missing calendar month", () => {
    const row = {
      symbol: "2330", name: "台積電", open: 100, high: 101, low: 99, close: 100, volume: 1_000,
    };
    expect(() => validateOfficialHistoryContinuity(
      Array.from({ length: 239 }, (_, index) => ({ ...row, date: `2025-01-${String(index + 1).padStart(2, "0")}` })),
    )).toThrow("不足");
    const continuous = Array.from({ length: 240 }, (_, index) => {
      const date = new Date(Date.UTC(2025, 0, 1 + index));
      return { ...row, date: date.toISOString().slice(0, 10) };
    });
    continuous[120] = { ...continuous[120], date: "2026-03-01" };
    expect(() => validateOfficialHistoryContinuity(continuous)).toThrow("日期缺口");
  });
});
