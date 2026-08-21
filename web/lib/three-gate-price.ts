import type { DailyPrice } from "./types";

export interface ThreeGatePrice {
  sourceDate: string;
  upper: number;
  middle: number;
  lower: number;
}

export type ThreeGatePosition = "crossed-above" | "crossed-below" | "above" | "below";

export interface ThreeGateLevelStatus {
  key: "upper" | "middle" | "lower";
  label: "上關價" | "中關價" | "下關價";
  price: number;
  position: ThreeGatePosition;
}

export function stockTickSize(price: number) {
  if (price < 10) return 0.01;
  if (price < 50) return 0.05;
  if (price < 100) return 0.1;
  if (price < 500) return 0.5;
  if (price < 1_000) return 1;
  return 5;
}

function roundToStockTick(price: number) {
  const tick = stockTickSize(price);
  const digits = tick < 0.1 ? 2 : tick < 1 ? 1 : 0;
  return Number((Math.round(price / tick) * tick).toFixed(digits));
}

export function calculateThreeGatePrice(
  prices: DailyPrice[],
  marketOpen: boolean,
): ThreeGatePrice | null {
  const source = prices.at(marketOpen ? -2 : -1);
  if (!source || source.high <= 0 || source.low <= 0 || source.high < source.low) return null;
  const range = source.high - source.low;
  return {
    sourceDate: source.date,
    upper: roundToStockTick(source.high + range * 0.382),
    middle: roundToStockTick((source.high + source.low) / 2),
    lower: roundToStockTick(source.low - range * 0.382),
  };
}

export function evaluateThreeGateLevels(
  currentPrice: number,
  previousClose: number | null | undefined,
  threeGate: ThreeGatePrice,
): ThreeGateLevelStatus[] {
  const levels = [
    { key: "upper", label: "上關價", price: threeGate.upper },
    { key: "middle", label: "中關價", price: threeGate.middle },
    { key: "lower", label: "下關價", price: threeGate.lower },
  ] as const;
  return levels.map((level) => {
    const crossedAbove = previousClose != null && previousClose < level.price && currentPrice >= level.price;
    const crossedBelow = previousClose != null && previousClose >= level.price && currentPrice < level.price;
    const position: ThreeGatePosition = crossedAbove
      ? "crossed-above"
      : crossedBelow
        ? "crossed-below"
        : currentPrice >= level.price
          ? "above"
          : "below";
    return { ...level, position };
  });
}
