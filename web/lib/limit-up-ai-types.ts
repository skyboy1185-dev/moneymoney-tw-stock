export interface LimitUpAiSettings {
  capital: number;
  minPrice: number;
  maxPrice: number;
  minAverageTurnover20d: number;
  minVolumeRatio20d: number;
  firstPositionPct: number;
  maxPositionPct: number;
  maxPositions: number;
  maxLossPerTradePct: number;
  maxDailyLossPct: number;
  maxConsecutiveStops: number;
  overnightTotalPct: number;
  overnightSinglePct: number;
  excludeLockedLimitUp: boolean;
  soundEnabled: boolean;
  updatedAt: string;
}

export interface LimitUpAiStatus {
  status: string;
  startedAt?: string | null;
  lastRunAt?: string | null;
  lastSuccessAt?: string | null;
  lastError?: string | null;
  lastResult?: unknown;
  lastUserCount: number;
  cycleCount: number;
  intervalSeconds: number;
  marketSessionActive: boolean;
  marketTime: string;
  userId?: string;
}

export interface LimitUpCandidate {
  id: string;
  symbol: string;
  stockName: string;
  market: string;
  rank: number;
  price: number;
  previousClose: number;
  limitUpPrice: number;
  limitDistancePercent: number;
  changePercent: number;
  volume: number;
  turnover: number;
  estimatedAverageTurnover20d: number;
  estimatedVolumeRatio20d: number;
  score: number;
  category: "attack" | "monitor" | "watch" | "rejected";
  categoryLabel: string;
  setupType: string;
  setupLabel: string;
  actionable: boolean;
  alertable: boolean;
  isLockedLimitUp: boolean;
  entryBlockReason: string;
  stopLoss: number;
  target1: number;
  target2: number;
  riskRewardRatio: number;
  components: Record<string, number>;
  riskDeduction: number;
  largeOrderForce: number;
  largeOrderContinuousBuy: boolean;
  largeOrderSource: "real_tick" | "quote_proxy" | "unavailable" | string;
  largeOrderStatus?: string | null;
  vwapStatus?: string | null;
  fiveMinuteStructure?: string | null;
  orderBookEstimated: boolean;
  failures: string[];
  warnings: string[];
  reasons: string[];
  snapshotAt: string;
}

export interface LimitUpPosition {
  id: number;
  symbol: string;
  stockName: string;
  market: string;
  setupType: string;
  status: string;
  entryAt: string;
  exitAt?: string | null;
  entryPrice: number;
  currentPrice: number;
  exitPrice?: number | null;
  quantity: number;
  remainingQuantity: number;
  stopLoss: number;
  target1: number;
  target2: number;
  highestPrice: number;
  lowestPrice: number;
  takeProfitStage: number;
  scoreEntry: number;
  scoreCurrent: number;
  overnightScore: number;
  overnightHoldPct: number;
  realizedPnl: number;
  unrealizedPnl: number;
  returnPercent: number;
  latestAction: string;
  updatedAt: string;
}

export interface LimitUpTrade {
  id: number;
  positionId?: number | null;
  symbol: string;
  stockName: string;
  action: string;
  setupType: string;
  price: number;
  quantity: number;
  grossAmount: number;
  realizedPnl: number;
  reason: string;
  executedAt: string;
}

export interface LimitUpAiPerformanceBucket {
  tradeCount: number;
  buyCount: number;
  sellCount: number;
  winCount: number;
  lossCount: number;
  winRate: number;
  realizedPnl: number;
  unrealizedPnl: number;
  totalPnl: number;
  totalReturnPct: number;
  averageWin: number;
  averageLoss: number;
  maximumSingleLoss: number;
  openPositionCount: number;
}

export interface LimitUpAiPerformance {
  today: LimitUpAiPerformanceBucket;
  month: LimitUpAiPerformanceBucket;
  all: LimitUpAiPerformanceBucket;
  period: string;
  updatedAt: string;
}

export interface LimitUpAiNotification {
  id: number;
  type: string;
  priority: number;
  title: string;
  message: string;
  symbol?: string | null;
  stockName?: string | null;
  setupType?: string | null;
  price?: number | null;
  quantity?: number | null;
  amount?: number | null;
  realizedPnl?: number | null;
  score?: number | null;
  reason: string;
  isRead: boolean;
  readAt?: string | null;
  createdAt: string;
}

export interface LimitUpDashboard {
  updatedAt: string;
  settings: LimitUpAiSettings;
  summary: {
    candidateCount: number;
    attackCount: number;
    alertableCount: number;
    actionableCount: number;
    limitBoardCount: number;
    openPositionCount: number;
    realizedPnl: number;
    unrealizedPnl: number;
    totalPnl: number;
    winRate: number;
  };
  candidates: LimitUpCandidate[];
  limitBoard: LimitUpCandidate[];
  alerts: LimitUpCandidate[];
  nearEntries: LimitUpCandidate[];
  watchlist: LimitUpCandidate[];
  positions: LimitUpPosition[];
  trades: LimitUpTrade[];
  limitMonitors: LimitUpCandidate[];
  overnightEvaluations: LimitUpPosition[];
  performance: LimitUpAiPerformance;
  notifications: LimitUpAiNotification[];
  unreadCount: number;
  dataNotice: string;
}

export interface LimitUpReplay {
  tradingDate: string;
  items: LimitUpCandidate[];
  total: number;
  attackTotal: number;
  alertableTotal?: number;
  actionableTotal: number;
  updatedAt: string;
}
