import type { DailyPrice } from "./types";

export interface ThreeGatePrice {
  sourceDate: string;
  upper: number;
  middle: number;
  lower: number;
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
