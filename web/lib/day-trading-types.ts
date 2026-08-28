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
  formalLongSignalsAllowed: boolean;
  formalShortSignalsAllowed: boolean;
  warmupMinutes: number;
  warmupUntil: string;
  quoteSamples: number;
  dataQualityMode?: string;
  minimumLiveSamples: number;
  nextTransitionAt: string | null;
  schedule: {
    preheatTime: string;
    stockPoolTime: string;
    healthCheckTime: string;
    marketOpenTime: string;
    signalStartTime: string;
    latestEntryTime: string;
    shortEntryCutoffTime: string;
    longEntryCutoffTime: string;
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
  activeRobot: DayTradingStrategyRobot;
  strategyRobots: DayTradingStrategyRobot[];
  reasons: string[];
  dataStatus: string;
  dataDelaySeconds: number;
  dataQualityMode?: string;
  dataQualityWarning?: string | null;
  formalBlockReason?: string | null;
  quoteCoverageRatio?: number;
  quoteCoverageCount?: number;
  candidateUniverseCount?: number;
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
  supervisor?: {
    candidateUniverseCount?: number;
    candidateUniverseSource?: string;
    quoteCoverageCount?: number;
    threeGateCoverageCount?: number;
    warmedSymbolCount?: number;
    highFrequencyTrackingCount?: number;
    baselineQuoteRefreshSeconds?: number;
    priorityQuoteRefreshSeconds?: number;
  };
  mode?: "official" | "warming_up" | "demo";
  dataNotice?: string;
  disclaimer?: string;
  degraded?: boolean;
  fallbackReason?: string | null;
  fallbackAt?: string | null;
}

export interface DayTradingStrategyRobot {
  id: string;
  name: string;
  direction: "long" | "short" | "both";
  directionLabel?: string;
  useWhen: string;
  description: string;
  entryRule: string;
  avoidRule: string;
  confidence?: number;
  confidenceLabel?: string;
  status?: "active" | "warming_up" | "managing" | "paused" | "standby";
  statusLabel?: string;
  reasons?: string[];
  selected?: boolean;
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
  strategyRobotId?: string;
  strategyRobotName?: string;
  strategyConfidence?: number;
  strategyAligned?: boolean;
  healthScore: number;
  riskRewardRatio: number;
  vwapStatus: string;
  volumeStatus: string;
  largeOrderForce: number;
  largeOrderDataAvailable?: boolean;
  largeOrderContinuousBuy?: boolean;
  largeOrderContinuousSell?: boolean;
  largeOrderStatus?: string;
  largeOrderNetLots?: number;
  largeOrderRecentNetLots?: number;
  largeOrderBuySellRatio?: number | null;
  largeOrderPositiveSteps?: number;
  largeOrderNegativeSteps?: number;
  largeOrderDirectionalSteps?: number;
  largeOrderUpdatedAt?: string | null;
  largeOrderIsEstimate?: boolean;
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
  entryConfirmationMode?: "three_gate" | "vwap_fallback" | "waiting_three_gate";
  entryConfirmationModeLabel?: string;
  threeGateFallback?: boolean;
  threeGateAligned?: boolean;
  threeGateOpposed?: boolean;
  threeGateReady?: boolean;
  threeGate?: {
    sourceDate: string;
    upper: number;
    middle: number;
    lower: number;
  } | null;
  threeGateDirection?: DayDirection | null;
  threeGateLevel?: "upper" | "middle" | "lower" | null;
  threeGatePosition?: "above" | "below" | null;
  threeGateCrossed?: boolean;
  threeGateStatus?: string;
  threeGateOpeningPattern?: "open-above-middle" | "open-below-lower" | null;
  threeGateRetestRequired?: boolean;
  threeGateRetestTouched?: boolean;
  threeGateRetestReady?: boolean;
  threeGateInvalidated?: boolean;
  threeGateEntryLevel?: "middle" | "lower" | null;
  threeGateEntryStatus?: string;
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
  recommendedQuantityLots?: number;
  trackedQuantityLots?: number;
  trackingStatus?: string;
  strategyAllocations?: Record<string, {
    key: string;
    label: string;
    quantityLots: number;
    allocatedCapital: number;
    status: string;
  }>;
  qualificationFailures: string[];
  recommendedAt?: string;
}

