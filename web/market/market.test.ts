import { describe, expect, it } from "vitest";
import { calculateMarketForce } from "./MarketForceCalculator";
import { strategySelector } from "./StrategySelector";
import { allRobots } from "@/robots";
import { stockService } from "@/services/stock-service";
import type { MarketContext } from "@/lib/market-types";

const context: MarketContext = {
  score: 52, direction: "bull", confidence: 82, reasons: ["測試"],
  regime: "wave_up", marketOpen: false,
  indexPrice: 24000, indexChange: 200, indexChangePercent: .8,
  futuresPrice: 24000, futuresChange: 180, futuresChangePercent: .75,
  adx: 28, indexAboveMa20: true, indexAboveMa60: true, indexAboveMa120: true,
  ma20Slope: .4, ma60Slope: .2, macdAboveZero: true,
  largeOrderNet: 3e9, smallOrderNet: -1e9, breadthUp: 600, breadthDown: 300,
};

describe("calculateMarketForce", () => {
  it("依七項權重計算且限制於 -100 到 100", () => {
    const bullish = calculateMarketForce({
      largeOrderNet: 4e9, futuresDirection: 70, indexTrend: 60,
      marketBreadth: 55, indexVsVwap: 50, volumeMomentum: 30, aboveMa20Ratio: 45,
    });
    expect(bullish.score).toBeGreaterThan(20);
    expect(["bull", "strong_bull"]).toContain(bullish.direction);
    expect(bullish.confidence).toBeGreaterThanOrEqual(35);
    expect(bullish.reasons.length).toBeGreaterThanOrEqual(3);
  });
});

describe("策略機器人", () => {
  it("七個 Robot 都實作 analyze、filter、score、recommend", async () => {
    const stock = await stockService.getStock("2330");
    expect(stock).not.toBeNull();
    expect(allRobots).toHaveLength(7);
    allRobots.forEach((robot) => {
      const analysis = robot.analyze(stock!, context);
      expect(typeof robot.filter(stock!, context)).toBe("boolean");
      expect(robot.score(stock!, context)).toBeGreaterThanOrEqual(0);
      expect(robot.recommend(stock!, context)).toEqual(analysis.reasons);
    });
  });

  it("偏多盤推薦波段起漲、多頭回檔與盤整突破", () => {
    const recommended = strategySelector.select("bull").filter((item) => item.enabled);
    expect(recommended.map((item) => item.id)).toEqual(["trend-start", "bull-pullback", "sideways-breakout"]);
  });
});
