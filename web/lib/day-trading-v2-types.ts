export type TradingMode = "PAPER" | "BACKTEST" | "LIVE";

export type Performance = {
  initialCapital: string;
  endingCapital: string;
  grossPnl: string;
  totalCost: string;
  buyTurnover: string;
  sellTurnover: string;
  totalTurnover: string;
  listedCommission: string;
  paidCommission: string;
  commissionRebate: string;
  commissionDiscount: string;
  commissionDiscountLabel: string;
  transactionMetricsAvailable: boolean;
  totalProfit: string;
  totalLoss: string;
  netPnl: string;
  netReturnPct: string;
  tradeCount: number;
  winCount: number;
  lossCount: number;
  flatCount: number;
  winRate: string;
  sampleSufficient: boolean;
  averageWin: string;
  averageLoss: string;
  payoffRatio: string | null;
  profitFactor: string | null;
  maxWin: string;
  maxLoss: string;
};

export type RegimePerformanceRow = Performance & {
  strategyId: string; strategyName: string;
  marketRegime: string; marketRegimeLabel: string;
  expectancy: string; maxDrawdown: string; minimumSample: number;
  suitability: "SUITABLE" | "CAUTION" | "INSUFFICIENT";
  fitRank: number | null;
  trades?: Array<{ id: string; symbol: string; entryTime: string | null; exitTime: string | null; grossPnl: string; cost: string; netPnl: string }>;
};

export type RegimePerformance = {
  source: "PAPER" | "LIVE" | "BACKTEST" | "CHALLENGER";
  sourceName: string; sourceId: string; period: string; role: string | null;
  minimumSample: number;
  coverage: { totalTrades: number; classifiedTrades: number; unknownTrades: number; classifiedPct: string };
  regimes: Array<{ id: string; label: string }>;
  rows: RegimePerformanceRow[];
  bestByRegime: Array<{ marketRegime: string; marketRegimeLabel: string; best: RegimePerformanceRow | null }>;
  bestByStrategy: Array<{ strategyId: string; strategyName: string; best: RegimePerformanceRow | null }>;
  mostProfitable: RegimePerformanceRow | null;
  largestLoss: RegimePerformanceRow | null;
};

export type Robot = {
  id: number;
  strategyId: string;
  name: string;
  enabled: boolean;
  side: "LONG";
  allocation: string;
  status: string;
  consecutiveLosses: number;
  today: Performance;
  month: Performance;
  all: Performance;
  usedCapital: string;
  lastTradeTime: string | null;
};

export type Position = {
  id: string;
  mode: TradingMode;
  symbol: string;
  stockName: string;
  sector: string;
  strategyId: string;
  strategyVersion: string;
  signalId: string;
  side: "LONG";
  quantity: number;
  entryPrice: string;
  currentPrice: string;
  stopPrice: string;
  firstTargetPrice: string;
  trailingStopPrice: string;
  usedCapital: string;
  unrealizedGrossPnl: string;
  entryTime: string;
  status: string;
  entryReasons: string[];
  confidence: string;
  riskReward: string;
};

export type Trade = {
  id: string;
  mode: TradingMode;
  symbol: string;
  stockName: string;
  strategyId: string;
  strategyVersion: string;
  quantity: number;
  signalTime: string;
  entryOrderTime: string;
  entryFillTime: string;
  entryPrice: string;
  exitSignalTime: string;
  exitOrderTime: string;
  exitFillTime: string;
  exitPrice: string;
  grossPnl: string;
  buyFee: string;
  sellFee: string;
  transactionTax: string;
  slippage: string;
  otherCost: string;
  netPnl: string;
  netReturnPct: string;
  entryReason: string;
  exitReason: string;
};

