import { calculateKD, resampleCandles } from "@/lib/technical";
import { calculateThreePeriodDeductionSignal } from "@/lib/deduction-signals";
import { calculateIndicators } from "@/lib/indicators";
import { MANUAL_STRATEGIES } from "@/lib/manual-strategies";
import type { ManualScreenRow, ManualStrategy } from "@/lib/market-types";
import type { KDPoint } from "@/lib/market-types";
import type { DailyPrice, StockMeta } from "@/lib/types";
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

export interface MultiMovingAverageUpSignal {
  ma5: number;
  ma10: number;
  ma20: number;
  ma60: number | null;
  ma5SlopePercent: number;
  ma10SlopePercent: number;
  ma20SlopePercent: number;
  projectedMa5: number;
  projectedMa10: number;
  projectedMa20: number;
  nextDayUpMinimumClose: number;
  continuationBufferPercent: number;
  bullishAlignment: boolean;
  matches: boolean;
}

const round = (value: number, digits = 4) => {
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
};

export function isShortTermBullishAlignment(
  price: number,
  ma5: number | null,
  ma10: number | null,
  ma20: number | null,
  ma60: number | null,
): boolean {
  return ma5 != null && ma10 != null && ma20 != null && ma60 != null
    && price > ma5
    && price > ma60
    && ma5 > ma10
    && ma10 > ma20;
}

/**
 * Confirm that MA5/10/20 are rising now and would still rise on the next
 * session if the next close stayed at the latest known close. The projection
 * is a transparent moving-average deduction calculation, not a price forecast.
 */
export function calculateMultiMovingAverageUpSignal(
  candles: DailyPrice[],
  asOfDate?: string | null,
): MultiMovingAverageUpSignal | null {
  const effective = asOfDate ? candles.filter((candle) => candle.date <= asOfDate) : candles;
  if (effective.length < 21) return null;
  if (effective.some((candle) => !Number.isFinite(candle.close) || candle.close <= 0)) return null;
  const indicators = calculateIndicators(effective);
  const current = indicators.at(-1);
  const previous = indicators.at(-2);
  const latestClose = effective.at(-1)?.close;
  if (
    latestClose == null || latestClose <= 0
    || current?.ma5 == null || current.ma10 == null || current.ma20 == null
    || previous?.ma5 == null || previous.ma10 == null || previous.ma20 == null
  ) return null;

  const periods = [5, 10, 20] as const;
  const currentMas = [current.ma5, current.ma10, current.ma20] as const;
  const previousMas = [previous.ma5, previous.ma10, previous.ma20] as const;
  const outgoingCloses = periods.map((period) => effective[effective.length - period].close);
  const slopes = currentMas.map((value, index) => round(((value - previousMas[index]) / previousMas[index]) * 100, 4));
  const projected = currentMas.map((value, index) => round(
    value + (latestClose - outgoingCloses[index]) / periods[index],
    4,
  ));
  const nextDayUpMinimumClose = Math.max(...outgoingCloses);
  const continuationBufferPercent = round(
    ((latestClose - nextDayUpMinimumClose) / nextDayUpMinimumClose) * 100,
    2,
  );
  return {
    ma5: current.ma5,
    ma10: current.ma10,
    ma20: current.ma20,
    ma60: current.ma60,
    ma5SlopePercent: slopes[0],
    ma10SlopePercent: slopes[1],
    ma20SlopePercent: slopes[2],
    projectedMa5: projected[0],
    projectedMa10: projected[1],
    projectedMa20: projected[2],
    nextDayUpMinimumClose: round(nextDayUpMinimumClose),
    continuationBufferPercent,
    bullishAlignment: isShortTermBullishAlignment(
      latestClose,
      current.ma5,
      current.ma10,
      current.ma20,
      current.ma60,
    ),
    matches: currentMas.every((value, index) => value > previousMas[index])
      && outgoingCloses.every((outgoingClose) => latestClose > outgoingClose),
  };
}

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
  if (strategy.signalMode === "ma-multi-up") return false;
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

