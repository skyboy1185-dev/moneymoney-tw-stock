import { describe, expect, it } from "vitest";
import type { DailyPrice } from "./types";
import { calculateSupportResistance } from "./support-resistance";

function candle(index: number, low: number, high: number, close = 100): DailyPrice {
  return {
    symbol: "TEST",
    name: "測試",
    date: `2026-01-${String(index + 1).padStart(2, "0")}`,
    open: close,
    high,
    low,
    close,
    volume: 1_000_000,
  };
}

describe("calculateSupportResistance", () => {
  it("returns the nearest two distinct pivot levels on each side", () => {
    const lows = [98, 97, 94, 97, 98, 96, 90, 96, 98, 97, 92, 97, 99];
    const highs = [102, 104, 108, 104, 103, 106, 112, 106, 103, 105, 115, 105, 101];
    const prices = lows.map((low, index) => candle(index, low, highs[index]));
    prices.at(-1)!.close = 100;

    expect(calculateSupportResistance(prices)).toEqual({
      support1: 94,
      support2: 92,
      resistance1: 108,
      resistance2: 112,
    });
  });

  it("never repeats first and second levels and keeps them on the correct side", () => {
    const prices = Array.from({ length: 30 }, (_, index) =>
      candle(index, 90 + index * 0.1, 110 + index * 0.1),
    );
    prices.at(-1)!.close = 100;
    const levels = calculateSupportResistance(prices);

    expect(levels.support1).toBeLessThan(100);
    expect(levels.support2).toBeLessThan(levels.support1!);
    expect(levels.resistance1).toBeGreaterThan(100);
    expect(levels.resistance2).toBeGreaterThan(levels.resistance1!);
  });
});
