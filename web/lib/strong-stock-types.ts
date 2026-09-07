export type StrongRanking = {
  id: number; tradeDate: string; rank: number; symbol: string; name: string; market: string; industry: string;
  totalScore: string; relativeStrengthScore: string; trendScore: string; industryScore: string;
  volumeChipScore: string; fundamentalScore: string; valuationRiskScore: string; dataCompleteness: string;
  closePrice: string; high52wDistancePct: string | null; entryLow: string | null; entryHigh: string | null;
  breakoutPrice: string | null; pullbackPrice: string | null; stopPrice: string | null; addPrice: string | null;
  riskReward: string | null; suggestedCapital: string; status: string; entryType: string;
  reasons: string[]; blockedReasons: string[]; scoreDetails: Record<string, unknown>; strategyVersion: string; calculatedAt: string;
};

export type StrongPosition = {
  id: string; symbol: string; name: string; industry: string; quantity: number; averageCost: string;
  currentPrice: string; investedCapital: string; marketValue: string; unrealizedPnl: string; returnPct: string;
  initialStop: string; trailingStop: string; nextAddPrice: string | null; currentScore: string;
  entryType: string; strategyVersion: string; tranches: Array<Record<string, unknown>>; reasons: string[];
  warnings: string[]; status: string; entryAt: string;
};

export type StrongTrade = {
  id: string; symbol: string; name: string; industry: string; entryType: string; quantity: number;
  entryPrice: string; exitPrice: string; entryAt: string; exitAt: string; grossPnl: string;
  buyFee: string; sellFee: string; tax: string; slippage: string; netPnl: string; returnPct: string;
  strategyVersion: string; entryReason: string; exitReason: string;
};

export type StrongDashboard = {
  systemName: string; mode: "PAPER"; liveTradingAvailable: false; paperEnabled: boolean;
  config: Record<string, string | number | boolean>;
  marketRegime: { tradeDate: string | null; regime: string; label: string; confidence: string; suggestedExposurePct: string; reasons: string[] };
  performance: Record<string, string | number | null>;
  strategyHealth: { status: string; reasons: string[]; recent20: Record<string, unknown>; recent50: Record<string, unknown>; automaticRiskIncrease: false; candidateMayTradeLive: false };
  rankings: StrongRanking[];
  industries: Array<{ rank: number; industry: string; score: string; percentile: string; memberCount: number; details: Record<string, unknown> }>;
  positions: StrongPosition[]; trades: StrongTrade[];
  pendingOrders: Array<{ id: string; symbol: string; name: string; limitPrice: string; quantity: number; validDate: string; entryType: string; status: string; reason: string }>;
  equityCurve: Array<{ date: string; cash: string; marketValue: string; totalEquity: string; dailyPnl: string; drawdownPct: string }>;
  notifications: Array<{ id: number; eventType: string; title: string; message: string; priority: string; read: boolean; createdAt: string }>;
  dataStatus: { status: string; latestTradeDate: string | null; lastSuccessfulUpdate: string | null; sources: unknown; missing: string[]; error: string; historicalBacktestReady: boolean };
  notice: string;
};
