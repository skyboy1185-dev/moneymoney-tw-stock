import { getDatabase } from "@/database/event-store";
import type { HoldingItem, RankingRow, WatchlistItem, WatchStatus } from "@/lib/market-types";
import { loadMarketSnapshot } from "@/services/scanner-worker-client";
import { getOfficialQuote, mergeOfficialQuote } from "@/services/market-data/official-quote-provider";
import { stockService } from "@/services/stock-service";

interface WatchRecord {
  id: number; user_id: string; symbol: string; name: string; added_at: string;
  added_price: number; added_score: number; original_robot_id: string;
  original_robot_name: string; original_reasons_json: string;
}

interface HoldingRecord {
  id: number; user_id: string; symbol: string; name: string; cost: number; lots: number;
  buy_date: string; added_at: string; original_selected_at: string;
  original_selected_price: number; original_ai_score: number; original_robot_id: string;
  original_robot_name: string; original_reasons_json: string;
}

function snapshotScore(stock: Awaited<ReturnType<typeof stockService.getStock>>) {
  if (!stock) return 0;
  const latest = stock.prices.at(-1)!;
  const indicator = stock.indicators.at(-1)!;
  const previous = stock.indicators.at(-2)!;
  let score = 45;
  if (indicator.ma20 && latest.close > indicator.ma20) score += 10;
  if (indicator.ma60 && latest.close > indicator.ma60) score += 7;
  if (indicator.histogram != null && indicator.histogram >= 0) score += 9;
  if (indicator.dif != null && indicator.signal != null && indicator.dif > indicator.signal) score += 7;
  if (indicator.histogram != null && previous.histogram != null && indicator.histogram > previous.histogram) score += 6;
  return Math.min(100, score);
}

function assess(
  addedAt: string,
  addedScore: number,
  currentScore: number,
  latestPrice: number,
  indicator: NonNullable<Awaited<ReturnType<typeof stockService.getStock>>>["indicators"][number],
  previousHistogram: number | null,
): { status: WatchStatus; invalidReasons: string[] } {
  const invalidReasons: string[] = [];
  if (indicator.ma20 != null && latestPrice < indicator.ma20) invalidReasons.push("跌破 MA20");
  if (indicator.histogram != null && previousHistogram != null && indicator.histogram >= 0 && indicator.histogram < previousHistogram) invalidReasons.push("MACD 紅柱縮短");
  if (indicator.histogram != null && indicator.histogram < 0) invalidReasons.push("MACD 已翻綠");
  if (currentScore < addedScore - 8) invalidReasons.push("大盤適配度降低");
  const ageHours = (Date.now() - new Date(addedAt).getTime()) / 3_600_000;
  if (indicator.macdSignal === "exit" || (indicator.ma20 != null && latestPrice < indicator.ma20 && currentScore < 60)) {
    return { status: "出場警戒", invalidReasons };
  }
  if (currentScore <= addedScore - 8 || (indicator.histogram != null && indicator.histogram < 0)) {
    return { status: "動能轉弱", invalidReasons };
  }
  if (indicator.histogram != null && previousHistogram != null && indicator.histogram > previousHistogram && currentScore >= addedScore) {
    return { status: "回檔轉強", invalidReasons };
  }
  if (currentScore >= 80 || currentScore >= addedScore + 4) return { status: "持續強勢", invalidReasons };
  return { status: ageHours < 24 ? "剛加入觀察" : "動能轉弱", invalidReasons };
}

function recordScore(userId: string, symbol: string, listType: "watchlist" | "holding", score: number) {
  const recordedAt = `${new Date().toISOString().slice(0, 16)}:00.000Z`;
  getDatabase().prepare(`
    INSERT OR IGNORE INTO ai_score_history (user_id, symbol, list_type, score, recorded_at)
    VALUES (?, ?, ?, ?, ?)
  `).run(userId, symbol, listType, score, recordedAt);
}

