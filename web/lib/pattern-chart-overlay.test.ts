import { describe, expect, it } from "vitest";
import {
  buildPatternOverlayLines,
  catmullRomPath,
  selectPatternChartRows,
  type PatternChartCandle,
  type PatternChartKeyPoint,
} from "./pattern-chart-overlay";

const point = (name: string, date: string, price: number): PatternChartKeyPoint => ({
  name,
  date,
  price,
  confirmedDate: date,
});

const candles: PatternChartCandle[] = Array.from({ length: 200 }, (_, index) => {
  const date = new Date("2025-01-01T00:00:00Z");
  date.setUTCDate(date.getUTCDate() + index);
  return {
    date: date.toISOString().slice(0, 10),
    open: 100,
    high: 102,
    low: 98,
    close: 101,
  };
});

describe("pattern chart overlay", () => {
  it.each([
    "HEAD_SHOULDERS_BOTTOM",
    "DOUBLE_BOTTOM",
    "CUP_HANDLE",
  ])("connects %s pivots in chronological order", (patternType) => {
    const lines = buildPatternOverlayLines(patternType, [
      point("第三點", "2025-03-03", 98),
      point("第一點", "2025-03-01", 100),
      point("第二點", "2025-03-02", 105),
    ]);

    expect(lines).toHaveLength(1);
    expect(lines[0].kind).toBe("polyline");
    expect(lines[0].points.map((item) => item.name)).toEqual(["第一點", "第二點", "第三點"]);
  });

  it("uses a smooth path for a rounded bottom", () => {
    const lines = buildPatternOverlayLines("ROUNDED_BOTTOM", [
      point("圓弧起點壓力", "2025-03-01", 110),
      point("圓弧底", "2025-03-10", 80),
      point("右側轉強", "2025-03-20", 105),
    ]);
    const path = catmullRomPath(lines[0].points.map((item, index) => ({ x: index * 10, y: item.price })));

    expect(lines[0].kind).toBe("curve");
    expect(path).toContain(" C ");
    expect(path).toMatch(/20 105$/);
  });

  it("draws ascending-triangle resistance and support separately", () => {
    const lines = buildPatternOverlayLines("ASCENDING_TRIANGLE", [
      point("墊高低點2", "2025-03-12", 96),
      point("水平壓力2", "2025-03-10", 110),
      point("墊高低點1", "2025-03-02", 90),
      point("水平壓力1", "2025-03-01", 109),
    ]);

    expect(lines.map((line) => [line.label, line.points.map((item) => item.name)])).toEqual([
      ["水平壓力", ["水平壓力1", "水平壓力2"]],
      ["上升支撐", ["墊高低點1", "墊高低點2"]],
    ]);
  });

  it("includes the earliest visible pivot while capping the chart at 180 bars", () => {
    const selected = selectPatternChartRows(candles, [
      point("起點", candles[35].date, 100),
      point("終點", candles[150].date, 105),
    ]);

    expect(selected).toHaveLength(170);
    expect(selected[0].date).toBe(candles[30].date);
    expect(selected.at(-1)?.date).toBe(candles.at(-1)?.date);
  });

  it("falls back to the latest 80 bars when no pivot date can be located", () => {
    const selected = selectPatternChartRows(candles, [point("缺少", "2024-01-01", 100)]);
    expect(selected).toEqual(candles.slice(-80));
  });
});
