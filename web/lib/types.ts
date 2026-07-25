export type MacdSignalType = "entry" | "exit" | null;
export type Market = "上市" | "上櫃";

export interface DailyPrice {
  symbol: string;
  name: string;
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface TechnicalIndicator {
  date: string;
  ma5: number | null;
  ma10: number | null;
  ma20: number | null;
  ma30: number | null;
  ma60: number | null;
  ma120: number | null;
  ma240: number | null;
  dif: number | null;
  signal: number | null;
  histogram: number | null;
  macdSignal: MacdSignalType;
}

export interface StockMeta {
  symbol: string;
  name: string;
  industry: string;
  market: Market;
  peRatio: number | null;
  dividendYield: number | null;
  priceToBook: number | null;
  eps: number | null;
  marketCap: number | null;
}

export interface StockPayload {
  meta: StockMeta;
  prices: DailyPrice[];
  indicators: TechnicalIndicator[];
  updatedAt: string;
  quote?: StockQuote;
  dataMode?: "demo" | "official_quote_demo_history";
  dataNotice?: string;
}

export interface StockQuote {
  symbol: string;
  name: string;
  date: string;
  time: string;
  open: number;
  high: number;
  low: number;
  price: number;
  previousClose: number;
  change: number;
  changePercent: number;
  volume: number;
  bestBid?: number;
  bestAsk?: number;
  source: "TWSE MIS" | "TWSE OpenAPI" | "TPEx OpenAPI";
  isRealtime: boolean;
}

export interface ScreenerFilters {
  minPrice: string;
  maxPrice: string;
  minVolume: string;
  minChange: string;
  maxChange: string;
  industry: string;
  market: string;
  technical: string[];
}

export interface ScreenerRow {
  symbol: string;
  name: string;
  industry: string;
  market: Market;
  price: number;
  changePercent: number;
  volume: number;
  ma5: number | null;
  ma20: number | null;
  ma60: number | null;
  dif: number | null;
  signal: number | null;
  histogram: number | null;
  latestSignal: MacdSignalType;
  signalDate: string | null;
  flags: string[];
}
