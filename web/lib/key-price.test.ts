import { describe, expect, it } from "vitest";
import type { DailyPrice } from "./types";
import { assessKeyPrice } from "./key-price";

function price(day: number, high: number, close = high - 1): DailyPrice {
  return {
    symbol: "2330",
    name: "台積電",
    date: `2026-07-${String(day).padStart(2, "0")}`,
    open: close - 1,
    high,
    low: close - 2,
    close,
    volume: 1_000_000,
  };
}

describe("assessKeyPrice", () => {
  it("uses the previous 20-session high and excludes today's high", () => {
    const history = Array.from({ length: 20 }, (_, index) => price(index + 1, 100 + index));
    const result = assessKeyPrice([...history, price(21, 140, 120)]);

    expect(result.keyPrice).toBe(119);
    expect(result.aboveKeyPrice).toBe(true);
    expect(result.keyPriceDistancePct).toBe(.84);
  });

  it("does not award a breakout when the close remains below the key price", () => {
    const history = Array.from({ length: 20 }, (_, index) => price(index + 1, 100 + index));
    const result = assessKeyPrice([...history, price(21, 125, 118)]);

    expect(result.keyPrice).toBe(119);
    expect(result.aboveKeyPrice).toBe(false);
    expect(result.keyPriceDistancePct).toBe(-.84);
  });
});
