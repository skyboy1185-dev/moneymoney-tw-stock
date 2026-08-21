import { describe, expect, it } from "vitest";
import type { DailyPrice } from "./types";
import { calculateThreeGatePrice, evaluateThreeGateLevels, stockTickSize } from "./three-gate-price";

function candle(date: string, high: number, low: number, close: number): DailyPrice {
  return {
    symbol: "2317",
    name: "鴻海",
    date,
    open: close,
    high,
    low,
    close,
    volume: 1_000_000,
  };
}

describe("three-gate price", () => {
  it("uses the latest completed candle during market hours", () => {
    const prices = [
      candle("2026-07-27", 260, 250, 253),
      candle("2026-07-28", 247, 238, 238),
    ];

    expect(calculateThreeGatePrice(prices, true)).toEqual({
      sourceDate: "2026-07-27",
      upper: 264,
      middle: 255,
      lower: 246,
    });
  });

  it("uses today's completed candle for the next session after close", () => {
    const prices = [
      candle("2026-07-27", 260, 250, 253),
      candle("2026-07-28", 247, 238, 238),
    ];

    expect(calculateThreeGatePrice(prices, false)).toEqual({
      sourceDate: "2026-07-28",
      upper: 250.5,
      middle: 242.5,
      lower: 234.5,
    });
  });

  it("follows Taiwan stock tick sizes", () => {
    expect([9, 20, 75, 238, 750, 1_500].map(stockTickSize))
      .toEqual([0.01, 0.05, 0.1, 0.5, 1, 5]);
  });

  it("detects standing above and crossing below all three gate levels", () => {
    const gate = { sourceDate: "2026-07-27", upper: 264, middle: 255, lower: 246 };
    expect(evaluateThreeGateLevels(253, 260, gate)).toEqual([
      { key: "upper", label: "上關價", price: 264, position: "below" },
      { key: "middle", label: "中關價", price: 255, position: "crossed-below" },
      { key: "lower", label: "下關價", price: 246, position: "above" },
    ]);
  });

  it("detects crossing above each gate and treats equality as standing above", () => {
    const gate = { sourceDate: "2026-07-27", upper: 264, middle: 255, lower: 246 };
    expect(evaluateThreeGateLevels(264, 240, gate).map(({ position }) => position))
      .toEqual(["crossed-above", "crossed-above", "crossed-above"]);
  });
});
