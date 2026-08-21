export type WhaleRankingType = "composite" | "big400" | "big1000" | "lots" | "value" | "retail" | "shareholders";

export interface WhaleHistoryPoint {
  reportDate: string;
  big400Ratio: number;
  big1000Ratio: number;
  retailRatio: number;
  totalShareholders: number;
  totalShares: number;
  price: number | null;
  volume: number | null;
}

export interface WhaleAccumulationItem {
  rank: number;
  stockCode: string;
  stockName: string;
  market: string;
  industry: string;
  latestPrice: number | null;
  periodPriceChangePct: number | null;
  averagePrice: number | null;
  priceSource: string;
  big400Start: number;
  big400End: number;
  big400Change: number;
  big1000Start: number;
  big1000End: number;
  big1000Change: number;
  retailStart: number;
  retailEnd: number;
  retailChange: number;
  shareholderStart: number;
  shareholderEnd: number;
  shareholderChange: number;
  shareholderChangePct: number;
  totalShares: number;
  estimatedIncreaseShares: number;
  estimatedIncreaseLots: number;
  estimatedAccumulationValue: number | null;
  continuousIncreasePeriods: number;
  continuationLabel: string;
  trendConsistency: number;
  signals: string[];
  chipStatus: string;
  anomalyFlag: boolean;
  anomalyReason: string;
  singlePeriodReversal: boolean;
  missingFields: string[];
  whaleAccumulationScore: number;
  scoreBreakdown: {
    big400: number;
    big1000: number;
    retail: number;
    shareholders: number;
    value: number;
    priceNotSurged: number;
  };
  history?: WhaleHistoryPoint[];
}

export interface WhaleAccumulationResponse {
  rankingType: WhaleRankingType;
  requestedRange: { start: string; end: string };
  actualRange: { start: string; end: string };
  availableRange: { start: string; end: string };
  dataMode: "official_tdcc" | "demo";
  dataSource: string;
  dataNotice: string;
  industries: string[];
  summaryCards: Array<{
    key: string;
    label: string;
    stockCode: string | null;
    stockName: string | null;
    value: number | null;
    valueType: "percentagePoint" | "negativePercentagePoint" | "currency" | "score";
  }>;
  totalMatched: number;
  items: WhaleAccumulationItem[];
  updatedAt: string;
}

export interface WhaleTrendResponse {
  requestedRange: { start: string; end: string };
  actualRange: { start: string; end: string };
  dataMode: "official_tdcc" | "demo";
  dataNotice: string;
  item: WhaleAccumulationItem & { history: WhaleHistoryPoint[] };
}

export interface WhaleAccumulationFilters {
  startDate: string;
  endDate: string;
  rankingType: WhaleRankingType;
  limit: 20 | 30 | 50 | 100;
  keyword: string;
  industry: string;
  minBig400: number;
  minBig1000: number;
  minLots: number;
  minValue: number;
  maxPriceChange: number;
  minScore: number;
}
