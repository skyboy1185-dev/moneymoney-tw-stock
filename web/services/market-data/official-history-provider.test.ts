import { describe, expect, it } from "vitest";
import type { StockMeta } from "@/lib/types";
import {
  mergeOfficialHistoryWithQuote,
  parseFinMindHistory,
  parseTpexMonthlyHistory,
  parseTwseMonthlyHistory,
  validateOfficialHistoryContinuity,
} from "./official-history-provider";

const listed: StockMeta = {
  symbol: "2330", name: "台積電", industry: "半導體", market: "上市",
  peRatio: null, dividendYield: null, priceToBook: null, eps: null, marketCap: null,
};

const otc: StockMeta = {
  ...listed, symbol: "6488", name: "環球晶", market: "上櫃",
};

describe("official historical price parsers", () => {
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
