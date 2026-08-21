export type LongTermMode = "long_only" | "focused_long";
export type LongTermDirection = "long";

export interface LongTermModel {
  key: string;
  name: string;
  description: string;
}

export interface LongTermPositionItem {
  id: number;
  symbol: string;
  name: string;
  market: string;
  industry: string;
  direction: LongTermDirection;
  modelKey: string;
  modelName: string;
  entryDate: string;
  entryPrice: number;
  currentPrice: number;
  actualReturnPercent: number;
  priceReturnPercent: number;
  dividendReturnPercent: number;
  totalReturnPercent: number;
  dividendPerShare: number;
  dividendIncome: number;
  dividendDataAvailable: boolean;
  predictedMonthReturnPercent: number;
  allocationWeightPercent: number;
  allocatedCapital: number;
  quantity: number;
  investedCapital: number;
  priceProfit: number;
  unrealizedProfit: number;
  totalProfit: number;
  selectionScore: number;
  currentScore: number;
  holdingTradingDays: number;
  minimumHoldingDays: number;
  eligibleToReplace: boolean;
  minimumExitDate: string;
  reasons: string[];
}

export interface LongTermClosedItem {
  id: number;
  symbol: string;
  name: string;
  direction: LongTermDirection;
  modelName: string;
  entryDate: string;
  exitDate: string | null;
  entryPrice: number;
  exitPrice: number;
  actualReturnPercent: number;
  priceReturnPercent: number;
  dividendReturnPercent: number;
  totalReturnPercent: number;
  dividendPerShare: number;
  dividendIncome: number;
  priceProfit: number;
  totalProfit: number;
  dividendDataAvailable: boolean;
  exitReason: string | null;
}

export interface LongTermPerformanceRow {
  key: string;
  name: string;
  symbol: string | null;
  isModel: boolean;
  benchmarkType: "model" | "market" | "ten_year_cagr" | "ten_year_cagr_group";
  rank10Year: number | null;
  annualizedReturn10Year: number | null;
  selectionDate: string | null;
  historyStartDate: string | null;
  historyEndDate: string | null;
  startDate: string;
  startPrice: number | null;
  currentPrice: number | null;
  cumulativeReturnPercent: number;
  priceReturnPercent: number;
  dividendReturnPercent: number;
  dividendPerShare?: number;
  dividendIncome?: number;
  dividendDataAvailable: boolean;
  leadVsBenchmarkPercent: number | null;
  status: "model" | "leading" | "trailing" | "tied";
  componentCount?: number;
  constituents?: Array<{
    rank: number;
    symbol: string;
    name: string;
    annualizedReturn10Year: number;
    allocationWeightPercent: number;
    startDate: string | null;
    entryPrice: number | null;
    currentPrice: number | null;
    returnPercent: number;
    priceReturnPercent: number;
    dividendReturnPercent: number;
    dividendPerShare: number;
    dividendDataAvailable: boolean;
  }>;
}

export interface LongTermTradeMessage {
  id: number;
  timestamp: string;
  tradeDate: string;
  portfolioMode: LongTermMode;
  positionId: number;
  stockCode: string;
  stockName: string;
  eventType: "BUY" | "SELL";
  price: number;
  allocationWeightPercent: number;
  allocatedCapital: number;
  quantity: number;
  pnl: number | null;
  pnlPercent: number | null;
  reason: string;
  isRead: boolean;
}

export interface LongTermBacktestRow {
  key: string;
  name: string;
  symbol: string | null;
  strategyType: "model" | "benchmark";
  rank: number;
  returnPercent: number;
  annualizedReturnPercent: number;
  maximumDrawdownPercent: number;
  leadVs0050Percent: number;
  entryCount: number;
  replacementCount: number;
  currentHoldings: string[];
  minimumHoldingDays?: number;
  balanceScore?: number;
  weeklyReviewCount?: number;
  constituentCount?: number;
}

export interface LongTermYtdBacktestResponse {
  periodLabel: string;
  fromDate: string;
  toDate: string;
  rows: LongTermBacktestRow[];
  universeCount: number;
  requestedUniverseCount: number;
  stableRotation: {
    name: string;
    selectedMinimumHoldingDays: number;
    selectionMethod: string;
    rules: {
      targetCount: number;
      reviewFrequency: string;
      protectedRank: number;
      minimumScoreGap: number;
      maximumWeeklyReplacements: number;
    };
    variants: Array<{
      minimumHoldingDays: number;
      returnPercent: number;
      annualizedReturnPercent: number;
      maximumDrawdownPercent: number;
      balanceScore: number;
      entryCount: number;
      replacementCount: number;
      weeklyReviewCount: number;
      currentHoldings: string[];
      selected: boolean;
    }>;
  };
  dataSource: string;
  returnBasis: "adjusted_total_return";
  methodology: string;
  limitations: string[];
  calculatedAt: string;
}

export interface LongTermPortfolioResponse {
  mode: LongTermMode;
  modeLabel: string;
  startDate: string;
  selectionTime: string;
  targetCount: number;
  minimumHoldingTradingDays: number;
  models: LongTermModel[];
  items: LongTermPositionItem[];
  closedItems: LongTermClosedItem[];
  summary: {
    openCount: number;
    longCount: number;
    shortCount: number;
    actualReturnPercent: number;
    predictedMonthReturnPercent: number;
    realizedReturnPercent: number;
    completedTradeCount: number;
  };
  capitalAllocation: {
    totalCapital: number;
    plannedCapital: number;
    investedCapital: number;
    unallocatedCapital: number;
    unrealizedProfit: number;
    unrealizedPriceProfit: number;
    openDividendIncome: number;
    realizedProfit: number;
    realizedPriceProfit: number;
    realizedDividendIncome: number;
    dividendIncome: number;
    totalProfit: number;
    estimatedEquity: number;
    methodology: string;
  };
  performanceComparison: {
    rows: LongTermPerformanceRow[];
    modelReturnPercent: number;
    beatsAllBenchmarks: boolean;
    goal: string;
    methodology: string;
    returnBasis: "cash_dividend_total_return";
  };
  dividendData: {
    source: string;
    availableCount: number;
    requestedCount: number;
    methodology: string;
  };
  tradeMessages: LongTermTradeMessage[];
  unreadTradeMessageCount: number;
  lastSelectionDate: string | null;
  lastSelectionAt: string | null;
  status: "active" | "waiting_start";
  notice: string;
  updatedAt: string;
}
