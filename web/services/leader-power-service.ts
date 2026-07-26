import { calculatePowerScore, type PowerScoreResult } from "@/lib/power-score";
import { getOfficialQuote, mergeOfficialQuote } from "@/services/market-data/official-quote-provider";
import { payloadFor } from "@/services/stock-service";
import {
  TOP_WEIGHTED_LEADERS,
  WEIGHTED_LEADER_SOURCE_DATE,
  WEIGHTED_LEADER_SOURCE_URL,
  weightedLeaderMeta,
} from "./weighted-leaders";

export interface LeaderPowerRow {
  rank: number;
  symbol: string;
  name: string;
  weight: number;
  industry: string;
  price: number;
  changePercent: number;
  quoteSource: string;
  quoteTime: string;
  score: PowerScoreResult;
}

export interface LeaderPowerResponse {
  sourceDate: string;
  sourceUrl: string;
  updatedAt: string;
  dataNotice: string;
  rows: LeaderPowerRow[];
}

let cache: { value: LeaderPowerResponse; expiresAt: number } | null = null;

export async function buildLeaderPowerResponse(): Promise<LeaderPowerResponse> {
  if (cache && cache.expiresAt > Date.now()) return cache.value;
  const rows = await Promise.all(TOP_WEIGHTED_LEADERS.map(async (leader): Promise<LeaderPowerRow> => {
    const meta = weightedLeaderMeta(leader);
    const basePayload = payloadFor(meta);
    const officialQuote = await getOfficialQuote(meta);
    const payload = officialQuote ? mergeOfficialQuote(basePayload, officialQuote) : basePayload;
    const latest = payload.prices.at(-1)!;
    const previous = payload.prices.at(-2)!;
    return {
      ...leader,
      price: latest.close,
      changePercent: previous.close ? (latest.close - previous.close) / previous.close * 100 : 0,
      quoteSource: officialQuote?.source ?? "展示資料",
      quoteTime: officialQuote ? `${officialQuote.date} ${officialQuote.time}` : payload.updatedAt,
      score: calculatePowerScore(payload.prices, payload.indicators),
    };
  }));
  const value: LeaderPowerResponse = {
    sourceDate: WEIGHTED_LEADER_SOURCE_DATE,
    sourceUrl: WEIGHTED_LEADER_SOURCE_URL,
    updatedAt: new Date().toISOString(),
    dataNotice: "現價優先使用官方市場報價；歷史技術資料仍含展示資料。未串接的籌碼、法人、族群與大盤因子不加分，並列入扣分原因。",
    rows,
  };
  cache = { value, expiresAt: Date.now() + 60_000 };
  return value;
}
