export type LargeHolderRankingType = "over400" | "over1000";
export type LargeHolderMarketFilter = "all" | "listed" | "otc";

export interface LargeHolderRankingItem {
  rank: number;
  stockCode: string;
  stockName: string;
  market: "上市" | "上櫃";
  industry: string;
  latestPrice: number | null;
  weeklyChangePct: number | null;
  currentLargeHolderRatio: number;
  previousLargeHolderRatio: number;
  changePercentagePoint: number;
  changePercentage: number | null;
  currentHolderCount: number;
  holderCountChange: number;
  currentLotCount: number;
  previousLotCount: number;
  lotCountChange: number;
  foreignNetBuy5d: number | null;
  investmentTrustNetBuy5d: number | null;
  dealerNetBuy5d: number | null;
  mainForceNetBuy5d: number | null;
  volumeChange5d: number | null;
  averageTurnover20d: number | null;
  technicalStatus: string;
  healthScore: number;
  aiSignal: string;
  anomalyFlag: boolean;
  anomalyReason: string;
  warnings: string[];
  quoteSource: string;
  quoteTimestamp: string;
}

export interface LargeHolderRankingResponse {
  type: LargeHolderRankingType;
  currentReportDate: string;
  previousReportDate: string;
  updatedAt: string;
  dataMode: "demo" | "official_tdcc";
  dataSource: string;
  dataNotice: string;
  industries: string[];
  items: LargeHolderRankingItem[];
  syncResult?: { status: string; message?: string; reportDate?: string };
}

export interface LargeHolderHistoryPoint {
  reportDate: string;
  stockCode: string;
  stockName?: string;
  ratioOver400: number;
  ratioOver1000: number;
  holdersOver400: number;
  holdersOver1000: number;
  lotsOver400: number;
  lotsOver1000: number;
  price: number | null;
  volume: number | null;
  foreignNetBuy: number | null;
  investmentTrustNetBuy: number | null;
  dealerNetBuy: number | null;
  mainForceNetBuy: number | null;
  marginBalanceChange: number | null;
}

export interface LargeHolderHistoryResponse {
  stockCode: string;
  stockName: string;
  dataMode: "demo" | "official_tdcc";
  dataSource: string;
  dataNotice: string;
  items: LargeHolderHistoryPoint[];
}
