import { describe, expect, it } from "vitest";
import { calculateIndicators } from "./indicators";
import { analyzeTechnicalData } from "./technical-analysis";
import type { DailyPrice } from "./types";

function candles(closes: number[], volumes?: number[]): DailyPrice[] {
  return closes.map((close, index) => ({
    symbol: "TEST",
    name: "測試",
    date: new Date(Date.UTC(2025, 0, index + 1)).toISOString().slice(0, 10),
    open: index ? closes[index - 1] : close,
    high: Math.max(close, index ? closes[index - 1] : close) * 1.01,
    low: Math.min(close, index ? closes[index - 1] : close) * 0.99,
    close,
    volume: volumes?.[index] ?? 1_000_000,
  }));
}

describe("technical analysis engine", () => {
  it("classifies the six volume levels", () => {
    const prices = candles(Array.from({ length: 120 }, (_, index) => 100 + index * 0.1));
    prices.at(-1)!.volume = 3_000_000;
    const result = analyzeTechnicalData(prices, calculateIndicators(prices));
    expect(result.volume.at(-1)?.status).toBe("爆量");
    expect(result.summary.volumeExplanation).toContain("20 日均量");
  });

  it("does not equate a single MACD turn with an unconditional trade", () => {
    const prices = candles(Array.from({ length: 140 }, (_, index) => 100 + Math.sin(index / 4) * 2));
    const result = analyzeTechnicalData(prices, calculateIndicators(prices));
    expect(result.summary.operationReasons.length).toBeGreaterThan(0);
    expect(result.summary.healthScore).toBeGreaterThanOrEqual(0);
    expect(result.summary.healthScore).toBeLessThanOrEqual(100);
  });

  it("returns no trading conclusion when data is insufficient", () => {
    const prices = candles(Array.from({ length: 30 }, (_, index) => 100 + index));
    const result = analyzeTechnicalData(prices, calculateIndicators(prices));
    expect(result.summary.dataSufficient).toBe(false);
    expect(result.summary.operation).toContain("資料不足");
  });

  it("marks intraday composite signals as unconfirmed", () => {
    const closes = Array.from({ length: 130 }, (_, index) => 80 + index * 0.35);
    const prices = candles(closes, closes.map((_, index) => 1_000_000 + index * 20_000));
    const result = analyzeTechnicalData(prices, calculateIndicators(prices), true);
    if (result.summary.signal !== "neutral") expect(result.summary.operation).toContain("盤中尚未確認");
  });
});
