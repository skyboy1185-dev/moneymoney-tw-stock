import type { MarketContext, RankingRow } from "@/lib/market-types";

const MAXIMUM_RECOMMENDATIONS = 5;
const MINIMUM_RETENTION_MS = 3 * 60 * 1000;
const REPLACEMENT_SCORE_GAP = 5;

function priority(a: RankingRow, b: RankingRow) {
  return b.score - a.score
    || b.strategyFit - a.strategyFit
    || b.marketFit - a.marketFit
    || b.riskRewardRatio - a.riskRewardRatio
    || Number(b.volumeQualified) - Number(a.volumeQualified)
    || Number(b.quoteFresh) - Number(a.quoteFresh)
    || a.distanceMa20 - b.distanceMa20
    || a.riskTags.length - b.riskTags.length;
}

export function formalQualification(row: RankingRow, market: MarketContext): string[] {
  const failures = [...row.hardRiskFailures];
  if (!row.themes?.some((theme) => theme === "AI" || theme === "低軌衛星")) {
    failures.push("不屬於 AI 或低軌衛星主題股票池");
  }
  if (row.score < 75) failures.push("條件符合分數低於 75");
  if (row.strategyFit < 75) failures.push("策略適配度低於 75%");
  if (row.marketFit < 55) failures.push("大盤適配度不足");
  if (!row.quoteFresh) failures.push("行情時間過期或非盤中報價");
  if (!row.isOfficialPrice) failures.push("行情來源不是官方市場資訊");
  if (!row.volumeQualified) failures.push("成交量不足");
  if (row.turnover < 50_000_000) failures.push("成交金額不足");
  if (!row.liquidityQualified) failures.push("流動性不足");
  if (row.spreadPercentage == null) failures.push("缺少即時買賣價差");
  else if (row.spreadPercentage > .5) failures.push("買賣價差過大");
  if (row.riskRewardRatio < 1.5) failures.push("風險報酬比低於 1：1.5");
  if (Math.abs(row.distanceMa20) > 12) failures.push("距離 MA20 過遠");
  if ((row.rsi ?? 50) >= 80) failures.push("RSI 嚴重過熱");
  if (market.direction === "strong_bear" && !["bear-rebound", "exit-warning"].includes(row.strategyId)) {
    failures.push("大盤強空，禁止追多");
  }
  return failures.filter((value, index, values) => values.indexOf(value) === index);
}

export class FormalRecommendationEngine {
  private retained = new Map<string, { signalId: string; recommendedAt: number }>();

  select(rows: RankingRow[], market: MarketContext, now = Date.now()): RankingRow[] {
    if (!market.marketOpen) return [];
    const qualified = rows
      .map((row) => ({ row, failures: formalQualification(row, market) }))
      .filter(({ failures }) => failures.length === 0)
      .map(({ row }) => row)
      .sort(priority);

    const next: RankingRow[] = [];
    for (const row of qualified) {
      const retained = this.retained.get(row.symbol);
      if (retained && now - retained.recommendedAt < MINIMUM_RETENTION_MS) next.push(row);
    }
    for (const row of qualified) {
      if (next.some((item) => item.symbol === row.symbol)) continue;
      if (next.length < MAXIMUM_RECOMMENDATIONS) {
        next.push(row);
        continue;
      }
      const fifth = [...next].sort(priority)[MAXIMUM_RECOMMENDATIONS - 1];
      if (row.score >= fifth.score + REPLACEMENT_SCORE_GAP) {
        next.splice(next.findIndex((item) => item.symbol === fifth.symbol), 1, row);
      }
    }

    const selected = next.sort(priority).slice(0, MAXIMUM_RECOMMENDATIONS);
    const activeSymbols = new Set(selected.map((row) => row.symbol));
    [...this.retained.keys()].forEach((symbol) => {
      if (!activeSymbols.has(symbol)) this.retained.delete(symbol);
    });
    selected.forEach((row) => {
      if (!this.retained.has(row.symbol)) {
        this.retained.set(row.symbol, { signalId: row.signalId, recommendedAt: now });
      }
    });
    return selected.map((row, index) => ({
      ...row,
      rank: index + 1,
      isFeatured: true,
      signalId: this.retained.get(row.symbol)?.signalId ?? row.signalId,
    }));
  }
}

export const formalRecommendationEngine = new FormalRecommendationEngine();
