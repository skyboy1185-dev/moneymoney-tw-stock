import type { ElectronicChipFlowAlertsResponse } from "@/lib/electronic-chip-flow-alerts";
import type {
  LimitUpAiNotification,
  LimitUpAiPerformance,
  LimitUpAiPerformanceBucket,
  LimitUpAiSettings,
  LimitUpCandidate,
  LimitUpDashboard,
  LimitUpPosition,
  LimitUpReplay,
  LimitUpTrade,
} from "@/lib/limit-up-ai-types";

type JsonRecord = Record<string, unknown>;

const CATEGORIES = ["attack", "monitor", "watch", "rejected"] as const;

function isRecord(value: unknown): value is JsonRecord {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function record(value: unknown): JsonRecord {
  return isRecord(value) ? value : {};
}

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

export function finiteNumber(value: unknown, fallback = 0): number {
  const numeric = typeof value === "number" ? value : typeof value === "string" ? Number(value) : NaN;
  return Number.isFinite(numeric) ? numeric : fallback;
}

function bool(value: unknown, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function list<T>(value: unknown, mapper: (item: unknown, index: number) => T): T[] {
  return Array.isArray(value) ? value.map(mapper) : [];
}

function textList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function currentIso(): string {
  return new Date().toISOString();
}

const DEFAULT_SETTINGS: LimitUpAiSettings = {
  capital: 3_000_000,
  minPrice: 20,
  maxPrice: 500,
  minAverageTurnover20d: 100_000_000,
  minVolumeRatio20d: 1.8,
  firstPositionPct: 0.1,
  maxPositionPct: 0.2,
  maxPositions: 3,
  maxLossPerTradePct: 0.005,
  maxDailyLossPct: 0.01,
  maxConsecutiveStops: 3,
  overnightTotalPct: 0.3,
  overnightSinglePct: 0.15,
  excludeLockedLimitUp: true,
  soundEnabled: true,
  updatedAt: "",
};

function normalizeSettings(value: unknown): LimitUpAiSettings {
  const source = record(value);
  return {
    capital: finiteNumber(source.capital, DEFAULT_SETTINGS.capital),
    minPrice: finiteNumber(source.minPrice, DEFAULT_SETTINGS.minPrice),
    maxPrice: finiteNumber(source.maxPrice, DEFAULT_SETTINGS.maxPrice),
    minAverageTurnover20d: finiteNumber(source.minAverageTurnover20d, DEFAULT_SETTINGS.minAverageTurnover20d),
    minVolumeRatio20d: finiteNumber(source.minVolumeRatio20d, DEFAULT_SETTINGS.minVolumeRatio20d),
    firstPositionPct: finiteNumber(source.firstPositionPct, DEFAULT_SETTINGS.firstPositionPct),
    maxPositionPct: finiteNumber(source.maxPositionPct, DEFAULT_SETTINGS.maxPositionPct),
    maxPositions: finiteNumber(source.maxPositions, DEFAULT_SETTINGS.maxPositions),
    maxLossPerTradePct: finiteNumber(source.maxLossPerTradePct, DEFAULT_SETTINGS.maxLossPerTradePct),
    maxDailyLossPct: finiteNumber(source.maxDailyLossPct, DEFAULT_SETTINGS.maxDailyLossPct),
    maxConsecutiveStops: finiteNumber(source.maxConsecutiveStops, DEFAULT_SETTINGS.maxConsecutiveStops),
    overnightTotalPct: finiteNumber(source.overnightTotalPct, DEFAULT_SETTINGS.overnightTotalPct),
    overnightSinglePct: finiteNumber(source.overnightSinglePct, DEFAULT_SETTINGS.overnightSinglePct),
    excludeLockedLimitUp: bool(source.excludeLockedLimitUp, DEFAULT_SETTINGS.excludeLockedLimitUp),
    soundEnabled: bool(source.soundEnabled, DEFAULT_SETTINGS.soundEnabled),
    updatedAt: text(source.updatedAt),
  };
}

function normalizeBucket(value: unknown): LimitUpAiPerformanceBucket {
  const source = record(value);
  return {
    tradeCount: finiteNumber(source.tradeCount),
    buyCount: finiteNumber(source.buyCount),
    sellCount: finiteNumber(source.sellCount),
    winCount: finiteNumber(source.winCount),
    lossCount: finiteNumber(source.lossCount),
    winRate: finiteNumber(source.winRate),
    realizedPnl: finiteNumber(source.realizedPnl),
    unrealizedPnl: finiteNumber(source.unrealizedPnl),
    totalPnl: finiteNumber(source.totalPnl),
    totalReturnPct: finiteNumber(source.totalReturnPct),
    averageWin: finiteNumber(source.averageWin),
    averageLoss: finiteNumber(source.averageLoss),
    maximumSingleLoss: finiteNumber(source.maximumSingleLoss),
    openPositionCount: finiteNumber(source.openPositionCount),
  };
}

function normalizePerformance(value: unknown): LimitUpAiPerformance {
  const source = record(value);
  return {
    today: normalizeBucket(source.today),
    month: normalizeBucket(source.month),
    all: normalizeBucket(source.all),
    period: text(source.period, new Date().toISOString().slice(0, 7)),
    updatedAt: text(source.updatedAt, currentIso()),
  };
}

export function normalizeLimitUpCandidate(value: unknown, index = 0): LimitUpCandidate {
  const source = record(value);
  const category = text(source.category);
  return {
    id: text(source.id, `${text(source.symbol, "unknown")}-${index}`),
    symbol: text(source.symbol),
    stockName: text(source.stockName),
    market: text(source.market),
    rank: finiteNumber(source.rank, index + 1),
    price: finiteNumber(source.price),
    previousClose: finiteNumber(source.previousClose),
    limitUpPrice: finiteNumber(source.limitUpPrice),
    limitDistancePercent: finiteNumber(source.limitDistancePercent, 100),
    changePercent: finiteNumber(source.changePercent),
    volume: finiteNumber(source.volume),
    turnover: finiteNumber(source.turnover),
    estimatedAverageTurnover20d: finiteNumber(source.estimatedAverageTurnover20d),
    estimatedVolumeRatio20d: finiteNumber(source.estimatedVolumeRatio20d),
    score: finiteNumber(source.score),
    category: CATEGORIES.includes(category as LimitUpCandidate["category"]) ? category as LimitUpCandidate["category"] : "rejected",
    categoryLabel: text(source.categoryLabel, "資料不足"),
    setupType: text(source.setupType),
    setupLabel: text(source.setupLabel, "等待資料"),
    actionable: bool(source.actionable),
    stopLoss: finiteNumber(source.stopLoss),
    target1: finiteNumber(source.target1),
    target2: finiteNumber(source.target2),
    riskRewardRatio: finiteNumber(source.riskRewardRatio),
    components: isRecord(source.components) ? source.components as Record<string, number> : {},
    riskDeduction: finiteNumber(source.riskDeduction),
    largeOrderForce: finiteNumber(source.largeOrderForce),
    largeOrderContinuousBuy: bool(source.largeOrderContinuousBuy),
    largeOrderStatus: text(source.largeOrderStatus) || null,
    vwapStatus: text(source.vwapStatus) || null,
    fiveMinuteStructure: text(source.fiveMinuteStructure) || null,
    orderBookEstimated: bool(source.orderBookEstimated, true),
    failures: textList(source.failures),
    warnings: textList(source.warnings),
    reasons: textList(source.reasons),
    snapshotAt: text(source.snapshotAt, currentIso()),
  };
}

function normalizePosition(value: unknown): LimitUpPosition {
  const source = record(value);
  return {
    id: finiteNumber(source.id),
    symbol: text(source.symbol),
    stockName: text(source.stockName),
    market: text(source.market),
    setupType: text(source.setupType),
    status: text(source.status, "unknown"),
    entryAt: text(source.entryAt, currentIso()),
    exitAt: text(source.exitAt) || null,
    entryPrice: finiteNumber(source.entryPrice),
    currentPrice: finiteNumber(source.currentPrice),
    exitPrice: source.exitPrice == null ? null : finiteNumber(source.exitPrice),
    quantity: finiteNumber(source.quantity),
    remainingQuantity: finiteNumber(source.remainingQuantity),
    stopLoss: finiteNumber(source.stopLoss),
    target1: finiteNumber(source.target1),
    target2: finiteNumber(source.target2),
    highestPrice: finiteNumber(source.highestPrice),
    lowestPrice: finiteNumber(source.lowestPrice),
    takeProfitStage: finiteNumber(source.takeProfitStage),
    scoreEntry: finiteNumber(source.scoreEntry),
    scoreCurrent: finiteNumber(source.scoreCurrent),
    overnightScore: finiteNumber(source.overnightScore),
    overnightHoldPct: finiteNumber(source.overnightHoldPct),
    realizedPnl: finiteNumber(source.realizedPnl),
    unrealizedPnl: finiteNumber(source.unrealizedPnl),
    returnPercent: finiteNumber(source.returnPercent),
    latestAction: text(source.latestAction, "等待資料"),
    updatedAt: text(source.updatedAt, currentIso()),
  };
}

function normalizeTrade(value: unknown): LimitUpTrade {
  const source = record(value);
  return {
    id: finiteNumber(source.id),
    positionId: source.positionId == null ? null : finiteNumber(source.positionId),
    symbol: text(source.symbol),
    stockName: text(source.stockName),
    action: text(source.action, "UNKNOWN"),
    setupType: text(source.setupType),
    price: finiteNumber(source.price),
    quantity: finiteNumber(source.quantity),
    grossAmount: finiteNumber(source.grossAmount),
    realizedPnl: finiteNumber(source.realizedPnl),
    reason: text(source.reason),
    executedAt: text(source.executedAt, currentIso()),
  };
}

function normalizeNotification(value: unknown): LimitUpAiNotification {
  const source = record(value);
  return {
    id: finiteNumber(source.id),
    type: text(source.type, "INFO"),
    priority: finiteNumber(source.priority),
    title: text(source.title, "漲停機器人通知"),
    message: text(source.message),
    symbol: text(source.symbol) || null,
    stockName: text(source.stockName) || null,
    setupType: text(source.setupType) || null,
    price: source.price == null ? null : finiteNumber(source.price),
    quantity: source.quantity == null ? null : finiteNumber(source.quantity),
    amount: source.amount == null ? null : finiteNumber(source.amount),
    realizedPnl: source.realizedPnl == null ? null : finiteNumber(source.realizedPnl),
    score: source.score == null ? null : finiteNumber(source.score),
    reason: text(source.reason),
    isRead: bool(source.isRead),
    readAt: text(source.readAt) || null,
    createdAt: text(source.createdAt, currentIso()),
  };
}

export function normalizeNotificationPayload(value: unknown): { items: LimitUpAiNotification[]; unreadCount: number } {
  const source = record(value);
  const items = list(source.items, normalizeNotification);
  return {
    items,
    unreadCount: finiteNumber(source.unreadCount, items.filter((item) => !item.isRead).length),
  };
}

export function normalizeLimitUpDashboard(value: unknown): LimitUpDashboard {
  const source = record(value);
  const summary = record(source.summary);
  const candidates = list(source.candidates, normalizeLimitUpCandidate);
  const nearEntries = list(source.nearEntries, normalizeLimitUpCandidate);
  const positions = list(source.positions, normalizePosition);
  const notifications = list(source.notifications, normalizeNotification);
  const performance = normalizePerformance(source.performance);
  return {
    updatedAt: text(source.updatedAt, currentIso()),
    settings: normalizeSettings(source.settings),
    summary: {
      candidateCount: finiteNumber(summary.candidateCount, candidates.length),
      attackCount: finiteNumber(summary.attackCount, candidates.filter((item) => item.category === "attack").length),
      actionableCount: finiteNumber(summary.actionableCount, candidates.filter((item) => item.actionable).length),
      openPositionCount: finiteNumber(summary.openPositionCount, positions.filter((item) => item.status === "open").length),
      realizedPnl: finiteNumber(summary.realizedPnl),
      unrealizedPnl: finiteNumber(summary.unrealizedPnl),
      totalPnl: finiteNumber(summary.totalPnl),
      winRate: finiteNumber(summary.winRate),
    },
    candidates,
    nearEntries,
    watchlist: list(source.watchlist, normalizeLimitUpCandidate),
    positions,
    trades: list(source.trades, normalizeTrade),
    limitMonitors: list(source.limitMonitors, normalizeLimitUpCandidate),
    overnightEvaluations: list(source.overnightEvaluations, normalizePosition),
    performance,
    notifications,
    unreadCount: finiteNumber(source.unreadCount, notifications.filter((item) => !item.isRead).length),
    dataNotice: text(source.dataNotice, "資料暫時不完整，畫面已切換為安全顯示模式。"),
  };
}

export function normalizeLimitUpReplay(value: unknown): LimitUpReplay {
  const source = record(value);
  const items = list(source.items, normalizeLimitUpCandidate);
  return {
    tradingDate: text(source.tradingDate),
    items,
    total: finiteNumber(source.total, items.length),
    attackTotal: finiteNumber(source.attackTotal, items.filter((item) => item.category === "attack").length),
    actionableTotal: finiteNumber(source.actionableTotal, items.filter((item) => item.actionable).length),
    updatedAt: text(source.updatedAt, currentIso()),
  };
}

export function normalizeLargeOrderResponse(value: unknown): ElectronicChipFlowAlertsResponse {
  const source = record(value);
  return {
    ...(source as Partial<ElectronicChipFlowAlertsResponse>),
    tradeDate: text(source.tradeDate),
    status: text(source.status, "unavailable") as ElectronicChipFlowAlertsResponse["status"],
    marketOpen: bool(source.marketOpen),
    source: text(source.source),
    isEstimate: bool(source.isEstimate, true),
    windowMinutes: finiteNumber(source.windowMinutes, 5),
    minRecentNetLots: finiteNumber(source.minRecentNetLots),
    minBuySellRatio: finiteNumber(source.minBuySellRatio),
    minPositiveSteps: finiteNumber(source.minPositiveSteps),
    scannedCount: finiteNumber(source.scannedCount),
    baselineCycleScannedCount: finiteNumber(source.baselineCycleScannedCount),
    baselineCycleTargetSeconds: finiteNumber(source.baselineCycleTargetSeconds),
    lastFullScanAt: text(source.lastFullScanAt) || null,
    candidateCount: finiteNumber(source.candidateCount),
    disposedExcludedCount: finiteNumber(source.disposedExcludedCount),
    disposedExcludedSymbols: Array.isArray(source.disposedExcludedSymbols) ? source.disposedExcludedSymbols.filter((item): item is string => typeof item === "string") : [],
    restrictionStatus: source.restrictionStatus === "healthy" ? "healthy" : "degraded",
    popularCandidateCount: finiteNumber(source.popularCandidateCount),
    fastCandidateCount: finiteNumber(source.fastCandidateCount),
    cpoCandidateCount: finiteNumber(source.cpoCandidateCount),
    packagingTestCandidateCount: finiteNumber(source.packagingTestCandidateCount),
    powerCandidateCount: finiteNumber(source.powerCandidateCount),
    popularUniverseSource: text(source.popularUniverseSource),
    popularUniverseUpdatedAt: text(source.popularUniverseUpdatedAt) || null,
    hotScanCount: finiteNumber(source.hotScanCount),
    highFrequencyTrackingCount: finiteNumber(source.highFrequencyTrackingCount),
    pinnedTrackingCount: finiteNumber(source.pinnedTrackingCount),
    rankingLimit: finiteNumber(source.rankingLimit, 10),
    refreshSeconds: finiteNumber(source.refreshSeconds, 15),
    longRankingCount: finiteNumber(source.longRankingCount),
    warningCount: finiteNumber(source.warningCount),
    strengtheningCount: finiteNumber(source.strengtheningCount),
    jointIncreaseCount: finiteNumber(source.jointIncreaseCount),
    alerts: Array.isArray(source.alerts) ? source.alerts as ElectronicChipFlowAlertsResponse["alerts"] : [],
    longRankings: Array.isArray(source.longRankings) ? source.longRankings as ElectronicChipFlowAlertsResponse["alerts"] : undefined,
    trackedAlerts: Array.isArray(source.trackedAlerts) ? source.trackedAlerts as ElectronicChipFlowAlertsResponse["trackedAlerts"] : [],
    shortCount: finiteNumber(source.shortCount),
    shortRankingCount: finiteNumber(source.shortRankingCount),
    shortStrengtheningCount: finiteNumber(source.shortStrengtheningCount),
    shortAlerts: Array.isArray(source.shortAlerts) ? source.shortAlerts as ElectronicChipFlowAlertsResponse["shortAlerts"] : [],
    shortRankings: Array.isArray(source.shortRankings) ? source.shortRankings as ElectronicChipFlowAlertsResponse["shortAlerts"] : undefined,
    trackedShortAlerts: Array.isArray(source.trackedShortAlerts) ? source.trackedShortAlerts as ElectronicChipFlowAlertsResponse["trackedShortAlerts"] : [],
    lastError: text(source.lastError) || text(source.error) || null,
    notice: text(source.notice, "大單資料暫時無法取得；漲停機器人其他區塊仍會繼續顯示。"),
    updatedAt: text(source.updatedAt, currentIso()),
  };
}
