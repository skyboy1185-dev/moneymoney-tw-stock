import type { DailyPrice } from "./types";

function round(value: number) {
  return Math.round(value * 100) / 100;
}

export function assessKeyPrice(prices: DailyPrice[], lookback = 20) {
  const latest = prices.at(-1);
  const reference = prices.slice(-(lookback + 1), -1);
  if (!latest || !reference.length) {
    return { keyPrice: null, aboveKeyPrice: false, keyPriceDistancePct: null };
  }
  const keyPrice = Math.max(...reference.map((price) => price.high));
  return {
    keyPrice: round(keyPrice),
    aboveKeyPrice: latest.close >= keyPrice,
    keyPriceDistancePct: round((latest.close - keyPrice) / keyPrice * 100),
  };
}
