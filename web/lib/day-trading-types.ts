export type DayDirection = "long" | "short";
export type StreamConnection = "connecting" | "connected" | "reconnecting" | "disconnected";

export interface TradingAutomationState {
  timezone: string;
  localTime: string;
  tradingDate: string;
  isTradingDay: boolean;
  phase: string;
  robotStatus: string;
  statusMessage: string;
  formalSignalsAllowed: boolean;
  warmupMinutes: number;
  warmupUntil: string;
  quoteSamples: number;
  minimumLiveSamples: number;
  nextTransitionAt: string | null;
  schedule: {
    preheatTime: string;
    stockPoolTime: string;
    healthCheckTime: string;
    marketOpenTime: string;
    latestEntryTime: string;
    closeReminderTime: string;
    marketCloseTime: string;
  };
}

export interface MarketRegime {
  direction: string;
  directionLabel: string;
  score: number;
  environmentScore: number;
  environmentLabel: string;
  preferredDirection: string;
  shortRestriction: string;
  risk: string;
  longPermission: number;
  shortPermission: number;
  suitableStrategies: string[];
  forbiddenStrategies: string[];
  reasons: string[];
  dataStatus: string;
  dataDelaySeconds: number;
  dataSource: string;
  marketOpen: boolean;
  session: string;
  updatedAt: string;
  metrics: Record<string, string | number | string[]>;
  automation: TradingAutomationState;
  infrastructure: Record<string, string>;
  recommendationSummary: string;
  recommendedCount: number;
  maximumRecommendations: number;
  mode?: "official" | "warming_up" | "demo";
  dataNotice?: string;
  disclaimer?: string;
}

export interface DayTradingSignal {
  id: string;
  rank: number;
  symbol: string;
  stockName: string;
  market: string;
  direction: DayDirection;
  directionLabel: string;
  action: string;
  price: number;
  changePercent: number;
  volume: number;
  turnover: number;
  entryMin: number;
  entryMax: number;
  stopLoss: number;
  target1: number;
  target2: number;
  confidenceScore: number;
  healthScore: number;
  riskRewardRatio: number;
  vwapStatus: string;
  volumeStatus: string;
  largeOrderForce: number;
  industryStrength: string;
  reasons: string[];
  warnings: string[];
  generatedAt: string;
  expiresAt: string;
  serverNow?: string;
  quoteTimestamp: string;
  status: string;
  dataSource: string;
  dataMode?: string;
  dataNotice?: string;
  quoteStatus?: string;
  quoteIsRealtime?: boolean;
  spreadPercentage: number;
  tradingEligible: boolean;
  shortEligible: boolean;
  shortAvailabilityKnown: boolean;
  chaseBlocked: boolean;
  stopDistancePercent: number;
  marketAlignment: number;
  confirmationScore: number;
  isOfficialRecommendation: boolean;
  recommendationLabel: string;
  qualificationFailures: string[];
  recommendedAt?: string;
}

export interface DayTradingPosition {
  id: number;
  signalId: string;
  symbol: string;
  stockName: string;
  direction: DayDirection;
  directionLabel: string;
  entryPrice: number;
  quantity: number;
  openedAt: string;
  currentPrice: number;
  unrealizedProfit: number;
  returnPercentage: number;
  stopLoss: number;
  target1: number;
  target2: number;
  trailingStop: number | null;
  healthScore: number;
  latestAction: string;
  status: string;
  soundEnabled: boolean;
  holdingSeconds: number;
  updatedAt: string;
}

export interface DayTradingAlert {
  id: number;
  positionId?: number;
  signalId?: string;
  level: "normal" | "important" | "emergency";
  type: string;
  title: string;
  message: string;
  action: string;
  reason: string;
  price: number;
  createdAt: string;
  readAt?: string | null;
}

export interface DayTradingTrade {
  id: number;
  symbol: string;
  stockName: string;
  direction: DayDirection;
  entryTime: string;
  entryPrice: number;
  exitTime: string;
  exitPrice: number;
  quantity: number;
  fee: number;
  tax: number;
  slippage: number;
  profit: number;
  returnPercentage: number;
  maxProfit: number;
  maxLoss: number;
  entryReason: string;
  exitReason: string;
  strategyName: string;
  followedSignal: boolean;
}

export interface DayTradingPerformance {
  tradeCount: number;
  winRate: number;
  totalProfit: number;
  averageProfit: number;
  maxLoss: number;
  maxConsecutiveLosses: number;
  longProfit: number;
  shortProfit: number;
  profitFactor: number;
}

export interface DayTradingSettings {
  capital: number;
  maxRiskPerTrade: number;
  maxDailyLoss: number;
  maxDailyTrades: number;
  maxPositionPercentage: number;
  maxConsecutiveLosses: number;
  minimumRiskReward: number;
  maximumSpread: number;
  minimumVolume: number;
  minimumTurnover: number;
  latestEntryTime: string;
  closeReminderTime: string;
  notificationEnabled: boolean;
  soundEnabled: boolean;
  entryNotification: boolean;
  exitNotification: boolean;
  stopNotification: boolean;
  targetNotification: boolean;
  dataAlertNotification: boolean;
  highConfidenceOnly: boolean;
  minimumConfidence: number;
  notificationCooldown: number;
  repeatCount: number;
  timezone: "Asia/Taipei";
  preheatTime: string;
  stockPoolTime: string;
  healthCheckTime: string;
  marketOpenTime: string;
  marketCloseTime: string;
  warmupMinutes: 0 | 1 | 3 | 5 | 10;
  recommendationRefreshSeconds: 5 | 10 | 15 | 30;
  replacementScoreGap: number;
  minimumRetentionMinutes: number;
  minimumLiveSamples: number;
  maximumStopDistance: number;
}

export interface SignalSelectionPayload {
  recommended: DayTradingSignal[];
  candidates: DayTradingSignal[];
  totalRecommended: number;
  maximumRecommendations: number;
  summary: string;
}

export interface LineNotificationSettings {
  openingEnabled: boolean;
  longEntryEnabled: boolean;
  shortEntryEnabled: boolean;
  longExitEnabled: boolean;
  shortCoverEnabled: boolean;
  stopLossEnabled: boolean;
  dataAlertEnabled: boolean;
  closingSummaryEnabled: boolean;
  updatedAt: string;
}

export interface LineNotificationGroup {
  id: number;
  displayName: string;
  maskedGroupId: string;
  active: boolean;
  boundAt: string | null;
  lastPushAt: string | null;
}

export interface LineIntegrationStatus {
  officialAccountName: string;
  enabled: boolean;
  credentialsConfigured: boolean;
  connectionStatus: "disabled" | "missing_credentials" | "awaiting_group" | "connected";
  groups: LineNotificationGroup[];
  lastPushAt: string | null;
  todayPushCount: number;
  publicWebhookUrl: string;
  settings: LineNotificationSettings;
}

export interface EmergencyEvent {
  type: string;
  level: "emergency";
  id: string;
  title: string;
  message: string;
  action: string;
  reason: string;
  price: number;
  position?: Partial<DayTradingPosition>;
}
