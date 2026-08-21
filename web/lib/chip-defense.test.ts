import { describe, expect, it } from "vitest";
import { calculateChipDefense, calculateChipDefenseLevel } from "./chip-defense";
import type { DailyPrice } from "./types";

function price(day: number, low: number, high: number, close: number, volume: number): DailyPrice {
  return {
    symbol: "2330",
    name: "測試股",
    date: `2026-07-${String(day).padStart(2, "0")}`,
    open: close,
    high,
    low,
    close,
    volume,
  };
}

describe("chip defense", () => {
  it("uses the last 5 and 20 trading days for weekly and monthly defense", () => {
    const prices = Array.from({ length: 20 }, (_, index) => index < 15
      ? price(index + 1, 78, 82, 80, 300_000)
      : price(index + 1, 98, 102, 100, 600_000));
    const result = calculateChipDefense(prices, 105);

    expect(result.week?.tradingDays).toBe(5);
    expect(result.month?.tradingDays).toBe(20);
    expect(result.week?.defensePrice).toBeGreaterThan(98);
    expect(result.month?.defensePrice).toBeLessThan(90);
    expect(result.week?.status).toBe("held");
  });

  it("marks prices inside and below the dominant cost zone", () => {
    const prices = [
      price(1, 98, 102, 100, 500_000),
      price(2, 98, 102, 100, 500_000),
      price(3, 98, 102, 100, 500_000),
    ];
    const testing = calculateChipDefenseLevel(prices, 100, "week");
    const broken = calculateChipDefenseLevel(prices, 90, "week");

    expect(testing?.status).toBe("testing");
    expect(broken?.status).toBe("broken");
    expect(broken?.distancePct).toBeLessThan(0);
  });

  it("returns no level when price data cannot form a range", () => {
    expect(calculateChipDefense([], 100).week).toBeNull();
    expect(calculateChipDefense([price(1, 100, 100, 100, 100_000)], 100).month).toBeNull();
  });
});
