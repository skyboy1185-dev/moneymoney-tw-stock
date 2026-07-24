import { describe, expect, it } from "vitest";
import { calculateADX, calculateKD, calculateRSI, resampleCandles } from "./technical";
import type { DailyPrice } from "./types";

function candles(closes: number[]): DailyPrice[] {
  return closes.map((close, index) => ({
    symbol: "TEST", name: "測試", date: `2026-01-${String(index + 1).padStart(2, "0")}`,
    open: close, high: close + 1, low: close - 1, close, volume: 1_000_000,
  }));
}

describe("calculateKD", () => {
  it("依 9、3、3 產生 0 到 100 的 K/D 並辨認低檔金叉", () => {
    const data = candles([100, 98, 96, 94, 92, 90, 88, 86, 84, 82, 81, 80, 79, 78, 77, 76, 78, 82, 88]);
    const result = calculateKD(data);
    expect(result.slice(0, 8).every((point) => point.k === null && point.d === null)).toBe(true);
    expect(result.filter((point) => point.k != null).every((point) => point.k! >= 0 && point.k! <= 100 && point.d! >= 0 && point.d! <= 100)).toBe(true);
    expect(result.some((point) => point.goldenCross)).toBe(true);
  });

  it("增加未來 K 棒不會改變既有 KD", () => {
    const base = candles(Array.from({ length: 20 }, (_, index) => 100 + Math.sin(index) * 5));
    expect(calculateKD([...base, ...candles([999])]).slice(0, 20)).toEqual(calculateKD(base));
  });
});

describe("其他技術指標", () => {
  it("RSI 與 ADX 對資料不足回傳 null，形成後維持合理範圍", () => {
    const data = candles(Array.from({ length: 50 }, (_, index) => 100 + index + Math.sin(index) * 3));
    const rsi = calculateRSI(data);
    const adx = calculateADX(data);
    expect(rsi[5]).toBeNull();
    expect(adx[20]).toBeNull();
    expect(rsi.at(-1)).toBeGreaterThanOrEqual(0);
    expect(rsi.at(-1)).toBeLessThanOrEqual(100);
    expect(adx.at(-1)).toBeGreaterThanOrEqual(0);
    expect(adx.at(-1)).toBeLessThanOrEqual(100);
  });

  it("週線與月線使用首開、最高、最低、末收與成交量加總", () => {
    const data = candles([100, 102, 99, 105, 103]).map((item, index) => ({
      ...item,
      date: `2026-01-${String(index + 5).padStart(2, "0")}`,
    }));
    const weekly = resampleCandles(data, "week");
    expect(weekly[0].open).toBe(100);
    expect(weekly[0].close).toBe(103);
    expect(weekly[0].volume).toBe(5_000_000);
    expect(resampleCandles(data, "month")).toHaveLength(1);
  });
});
