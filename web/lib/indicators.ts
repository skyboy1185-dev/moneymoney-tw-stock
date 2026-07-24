import type { DailyPrice, MacdSignalType, TechnicalIndicator } from "./types";

export interface MACDPoint {
  date: string;
  dif: number;
  signal: number;
  histogram: number;
}

function round(value: number): number {
  return Math.round(value * 10000) / 10000;
}

export function calculateEMA(values: number[], period: number): number[] {
  if (!values.length) return [];
  const multiplier = 2 / (period + 1);
  const result = [values[0]];
  for (let index = 1; index < values.length; index += 1) {
    result.push(values[index] * multiplier + result[index - 1] * (1 - multiplier));
  }
  return result;
}

export function calculateMACD(
  prices: Pick<DailyPrice, "date" | "close">[],
  fastPeriod = 12,
  slowPeriod = 26,
  signalPeriod = 9,
): MACDPoint[] {
  if (!prices.length) return [];
  const closes = prices.map((price) => price.close);
  const fast = calculateEMA(closes, fastPeriod);
  const slow = calculateEMA(closes, slowPeriod);
  const dif = fast.map((value, index) => value - slow[index]);
  const signal = calculateEMA(dif, signalPeriod);
  return prices.map((price, index) => ({
    date: price.date,
    dif: round(dif[index]),
    signal: round(signal[index]),
    histogram: round(dif[index] - signal[index]),
  }));
}

export function generateMACDSignals(
  macd: Pick<MACDPoint, "date" | "histogram">[],
): { date: string; macdSignal: MacdSignalType }[] {
  return macd.map((point, index) => {
    if (index === 0) return { date: point.date, macdSignal: null };
    const previous = macd[index - 1].histogram;
    const current = point.histogram;
    if (previous < 0 && current >= 0) return { date: point.date, macdSignal: "entry" };
    if (previous >= 0 && current < 0) return { date: point.date, macdSignal: "exit" };
    return { date: point.date, macdSignal: null };
  });
}

export function calculateSMA(values: number[], period: number): (number | null)[] {
  let rolling = 0;
  return values.map((value, index) => {
    rolling += value;
    if (index >= period) rolling -= values[index - period];
    return index >= period - 1 ? round(rolling / period) : null;
  });
}

export function calculateIndicators(prices: DailyPrice[]): TechnicalIndicator[] {
  const closes = prices.map((price) => price.close);
  const periods = [5, 10, 20, 30, 60, 120, 240] as const;
  const averages = Object.fromEntries(
    periods.map((period) => [period, calculateSMA(closes, period)]),
  ) as Record<(typeof periods)[number], (number | null)[]>;
  const macd = calculateMACD(prices);
  const signals = generateMACDSignals(macd);

  return prices.map((price, index) => ({
    date: price.date,
    ma5: averages[5][index],
    ma10: averages[10][index],
    ma20: averages[20][index],
    ma30: averages[30][index],
    ma60: averages[60][index],
    ma120: averages[120][index],
    ma240: averages[240][index],
    dif: macd[index]?.dif ?? null,
    signal: macd[index]?.signal ?? null,
    histogram: macd[index]?.histogram ?? null,
    macdSignal: signals[index]?.macdSignal ?? null,
  }));
}
