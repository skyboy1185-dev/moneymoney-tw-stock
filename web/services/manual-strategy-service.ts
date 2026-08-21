import { calculateKD, resampleCandles } from "@/lib/technical";
import { calculateThreePeriodDeductionSignal } from "@/lib/deduction-signals";
import { calculateIndicators } from "@/lib/indicators";
import { MANUAL_STRATEGIES } from "@/lib/manual-strategies";
import type { ManualScreenRow, ManualStrategy } from "@/lib/market-types";
import type { KDPoint } from "@/lib/market-types";
import type { DailyPrice } from "@/lib/types";
import { stockCatalog } from "@/services/stock-service";
import { getOfficialQuotes } from "@/services/market-data/official-quote-provider";
import { buildOfficialStockPayload } from "@/services/market-data/official-history-provider";

export { MANUAL_STRATEGIES };
export { calculateThreePeriodDeductionSignal } from "@/lib/deduction-signals";

type StrategySignalValues = {
  twoPreviousHistogram: number | null;
  previousHistogram: number | null;
  currentHistogram: number | null;
  previousK: number | null;
  previousD: number | null;
  currentK: number | null;
  currentD: number | null;
  dailyVolumeShares: number;
};

export interface KdBullishDivergenceResult {
  previousDate: string;
  currentDate: string;
  previousLow: number;
  currentLow: number;
  previousK: number;
  previousD: number;
  currentK: number;
  currentD: number;
  strength: number;
  middleDate?: string;
  middleLow?: number;
  middleK?: number;
  middleD?: number;
}

export function detectSingleKdBullishDivergence(
  candles: DailyPrice[],
  kd: KDPoint[],
  lookback = 30,
): KdBullishDivergenceResult | null {
  if (candles.length < 12 || kd.length !== candles.length) return null;
  const recentStart = Math.max(8, candles.length - 3);
  const recentCandidates = candles
    .map((candle, index) => ({ candle, point: kd[index], index }))
    .slice(recentStart)
    .filter(({ point }) => point?.k != null && point?.d != null);
  if (!recentCandidates.length) return null;
  const recent = recentCandidates.reduce((lowest, item) =>
    item.candle.low < lowest.candle.low ? item : lowest);
  const priorEnd = recent.index - 4;
  const priorStart = Math.max(8, recent.index - lookback);
  if (priorEnd < priorStart) return null;
  const priorCandidates = candles
    .map((candle, index) => ({ candle, point: kd[index], index }))
    .slice(priorStart, priorEnd + 1)
    .filter(({ point }) => point?.k != null && point?.d != null);
  if (!priorCandidates.length) return null;
  const prior = priorCandidates.reduce((lowest, item) =>
    item.candle.low < lowest.candle.low ? item : lowest);
  const previousK = prior.point.k!;
  const previousD = prior.point.d!;
  const currentK = recent.point.k!;
  const currentD = recent.point.d!;
  const priceMadeLowerLow = recent.candle.low < prior.candle.low * 0.999;
  const bothInLowZone = Math.max(previousK, previousD, currentK, currentD) <= 30;
  const oscillatorMadeHigherLow = currentK >= previousK + 2 && currentD > previousD;
  if (!priceMadeLowerLow || !bothInLowZone || !oscillatorMadeHigherLow) return null;
  return {
    previousDate: prior.candle.date,
    currentDate: recent.candle.date,
    previousLow: prior.candle.low,
    currentLow: recent.candle.low,
    previousK,
    previousD,
    currentK,
    currentD,
    strength: Math.round((((currentK - previousK) + (currentD - previousD)) / 2) * 100) / 100,
  };
}

