export type InstitutionId = "foreign" | "trust" | "dealer" | "total";

export interface FlowAmount {
  buy: number;
  sell: number;
  net: number;
}

export interface MarketFlowAmount {
  listed: FlowAmount;
  otc: FlowAmount;
  total: FlowAmount;
}

export interface InstitutionFlowRow {
  id: InstitutionId;
  label: string;
  day: MarketFlowAmount;
  month: MarketFlowAmount;
  year: MarketFlowAmount;
}

export type RetailFuturesId = "mini" | "micro";

export interface RetailFuturesPosition {
  id: RetailFuturesId;
  label: string;
  contract: "MTX" | "TMF";
  marketOpenInterest: number;
  institutionalLong: number;
  institutionalShort: number;
  retailLong: number;
  retailShort: number;
  retailNet: number;
  ratioPct: number;
  bias: "偏多" | "偏空" | "中性";
}

export interface RetailFuturesHistoryPoint {
  date: string;
  miniRatioPct: number;
  microRatioPct: number;
  miniNet: number;
  microNet: number;
}

export interface InstitutionalInvestorResponse {
  asOfDate: string;
  monthLabel: string;
  yearLabel: string;
  updatedAt: string;
  items: InstitutionFlowRow[];
  retailFutures: {
    asOfDate: string;
    items: RetailFuturesPosition[];
    history: RetailFuturesHistoryPoint[];
    foreignNet: {
      contract: "TX";
      asOfDate: string;
      long: number;
      short: number;
      net: number;
      previousDate: string | null;
      previousNet: number | null;
      change: number | null;
    };
    formula: string;
    notice: string;
    sourceUrl: string;
  };
  dataNotice: string;
  sources: Array<{
    market: "上市" | "上櫃";
    provider: string;
    url: string;
  }>;
}
