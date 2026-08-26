import { describe, expect, it } from "vitest";
import type { MarketIndexDefenseResponse } from "./market-index-defense";
import type { MarketContext } from "./market-types";
import { buildTaiwanIndexKeyLevels } from "./taiwan-index-key-levels";

function context(values: Partial<MarketContext> = {}): MarketContext {
  return {
    score: 20,
    direction: "bull",
    confidence: 80,
    reasons: [],
    regime: "wave_up",
    marketOpen: true,
    futuresMarketOpen: true,
    indexPrice: 45_250,
    indexChange: 100,
    indexChangePercent: 0.2,
    futuresPrice: 45_320,
    futuresChange: 120,
    futuresChangePercent: 0.27,
    adx: 20,
    indexAboveMa20: true,
    indexAboveMa60: true,
    indexAboveMa120: true,
    ma20Slope: 1,
    ma60Slope: 1,
    macdAboveZero: true,
    largeOrderNet: 1_000,
    smallOrderNet: -200,
    breadthUp: 600,
    breadthDown: 300,
    futuresContract: "202608",
    futuresSource: "TAIFEX",
    futuresQuoteAt: "2026-08-26 10:15",
    indexSource: "TWSE MIS",
    indexQuoteAt: "2026-08-26 10:15",
    ...values,
  };
}

function defense(): MarketIndexDefenseResponse {
  return {
    indexName: "發行量加權股價指數",
    currentPrice: 45_250,
    source: "TWSE",
    quoteAt: "2026-08-26 10:15",
    calculationNote: "測試",
    defense: {
      week: {
        timeframe: "week",
        label: "週防守",
        tradingDays: 5,
        startDate: "2026-08-20",
        endDate: "2026-08-26",
        defensePrice: 45_060,
        zoneLow: 44_980,
        zoneHigh: 45_120,
        zoneVolumePct: 36,
        currentPrice: 45_250,
        distancePct: 0.42,
        status: "held",
        statusLabel: "站上",
      },
      month: {
        timeframe: "month",
        label: "月防守",
        tradingDays: 20,
        startDate: "2026-07-30",
        endDate: "2026-08-26",
        defensePrice: 44_320,
        zoneLow: 44_180,
        zoneHigh: 44_460,
        zoneVolumePct: 28,
        currentPrice: 45_250,
        distancePct: 2.1,
        status: "held",
        statusLabel: "站上",
      },
    },
    otc: null,
  };
}

describe("taiwan index key levels", () => {
  it("uses futures as the reference price when available", () => {
    const levels = buildTaiwanIndexKeyLevels(context({ futuresPrice: 45_320, indexPrice: 45_250 }), defense());

    expect(levels.available).toBe(true);
    expect(levels.referencePrice).toBe(45_320);
    expect(levels.referenceSource).toContain("台指期");
    expect(levels.pivot?.source).toBe("上方整數關卡");
    expect(levels.support?.source).toBe("近5日大量區");
    expect(levels.downsideTargets.length).toBeGreaterThan(0);
    expect(levels.title).toContain("多空分界");
    expect(levels.title).toContain("支撐");
  });

  it("falls back to weighted index when futures are unavailable", () => {
    const levels = buildTaiwanIndexKeyLevels(context({ futuresPrice: 0, indexPrice: 45_250 }), defense());

    expect(levels.referencePrice).toBe(45_250);
    expect(levels.referenceSource).toBe("加權指數即時價");
  });

  it("does not return legacy hard-coded levels unless input data produces them", () => {
    const levels = buildTaiwanIndexKeyLevels(context({ futuresPrice: 45_320, indexPrice: 45_250 }), defense());
    const rendered = [
      levels.pivot?.value,
      levels.support?.low,
      levels.support?.high,
      ...levels.downsideTargets.map((item) => item.value),
    ];

    expect(rendered).not.toContain(45_000);
    expect(rendered).not.toContain(44_780);
    expect(rendered).not.toContain(44_800);
    expect(rendered).not.toContain(44_500);
    expect(rendered).not.toContain(44_261);
  });

  it("returns a safe unavailable state when official prices are missing", () => {
    const levels = buildTaiwanIndexKeyLevels(context({ futuresPrice: 0, indexPrice: 0 }), null);

    expect(levels.available).toBe(false);
    expect(levels.stateLabel).toBe("等待官方行情");
    expect(levels.pivot).toBeNull();
    expect(levels.support).toBeNull();
    expect(levels.downsideTargets).toEqual([]);
  });
});
