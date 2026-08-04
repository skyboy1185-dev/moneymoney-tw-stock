import { calculatePowerScore, type PowerScoreResult } from "@/lib/power-score";
import { buildOfficialStockPayload } from "@/services/market-data/official-history-provider";
import { getOfficialQuotes } from "@/services/market-data/official-quote-provider";
import { stockCatalog } from "@/services/stock-service";
import type { StockMeta } from "@/lib/types";
import {
  TOP_WEIGHTED_LEADERS,
  WEIGHTED_LEADER_SOURCE_DATE,
  WEIGHTED_LEADER_SOURCE_URL,
  weightedLeaderMeta,
} from "./weighted-leaders";

export type LeaderPowerView = "weighted" | "electronics";

export interface LeaderPowerRow {
  rank: number;
  symbol: string;
  name: string;
  weight: number | null;
  industry: string;
  price: number;
  changePercent: number;
  quoteSource: string;
  quoteTime: string;
  score: PowerScoreResult;
}

export interface LeaderPowerResponse {
  view: LeaderPowerView;
  title: string;
  candidateCount: number;
  sourceDate: string;
  sourceUrl: string;
  updatedAt: string;
  dataNotice: string;
  rows: LeaderPowerRow[];
}

const ELECTRONIC_INDUSTRIES = new Set([
  "半導體",
  "電子零組件",
  "電腦及週邊",
  "電腦及週邊設備",
  "光電",
  "通信網路",
  "電子通路",
  "資訊服務",
  "其他電子",
]);

const cache = new Map<LeaderPowerView, { value: LeaderPowerResponse; expiresAt: number }>();

export function electronicPowerUniverse(stocks: StockMeta[] = stockCatalog): StockMeta[] {
  return stocks.filter((stock) => ELECTRONIC_INDUSTRIES.has(stock.industry));
}

export function rankElectronicPowerRows(rows: LeaderPowerRow[]): LeaderPowerRow[] {
  return [...rows]
    .sort((left, right) =>
      right.score.powerValue - left.score.powerValue
      || right.score.healthScore - left.score.healthScore
      || right.changePercent - left.changePercent
      || left.symbol.localeCompare(right.symbol),
    )
    .slice(0, 15)
    .map((row, index) => ({ ...row, rank: index + 1 }));
}

async function buildRows(
  candidates: Array<{ meta: StockMeta; rank: number; weight: number | null }>,
): Promise<LeaderPowerRow[]> {
  const quotes = await getOfficialQuotes(candidates.map((candidate) => candidate.meta));
  const settled = await Promise.allSettled(candidates.map(async (candidate): Promise<LeaderPowerRow> => {
    const quote = quotes.get(candidate.meta.symbol) ?? null;
    const payload = await buildOfficialStockPayload(candidate.meta, quote);
    const latest = payload.prices.at(-1);
    const previous = payload.prices.at(-2);
    if (!latest || !previous) throw new Error(`${candidate.meta.symbol} 歷史資料不足`);
    return {
      rank: candidate.rank,
      symbol: candidate.meta.symbol,
      name: candidate.meta.name,
      weight: candidate.weight,
      industry: candidate.meta.industry,
      price: latest.close,
      changePercent: previous.close
        ? (latest.close - previous.close) / previous.close * 100
        : 0,
      quoteSource: quote?.source
        ?? payload.dataQuality?.historySource
        ?? "官方日 K",
      quoteTime: quote
        ? `${quote.date} ${quote.time}`
        : payload.updatedAt,
      score: calculatePowerScore(payload.prices, payload.indicators),
    };
  }));
  return settled.flatMap((result) => result.status === "fulfilled" ? [result.value] : []);
}

export async function buildLeaderPowerResponse(
  view: LeaderPowerView = "weighted",
): Promise<LeaderPowerResponse> {
  const cached = cache.get(view);
  if (cached && cached.expiresAt > Date.now()) return cached.value;
  const electronicUniverse = electronicPowerUniverse();
  const candidates = view === "weighted"
    ? TOP_WEIGHTED_LEADERS.map((leader) => ({
      meta: weightedLeaderMeta(leader),
      rank: leader.rank,
      weight: leader.weight,
    }))
    : electronicUniverse.map((meta, index) => ({
      meta,
      rank: index + 1,
      weight: null,
    }));
  const availableRows = await buildRows(candidates);
  const rows = view === "electronics"
    ? rankElectronicPowerRows(availableRows)
    : availableRows.sort((left, right) => left.rank - right.rank);
  if (!rows.length) throw new Error("官方歷史行情暫時無法取得");
  const latestDate = rows
    .map((row) => row.score.quoteDate)
    .filter(Boolean)
    .sort()
    .at(-1) ?? new Date().toISOString().slice(0, 10);
  const value: LeaderPowerResponse = {
    view,
    title: view === "weighted" ? "前 15 大權值股馬力榜" : "電子股馬力前 15 名",
    candidateCount: candidates.length,
    sourceDate: view === "weighted" ? WEIGHTED_LEADER_SOURCE_DATE : latestDate,
    sourceUrl: view === "weighted"
      ? WEIGHTED_LEADER_SOURCE_URL
      : "https://openapi.twse.com.tw/",
    updatedAt: new Date().toISOString(),
    dataNotice: view === "weighted"
      ? "現價與歷史技術資料均使用實際市場資料計算；未串接的籌碼、法人、族群與大盤因子不加分。"
      : `從內建上市、上櫃電子股觀察池 ${electronicUniverse.length} 檔，以官方日 K 馬力值、健康度及當日漲跌排序前 15 名；非全市場無限制掃描。`,
    rows,
  };
  cache.set(view, { value, expiresAt: Date.now() + 60_000 });
  return value;
}
