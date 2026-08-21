export interface RocketCandidate {
  id: number; rank: number; isTop5: boolean; stockCode: string; stockName: string;
  marketType: string; sectorName: string; sectorRank: number; status: string; statusLabel: string;
  patternType: string; marketRegime: string; currentPrice: number; changePercent: number;
  rocketScore: number; chaseRiskScore: number; volumeRatio: number; breakoutPrice: number;
  stopLossPrice: number; targetPrice1: number; targetPrice2: number; riskRewardRatio: number;
  atr: number | null; ma5: number | null; ma10: number | null; ma20: number | null;
  scoreBreakdown: Record<string, number | null>; dataAvailabilityPercent: number;
  reasons: string[]; missingData: string[]; updatedAt: string;
}

export interface RocketNotification {
  notificationId: number; timestamp: string; stockCode: string | null; stockName: string | null;
  notificationType: string; priority: number; title: string; message: string; price: number | null;
  rocketScore: number | null; chaseRisk: number | null; positionSize: number | null;
  amount: number | null; pnl: number | null; pnlPercent: number | null; reason: string;
  strategyType: string | null; isRead: boolean;
}

export interface RocketStatRow {
  key: string; tradeCount: number; winRate: number; averageReturnPercent: number;
  averageWinPercent: number; averageLossPercent: number; profitFactor: number | null;
  maximumLossPercent: number; maximumDrawdownPercent: number; totalReturnPercent: number;
}

export interface RocketDashboard {
  market: { regime: string; label: string; score: number; maximumExposurePercent: number; strategy: string; reasons: string[]; missingFields: string[]; updatedAt: string | null };
  account: { initialCapital: number; cash: number; marketValue: number; totalEquity: number; cumulativePnl: number; returnPercent: number; todayPnl: number; realizedPnl: number; unrealizedPnl: number; positionCount: number };
  top5: RocketCandidate[]; candidates: RocketCandidate[]; candidateMessage: string | null;
  sectors: Array<{ rank: number; name: string; score: number; return1d: number | null; return3d: number | null; return5d: number | null; advanceRatio: number | null; newHighRatio: number | null }>;
  positions: Array<{ id: number; stockCode: string; stockName: string; averageCost: number; currentPrice: number; quantity: number; cost: number; marketValue: number; unrealizedPnl: number; returnPercent: number; highestProfit: number; maximumLoss: number; stopLoss: number; trailingStop: number | null; holdingDays: number; rocketScoreEntry: number; rocketScoreCurrent: number; addStage: number; latestAction: string }>;
  performance: { totalTrades: number; winningTrades: number; losingTrades: number; winRate: number; averageWinPercent: number; averageLossPercent: number; payoffRatio: number | null; profitFactor: number | null; expectancyPercent: number; maximumWinningStreak: number; maximumLosingStreak: number; maximumDrawdownPercent: number; totalReturnPercent: number; realizedPnl: number };
  equityCurve: Array<{ date: string; cash: number; marketValue: number; totalEquity: number; dailyPnl: number; cumulativePnl: number; drawdownPercent: number }>;
  strategyStats: RocketStatRow[]; scoreStats: RocketStatRow[]; holdingStats: RocketStatRow[]; regimeStats: RocketStatRow[];
  completedTrades: Array<{ id: number; stockCode: string; stockName: string; signalDate: string; signalTime: string; entryPrice: number; averageCost: number; quantity: number; investedAmount: number; strategyType: string; rocketScore: number; sectorName: string; marketRegime: string; stopLossPrice: number; highestPrice: number; lowestPrice: number; exitPrice: number; exitDate: string | null; holdingDays: number; profit: number; returnPercent: number; maximumFavorableExcursion: number; maximumAdverseExcursion: number; isProfit: boolean; exitReason: string | null }>;
  trades: Array<{ id: number; positionId: number; timestamp: string; stockCode: string; stockName: string; action: string; strategyType: string; price: number; quantity: number; grossAmount: number; fee: number; tax: number; netAmount: number; realizedPnl: number; reason: string }>;
  notifications: RocketNotification[]; unreadCount: number;
  settings: { brokerFeeDiscount: number; slippageRate: number; soundEnabled: boolean; commissionRate: number; taxRate: number };
  updatedAt: string;
}

export interface RocketBacktest {
  status: "insufficient_history" | "completed"; period: string; initialCapital: number;
  endingCapital?: number; totalReturnPercent?: number; tradeDays: number; tradeCount: number;
  winRate?: number; averageReturnPercent?: number; message?: string; lookAheadBias: false; methodology?: string;
}
