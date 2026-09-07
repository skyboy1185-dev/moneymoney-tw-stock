import { describe, expect, it } from "vitest";
import {
  selectVisibleManualStrategyMatches,
  type ManualStrategyMatch,
  type StockManualStrategyMatches,
} from "./manual-strategy-matches";

const match = (strategyId: string, signalDate = "2026-09-07"): ManualStrategyMatch => ({
  strategyId,
  strategyName: `策略 ${strategyId}`,
  timeframe: "day",
  signalMode: "confirmed",
  signalDate,
  signalStatus: "confirmed",
});

const result = (matches: ManualStrategyMatch[]): StockManualStrategyMatches => ({
  symbol: "2330",
  matches,
  calculatedAt: "2026-09-07T10:00:00+08:00",
  asOfDate: "2026-09-07",
  latestPriceDate: "2026-09-07",
});

describe("selectVisibleManualStrategyMatches", () => {
  it("顯示前三個策略並回傳剩餘數量", () => {
    const selected = selectVisibleManualStrategyMatches(
      result([match("a"), match("b"), match("c"), match("d"), match("e")]),
      "2026-09-07",
    );
    expect(selected.visible.map((item) => item.strategyId)).toEqual(["a", "b", "c"]);
    expect(selected.hiddenCount).toBe(2);
    expect(selected.all).toHaveLength(5);
  });

  it("排除指定交易日之後才出現的策略訊號", () => {
    const selected = selectVisibleManualStrategyMatches(
      result([match("current"), match("future", "2026-09-08")]),
      "2026-09-07",
    );
    expect(selected.all.map((item) => item.strategyId)).toEqual(["current"]);
    expect(selected.hiddenCount).toBe(0);
  });

  it("沒有比對結果時安全回傳空陣列", () => {
    expect(selectVisibleManualStrategyMatches(undefined, "2026-09-07")).toEqual({
      visible: [], hiddenCount: 0, all: [],
    });
  });
});
