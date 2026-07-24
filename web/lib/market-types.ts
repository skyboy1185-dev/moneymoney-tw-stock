import type { DailyPrice, Market, MacdSignalType, StockPayload } from "./types";

export type Timeframe = "day" | "week" | "month";
export type MarketDirection = "strong_bull" | "bull" | "sideways" | "bear" | "strong_bear" | "transition";
export type MarketRegime = "wave_up" | "range" | "wave_down" | "transition";
export type SignalStatus = "temporary" | "confirmed" | "cancelled";

export interface KDPoint {
  date: string;
  k: number | null;
  d: number | null;
  goldenCross: boolean;
}

export interface ManualStrategy {
  id: string;
  name: string;
  timeframe: Timeframe;
  volumeThreshold: number;
  requiresKD: boolean;
}

export interface ManualScreenRow {
  rank: number;
  symbol: string;
  name: string;
  market: Market;
  price: number;
  changePercent: number;
  volume: number;
  timeframe: Timeframe;
  dif: number | null;
  signal: number | null;
  histogram: number | null;
  k: number | null;
  d: number | null;
  signalDate: string;
}

export interface MarketForceInput {
  largeOrderNet: number;
  futuresDirection: number;
  indexTrend: number;
  marketBreadth: number;
  indexVsVwap: number;
  volumeMomentum: number;
  aboveMa20Ratio: number;
}

export interface MarketForceResult {
  score: number;
  direction: MarketDirection;
  confidence: number;
  reasons: string[];
}

export interface MarketContext extends MarketForceResult {
  regime: MarketRegime;
  marketOpen: boolean;
  futuresMarketOpen: boolean;
  indexPrice: number;
  indexChange: number;
  indexChangePercent: number;
  futuresPrice: number;
  futuresChange: number;
  futuresChangePercent: number;
  adx: number;
  indexAboveMa20: boolean;
  indexAboveMa60: boolean;
  indexAboveMa120: boolean;
  ma20Slope: number;
  ma60Slope: number;
  macdAboveZero: boolean;
  largeOrderNet: number;
  smallOrderNet: number;
  breadthUp: number;
  breadthDown: number;
  futuresContract?: string;
  futuresSource?: string;
  futuresQuoteAt?: string;
  indexSource?: string;
  indexQuoteAt?: string;
}

export interface MetricCard {
  id: string;
  label: string;
  value: number;
  unit: string;
  change1m: number;
  change3m: number;
  change10m: number;
  updatedAt: string;
  source?: string;
  detail?: string;
  isOfficial?: boolean;
  hasIntradayChanges?: boolean;
}

export interface StrategyRecommendation {
  id: string;
  name: string;
  fit: number;
  stars: number;
  reason: string;
  risk: "低" | "中低" | "中" | "高";
  enabled: boolean;
}

export interface RobotResult {
  matched: boolean;
  score: number;
  reasons: string[];
  risks: string[];
}

export interface StrategyRobot {
  id: string;
  name: string;
  supportedRegimes: MarketDirection[];
  analyze(stock: StockPayload, market: MarketContext): RobotResult;
  filter(stock: StockPayload, market: MarketContext): boolean;
  score(stock: StockPayload, market: MarketContext): number;
  recommend(stock: StockPayload, market: MarketContext): string[];
}

export interface RankingRow {
  rank: number;
  symbol: string;
  name: string;
  price: number;
  changePercent: number;
  volume: number;
  strategyId: string;
  strategyName: string;
  score: number;
  strategyFit: number;
  marketDirection: MarketDirection;
  macdState: string;
  signalStatus: SignalStatus;
  triggeredAt: string;
  reasons: string[];
  riskTags: string[];
  movement: "new" | "up" | "down" | "leaving" | "steady";
  updatedAt: string;
  priceSource?: string;
  priceDate?: string;
  priceTime?: string;
  isOfficialPrice?: boolean;
}

export interface TimelinePoint {
  time: string;
  largeOrder: number;
  smallOrder: number;
  force: number;
  futuresPercent: number;
  indexPercent: number;
  stockCount: number;
  direction: MarketDirection;
}

export interface MarketSnapshot {
  mode: "demo";
  marketOpen: boolean;
  futuresMarketOpen: boolean;
  marketStatus: string;
  updatedAt: string;
  delaySeconds: number;
  nextUpdateSeconds: number;
  force: MarketForceResult;
  context: MarketContext;
  recommendations: StrategyRecommendation[];
  activeStrategyIds: string[];
  rankings: RankingRow[];
  metrics: MetricCard[];
  timeline: TimelinePoint[];
  events: SystemEvent[];
}

export interface SystemEvent {
  type: string;
  symbol?: string;
  strategyId?: string;
  oldValue?: string | number;
  newValue?: string | number;
  price?: number;
  score?: number;
  triggeredAt: string;
  reasons: string[];
}

export type WatchStatus = "剛加入觀察" | "持續強勢" | "回檔轉強" | "動能轉弱" | "出場警戒";

export interface WatchlistItem {
  id: number;
  symbol: string;
  name: string;
  addedAt: string;
  addedPrice: number;
  latestPrice: number;
  returnPercent: number;
  addedScore: number;
  currentScore: number;
  scoreChange: number;
  originalRobotId: string;
  originalRobotName: string;
  originalReasons: string[];
  status: WatchStatus;
  matchesOriginalStrategy: boolean;
  invalidReasons: string[];
  updatedAt: string;
}

export interface HoldingItem {
  id: number;
  symbol: string;
  name: string;
  cost: number;
  lots: number;
  shares: number;
  buyDate: string;
  addedAt: string;
  latestPrice: number;
  marketValue: number;
  unrealizedProfit: number;
  returnPercent: number;
  originalSelectedAt: string;
  originalSelectedPrice: number;
  originalAiScore: number;
  currentAiScore: number;
  originalRobotId: string;
  originalRobotName: string;
  originalReasons: string[];
  status: WatchStatus;
  updatedAt: string;
}

export interface MarketDataProvider {
  getQuote(symbol: string): Promise<DailyPrice | null>;
  getQuotes(symbols: string[]): Promise<DailyPrice[]>;
  getHistoricalCandles(symbol: string, timeframe: Timeframe): Promise<DailyPrice[]>;
  getStockList(): Promise<{ symbol: string; name: string; market: Market }[]>;
  getMarketStatus(): Promise<{ open: boolean; label: string; updatedAt: string }>;
}

export interface MarketDirectionProvider {
  getMarketIndex(): Promise<{ price: number; change: number; changePercent: number; source?: string; quoteAt?: string; isOfficial?: boolean }>;
  getIndexFutures(): Promise<{
    price: number;
    change: number;
    changePercent: number;
    contract?: string;
    session?: string;
    sessionOpen?: boolean;
    source?: string;
    quoteAt?: string;
    isOfficial?: boolean;
    change1m?: number;
    change3m?: number;
    change10m?: number;
  }>;
  getTradeTicks(): Promise<{ price: number; amount: number; side: "buy" | "sell" }[]>;
  getMarketBreadth(): Promise<{ up: number; down: number; flat: number }>;
  getOrderStatistics(): Promise<{ largeOrderNet: number; smallOrderNet: number }>;
}
