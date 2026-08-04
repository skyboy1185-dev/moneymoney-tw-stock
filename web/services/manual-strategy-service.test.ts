import { describe, expect, it } from "vitest";
import { MANUAL_STRATEGIES, matchesManualStrategy } from "./manual-strategy-service";

const dayMacd = MANUAL_STRATEGIES.find((item) => item.id === "day-macd")!;
const dayMacdKd = MANUAL_STRATEGIES.find((item) => item.id === "day-macd-kd")!;

describe("策略選股器嚴格訊號規則", () => {
  const matched = {
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

  it.each([
    ["day-macd", 500_000, false],
    ["week-macd", 3_500_000, false],
    ["month-macd", 10_000_000, false],
    ["day-macd-kd", 500_000, true],
    ["week-macd-kd", 3_500_000, true],
    ["month-macd-kd", 10_000_000, true],
  ] as const)("%s 套用正確的當日量門檻與 KD 規則", (strategyId, threshold, requiresKd) => {
    const strategy = MANUAL_STRATEGIES.find((item) => item.id === strategyId)!;
    expect(strategy.volumeThreshold).toBe(threshold);
    expect(strategy.requiresKD).toBe(requiresKd);
    expect(matchesManualStrategy(strategy, { ...matched, dailyVolumeShares: threshold })).toBe(false);
    expect(matchesManualStrategy(strategy, { ...matched, dailyVolumeShares: threshold + 1 })).toBe(true);
    if (requiresKd) {
      expect(matchesManualStrategy(strategy, {
        ...matched, dailyVolumeShares: threshold + 1, previousK: 45, previousD: 40,
      })).toBe(false);
    }
  });
});
