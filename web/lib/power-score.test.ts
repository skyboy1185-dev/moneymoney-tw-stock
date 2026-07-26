import { describe, expect, it } from "vitest";
import { calculateIndicators } from "./indicators";
import { calculatePowerScore } from "./power-score";
import type { DailyPrice } from "./types";

function series(direction: "up" | "down"): DailyPrice[] {
  return Array.from({ length: 180 }, (_, index) => {
    const close = direction === "up" ? 80 + index * 0.45 : 180 - index * 0.45;
    return {
      symbol: "TEST", name: "測試", date: new Date(Date.UTC(2025, 0, index + 1)).toISOString().slice(0, 10),
      open: close * (direction === "up" ? 0.995 : 1.005), high: close * 1.01, low: close * 0.99,
      close, volume: 1_000_000 + index * 10_000,
    };
  });
}

describe("AI leader power score", () => {
  it("maps health score deterministically to 0-17 power value", () => {
    const prices = series("up");
    const result = calculatePowerScore(prices, calculateIndicators(prices));
    expect(result.powerValue).toBe(Math.round(result.healthScore / 100 * 17));
    expect(result.healthScore).toBeGreaterThanOrEqual(0);
    expect(result.healthScore).toBeLessThanOrEqual(100);
  });

  it("gives a stronger score to an uptrend than a downtrend", () => {
    const up = series("up");
    const down = series("down");
    expect(calculatePowerScore(up, calculateIndicators(up)).healthScore)
      .toBeGreaterThan(calculatePowerScore(down, calculateIndicators(down)).healthScore);
  });

  it("does not fabricate unavailable chip and institutional data", () => {
    const prices = series("up");
    const result = calculatePowerScore(prices, calculateIndicators(prices));
    expect(result.dataCoverage).toBe(75);
    expect(result.deductions.some((reason) => reason.includes("資料未串接"))).toBe(true);
    expect(result.sections.find((section) => section.name === "籌碼")?.score).toBe(0);
  });

  it("includes price levels, rating and deduction reasons", () => {
    const prices = series("up");
    const result = calculatePowerScore(prices, calculateIndicators(prices));
    expect(result.stopLoss).not.toBeNull();
    expect(result.takeProfit).not.toBeNull();
    expect(result.starLabel).toHaveLength(5);
    expect(result.deductions.length).toBeGreaterThan(0);
  });
});
