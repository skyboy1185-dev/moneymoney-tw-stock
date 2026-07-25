import type { RankingRow } from "./market-types";

export interface AIPortfolioSettings {
  totalCapital: number;
  minimumCashPercentage: number;
  maxTotalExposure: number;
  maxPositionPercentage: number;
  maxIndustryPercentage: number;
  maxRiskPerTrade: number;
  maxPortfolioRisk: number;
  maximumAddOnCount: number;
  initialEntryRatio: number;
  firstAddOnRatio: number;
  secondAddOnRatio: number;
  allowAddOn: boolean;
  prohibitAveragingDown: boolean;
  dailySummaryEnabled: boolean;
  updatedAt: string;
}

export interface AIAllocation {
  totalCapital: number;
  investedAmount: number;
  availableCapital: number;
  actualExposurePercentage: number;
  cashPercentage: number;
  portfolioRiskAmount: number;
  portfolioRiskPercentage: number;
  industryExposure: { industry: string; amount: number; percentage: number }[];
  cacheMode: string;
  cacheHealthy: boolean;
}

export interface AIStockMonitor {
  id: number;
  symbol: string;
  stockName: string;
  market: string;
  industry: string;
  strategyName: string;
  secondaryStrategies: string[];
  signalId: string;
  monitorStatus: string;
  totalScore: number;
  strategyFit: number;
  marketFit: number;
  healthScore: number;
  currentPrice: number;
  entryMin: number;
  entryMax: number;
  stopLoss: number;
  target1: number;
  target2: number;
  riskRewardRatio: number;
  targetAllocationPercentage: number;
  initialAllocationPercentage: number;
  firstAddOnPercentage: number;
  secondAddOnPercentage: number;
  suggestedInitialAmount: number;
  suggestedInitialQuantity: number;
  estimatedRiskAmount: number;
  reasons: string[];
  warnings: string[];
  quoteSource: string;
  quoteTimestamp: string;
  createdAt: string;
  updatedAt: string;
  expiredAt: string;
}

export interface AIStockPosition {
  id: number;
  monitorId: number;
  symbol: string;
  stockName: string;
  industry: string;
  strategyName: string;
  entryPrice: number;
  averageCost: number;
  originalQuantity: number;
  remainingQuantity: number;
  entryTime: string;
  stopLoss: number;
  target1: number;
  target2: number;
  trailingStop: number | null;
  currentPrice: number;
  highestPrice: number;
  lowestPrice: number;
  maxUnrealizedProfit: number;
  maxUnrealizedLoss: number;
  realizedProfit: number;
  unrealizedProfit: number;
  returnPercentage: number;
  healthScore: number;
  latestAction: string;
  positionStatus: string;
  overnightStatus: boolean;
  targetAllocationPercentage: number;
  initialAllocationPercentage: number;
  currentAllocationPercentage: number;
  investedAmount: number;
  availableAddOnAmount: number;
  addOnCount: number;
  estimatedRiskAmount: number;
  addOnEnabled: boolean;
  quoteSource: string;
  quoteTimestamp: string;
}

export interface AIStockAlert {
  id: number;
  signalId: string;
  alertType: string;
  alertLevel: string;
  action: string;
  price: number;
  reason: string;
  linePushStatus: string;
  readAt: string | null;
  createdAt: string;
}

export interface AIStockDashboard {
  settings: AIPortfolioSettings;
  allocation: AIAllocation;
  waiting: AIStockMonitor[];
  positions: AIStockPosition[];
  ended: AIStockPosition[];
  alerts: AIStockAlert[];
  featured: RankingRow[];
  candidates: RankingRow[];
  updatedAt: string;
  disclaimer: string;
}
