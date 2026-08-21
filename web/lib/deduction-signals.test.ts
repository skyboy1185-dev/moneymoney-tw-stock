import { describe, expect, it } from "vitest";
import { findDeductionSignalMatches, findThreePeriodDeductionPositions } from "./deduction-signals";
import type { DailyPrice } from "./types";

function buildPrices(direction: "rising" | "falling" | "flat"): DailyPrice[] {
  const start = new Date("2024-01-01T00:00:00Z");
  return Array.from({ length: 800 }, (_, index) => {
    const date = new Date(start);
    date.setUTCDate(start.getUTCDate() + index);
    const close = direction === "rising"
      ? 100 + index * 0.5
      : direction === "falling"
        ? 900 - index * 0.5
        : 100;
    return {
      symbol: "2330",
      name: "台積電",
      date: date.toISOString().slice(0, 10),
      open: close,
      high: close,
      low: close,
      close,
      volume: 1_000_000,
    };
  });
}

describe("多空動能均線扣抵提醒", () => {
  it("同一檔股票可同時列出日、週、月扣三低", () => {
    const matches = findDeductionSignalMatches(buildPrices("rising"));
    expect(matches.map(({ timeframe, direction }) => ({ timeframe, direction }))).toEqual([
      { timeframe: "day", direction: "low" },
      { timeframe: "week", direction: "low" },
      { timeframe: "month", direction: "low" },
    ]);
  });

  it("同一檔股票可同時列出日、週、月扣三高", () => {
    const matches = findDeductionSignalMatches(buildPrices("falling"));
    expect(matches.map(({ timeframe, direction }) => ({ timeframe, direction }))).toEqual([
      { timeframe: "day", direction: "high" },
      { timeframe: "week", direction: "high" },
      { timeframe: "month", direction: "high" },
    ]);
  });

  it("扣抵值等於現價時不產生提醒", () => {
    expect(findDeductionSignalMatches(buildPrices("flat"))).toEqual([]);
  });

  it("標出未來三期將依序扣除的三根日 K", () => {
    const prices = buildPrices("rising").slice(0, 30);
    expect(findThreePeriodDeductionPositions(prices)).toEqual([
      { order: 1, date: prices[10].date, value: prices[10].close },
      { order: 2, date: prices[11].date, value: prices[11].close },
      { order: 3, date: prices[12].date, value: prices[12].close },
    ]);
  });

  it("可標出 MA5 未來三期的短線扣抵位置", () => {
    const prices = buildPrices("rising").slice(0, 10);
    expect(findThreePeriodDeductionPositions(prices, 5)).toEqual([
      { order: 1, date: prices[5].date, value: prices[5].close },
      { order: 2, date: prices[6].date, value: prices[6].close },
      { order: 3, date: prices[7].date, value: prices[7].close },
    ]);
  });
});
