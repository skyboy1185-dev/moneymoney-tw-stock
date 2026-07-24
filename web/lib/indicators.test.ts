import { describe, expect, it } from "vitest";
import { calculateEMA, calculateIndicators, calculateMACD, calculateSMA, generateMACDSignals } from "./indicators";
import type { DailyPrice } from "./types";

describe("calculateEMA", () => {
  it("只使用當日與先前的數值", () => {
    const original = calculateEMA([10, 12, 14], 3);
    const futureChanged = calculateEMA([10, 12, 14, 1000], 3);
    expect(futureChanged.slice(0, 3)).toEqual(original);
    expect(original).toEqual([10, 11, 12.5]);
  });
});

describe("calculateMACD", () => {
  it("依標準 12、26、9 EMA 產生 DIF、Signal 與 Histogram", () => {
    const prices = Array.from({ length: 40 }, (_, index) => ({
      date: `2026-01-${String(index + 1).padStart(2, "0")}`,
      close: 100 + index,
    }));
    const result = calculateMACD(prices);
    expect(result).toHaveLength(40);
    expect(result[0]).toEqual({ date: "2026-01-01", dif: 0, signal: 0, histogram: 0 });
    expect(result.at(-1)!.dif).toBeGreaterThan(0);
    expect(result.at(-1)!.histogram).toBeGreaterThan(0);
  });

  it("空資料回傳空陣列", () => {
    expect(calculateMACD([])).toEqual([]);
  });
});

describe("generateMACDSignals", () => {
  it("僅在柱狀圖穿越零軸的第一天產生進場與出場", () => {
    const result = generateMACDSignals([
      { date: "01", histogram: -0.5 },
      { date: "02", histogram: -0.1 },
      { date: "03", histogram: 0 },
      { date: "04", histogram: 0.4 },
      { date: "05", histogram: -0.01 },
      { date: "06", histogram: -0.3 },
    ]);
    expect(result.map((item) => item.macdSignal)).toEqual([null, null, "entry", null, "exit", null]);
  });

  it("零值視為紅柱，因此 0 到負值為出場", () => {
    const result = generateMACDSignals([
      { date: "01", histogram: 0 },
      { date: "02", histogram: -0.1 },
    ]);
    expect(result[1].macdSignal).toBe("exit");
  });
});

describe("calculateSMA / calculateIndicators", () => {
  it("在資料天數不足前回傳 null", () => {
    expect(calculateSMA([1, 2, 3, 4, 5], 3)).toEqual([null, null, 2, 3, 4]);
  });

  it("指標筆數與行情一致且不會被未來資料改寫", () => {
    const make = (close: number, index: number): DailyPrice => ({
      symbol: "TEST", name: "測試", date: `2026-02-${String(index + 1).padStart(2, "0")}`,
      open: close, high: close, low: close, close, volume: 1000,
    });
    const base = Array.from({ length: 30 }, (_, index) => make(100 + index, index));
    const original = calculateIndicators(base);
    const extended = calculateIndicators([...base, make(999, 30)]);
    expect(original).toHaveLength(30);
    expect(extended.slice(0, 30)).toEqual(original);
    expect(original[3].ma5).toBeNull();
    expect(original[4].ma5).toBe(102);
  });
});
