import { describe, expect, it } from "vitest";
import type { StockDeductionSignals } from "@/lib/deduction-signals";
import type { ElectronicChipFlowAlert } from "@/lib/electronic-chip-flow-alerts";
import { buildDingSelectionRows } from "@/lib/ding-selection";

const alert = (symbol: string, rank: number): ElectronicChipFlowAlert => ({
  symbol,
  name: symbol,
  market: "上市",
  industry: "AI",
  themes: [],
  time: "10:00",
  rank,
  largeNetLots: 1,
  dayLargeBuyLots: 1,
  dayLargeSellLots: 0,
  daySmallBuyLots: 0,
  daySmallSellLots: 0,
  recentNetBuyLots: 1,
  recentSmallNetBuyLots: 0,
  combinedNetBuyLots: 1,
  recentBuyLots: 1,
  recentSellLots: 0,
  recentSmallBuyLots: 0,
  recentSmallSellLots: 0,
  buySellRatio: 99,
  positiveSteps: 1,
  smallPositiveSteps: 0,
  updatedAt: "2026-08-27T10:00:00+08:00",
  occurrenceCount: 0,
  firstDetectedAt: "2026-08-27T10:00:00+08:00",
  cycleStartedAt: "2026-08-27T10:00:00+08:00",
  lastDetectedAt: "2026-08-27T10:00:00+08:00",
  peakRecentNetBuyLots: 1,
  momentumChangeLots: 0,
  momentumChangePercent: 0,
  trend: "sustained",
  trendLabel: "steady",
  trendStreak: 1,
  alertLevel: "info",
  isWarning: false,
  reinforced: false,
  simultaneousIncrease: false,
  currentQualifies: false,
  message: "steady",
  history: [],
});

const signal = (symbol: string, signalDate: string): StockDeductionSignals => ({
  symbol,
  currentPrice: 100,
  previousClose: 99,
  threeGate: null,
  calculatedAt: "2026-08-27T10:00:00+08:00",
  matches: [{
    timeframe: "day",
    direction: "low",
    maPeriod: 20,
    deductionValues: [80, 82, 84],
    deductionAverage: 82,
    deductionGapPercent: 18,
    projectedMaValues: [101, 102, 103],
    signalDate,
  }],
});

describe("buildDingSelectionRows", () => {
  it("keeps Ding selections ordered by the visible cumulative ranking", () => {
    const rows = buildDingSelectionRows(
      [signal("2330", "2026-08-27"), signal("2408", "2026-08-27")],
      [alert("2408", 1), alert("2330", 2)],
      "2026-08-27",
    );

    expect(rows.map((row) => [row.symbol, row.sourceRank])).toEqual([
      ["2408", 1],
      ["2330", 2],
    ]);
  });

  it("drops deduction matches dated after the requested as-of date", () => {
    const rows = buildDingSelectionRows(
      [signal("2330", "2026-08-28")],
      [alert("2330", 1)],
      "2026-08-27",
    );

    expect(rows).toEqual([]);
  });
});