export function detectDoubleKdBullishDivergence(
  candles: DailyPrice[],
  kd: KDPoint[],
  lookback = 45,
): KdBullishDivergenceResult | null {
  if (candles.length < 18 || kd.length !== candles.length) return null;
  const candidates = candles.map((candle, index) => ({ candle, point: kd[index], index }));
  const recentCandidates = candidates.slice(Math.max(8, candles.length - 3))
    .filter(({ point }) => point?.k != null && point?.d != null);
  if (!recentCandidates.length) return null;
  const recent = recentCandidates.reduce((lowest, item) =>
    item.candle.low < lowest.candle.low ? item : lowest);
  const sequenceStart = Math.max(8, recent.index - lookback);
  const middleEnd = recent.index - 4;
  if (middleEnd < sequenceStart) return null;
  const middleCandidates = candidates.slice(sequenceStart, middleEnd + 1)
    .filter(({ point }) => point?.k != null && point?.d != null);
  if (!middleCandidates.length) return null;
  const middle = middleCandidates.reduce((lowest, item) =>
    item.candle.low < lowest.candle.low ? item : lowest);
  const previousEnd = middle.index - 4;
  if (previousEnd < sequenceStart) return null;
  const previousCandidates = candidates.slice(sequenceStart, previousEnd + 1)
    .filter(({ point }) => point?.k != null && point?.d != null);
  if (!previousCandidates.length) return null;
  const previous = previousCandidates.reduce((lowest, item) =>
    item.candle.low < lowest.candle.low ? item : lowest);

  const previousK = previous.point.k!;
  const previousD = previous.point.d!;
  const middleK = middle.point.k!;
  const middleD = middle.point.d!;
  const currentK = recent.point.k!;
  const currentD = recent.point.d!;
  const priceThreeLowerLows = previous.candle.low > middle.candle.low * 1.001
    && middle.candle.low > recent.candle.low * 1.001;
  const allInLowZone = Math.max(previousK, previousD, middleK, middleD, currentK, currentD) <= 30;
  const kdThreeHigherLows = middleK >= previousK + 2 && middleD > previousD
    && currentK >= middleK + 2 && currentD > middleD;
  if (!priceThreeLowerLows || !allInLowZone || !kdThreeHigherLows) return null;
  return {
    previousDate: previous.candle.date,
    middleDate: middle.candle.date,
    currentDate: recent.candle.date,
    previousLow: previous.candle.low,
    middleLow: middle.candle.low,
    currentLow: recent.candle.low,
    previousK,
    previousD,
    middleK,
    middleD,
    currentK,
    currentD,
    strength: Math.round((((currentK - previousK) + (currentD - previousD)) / 2) * 100) / 100,
  };
}

export function estimateMacdBarsToPositive(values: Pick<StrategySignalValues,
  "twoPreviousHistogram" | "previousHistogram" | "currentHistogram"
>): number | null {
  const older = values.twoPreviousHistogram;
  const previous = values.previousHistogram;
  const current = values.currentHistogram;
  if (older == null || previous == null || current == null || older >= 0 || previous >= 0 || current >= 0) return null;

  const olderImprovement = previous - older;
  const currentImprovement = current - previous;
  if (olderImprovement <= 0 || currentImprovement <= 0 || currentImprovement < olderImprovement * 0.5) return null;

  const averageImprovement = (olderImprovement + currentImprovement) / 2;
  if (averageImprovement <= 0) return null;
  return Math.round((Math.abs(current) / averageImprovement) * 10) / 10;
}

export function matchesManualStrategy(strategy: ManualStrategy, values: StrategySignalValues): boolean {
  if (strategy.deductionDirection) return false;
  if (strategy.signalMode === "kd-bullish-divergence" || strategy.signalMode === "kd-double-bullish-divergence") return false;
  if (strategy.signalMode === "kd-below") {
    const threshold = strategy.kdThreshold ?? 8;
    return values.currentK != null && values.currentD != null
      && values.currentK < threshold && values.currentD < threshold;
  }
  const macdFirstPositive = values.previousHistogram != null && values.currentHistogram != null
    && values.previousHistogram < 0 && values.currentHistogram > 0;
  const estimatedBarsToCross = estimateMacdBarsToPositive(values);
  const macdForecastPositive = estimatedBarsToCross != null && estimatedBarsToCross <= 2;
  const kdGoldenCrossBelow50 = values.previousK != null && values.previousD != null
    && values.currentK != null && values.currentD != null
    && values.previousK < values.previousD && values.currentK > values.currentD && values.currentK < 50;
  const matchesMacd = strategy.signalMode === "forecast" ? macdForecastPositive : macdFirstPositive;
  return matchesMacd
    && values.dailyVolumeShares > strategy.volumeThreshold
    && (!strategy.requiresKD || kdGoldenCrossBelow50);
}