async function currentData(symbol: string, rankings: RankingRow[]) {
  const base = await stockService.getStock(symbol);
  if (!base) return null;
  const quote = await getOfficialQuote(base.meta);
  const stock = quote ? mergeOfficialQuote(base, quote) : base;
  const ranking = rankings.find((item) => item.symbol === symbol);
  return { stock, ranking, price: quote?.price ?? stock.prices.at(-1)!.close, score: ranking?.score ?? snapshotScore(stock) };
}

export async function listWatchlist(userId: string): Promise<WatchlistItem[]> {
  const records = getDatabase().prepare("SELECT * FROM watchlist_items WHERE user_id = ? ORDER BY added_at DESC").all(userId) as unknown as WatchRecord[];
  const snapshot = await loadMarketSnapshot(true);
  return (await Promise.all(records.map(async (record) => {
    const current = await currentData(record.symbol, snapshot.rankings);
    if (!current) return null;
    const indicator = current.stock.indicators.at(-1)!;
    const previousHistogram = current.stock.indicators.at(-2)?.histogram ?? null;
    const assessment = assess(record.added_at, record.added_score, current.score, current.price, indicator, previousHistogram);
    const matchesOriginalStrategy = current.ranking?.strategyId === record.original_robot_id;
    if (!matchesOriginalStrategy && !assessment.invalidReasons.length) assessment.invalidReasons.push("已不符合原始策略");
    if (!current.ranking) assessment.invalidReasons.push("成交量下降");
    recordScore(userId, record.symbol, "watchlist", current.score);
    return {
      id: record.id, symbol: record.symbol, name: record.name, addedAt: record.added_at,
      addedPrice: record.added_price, latestPrice: current.price,
      returnPercent: ((current.price - record.added_price) / record.added_price) * 100,
      addedScore: record.added_score, currentScore: current.score, scoreChange: current.score - record.added_score,
      originalRobotId: record.original_robot_id, originalRobotName: record.original_robot_name,
      originalReasons: JSON.parse(record.original_reasons_json),
      status: assessment.status, matchesOriginalStrategy, invalidReasons: [...new Set(assessment.invalidReasons)],
      updatedAt: snapshot.updatedAt,
    } satisfies WatchlistItem;
  }))).filter((item): item is WatchlistItem => item !== null);
}

export function addWatchlist(userId: string, ranking: RankingRow): "created" | "duplicate" {
  const result = getDatabase().prepare(`
    INSERT OR IGNORE INTO watchlist_items
    (user_id, symbol, name, added_at, added_price, added_score, original_robot_id, original_robot_name, original_reasons_json)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
  `).run(
    userId, ranking.symbol, ranking.name, new Date().toISOString(), ranking.price, ranking.score,
    ranking.strategyId, ranking.strategyName, JSON.stringify(ranking.reasons.slice(0, 3)),
  );
  return Number(result.changes) === 1 ? "created" : "duplicate";
}

export function addWatchlistSnapshot(
  userId: string,
  item: { symbol: string; name: string; price: number; score: number; sourceName: string; reasons: string[] },
): "created" | "duplicate" {
  const result = getDatabase().prepare(`
    INSERT OR IGNORE INTO watchlist_items
    (user_id, symbol, name, added_at, added_price, added_score, original_robot_id, original_robot_name, original_reasons_json)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
  `).run(
    userId, item.symbol, item.name, new Date().toISOString(), item.price, item.score,
    "large_holder_ranking", item.sourceName, JSON.stringify(item.reasons.slice(0, 3)),
  );
  return Number(result.changes) === 1 ? "created" : "duplicate";
}

export function removeWatchlist(userId: string, symbol: string) {
  return Number(getDatabase().prepare("DELETE FROM watchlist_items WHERE user_id = ? AND symbol = ?").run(userId, symbol).changes) > 0;
}

