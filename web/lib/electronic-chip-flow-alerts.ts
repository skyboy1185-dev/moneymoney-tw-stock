export type ElectronicChipFlowAlertStatus =
  | "realtime"
  | "scanning"
  | "warming"
  | "closed"
  | "unavailable"
  | "disconnected";

export interface ElectronicChipFlowAlert {
  direction?: "long" | "short";
  symbol: string;
  name: string;
  market: "上市" | "上櫃";
  industry: string;
  themes: string[];
  time: string;
  largeNetLots: number;
  dayLargeBuyLots: number;
  dayLargeSellLots: number;
  daySmallBuyLots: number;
  daySmallSellLots: number;
  recentNetBuyLots: number;
  recentNetSellLots?: number;
  recentSmallNetBuyLots: number;
  combinedNetBuyLots: number;
  recentBuyLots: number;
  recentSellLots: number;
  recentSmallBuyLots: number;
  recentSmallSellLots: number;
  buySellRatio: number;
  sellBuyRatio?: number;
  positiveSteps: number;
  negativeSteps?: number;
  smallPositiveSteps: number;
  recentGrossLargeLots?: number;
  effectiveNetThresholdLots?: number;
  largeOrderOffsetting?: boolean;
  updatedAt: string;
  lastScannedAt?: string | null;
  lastTradeAt?: string | null;
  lastLargeOrderAt?: string | null;
  scanAgeSeconds?: number | null;
  tradeAgeSeconds?: number | null;
  largeOrderAgeSeconds?: number | null;
  dataState?: "active" | "warming" | "stale" | "offsetting" | "no_new_large_order" | "closed";
  dataStateLabel?: string;
  scanError?: string | null;
  largeOrderThresholdAmount?: number | null;
  largeOrderThresholdMode?: string | null;
  largeOrderThresholdPercentile?: number | null;
  largeOrderThresholdSampleCount?: number | null;
  occurrenceCount: number;
  firstDetectedAt: string;
  cycleStartedAt: string;
  lastDetectedAt: string;
  peakRecentNetBuyLots: number;
  momentumChangeLots: number;
  momentumChangePercent: number;
  trend: "starting" | "strengthening" | "sustained" | "weakening" | "fading";
  trendLabel: string;
  trendStreak: number;
  alertLevel: "info" | "positive" | "warning" | "critical";
  isWarning: boolean;
  reinforced: boolean;
  simultaneousIncrease: boolean;
  currentQualifies: boolean;
  message: string;
  history: ElectronicChipFlowMomentumPoint[];
}

export interface ElectronicChipFlowQuote {
  symbol: string;
  name: string;
  price: number;
  previousClose: number;
  change: number;
  changePercent: number;
  quoteTimestamp: string;
  source: string;
  isRealtime: boolean;
}

export interface ElectronicChipFlowPricePoint {
  timestamp: string;
  price: number;
  isRealtime: boolean;
}

export interface ElectronicChipFlowPriceHistory {
  symbol: string;
  points: ElectronicChipFlowPricePoint[];
}

export interface ElectronicChipFlowMomentumPoint {
  time: string;
  recentNetBuyLots: number;
  recentSmallNetBuyLots: number;
  combinedNetBuyLots: number;
  changeLots: number;
  qualified: boolean;
  simultaneousIncrease: boolean;
}

export interface ElectronicChipFlowMarketPulse {
  status: "realtime" | "warming" | "closed";
  direction: "bullish" | "bearish" | "neutral";
  directionLabel: string;
  trend: "bull_strengthening" | "bull_weakening" | "bull_stable" | "bear_strengthening" | "bear_weakening" | "bear_stable" | "neutral";
  trendLabel: string;
  largeNetLots: number;
  largeChangeLots: number;
  smallNetLots: number;
  smallChangeLots: number;
  combinedNetLots: number;
  coverageCount: number;
  updatedAt: string | null;
  isEstimate: boolean;
  source: string;
}

export interface ElectronicChipFlowAlertsResponse {
  tradeDate: string;
  status: ElectronicChipFlowAlertStatus;
  marketOpen: boolean;
  source: string;
  providerRateLimited?: boolean;
  providerRetrySeconds?: number;
  isEstimate: boolean;
  windowMinutes: number;
  minRecentNetLots: number;
  minBuySellRatio: number;
  minPositiveSteps: number;
  scannedCount: number;
  baselineCycleScannedCount: number;
  baselineCycleTargetSeconds: number;
  lastFullScanAt: string | null;
  candidateCount: number;
  candidateTarget?: number;
  candidateCoveragePercent?: number;
  universeStatus?: "healthy" | "degraded" | "fallback";
  universeNotice?: string | null;
  lastSuccessfulUniverseAt?: string | null;
  disposedExcludedCount: number;
  disposedExcludedSymbols: string[];
  restrictionStatus: "healthy" | "degraded";
  payloadCacheHit?: boolean;
  payloadCacheHits?: number;
  payloadCacheMisses?: number;
  popularCandidateCount: number;
  fastCandidateCount: number;
  cpoCandidateCount: number;
  packagingTestCandidateCount: number;
  powerCandidateCount: number;
  popularUniverseSource: string;
  popularUniverseUpdatedAt: string | null;
  hotScanCount: number;
  highFrequencyTrackingCount: number;
  pinnedTrackingCount: number;
  expandedTrackingCount?: number;
  refreshSeconds: number;
  warningCount: number;
  strengtheningCount: number;
  jointIncreaseCount: number;
  marketPulse?: ElectronicChipFlowMarketPulse;
  alerts: ElectronicChipFlowAlert[];
  trackedAlerts: ElectronicChipFlowAlert[];
  shortCount: number;
  shortStrengtheningCount: number;
  shortAlerts: ElectronicChipFlowAlert[];
  trackedShortAlerts: ElectronicChipFlowAlert[];
  lastError: string | null;
  notice: string;
  updatedAt: string;
}
