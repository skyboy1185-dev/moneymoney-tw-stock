import { describe, expect, it } from "vitest";
import { calculateIndustryStrength, isTaipeiCashSession, latestCommonTradeDate, topIndustryHotspots, type IndustryHotspot } from "./content-service";

const member = (changePercent: number, index: number) => ({
  symbol: String(1000 + index), name: `股票${index}`, changePercent,
});

describe("industry strength", () => {
  it("uses a trimmed mean so one extreme winner cannot dominate a broad weak group", () => {
    const result = calculateIndustryStrength([-2, -1.5, -1, -.8, -.5, -.4, -.3, -.2, -.1, 10]
      .map(member));
    expect(result.changePercent).toBeLessThan(0);
    expect(result.advanceRatio).toBe(10);
    expect(result.strengthScore).toBeLessThan(40);
  });

  it("rewards broad participation when most members rise", () => {
    const broad = calculateIndustryStrength([1, 1.2, 1.4, 1.5, 1.8, 2, 2.2, 2.5, 2.7, 3].map(member));
    const narrow = calculateIndustryStrength([-1, -.8, -.5, -.2, 0, 0, .2, .5, 1, 8].map(member));
    expect(broad.advanceRatio).toBe(100);
    expect(broad.strengthScore).toBeGreaterThan(narrow.strengthScore);
  });

  it("returns stable zero values for an empty group", () => {
    expect(calculateIndustryStrength([])).toEqual({
      changePercent: 0, medianChangePercent: 0, advanceRatio: 0, leaderBreadth: 0, strengthScore: 0,
    });
  });

  it("selects only the latest trade date shared by listed and OTC markets", () => {
    expect(latestCommonTradeDate(["2026-09-17", "2026-09-18"], ["2026-09-16", "2026-09-17"]))
      .toBe("2026-09-17");
    expect(latestCommonTradeDate(["2026-09-18"], ["2026-09-17"])).toBeNull();
  });

  it("keeps the bar ranked by composite strength instead of average change", () => {
    const hotspot = (industry: string, strengthScore: number, changePercent: number) => ({
      industry, strengthScore, changePercent, medianChangePercent: 0, advanceRatio: 0,
      leaderBreadth: 0, momentum: 0, stockCount: 1, leaders: [], status: "整理" as const,
    }) satisfies IndustryHotspot;
    const result = topIndustryHotspots([
      hotspot("平均漲幅較高", 60, 8), hotspot("綜合強度第一", 90, 2), hotspot("綜合強度第二", 80, 3),
    ], 2);
    expect(result.map((item) => item.industry)).toEqual(["綜合強度第一", "綜合強度第二"]);
  });

  it("detects the Taipei cash session for stale intraday warnings", () => {
    expect(isTaipeiCashSession(new Date("2026-09-21T02:00:00Z"))).toBe(true);
    expect(isTaipeiCashSession(new Date("2026-09-21T06:00:00Z"))).toBe(false);
    expect(isTaipeiCashSession(new Date("2026-09-20T02:00:00Z"))).toBe(false);
  });
});
