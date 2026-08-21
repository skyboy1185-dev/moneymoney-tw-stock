import { describe, expect, it } from "vitest";
import {
  calculateThreePeriodDeductionSignal,
  detectSingleKdBullishDivergence,
  detectDoubleKdBullishDivergence,
  estimateMacdBarsToPositive,
  MANUAL_STRATEGIES,
  matchesManualStrategy,
} from "./manual-strategy-service";
import type { KDPoint } from "@/lib/market-types";
import type { DailyPrice } from "@/lib/types";

const dayMacd = MANUAL_STRATEGIES.find((item) => item.id === "day-macd")!;
const dayMacdKd = MANUAL_STRATEGIES.find((item) => item.id === "day-macd-kd")!;
const dayMacdForecast = MANUAL_STRATEGIES.find((item) => item.id === "day-macd-forecast")!;
const dayMacdKdForecast = MANUAL_STRATEGIES.find((item) => item.id === "day-macd-kd-forecast")!;
const dayKdBelow8 = MANUAL_STRATEGIES.find((item) => item.id === "day-kd-below-8")!;

describe("策略選股器嚴格訊號規則", () => {
  const matched = {
    twoPreviousHistogram: -0.03,
    previousHistogram: -0.01, currentHistogram: 0.01,
    previousK: 38, previousD: 40, currentK: 42, currentD: 41,
    dailyVolumeShares: 500_001,
  };

  it("OSC 必須嚴格由負轉正，零值不算翻紅", () => {
    expect(matchesManualStrategy(dayMacd, matched)).toBe(true);
    expect(matchesManualStrategy(dayMacd, { ...matched, previousHistogram: 0 })).toBe(false);
    expect(matchesManualStrategy(dayMacd, { ...matched, currentHistogram: 0 })).toBe(false);
  });

  it("成交量必須嚴格大於門檻", () => {
    expect(matchesManualStrategy(dayMacd, { ...matched, dailyVolumeShares: 500_000 })).toBe(false);
    expect(matchesManualStrategy(dayMacd, { ...matched, dailyVolumeShares: 500_001 })).toBe(true);
  });

  it("KD 必須嚴格黃金交叉且目前 K 小於 50", () => {
    expect(matchesManualStrategy(dayMacdKd, matched)).toBe(true);
    expect(matchesManualStrategy(dayMacdKd, { ...matched, previousK: 40 })).toBe(false);
    expect(matchesManualStrategy(dayMacdKd, { ...matched, currentK: 52, currentD: 49 })).toBe(false);
  });

  it("日週月 KD 極低檔都要求 K、D 同時嚴格低於 8", () => {
    const strategies = MANUAL_STRATEGIES.filter((strategy) => strategy.signalMode === "kd-below");
    expect(strategies.map(({ timeframe, kdThreshold }) => ({ timeframe, kdThreshold }))).toEqual([
      { timeframe: "day", kdThreshold: 8 },
      { timeframe: "week", kdThreshold: 8 },
      { timeframe: "month", kdThreshold: 8 },
    ]);
    for (const strategy of strategies) {
      expect(matchesManualStrategy(strategy, {
        ...matched, currentK: 7.99, currentD: 7.5, currentHistogram: -1, dailyVolumeShares: 0,
      })).toBe(true);
      expect(matchesManualStrategy(strategy, { ...matched, currentK: 8, currentD: 7.5 })).toBe(false);
      expect(matchesManualStrategy(strategy, { ...matched, currentK: 7.5, currentD: 8 })).toBe(false);
    }
  });

  it("KD 低於 8 策略不綁 MACD 翻紅或成交量", () => {
    expect(matchesManualStrategy(dayKdBelow8, {
      ...matched,
      previousHistogram: 1,
      currentHistogram: -1,
      currentK: 4,
      currentD: 6,
      dailyVolumeShares: 0,
    })).toBe(true);
  });

  it("一次低檔背離要求價格創低但 K、D 低點同步墊高", () => {
    const candles: DailyPrice[] = Array.from({ length: 35 }, (_, index) => ({
      symbol: "2330", name: "台積電",
      date: new Date(Date.UTC(2026, 0, index + 1)).toISOString().slice(0, 10),
      open: 105, high: 110, low: index === 20 ? 90 : index === 34 ? 85 : 100,
      close: 105, volume: 1_000_000,
    }));
    const kd: KDPoint[] = candles.map((candle) => ({ date: candle.date, k: 20, d: 20, goldenCross: false }));
    kd[20] = { ...kd[20], k: 10, d: 12 };
    kd[34] = { ...kd[34], k: 16, d: 17 };

    const signal = detectSingleKdBullishDivergence(candles, kd);
    expect(signal).toMatchObject({
      previousLow: 90, currentLow: 85, previousK: 10, currentK: 16,
      previousD: 12, currentD: 17, strength: 5.5,
    });
    const weakerKd = kd.map((point) => ({ ...point }));
    weakerKd[34] = { ...weakerKd[34], k: 8, d: 10 };
    expect(detectSingleKdBullishDivergence(candles, weakerKd)).toBeNull();
  });

  it("提供日 KD 一次低檔背離策略", () => {
    const strategies = MANUAL_STRATEGIES.filter((strategy) => strategy.signalMode === "kd-bullish-divergence");
    expect(strategies.map(({ id, timeframe, divergenceLookback }) => ({ id, timeframe, divergenceLookback }))).toEqual([{
      id: "day-kd-single-bullish-divergence", timeframe: "day", divergenceLookback: 30,
    }]);
  });

  it("二度低檔背離要求三個價格低點下降、三個 KD 低點墊高", () => {
    const candles: DailyPrice[] = Array.from({ length: 50 }, (_, index) => ({
      symbol: "2330", name: "台積電",
      date: new Date(Date.UTC(2026, 0, index + 1)).toISOString().slice(0, 10),
      open: 105, high: 110,
      low: index === 15 ? 95 : index === 30 ? 90 : index === 49 ? 85 : 100,
      close: 105, volume: 1_000_000,
    }));
    const kd: KDPoint[] = candles.map((candle) => ({ date: candle.date, k: 20, d: 20, goldenCross: false }));
    kd[15] = { ...kd[15], k: 8, d: 10 };
    kd[30] = { ...kd[30], k: 12, d: 14 };
    kd[49] = { ...kd[49], k: 17, d: 18 };

    expect(detectDoubleKdBullishDivergence(candles, kd)).toMatchObject({
      previousLow: 95, middleLow: 90, currentLow: 85,
      previousK: 8, middleK: 12, currentK: 17,
      previousD: 10, middleD: 14, currentD: 18,
      strength: 8.5,
    });
    const brokenSequence = kd.map((point) => ({ ...point }));
    brokenSequence[30] = { ...brokenSequence[30], k: 7, d: 9 };
    expect(detectDoubleKdBullishDivergence(candles, brokenSequence)).toBeNull();
  });

  it("提供日 KD 二度低檔背離策略", () => {
    const strategy = MANUAL_STRATEGIES.find((item) => item.id === "day-kd-double-bullish-divergence");
    expect(strategy).toMatchObject({
      timeframe: "day", signalMode: "kd-double-bullish-divergence", divergenceLookback: 45,
    });
  });

  it.each([
    ["day-macd", 500_000, false, "confirmed"],
    ["week-macd", 3_500_000, false, "confirmed"],
    ["month-macd", 10_000_000, false, "confirmed"],
    ["day-macd-kd", 500_000, true, "confirmed"],
    ["week-macd-kd", 3_500_000, true, "confirmed"],
    ["month-macd-kd", 10_000_000, true, "confirmed"],
  ] as const)("%s 套用正確的當日量門檻與 KD 規則", (strategyId, threshold, requiresKd, signalMode) => {
    const strategy = MANUAL_STRATEGIES.find((item) => item.id === strategyId)!;
    expect(strategy.volumeThreshold).toBe(threshold);
    expect(strategy.requiresKD).toBe(requiresKd);
    expect(strategy.signalMode).toBe(signalMode);
    expect(matchesManualStrategy(strategy, { ...matched, dailyVolumeShares: threshold })).toBe(false);
    expect(matchesManualStrategy(strategy, { ...matched, dailyVolumeShares: threshold + 1 })).toBe(true);
    if (requiresKd) {
      expect(matchesManualStrategy(strategy, {
        ...matched, dailyVolumeShares: threshold + 1, previousK: 45, previousD: 40,
      })).toBe(false);
    }
  });

  it("提供六個與原策略門檻相同的預測翻紅策略", () => {
    const forecasts = MANUAL_STRATEGIES.filter((strategy) => strategy.signalMode === "forecast");
    expect(forecasts).toHaveLength(6);
    expect(forecasts.map(({ timeframe, volumeThreshold, requiresKD }) => ({ timeframe, volumeThreshold, requiresKD })))
      .toEqual(MANUAL_STRATEGIES.filter((strategy) => strategy.signalMode === "confirmed")
        .map(({ timeframe, volumeThreshold, requiresKD }) => ({ timeframe, volumeThreshold, requiresKD })));
  });

  it("預測版要求三根負柱狀體連續收斂，且推估兩根 K 內翻紅", () => {
    const forecastMatched = {
      ...matched,
      twoPreviousHistogram: -0.06,
      previousHistogram: -0.035,
      currentHistogram: -0.015,
    };
    expect(estimateMacdBarsToPositive(forecastMatched)).toBe(0.7);
    expect(matchesManualStrategy(dayMacdForecast, forecastMatched)).toBe(true);
    expect(matchesManualStrategy(dayMacd, forecastMatched)).toBe(false);
    expect(matchesManualStrategy(dayMacdForecast, { ...forecastMatched, currentHistogram: 0.001 })).toBe(false);
    expect(matchesManualStrategy(dayMacdForecast, { ...forecastMatched, currentHistogram: -0.08 })).toBe(false);
    expect(matchesManualStrategy(dayMacdForecast, {
      ...forecastMatched, twoPreviousHistogram: -0.06, previousHistogram: -0.03, currentHistogram: -0.028,
    })).toBe(false);
  });

  it("預測版仍套用成交量與 KD 低檔金叉條件", () => {
    const forecastMatched = {
      ...matched,
      twoPreviousHistogram: -0.06,
      previousHistogram: -0.035,
      currentHistogram: -0.015,
    };
    expect(matchesManualStrategy(dayMacdKdForecast, forecastMatched)).toBe(true);
    expect(matchesManualStrategy(dayMacdKdForecast, { ...forecastMatched, dailyVolumeShares: 500_000 })).toBe(false);
    expect(matchesManualStrategy(dayMacdKdForecast, { ...forecastMatched, previousK: 43 })).toBe(false);
  });

  it("提供日週月各自的扣三低與扣三高，共六個扣抵模型", () => {
    const deductions = MANUAL_STRATEGIES.filter((strategy) => strategy.deductionDirection != null);
    expect(deductions).toHaveLength(6);
    expect(deductions.map(({ timeframe, deductionDirection, maPeriod }) => ({
      timeframe, deductionDirection, maPeriod,
    }))).toEqual([
      { timeframe: "day", deductionDirection: "low", maPeriod: 20 },
      { timeframe: "day", deductionDirection: "high", maPeriod: 20 },
      { timeframe: "week", deductionDirection: "low", maPeriod: 20 },
      { timeframe: "week", deductionDirection: "high", maPeriod: 20 },
      { timeframe: "month", deductionDirection: "low", maPeriod: 20 },
      { timeframe: "month", deductionDirection: "high", maPeriod: 20 },
    ]);
  });

  it("扣三低要求未來三個扣抵值全部低於目前收盤價", () => {
    const signal = calculateThreePeriodDeductionSignal([
      80, 82, 84, 86, 88, 90, 91, 92, 93, 94,
      95, 96, 97, 98, 99, 100, 101, 102, 103, 104,
    ], 20)!;
    expect(signal.deductionValues).toEqual([80, 82, 84]);
    expect(signal.matchesLow).toBe(true);
    expect(signal.matchesHigh).toBe(false);
    expect(signal.projectedMaValues[2]).toBeGreaterThan(signal.currentMa);
    expect(signal.deductionGapPercent).toBeGreaterThan(0);
  });

  it("扣三高要求未來三個扣抵值全部高於目前收盤價，等於現價不算", () => {
    const high = calculateThreePeriodDeductionSignal([
      120, 118, 116, 114, 112, 110, 109, 108, 107, 106,
      105, 104, 103, 102, 101, 100, 99, 98, 97, 96,
    ], 20)!;
    expect(high.matchesHigh).toBe(true);
    expect(high.matchesLow).toBe(false);
    expect(high.projectedMaValues[2]).toBeLessThan(high.currentMa);
    expect(high.deductionGapPercent).toBeLessThan(0);

    const equal = calculateThreePeriodDeductionSignal([
      96, 118, 116, 114, 112, 110, 109, 108, 107, 106,
      105, 104, 103, 102, 101, 100, 99, 98, 97, 96,
    ], 20)!;
    expect(equal.matchesLow).toBe(false);
    expect(equal.matchesHigh).toBe(false);
  });
});
