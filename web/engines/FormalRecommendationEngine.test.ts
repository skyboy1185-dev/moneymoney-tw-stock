import { describe, expect, it } from "vitest";
import { FormalRecommendationEngine, formalQualification } from "./FormalRecommendationEngine";
import type { MarketContext, RankingRow } from "@/lib/market-types";

const market: MarketContext = {
  score: 45, direction: "bull", confidence: 80, reasons: [],
  regime: "wave_up", marketOpen: true, futuresMarketOpen: true,
  indexPrice: 30000, indexChange: 100, indexChangePercent: .3,
  futuresPrice: 30020, futuresChange: 120, futuresChangePercent: .4,
  adx: 25, indexAboveMa20: true, indexAboveMa60: true, indexAboveMa120: true,
  ma20Slope: .3, ma60Slope: .2, macdAboveZero: true,
  largeOrderNet: 1e8, smallOrderNet: 1e7, breadthUp: 600, breadthDown: 300,
};

function row(symbol: string, score = 80, overrides: Partial<RankingRow> = {}): RankingRow {
  return {
    rank: 0, symbol, name: symbol, market: "上市", industry: "半導體",
    price: 100, changePercent: 1, volume: 2_000_000,
    strategyId: "trend-start", strategyName: "波段起漲 Bot",
    score, strategyFit: 82, secondaryStrategies: ["多頭回檔 Bot"],
    marketFit: 80, healthScore: 80, riskRewardRatio: 2,
    entryMin: 99, entryMax: 101, stopLoss: 95, target1: 108, target2: 110,
    turnover: 200_000_000, spreadPercentage: .2, distanceMa20: 3, rsi: 60,
    volumeQualified: true, liquidityQualified: true, quoteFresh: true,
    hardRiskFailures: [], isFeatured: false, signalId: `ai-2026-07-27-${symbol}`,
    marketDirection: "bull", macdState: "今日翻紅", signalStatus: "confirmed",
    triggeredAt: "2026-07-27T09:10:00+08:00", reasons: ["MACD 翻紅", "站上 MA20", "成交量增加"],
    riskTags: [], movement: "new", updatedAt: "2026-07-27T09:10:00+08:00",
    priceSource: "TWSE MIS", priceDate: "2026-07-27", priceTime: "09:10:00",
    isOfficialPrice: true, ...overrides,
  };
}

describe("FormalRecommendationEngine", () => {
  it("正式推薦最多五檔且不會用低分股票湊滿", () => {
    const engine = new FormalRecommendationEngine();
    const selected = engine.select([
      row("2301", 95), row("2302", 90), row("2303", 85),
      row("2304", 82), row("2305", 80), row("2306", 79), row("2307", 74),
    ], market, new Date("2026-07-27T01:10:00Z").getTime());
    expect(selected).toHaveLength(5);
    expect(selected.every((item) => item.score >= 75)).toBe(true);
    expect(selected.some((item) => item.symbol === "2307")).toBe(false);
  });

  it("硬性風控直接排除，不只是扣分", () => {
    const risky = row("2317", 95, { hardRiskFailures: ["距離 MA20 過遠"] });
    expect(formalQualification(risky, market)).toContain("距離 MA20 過遠");
    expect(new FormalRecommendationEngine().select([risky], market)).toHaveLength(0);
  });

  it("次要策略最多兩個且盤外不產生正式推薦", () => {
    const candidate = row("2330", 90, { secondaryStrategies: ["A", "B"] });
    expect(candidate.secondaryStrategies).toHaveLength(2);
    expect(new FormalRecommendationEngine().select([candidate], { ...market, marketOpen: false })).toHaveLength(0);
  });
});
