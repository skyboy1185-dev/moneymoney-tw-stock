import type { MarketDirection, MarketForceInput, MarketForceResult } from "@/lib/market-types";

const clamp = (value: number, min = -100, max = 100) => Math.max(min, Math.min(max, value));

export function calculateMarketForce(input: MarketForceInput): MarketForceResult {
  const normalized = {
    largeOrder: clamp(input.largeOrderNet / 50_000_000),
    futures: clamp(input.futuresDirection),
    index: clamp(input.indexTrend),
    breadth: clamp(input.marketBreadth),
    vwap: clamp(input.indexVsVwap),
    volume: clamp(input.volumeMomentum),
    ma20: clamp(input.aboveMa20Ratio),
  };
  const values = Object.values(normalized);
  const score = Math.round(
    normalized.largeOrder * .30 +
    normalized.futures * .20 +
    normalized.index * .15 +
    normalized.breadth * .15 +
    normalized.vwap * .10 +
    normalized.volume * .05 +
    normalized.ma20 * .05,
  );
  const direction: MarketDirection =
    score >= 60 ? "strong_bull" :
    score >= 20 ? "bull" :
    score <= -60 ? "strong_bear" :
    score <= -20 ? "bear" : "sideways";
  const sign = Math.sign(score) || 1;
  const aligned = values.filter((value) => Math.sign(value) === sign).length;
  const dispersion = values.reduce((sum, value) => sum + Math.abs(value - score), 0) / values.length;
  const confidence = Math.round(clamp(48 + aligned * 7 - dispersion * .18, 35, 96));
  const reasons = [
    normalized.largeOrder > 20 ? "大單淨額偏向主動買進" : normalized.largeOrder < -20 ? "大單淨額偏向主動賣出" : "大單方向暫不明顯",
    normalized.futures > 20 ? "台指期方向偏多" : normalized.futures < -20 ? "台指期方向偏空" : "台指期震盪",
    normalized.breadth > 20 ? "上漲家數明顯多於下跌家數" : normalized.breadth < -20 ? "下跌家數占優" : "市場漲跌家數接近",
    normalized.ma20 > 20 ? "多數股票位於 MA20 之上" : "站上 MA20 比例仍需觀察",
  ];
  return { score, direction, confidence, reasons };
}