export async function listHoldings(userId: string): Promise<HoldingItem[]> {
  const records = getDatabase().prepare("SELECT * FROM holding_items WHERE user_id = ? ORDER BY added_at DESC").all(userId) as unknown as HoldingRecord[];
  const snapshot = await loadMarketSnapshot(true);
  return (await Promise.all(records.map(async (record) => {
    const current = await currentData(record.symbol, snapshot.rankings);
    if (!current) return null;
    const indicator = current.stock.indicators.at(-1)!;
    const assessment = assess(record.added_at, record.original_ai_score, current.score, current.price, indicator, current.stock.indicators.at(-2)?.histogram ?? null);
    const shares = record.lots * 1000;
    const marketValue = current.price * shares;
    const unrealizedProfit = (current.price - record.cost) * shares;
    recordScore(userId, record.symbol, "holding", current.score);
    return {
      id: record.id, symbol: record.symbol, name: record.name, cost: record.cost, lots: record.lots, shares,
      buyDate: record.buy_date, addedAt: record.added_at, latestPrice: current.price, marketValue,
      unrealizedProfit, returnPercent: ((current.price - record.cost) / record.cost) * 100,
      originalSelectedAt: record.original_selected_at, originalSelectedPrice: record.original_selected_price,
      originalAiScore: record.original_ai_score, currentAiScore: current.score,
      originalRobotId: record.original_robot_id, originalRobotName: record.original_robot_name,
      originalReasons: JSON.parse(record.original_reasons_json), status: assessment.status, updatedAt: snapshot.updatedAt,
    } satisfies HoldingItem;
  }))).filter((item): item is HoldingItem => item !== null);
}

export function addHolding(userId: string, ranking: RankingRow, cost: number, lots: number, buyDate: string): "created" | "duplicate" {
  const result = getDatabase().prepare(`
    INSERT OR IGNORE INTO holding_items
    (user_id, symbol, name, cost, lots, buy_date, added_at, original_selected_at, original_selected_price,
     original_ai_score, original_robot_id, original_robot_name, original_reasons_json)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `).run(
    userId, ranking.symbol, ranking.name, cost, lots, buyDate, new Date().toISOString(),
    ranking.triggeredAt, ranking.price, ranking.score, ranking.strategyId, ranking.strategyName,
    JSON.stringify(ranking.reasons.slice(0, 3)),
  );
  return Number(result.changes) === 1 ? "created" : "duplicate";
}

export function convertWatchToHolding(userId: string, symbol: string, cost: number, lots: number, buyDate: string): "created" | "duplicate" | "not_found" {
  const database = getDatabase();
  const watch = database.prepare("SELECT * FROM watchlist_items WHERE user_id = ? AND symbol = ?").get(userId, symbol) as unknown as WatchRecord | undefined;
  if (!watch) return "not_found";
  database.exec("BEGIN IMMEDIATE");
  try {
    const result = database.prepare(`
      INSERT OR IGNORE INTO holding_items
      (user_id, symbol, name, cost, lots, buy_date, added_at, original_selected_at, original_selected_price,
       original_ai_score, original_robot_id, original_robot_name, original_reasons_json)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `).run(
      userId, watch.symbol, watch.name, cost, lots, buyDate, new Date().toISOString(),
      watch.added_at, watch.added_price, watch.added_score, watch.original_robot_id,
      watch.original_robot_name, watch.original_reasons_json,
    );
    if (Number(result.changes) !== 1) { database.exec("ROLLBACK"); return "duplicate"; }
    database.prepare("DELETE FROM watchlist_items WHERE id = ? AND user_id = ?").run(watch.id, userId);
    database.exec("COMMIT");
    return "created";
  } catch (error) {
    database.exec("ROLLBACK");
    throw error;
  }
}

export function removeHolding(userId: string, symbol: string) {
  return Number(getDatabase().prepare("DELETE FROM holding_items WHERE user_id = ? AND symbol = ?").run(userId, symbol).changes) > 0;
}
