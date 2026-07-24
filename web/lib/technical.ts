import { calculateIndicators } from "./indicators";
import type { DailyPrice } from "./types";
import type { KDPoint, Timeframe } from "./market-types";

const round = (value: number) => Math.round(value * 10000) / 10000;

export function calculateKD(prices: DailyPrice[], period = 9, smoothK = 3, smoothD = 3): KDPoint[] {
  let previousK = 50;
  let previousD = 50;
  return prices.map((price, index) => {
    if (index < period - 1) return { date: price.date, k: null, d: null, goldenCross: false };
    const window = prices.slice(index - period + 1, index + 1);
    const highest = Math.max(...window.map((item) => item.high));
    const lowest = Math.min(...window.map((item) => item.low));
    const rsv = highest === lowest ? 50 : ((price.close - lowest) / (highest - lowest)) * 100;
    const k = ((smoothK - 1) * previousK + rsv) / smoothK;
    const d = ((smoothD - 1) * previousD + k) / smoothD;
    const goldenCross = previousK <= previousD && k > d && (k < 30 || d < 30);
    previousK = k;
    previousD = d;
    return { date: price.date, k: round(k), d: round(d), goldenCross };
  });
}

export function calculateRSI(prices: DailyPrice[], period = 14): (number | null)[] {
  let averageGain = 0;
  let averageLoss = 0;
  return prices.map((price, index) => {
    if (index === 0) return null;
    const change = price.close - prices[index - 1].close;
    const gain = Math.max(0, change);
    const loss = Math.max(0, -change);
    if (index <= period) {
      averageGain += gain / period;
      averageLoss += loss / period;
      if (index < period) return null;
    } else {
      averageGain = (averageGain * (period - 1) + gain) / period;
      averageLoss = (averageLoss * (period - 1) + loss) / period;
    }
    return round(averageLoss === 0 ? 100 : 100 - 100 / (1 + averageGain / averageLoss));
  });
}

export function calculateADX(prices: DailyPrice[], period = 14): (number | null)[] {
  const tr: number[] = [];
  const plusDM: number[] = [];
  const minusDM: number[] = [];
  prices.forEach((price, index) => {
    if (index === 0) { tr.push(price.high - price.low); plusDM.push(0); minusDM.push(0); return; }
    const previous = prices[index - 1];
    tr.push(Math.max(price.high - price.low, Math.abs(price.high - previous.close), Math.abs(price.low - previous.close)));
    const up = price.high - previous.high;
    const down = previous.low - price.low;
    plusDM.push(up > down && up > 0 ? up : 0);
    minusDM.push(down > up && down > 0 ? down : 0);
  });
  const result: (number | null)[] = Array(prices.length).fill(null);
  const dx: number[] = [];
  for (let index = period; index < prices.length; index += 1) {
    const atr = tr.slice(index - period + 1, index + 1).reduce((sum, value) => sum + value, 0);
    const plus = plusDM.slice(index - period + 1, index + 1).reduce((sum, value) => sum + value, 0);
    const minus = minusDM.slice(index - period + 1, index + 1).reduce((sum, value) => sum + value, 0);
    const plusDI = atr ? (plus / atr) * 100 : 0;
    const minusDI = atr ? (minus / atr) * 100 : 0;
    dx.push(plusDI + minusDI ? (Math.abs(plusDI - minusDI) / (plusDI + minusDI)) * 100 : 0);
    if (dx.length >= period) result[index] = round(dx.slice(-period).reduce((sum, value) => sum + value, 0) / period);
  }
  return result;
}

export function resampleCandles(prices: DailyPrice[], timeframe: Timeframe): DailyPrice[] {
  if (timeframe === "day") return prices;
  const groups = new Map<string, DailyPrice[]>();
  prices.forEach((price) => {
    const date = new Date(`${price.date}T00:00:00Z`);
    let key: string;
    if (timeframe === "month") key = price.date.slice(0, 7);
    else {
      const monday = new Date(date);
      const day = date.getUTCDay() || 7;
      monday.setUTCDate(date.getUTCDate() - day + 1);
      key = monday.toISOString().slice(0, 10);
    }
    groups.set(key, [...(groups.get(key) ?? []), price]);
  });
  return [...groups.values()].map((items) => {
    const first = items[0];
    const last = items.at(-1)!;
    return {
      symbol: first.symbol, name: first.name, date: last.date, open: first.open,
      high: Math.max(...items.map((item) => item.high)),
      low: Math.min(...items.map((item) => item.low)),
      close: last.close,
      volume: items.reduce((sum, item) => sum + item.volume, 0),
    };
  });
}

export function latestTechnical(prices: DailyPrice[]) {
  const indicators = calculateIndicators(prices);
  const kd = calculateKD(prices);
  const rsi = calculateRSI(prices);
  const adx = calculateADX(prices);
  return {
    indicator: indicators.at(-1)!,
    previousIndicator: indicators.at(-2)!,
    kd: kd.at(-1)!,
    previousKD: kd.at(-2)!,
    rsi: rsi.at(-1) ?? null,
    adx: adx.at(-1) ?? null,
  };
}
