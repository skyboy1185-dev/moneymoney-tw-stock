import { calculateKD, resampleCandles } from "@/lib/technical";
import { calculateIndicators } from "@/lib/indicators";
import type { ManualScreenRow, ManualStrategy } from "@/lib/market-types";
import { stockCatalog } from "@/services/stock-service";
import { getOfficialQuotes } from "@/services/market-data/official-quote-provider";
import { buildOfficialStockPayload } from "@/services/market-data/official-history-provider";

export const MANUAL_STRATEGIES: ManualStrategy[] = [
  { id: "day-macd", name: "日 K MACD 翻紅，成交量大於 500 張", timeframe: "day", volumeThreshold: 500_000, requiresKD: false },
  { id: "week-macd", name: "週 K MACD 翻紅，成交量大於 3,500 張", timeframe: "week", volumeThreshold: 3_500_000, requiresKD: false },
  { id: "month-macd", name: "月 K MACD 翻紅，成交量大於 10,000 張", timeframe: "month", volumeThreshold: 10_000_000, requiresKD: false },
  { id: "day-macd-kd", name: "日 K MACD 翻紅且 KD 低檔金叉，成交量大於 500 張", timeframe: "day", volumeThreshold: 500_000, requiresKD: true },
  { id: "week-macd-kd", name: "週 K MACD 翻紅且 KD 低檔金叉，成交量大於 3,500 張", timeframe: "week", volumeThreshold: 3_500_000, requiresKD: true },
  { id: "month-macd-kd", name: "月 K MACD 翻紅且 KD 低檔金叉，成交量大於 10,000 張", timeframe: "month", volumeThreshold: 10_000_000, requiresKD: true },
];

type StrategySignalValues = {
  previousHistogram: number | null;
  currentHistogram: number | null;
  previousK: number | null;
  previousD: number | null;
  currentK: number | null;
  currentD: number | null;
  dailyVolumeShares: number;
};

export function matchesManualStrategy(strategy: ManualStrategy, values: StrategySignalValues): boolean {
  const macdFirstPositive = values.previousHistogram != null && values.currentHistogram != null
    && values.previousHistogram < 0 && values.currentHistogram > 0;
  const kdGoldenCrossBelow50 = values.previousK != null && values.previousD != null
    && values.currentK != null && values.currentD != null
    && values.previousK < values.previousD && values.currentK > values.currentD && values.currentK < 50;
  return macdFirstPositive
    && values.dailyVolumeShares > strategy.volumeThreshold
    && (!strategy.requiresKD || kdGoldenCrossBelow50);
}

export async function screenStocksByStrategy(strategyId: string): Promise<ManualScreenRow[]> {
  const strategy = MANUAL_STRATEGIES.find((item) => item.id === strategyId);
  if (!strategy) throw new Error("不存在的選股策略");
  const quotes = await getOfficialQuotes(stockCatalog);
  const results = await Promise.all(stockCatalog.map(async (meta) => {
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
      const kdPoint = kd.at(-1);
      const previousKdPoint = kd.at(-2);
      if (!dailyLatest || !dailyPrevious || !latest || !indicator || !previousIndicator || !kdPoint || !previousKdPoint) return null;
      if (!matchesManualStrategy(strategy, {
        previousHistogram: previousIndicator.histogram,
        currentHistogram: indicator.histogram,
        previousK: previousKdPoint.k,
        previousD: previousKdPoint.d,
        currentK: kdPoint.k,
        currentD: kdPoint.d,
        dailyVolumeShares: dailyLatest.volume,
      })) return null;
      return {
        rank: 0, symbol: meta.symbol, name: meta.name, market: meta.market,
        price: dailyLatest.close, changePercent: ((dailyLatest.close - dailyPrevious.close) / dailyPrevious.close) * 100,
        volume: dailyLatest.volume, timeframe: strategy.timeframe,
        dif: indicator.dif, signal: indicator.signal, histogram: indicator.histogram,
        k: kdPoint.k, d: kdPoint.d, signalDate: latest.date,
      } satisfies ManualScreenRow;
    } catch {
      return null;
    }
  }));
  return results.filter((row): row is ManualScreenRow => row !== null)
    .sort((a, b) => (b.histogram ?? -Infinity) - (a.histogram ?? -Infinity))
    .map((row, index) => ({ ...row, rank: index + 1 }));
}
