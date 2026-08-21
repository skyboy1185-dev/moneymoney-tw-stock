import { resampleCandles } from "./technical";
import type { Timeframe } from "./market-types";
import type { DailyPrice } from "./types";
import type { ThreeGatePrice } from "./three-gate-price";

export type DeductionDirection = "low" | "high";

export interface ThreePeriodDeductionSignal {
  maPeriod: number;
  currentClose: number;
  currentMa: number;
  deductionValues: [number, number, number];
  deductionAverage: number;
  deductionGapPercent: number;
  projectedMaValues: [number, number, number];
  matchesLow: boolean;
  matchesHigh: boolean;
}

export interface DeductionPosition {
  order: 1 | 2 | 3;
  date: string;
  value: number;
}

export interface DeductionSignalMatch {
  timeframe: Timeframe;
  direction: DeductionDirection;
  maPeriod: number;
  deductionValues: [number, number, number];
  deductionAverage: number;
  deductionGapPercent: number;
  projectedMaValues: [number, number, number];
  signalDate: string;
}

export interface StockDeductionSignals {
  symbol: string;
  currentPrice: number | null;
  previousClose: number | null;
  threeGate: ThreeGatePrice | null;
  matches: DeductionSignalMatch[];
  calculatedAt: string;
}

const round = (value: number, digits = 4) => {
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
};

/**
 * Inspect the next three values that will leave an N-period moving average.
 * Projected MA values assume the current close stays unchanged. This exposes
 * the mechanical deduction structure without presenting it as a price forecast.
 */
export function calculateThreePeriodDeductionSignal(
  closes: number[],
  maPeriod = 20,
): ThreePeriodDeductionSignal | null {
  if (!Number.isInteger(maPeriod) || maPeriod < 3 || closes.length < maPeriod) return null;
  const currentClose = closes.at(-1);
  if (currentClose == null || !Number.isFinite(currentClose) || currentClose <= 0) return null;
  const window = closes.slice(-maPeriod);
  if (window.some((value) => !Number.isFinite(value) || value <= 0)) return null;
  const deductionValues = window.slice(0, 3) as [number, number, number];
  if (deductionValues.length !== 3) return null;
  const deductionAverage = deductionValues.reduce((sum, value) => sum + value, 0) / 3;
  let rollingSum = window.reduce((sum, value) => sum + value, 0);
  const projected = deductionValues.map((deduction) => {
    rollingSum = rollingSum - deduction + currentClose;
    return round(rollingSum / maPeriod);
  }) as [number, number, number];
  return {
    maPeriod,
    currentClose,
    currentMa: round(window.reduce((sum, value) => sum + value, 0) / maPeriod),
    deductionValues: deductionValues.map((value) => round(value)) as [number, number, number],
    deductionAverage: round(deductionAverage),
    deductionGapPercent: round(((currentClose - deductionAverage) / currentClose) * 100, 2),
    projectedMaValues: projected,
    matchesLow: deductionValues.every((value) => value < currentClose),
    matchesHigh: deductionValues.every((value) => value > currentClose),
  };
}

/** Locate the three historical candles that will leave the moving average next. */
export function findThreePeriodDeductionPositions(
  prices: DailyPrice[],
  maPeriod = 20,
): DeductionPosition[] {
  const signal = calculateThreePeriodDeductionSignal(prices.map((price) => price.close), maPeriod);
  if (!signal) return [];
  const start = prices.length - maPeriod;
  return ([0, 1, 2] as const).map((offset) => ({
    order: (offset + 1) as 1 | 2 | 3,
    date: prices[start + offset].date,
    value: signal.deductionValues[offset],
  }));
}

export function findDeductionSignalMatches(
  prices: DailyPrice[],
  maPeriod = 20,
): DeductionSignalMatch[] {
  const timeframes: Timeframe[] = ["day", "week", "month"];
  return timeframes.flatMap((timeframe) => {
    const candles = resampleCandles(prices, timeframe);
    const signal = calculateThreePeriodDeductionSignal(candles.map((candle) => candle.close), maPeriod);
    const latest = candles.at(-1);
    if (!signal || !latest) return [];
    const direction: DeductionDirection | null = signal.matchesLow
      ? "low"
      : signal.matchesHigh
        ? "high"
        : null;
    if (!direction) return [];
    return [{
      timeframe,
      direction,
      maPeriod,
      deductionValues: signal.deductionValues,
      deductionAverage: signal.deductionAverage,
      deductionGapPercent: signal.deductionGapPercent,
      projectedMaValues: signal.projectedMaValues,
      signalDate: latest.date,
    }];
  });
}