export function evaluateManualStrategy(
  meta: Pick<StockMeta, "symbol" | "name" | "market">,
  prices: DailyPrice[],
  strategy: ManualStrategy,
  signalStatus: "temporary" | "confirmed" = "confirmed",
  asOfDate?: string | null,
): ManualScreenRow | null {
  const effectivePrices = asOfDate ? prices.filter((price) => price.date <= asOfDate) : prices;
  const dailyLatest = effectivePrices.at(-1);
  const dailyPrevious = effectivePrices.at(-2);
  const candles = resampleCandles(effectivePrices, strategy.timeframe);
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
  const multiMaUp = strategy.signalMode === "ma-multi-up"
    ? calculateMultiMovingAverageUpSignal(candles)
    : null;
  if (!dailyLatest || !dailyPrevious || !latest) return null;
  if (strategy.signalMode === "ma-multi-up" && !multiMaUp?.matches) return null;
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
    && strategy.signalMode !== "ma-multi-up"
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
    && strategy.signalMode !== "ma-multi-up"
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
    ma5: multiMaUp?.ma5,
    ma10: multiMaUp?.ma10,
    ma20: multiMaUp?.ma20,
    ma60: multiMaUp?.ma60,
    ma5SlopePercent: multiMaUp?.ma5SlopePercent,
    ma10SlopePercent: multiMaUp?.ma10SlopePercent,
    ma20SlopePercent: multiMaUp?.ma20SlopePercent,
    projectedMa5: multiMaUp?.projectedMa5,
    projectedMa10: multiMaUp?.projectedMa10,
    projectedMa20: multiMaUp?.projectedMa20,
    nextDayUpMinimumClose: multiMaUp?.nextDayUpMinimumClose,
    continuationBufferPercent: multiMaUp?.continuationBufferPercent,
    bullishAlignment: multiMaUp?.bullishAlignment,
    signalStatus,
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
}

export async function screenStocksByStrategy(strategyId: string): Promise<ManualScreenRow[]> {
  const strategy = MANUAL_STRATEGIES.find((item) => item.id === strategyId);
  if (!strategy) throw new Error("不存在的選股策略");
  const quotes = await getOfficialQuotes(stockCatalog);
  const results = await Promise.all(stockCatalog.map(async (meta): Promise<ManualScreenRow | null> => {
    try {
      const stock = await buildOfficialStockPayload(meta, quotes.get(meta.symbol) ?? null);
      return evaluateManualStrategy(
        meta,
        stock.prices,
        strategy,
        stock.dataQuality?.status === "official_close" ? "confirmed" : "temporary",
      );
    } catch {
      return null;
    }
  }));
  return results.filter((row): row is ManualScreenRow => row !== null)
    .sort((a, b) => strategy.signalMode === "ma-multi-up"
      ? (b.continuationBufferPercent ?? -Infinity) - (a.continuationBufferPercent ?? -Infinity)
        || Math.min(b.ma5SlopePercent ?? -Infinity, b.ma10SlopePercent ?? -Infinity, b.ma20SlopePercent ?? -Infinity)
        - Math.min(a.ma5SlopePercent ?? -Infinity, a.ma10SlopePercent ?? -Infinity, a.ma20SlopePercent ?? -Infinity)
      : strategy.deductionDirection
      ? Math.abs(b.deductionGapPercent ?? 0) - Math.abs(a.deductionGapPercent ?? 0)
      : strategy.signalMode === "kd-below"
        ? Math.max(a.k ?? Infinity, a.d ?? Infinity) - Math.max(b.k ?? Infinity, b.d ?? Infinity)
      : strategy.signalMode === "kd-bullish-divergence" || strategy.signalMode === "kd-double-bullish-divergence"
        ? (b.divergenceStrength ?? -Infinity) - (a.divergenceStrength ?? -Infinity)
      : (b.histogram ?? -Infinity) - (a.histogram ?? -Infinity))
    .map((row, index) => ({ ...row, rank: index + 1 }));
}
