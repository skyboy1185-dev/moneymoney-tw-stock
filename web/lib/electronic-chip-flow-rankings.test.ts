import { describe, expect, it } from "vitest";
import type { ElectronicChipFlowAlert, ElectronicChipFlowAlertsResponse } from "@/lib/electronic-chip-flow-alerts";
import { selectLargeOrderRankings } from "@/lib/electronic-chip-flow-rankings";

const alert = (symbol: string): ElectronicChipFlowAlert => ({
  symbol,
  name: symbol,
  market: "上市",
  industry: "AI",
  themes: [],
  time: "10:00",
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
  updatedAt: "2026-07-29T10:00:00+08:00",
  occurrenceCount: 0,
  firstDetectedAt: "2026-07-29T10:00:00+08:00",
  cycleStartedAt: "2026-07-29T10:00:00+08:00",
  lastDetectedAt: "2026-07-29T10:00:00+08:00",
  peakRecentNetBuyLots: 1,
  momentumChangeLots: 0,
  momentumChangePercent: 0,
  trend: "sustained",
  trendLabel: "觀察中",
  trendStreak: 1,
  alertLevel: "info",
  isWarning: false,
  reinforced: false,
  simultaneousIncrease: false,
  currentQualifies: false,
  message: "觀察中",
  history: [],
});

const basePayload = (overrides: Partial<ElectronicChipFlowAlertsResponse>): ElectronicChipFlowAlertsResponse => ({
  tradeDate: "2026-07-29",
  status: "realtime",
  marketOpen: true,
  source: "test",
  isEstimate: true,
  windowMinutes: 5,
  minRecentNetLots: 10,
  minBuySellRatio: 1.5,
  minPositiveSteps: 2,
  scannedCount: 1,
  baselineCycleScannedCount: 1,
  baselineCycleTargetSeconds: 90,
  lastFullScanAt: null,
  candidateCount: 1,
  disposedExcludedCount: 0,
  disposedExcludedSymbols: [],
  restrictionStatus: "healthy",
  popularCandidateCount: 0,
  fastCandidateCount: 0,
  cpoCandidateCount: 0,
  packagingTestCandidateCount: 0,
  powerCandidateCount: 0,
  popularUniverseSource: "test",
  popularUniverseUpdatedAt: null,
  hotScanCount: 0,
  highFrequencyTrackingCount: 0,
  pinnedTrackingCount: 0,
  refreshSeconds: 2,
  warningCount: 0,
  strengtheningCount: 0,
  jointIncreaseCount: 0,
  alerts: [],
  trackedAlerts: [],
  shortCount: 0,
  shortStrengtheningCount: 0,
  shortAlerts: [],
  trackedShortAlerts: [],
  lastError: null,
  notice: "",
  updatedAt: "2026-07-29T10:00:00+08:00",
  ...overrides,
});

describe("selectLargeOrderRankings", () => {
  it("prefers continuous ranking fields over strict alert fields", () => {
    const payload = basePayload({
      alerts: [alert("2330")],
      longRankings: [alert("2408")],
      shortAlerts: [alert("2303")],
      shortRankings: [alert("2317")],
    });

    expect(selectLargeOrderRankings(payload, "long").map((item) => item.symbol)).toEqual(["2408"]);
    expect(selectLargeOrderRankings(payload, "short").map((item) => item.symbol)).toEqual(["2317"]);
  });

  it("falls back to strict alert fields for old backend payloads", () => {
    const payload = basePayload({
      alerts: [alert("2330")],
      shortAlerts: [alert("2303")],
    });

    expect(selectLargeOrderRankings(payload, "long").map((item) => item.symbol)).toEqual(["2330"]);
    expect(selectLargeOrderRankings(payload, "short").map((item) => item.symbol)).toEqual(["2303"]);
  });
});
