import { describe, expect, it } from "vitest";
import { buildVolumePriceTrend, buildVolumeProfile } from "./volume-profile";
import type { DailyPrice } from "./types";

function price(date: string, low: number, high: number, close: number, volume: number): DailyPrice {
  return {
    symbol: "2330",
    name: "台積電",
    date,
    open: close,
    high,
    low,
    close,
    volume,
  };
}

describe("volume profile", () => {
  it("將每日成交量依高低價重疊區間分配至價位箱", () => {
    const profile = buildVolumeProfile([
      price("2026-07-24", 90, 100, 98, 100_000),
      price("2026-07-27", 90, 100, 99, 120_000),
      price("2026-07-28", 110, 120, 115, 20_000),
    ], 60, 6);
    expect(profile).not.toBeNull();
    expect(profile?.totalVolume).toBe(240_000);
    expect(profile?.bins.reduce((sum, bin) => sum + bin.volume, 0)).toBe(240_000);
    expect(profile?.poc.high).toBeLessThanOrEqual(105);
    expect(profile?.zones[0].low).toBeLessThanOrEqual(100);
    expect(profile?.zones[0].volumePct).toBeGreaterThan(50);
  });

  it("判斷目前股價在最大量區的相對位置", () => {
    const profile = buildVolumeProfile([
      price("2026-07-24", 90, 100, 95, 100_000),
      price("2026-07-27", 90, 100, 98, 100_000),
      price("2026-07-28", 110, 120, 115, 10_000),
    ], 60, 6);
    expect(profile?.position).toBe("above");
    expect(profile?.positionLabel).toContain("上方");
  });

  it("資料不足或價格無區間時不建立分布", () => {
    expect(buildVolumeProfile([])).toBeNull();
    expect(buildVolumeProfile([price("2026-07-28", 100, 100, 100, 10_000)])).toBeNull();
  });

  it("將收盤價轉成可疊在成交量價位圖上的時間與價格座標", () => {
    const trend = buildVolumePriceTrend([
      price("2026-07-24", 90, 100, 95, 100_000),
      price("2026-07-27", 95, 110, 100, 120_000),
      price("2026-07-28", 100, 120, 115, 130_000),
    ], 3, 90, 120);
    expect(trend.map((point) => point.xPct)).toEqual([0, 50, 100]);
    expect(trend[0].yPct).toBeCloseTo(83.33, 1);
    expect(trend.at(-1)?.yPct).toBeCloseTo(16.67, 1);
  });
});