export interface DayTradingCandidateReplay extends DayTradingSignal {
  snapshotAt: string;
  originalOfficialRecommendation: boolean;
  wouldBeOfficialRecommendation: boolean;
  replayFailures: string[];
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
  automaticTracking?: boolean;
  automationStrategy?: string;
  automationStrategyLabel?: string;
  holdingPeriod?: "intraday" | "overnight_long";
  holdingPeriodLabel?: string;
  entryConfidence?: number;
  strategyConfidence?: number;
  overnightEligible?: boolean;
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
  automaticTracking?: boolean;
  automationStrategy?: string;
  automationStrategyLabel?: string;
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

export interface DayTradingPerformanceSummary {
  tradeCount: number;
  wins: number;
  losses: number;
  breakeven: number;
  winRate: number;
  totalProfit: number;
  realizedProfit: number;
  unrealizedProfit: number;
  totalPnl: number;
  grossProfit: number;
  fee: number;
  tax: number;
  slippage: number;
  tradingCost: number;
  commissionDiscount: number;
  commissionDiscountLabel: string;
  grossCommission: number;
  commissionRebate: number;
  rebateAccumulated: number;
  openPositionCount: number;
  averageProfit: number;
  maxLoss: number;
  maxConsecutiveLosses: number;
  longProfit: number;
  shortProfit: number;
  longRealizedProfit: number;
  longUnrealizedProfit: number;
  longTotalPnl: number;
  longTradeCount: number;
  longOpenPositionCount: number;
  shortRealizedProfit: number;
  shortUnrealizedProfit: number;
  shortTotalPnl: number;
  shortTradeCount: number;
  shortOpenPositionCount: number;
  profitFactor: number;
}

export interface DayTradingDailyPerformance extends DayTradingPerformanceSummary {
  tradeDate: string;
}

export interface DayTradingPerformance extends DayTradingPerformanceSummary {
  period: string;
  performanceStartDate: string | null;
  today: DayTradingDailyPerformance;
  strategy?: {
    key: string;
    label: string;
    description: string;
  };
  capitalPlan?: {
    dailyCapital: number;
    usedCapital: number;
    availableCapital: number;
    maxPositionCapital: number;
    maxPositionPercent: number;
    riskPerTradeBudget: number;
    riskPerTradePercent: number;
    dailyLossLimit: number;
    dailyLossLimitPercent: number;
    dailyPnl: number;
    lossLimitReached: boolean;
    sizingMethod: string;
  };
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
  signalStartTime: string;
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

export interface LineTradeDelivery {
  id: number;
  eventType: "long_entry" | "short_entry" | "long_exit" | "short_cover" | "stop_loss";
  side: "buy" | "sell" | "short" | "cover";
  sideLabel: "買進" | "賣出" | "放空" | "回補";
  signalId: string | null;
  symbol: string | null;
  action: string;
  messagePreview: string;
  sentAt: string;
}

export interface LineIntegrationStatus {
  officialAccountName: string;
  enabled: boolean;
  credentialsConfigured: boolean;
  connectionStatus: "disabled" | "missing_credentials" | "awaiting_group" | "connected";
  groups: LineNotificationGroup[];
  lastPushAt: string | null;
  todayPushCount: number;
  todayTradePushCount: number;
  gmailEnabled: boolean;
  gmailConfigured: boolean;
  gmailTransport: "apps_script" | "smtp" | "unconfigured";
  gmailRecipients: string[];
  todayGmailPushCount: number;
  todayGmailFailedCount: number;
  lastGmailPushAt: string | null;
  recentTradeDeliveries: LineTradeDelivery[];
  dailyTradeMessageLimit: number;
  monthlyMessageLimit: number | null;
  monthlyMessageUsage: number | null;
  monthlyMessageRemaining: number | null;
  remainingTradingDays: number;
  baseDailyTradeMessageLimit: number;
  effectiveDailyTradeMessageLimit: number;
  quotaResetAt: string;
  quotaCheckedAt: string;
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
