export type ElectronicChipFlowAlertStatus =
  | "realtime"
  | "scanning"
  | "warming"
  | "closed"
  | "unavailable"
  | "disconnected";

export interface ElectronicChipFlowAlert {
  symbol: string;
  name: string;
  industry: string;
  time: string;
  largeNetLots: number;
  recentNetBuyLots: number;
  recentBuyLots: number;
  recentSellLots: number;
  buySellRatio: number;
  positiveSteps: number;
  updatedAt: string;
}

export interface ElectronicChipFlowAlertsResponse {
  tradeDate: string;
  status: ElectronicChipFlowAlertStatus;
  marketOpen: boolean;
  source: string;
  isEstimate: boolean;
  windowMinutes: number;
  minRecentNetLots: number;
  minBuySellRatio: number;
  minPositiveSteps: number;
  scannedCount: number;
  candidateCount: number;
  alerts: ElectronicChipFlowAlert[];
  lastError: string | null;
  notice: string;
  updatedAt: string;
}
