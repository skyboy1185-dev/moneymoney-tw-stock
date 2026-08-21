import { describe, expect, it } from "vitest";
import type { ElectronicChipFlowAlert, ElectronicChipFlowQuote } from "@/lib/electronic-chip-flow-alerts";
import { evaluateLargeOrderGuidance } from "@/lib/large-order-action";

const NOW = new Date("2026-08-14T01:10:30.000Z");

function alert(overrides: Partial<ElectronicChipFlowAlert> = {}): ElectronicChipFlowAlert {
  return {
    direction: "long", recentNetBuyLots: 25, recentNetSellLots: 0,
    recentSmallNetBuyLots: 8, recentBuyLots: 30, recentSellLots: 5,
    buySellRatio: 6, positiveSteps: 3, occurrenceCount: 3,
    currentQualifies: true, isWarning: false, reinforced: true,
    trend: "strengthening", ...overrides,
  } as ElectronicChipFlowAlert;
}

function quote(changePercent: number, overrides: Partial<ElectronicChipFlowQuote> = {}): ElectronicChipFlowQuote {
  return {
    price: 100, change: changePercent, changePercent, isRealtime: true,
    quoteTimestamp: "2026-08-14T09:10:00+08:00", ...overrides,
  } as ElectronicChipFlowQuote;
}

describe("evaluateLargeOrderGuidance", () => {
  it("recommends buying only after conservative confirmations", () => {
    expect(evaluateLargeOrderGuidance({ alert: alert(), quote: quote(1.2), marketOpen: true, now: NOW }).action).toBe("buy");
  });

  it("strongly recommends buying after large orders repeatedly strengthen", () => {
    const result = evaluateLargeOrderGuidance({
      alert: alert({ trendStreak: 2, simultaneousIncrease: true }),
      quote: quote(1.2), marketOpen: true, now: NOW,
    });
    expect(result.action).toBe("strong_buy");
    expect(result.label).toBe("強烈建議買進");
    expect(result.reasons[0]).toContain("大單連續累積");
  });

  it("strongly recommends when independent price, small-order and group signals agree", () => {
    const result = evaluateLargeOrderGuidance({
      alert: alert({ reinforced: false, trend: "sustained", trendStreak: 0, simultaneousIncrease: true }),
      quote: quote(1.2),
      marketOpen: true,
      now: NOW,
      resonance: {
        group: "記憶體", direction: "up", count: 3,
        symbols: ["2337", "2344", "2408"], names: ["旺宏", "華邦電", "南亞科"],
        averageChangePercent: 1.1,
      },
    });
    expect(result.action).toBe("strong_buy");
    expect(result.reasons[0]).toContain("高度一致");
  });

  it("recommends shorting when persistent selling and price both point down", () => {
    const result = evaluateLargeOrderGuidance({
      alert: alert({
        direction: "short", recentNetBuyLots: -28, recentNetSellLots: 28,
        recentSmallNetBuyLots: -9, recentBuyLots: 5, recentSellLots: 33,
        sellBuyRatio: 6.6, negativeSteps: 3,
      }),
      quote: quote(-1.4), marketOpen: true, now: NOW,
    });
    expect(result.action).toBe("short");
    expect(result.cautions).toContain("下單前仍須確認券源與可放空資格");
  });

  it("strongly recommends shorting after repeated large-order selling", () => {
    const result = evaluateLargeOrderGuidance({
      alert: alert({
        direction: "short", recentNetBuyLots: -28, recentNetSellLots: 28,
        recentSmallNetBuyLots: -9, recentBuyLots: 5, recentSellLots: 33,
        sellBuyRatio: 6.6, negativeSteps: 3, trendStreak: 3,
        simultaneousIncrease: true,
      }),
      quote: quote(-1.4), marketOpen: true, now: NOW,
    });
    expect(result.action).toBe("strong_short");
    expect(result.label).toBe("強烈建議放空");
  });

  it("stays on watch when price does not confirm the large orders", () => {
    expect(evaluateLargeOrderGuidance({ alert: alert(), quote: quote(-0.4), marketOpen: true, now: NOW }).action).toBe("watch");
  });

  it("stays on watch when the quote is delayed", () => {
    const result = evaluateLargeOrderGuidance({
      alert: alert(), quote: quote(1.2, { isRealtime: false }), marketOpen: true, now: NOW,
    });
    expect(result.action).toBe("watch");
    expect(result.score).toBeNull();
  });

  it("shows no score when the market is closed or the scan is stale", () => {
    expect(evaluateLargeOrderGuidance({ alert: alert(), quote: quote(1.2), marketOpen: false, now: NOW }).score).toBeNull();
    expect(evaluateLargeOrderGuidance({
      alert: alert({ dataState: "stale", dataStateLabel: "掃描延遲 25 秒" }),
      quote: quote(1.2), marketOpen: true, now: NOW,
    }).score).toBeNull();
  });

  it("does not chase a move already beyond six percent", () => {
    expect(evaluateLargeOrderGuidance({ alert: alert(), quote: quote(6.5), marketOpen: true, now: NOW }).action).toBe("watch");
  });
});
