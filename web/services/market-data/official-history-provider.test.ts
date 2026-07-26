import { describe, expect, it } from "vitest";
import type { StockMeta } from "@/lib/types";
import {
  mergeOfficialHistoryWithQuote,
  parseTpexMonthlyHistory,
  parseTwseMonthlyHistory,
} from "./official-history-provider";

const listed: StockMeta = {
  symbol: "2330", name: "台積電", industry: "半導體", market: "上市",
  peRatio: null, dividendYield: null, priceToBook: null, eps: null, marketCap: null,
};

const otc: StockMeta = {
  ...listed, symbol: "6488", name: "環球晶", market: "上櫃",
};

describe("official historical price parsers", () => {
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
});
