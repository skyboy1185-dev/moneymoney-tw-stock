import { calculateKD, resampleCandles } from "@/lib/technical";
import { calculateIndicators } from "@/lib/indicators";
import type { ManualScreenRow, ManualStrategy } from "@/lib/market-types";
import { stockCatalog, stockService } from "@/services/stock-service";
import { getOfficialQuote, mergeOfficialQuote } from "@/services/market-data/official-quote-provider";

export const MANUAL_STRATEGIES: ManualStrategy[] = [
  { id: "day-macd", name: "日 K MACD 翻紅，成交量大於 500 張", timeframe: "day", volumeThreshold: 500_000, requiresKD: false },
  { id: "week-macd", name: "週 K MACD 翻紅，成交量大於 3,500 張", timeframe: "week", volumeThreshold: 3_500_000, requiresKD: false },
  { id: "month-macd", name: "月 K MACD 翻紅，成交量大於 10,000 張", timeframe: "month", volumeThreshold: 10_000_000, requiresKD: false },
  { id: "day-macd-kd", name: "日 K MACD 翻紅且 KD 低檔金叉，成交量大於 500 張", timeframe: "day", volumeThreshold: 500_000, requiresKD: true },
  { id: "week-macd-kd", name: "週 K MACD 翻紅且 KD 低檔金叉，成交量大於 3,500 張", timeframe: "week", volumeThreshold: 3_500_000, requiresKD: true },
  { id: "month-macd-kd", name: "月 K MACD 翻紅且 KD 低檔金叉，成交量大於 10,000 張", timeframe: "month", volumeThreshold: 10_000_000, requiresKD: true },
];

export async function screenStocksByStrategy(strategyId: string): Promise<ManualScreenRow[]> {
  const strategy = MANUAL_STRATEGIES.find((item) => item.id === strategyId);
  if (!strategy) throw new Error("不存在的選股策略");
  const results = await Promise.all(stockCatalog.map(async (meta) => {
    const baseStock = await stockService.getStock(meta.symbol);
    if (!baseStock) return null;
    const officialQuote = await getOfficialQuote(baseStock.meta);
    const stock = officialQuote ? mergeOfficialQuote(baseStock, officialQuote) : baseStock;
    const candles = resampleCandles(stock.prices, strategy.timeframe);
    const indicators = calculateIndicators(candles);
    const kd = calculateKD(candles);
    const latest = candles.at(-1);
    const previous = candles.at(-2);
    const indicator = indicators.at(-1);
    const kdPoint = kd.at(-1);
    if (!latest || !previous || !indicator || !kdPoint) return null;
    const macdEntry = indicator.macdSignal === "entry";
    const volumePassed = latest.volume > strategy.volumeThreshold;
    if (!macdEntry || !volumePassed || (strategy.requiresKD && !kdPoint.goldenCross)) return null;
    return {
      rank: 0, symbol: meta.symbol, name: meta.name, market: meta.market,
      price: latest.close, changePercent: ((latest.close - previous.close) / previous.close) * 100,
      volume: latest.volume, timeframe: strategy.timeframe,
      dif: indicator.dif, signal: indicator.signal, histogram: indicator.histogram,
      k: kdPoint.k, d: kdPoint.d, signalDate: latest.date,
    } satisfies ManualScreenRow;
  }));
  return results.filter((row): row is ManualScreenRow => row !== null)
    .sort((a, b) => (b.histogram ?? -Infinity) - (a.histogram ?? -Infinity))
    .map((row, index) => ({ ...row, rank: index + 1 }));
}
