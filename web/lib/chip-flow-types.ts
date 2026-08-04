export type ChipFlowStatus =
  | "realtime"
  | "delayed"
  | "no_data"
  | "awaiting_provider"
  | "invalid_symbol"
  | "disconnected";

export interface ChipFlowPoint {
  time: string;
  snapshotTime: string;
  largeBuyShares: number;
  largeSellShares: number;
  largeNetShares: number;
  largeBuyLots: number;
  largeSellLots: number;
  largeNetLots: number;
  mediumBuyShares: number;
  mediumSellShares: number;
  mediumNetShares: number;
  smallBuyShares: number;
  smallSellShares: number;
  smallNetShares: number;
  smallBuyLots: number;
  smallSellLots: number;
  smallNetLots: number;
  unknownShares: number;
  retailControlRatio: number | null;
  updatedAt: string;
}

export interface ChipFlowResponse {
  stockId: string;
  tradeDate: string;
  status: ChipFlowStatus;
  source: string;
  isEstimate: boolean;
  providerCapabilities: {
    completeIntradayTicks: boolean;
    hasTradeId: boolean;
    hasBidAskAtTrade: boolean;
    hasSourceSide: boolean;
  };
  missingFields: string[];
  largeOrderThreshold: number;
  largeOrderThresholdMode: "dynamic_percentile" | "fixed_floor";
  largeOrderThresholdPercentile: number;
  largeOrderThresholdSampleCount: number;
  smallOrderThreshold: number;
  excludedBeforeOpenShares: number;
  excludedBeforeOpenLots: number;
  excludedClosingAuctionShares: number;
  excludedClosingAuctionLots: number;
  excludedAfterHoursShares: number;
  excludedAfterHoursLots: number;
  latest: ChipFlowPoint | null;
  series: ChipFlowPoint[];
  notice: string;
  statusMessage: string;
  updatedAt: string;
}