export type Dashboard = {
  systemName: string;
  mode: TradingMode;
  systemStatus: "NORMAL" | "REDUCED" | "HALTED";
  liveTrading: { available: boolean; enabled: boolean; broker: string | null; reason: string };
  marketData: { realtime: string; historicalMinute: string | null; backtestReady: boolean; message: string };
  config: Record<string, string | number | boolean>;
  today: Performance;
  month: Performance;
  all: Performance;
  realizedPnl: string;
  unrealizedPnl: string;
  netPnl: string;
  usedCapital: string;
  availableCapital: string;
  remainingDailyRisk: string;
  positions: Position[];
  recentTrades: Trade[];
  robots: Robot[];
  runtime: RuntimeState;
  topCandidates: CandidateState[];
  skipReasons: Array<{ reason: string; count: number }>;
  controller: ControllerDashboard;
  optimization: OptimizationDashboard;
};

export type NotificationItem = {
  id: number;
  eventId: string;
  mode: TradingMode;
  eventType: string;
  title: string;
  message: string;
  read: boolean;
  createdAt: string;
};

export type RuntimeState = {
  running: boolean; status: string; phase: string; autoStart: boolean; initialized: boolean;
  receivingQuotes: boolean; scanning: boolean; orderAllowed: boolean;
  heartbeatAt: string | null; lastQuoteAt: string | null; lastScanAt: string | null; lastBarAt: string | null;
  nextScanAt: string | null; nextEventType: string; nextEventAt: string | null;
  scannedStockCount: number; candidateCount: number; signalCount: number; orderCount: number;
  skippedCount: number; completedTradeCount: number; latestError: string;
  heartbeatStale: boolean; quoteStale: boolean;
  dataStatus?: "current" | "stale" | "unavailable" | "waiting";
  dataReason?: string; executionError?: string;
  quoteHealth?: {
    observedAt: string; lastReceivedAt: string | null; trackedCount: number;
    freshCount: number; staleCount: number; overCapacity: boolean;
    activeSource?: string; providerMode?: string; ready?: boolean;
    entitlementReady?: boolean; entitlementReason?: string | null; lastSourceSwitchAt?: string | null;
    sourceSwitchCount?: number; subscriptionCount?: number; subscriptionLimit?: number;
    reconnectAttempts?: number;
  };
};

export type CandidateState = {
  symbol: string; stockName: string; sector: string; strategyId: string; confidence: string;
  signalLevel: "GENERAL" | "WATCH" | "NEAR_ENTRY" | "RISK_GATE";
  primaryReason: string; reasons: string[]; quoteAt: string | null; barAt: string | null; scannedAt: string;
};

export type ControllerCandidate = {
  id: string; cycleId: string; symbol: string; stockName: string; sector: string;
  strategyId: string; strategyVersion: string; signalTime: string;
  rawScore: string; finalScore: string; rank: number | null;
  entryPrice: string; stopPrice: string; targetPrice: string; riskReward: string;
  plannedCapital: string; allowed: boolean; status: string;
  scoreDetails: Record<string, string | null>; reasons: string[]; blockedReasons: string[];
};

export type ControllerDashboard = {
  marketRegime: string; marketRegimeLabel: string; confidence: string; reasons: string[];
  dataBlocked: boolean; updatedAt: string | null; nextUpdateAt: string | null;
  cycleId: string | null; cycleStatus: string; selectedCandidateId: string;
  candidates: ControllerCandidate[];
  strategyStates: Array<{ strategyId: string; name: string; status: string; riskMultiplier: string; candidateCount: number }>;
};

export type StrategyHealth = {
  strategyId: string; name: string; version: string;
  status: "NORMAL" | "ALERT" | "PAUSED" | "OPTIMIZING" | "WAITING_APPROVAL" | "INSUFFICIENT" | string;
  reasons: string[]; metrics: Record<string, unknown>; baseline: Record<string, unknown>;
  recommendedAction: string; capitalMultiplier: string; riskMultiplier: string;
};

export type OptimizationDashboard = {
  health: StrategyHealth[];
  jobs: Array<Record<string, unknown>>;
  challengers: Array<Record<string, unknown>>;
  deployments: Array<Record<string, unknown>>;
  datasets: Array<Record<string, unknown>>;
};