export async function screenStocksByStrategy(strategyId: string): Promise<ManualScreenRow[]> {
  const strategy = MANUAL_STRATEGIES.find((item) => item.id === strategyId);
  if (!strategy) throw new Error("不存在的選股策略");
  const quotes = await getOfficialQuotes(stockCatalog);
  const results = await Promise.all(stockCatalog.map(async (meta): Promise<ManualScreenRow | null> => {
    try {
      const stock = await buildOfficialStockPayload(meta, quotes.get(meta.symbol) ?? null);
      const dailyLatest = stock.prices.at(-1);
      const dailyPrevious = stock.prices.at(-2);
      const candles = resampleCandles(stock.prices, strategy.timeframe);
      const indicators = calculateIndicators(candles);
      const kd = calculateKD(candles);
      const latest = candles.at(-1);
      const indicator = indicators.at(-1);
      const previousIndicator = indicators.at(-2);
      const twoPreviousIndicator = indicators.at(-3);
      const kdPoint = kd.at(-1);
      const previousKdPoint = kd.at(-2);
      const divergence = strategy.signalMode === "kd-bullish-divergence"
        ? detectSingleKdBullishDivergence(candles, kd, strategy.divergenceLookback ?? 30)
        : strategy.signalMode === "kd-double-bullish-divergence"
          ? detectDoubleKdBullishDivergence(candles, kd, strategy.divergenceLookback ?? 45)
          : null;
      if (!dailyLatest || !dailyPrevious || !latest) return null;
      const deduction = strategy.deductionDirection
        ? calculateThreePeriodDeductionSignal(
          candles.map((candle) => candle.close),
          strategy.maPeriod ?? 20,
        )
        : null;
      const deductionMatches = strategy.deductionDirection === "low"
        ? deduction?.matchesLow
        : strategy.deductionDirection === "high"
          ? deduction?.matchesHigh
          : false;
      if (strategy.deductionDirection && !deductionMatches) return null;
      const kdThresholdStrategy = strategy.signalMode === "kd-below";
      const kdDivergenceStrategy = strategy.signalMode === "kd-bullish-divergence"
        || strategy.signalMode === "kd-double-bullish-divergence";
      if (kdThresholdStrategy && !kdPoint) return null;
      if (kdDivergenceStrategy && !divergence) return null;
      if (!strategy.deductionDirection && !kdThresholdStrategy && !kdDivergenceStrategy
        && (!indicator || !previousIndicator || !kdPoint || !previousKdPoint)) return null;
      const signalValues = {
        twoPreviousHistogram: twoPreviousIndicator?.histogram ?? null,
        previousHistogram: previousIndicator?.histogram ?? null,
        currentHistogram: indicator?.histogram ?? null,
        previousK: previousKdPoint?.k ?? null,
        previousD: previousKdPoint?.d ?? null,
        currentK: kdPoint?.k ?? null,
        currentD: kdPoint?.d ?? null,
        dailyVolumeShares: dailyLatest.volume,
      };
      if (!strategy.deductionDirection && !kdDivergenceStrategy
        && !matchesManualStrategy(strategy, signalValues)) return null;
      return {
        rank: 0, symbol: meta.symbol, name: meta.name, market: meta.market,
        price: dailyLatest.close, changePercent: ((dailyLatest.close - dailyPrevious.close) / dailyPrevious.close) * 100,
        volume: dailyLatest.volume, timeframe: strategy.timeframe,
        dif: indicator?.dif ?? null, signal: indicator?.signal ?? null, histogram: indicator?.histogram ?? null,
        signalMode: strategy.signalMode,
        estimatedBarsToCross: strategy.signalMode === "forecast" ? estimateMacdBarsToPositive(signalValues) : null,
        maPeriod: deduction?.maPeriod,
        deductionValues: deduction?.deductionValues,
        deductionAverage: deduction?.deductionAverage,
        deductionGapPercent: deduction?.deductionGapPercent,
        projectedMaValues: deduction?.projectedMaValues,
        divergencePreviousDate: divergence?.previousDate,
        divergenceMiddleDate: divergence?.middleDate,
        divergencePreviousLow: divergence?.previousLow,
        divergenceMiddleLow: divergence?.middleLow,
        divergenceCurrentLow: divergence?.currentLow,
        divergenceStrength: divergence?.strength,
        k: divergence?.currentK ?? kdPoint?.k ?? null,
        d: divergence?.currentD ?? kdPoint?.d ?? null,
        signalDate: divergence?.currentDate ?? latest.date,
      } satisfies ManualScreenRow;
    } catch {
      return null;
    }
  }));
  return results.filter((row): row is ManualScreenRow => row !== null)
    .sort((a, b) => strategy.deductionDirection
      ? Math.abs(b.deductionGapPercent ?? 0) - Math.abs(a.deductionGapPercent ?? 0)
      : strategy.signalMode === "kd-below"
        ? Math.max(a.k ?? Infinity, a.d ?? Infinity) - Math.max(b.k ?? Infinity, b.d ?? Infinity)
      : strategy.signalMode === "kd-bullish-divergence" || strategy.signalMode === "kd-double-bullish-divergence"
        ? (b.divergenceStrength ?? -Infinity) - (a.divergenceStrength ?? -Infinity)
      : (b.histogram ?? -Infinity) - (a.histogram ?? -Infinity))
    .map((row, index) => ({ ...row, rank: index + 1 }));
}
