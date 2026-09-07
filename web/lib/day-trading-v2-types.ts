export type TradingMode = "PAPER" | "BACKTEST" | "LIVE";

export type Performance = {
  initialCapital: string;
  endingCapital: string;
  netPnl: string;
  netReturnPct: string;
  tradeCount: number;
  winCount: number;
  lossCount: number;
  winRate: string;
  sampleSufficient: boolean;
  averageWin: string;
  averageLoss: string;
  payoffRatio: string | null;
  profitFactor: string | null;
  maxWin: string;
  maxLoss: string;
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
