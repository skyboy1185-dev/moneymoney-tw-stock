import type { MarketContext, RankingRow } from "@/lib/market-types";
import { allRobots } from "@/robots";
import { stockCatalog, stockService } from "@/services/stock-service";
import { getOfficialQuote, mergeOfficialQuote } from "@/services/market-data/official-quote-provider";

export class AutoScreeningEngine {
  async scan(market: MarketContext, activeStrategyIds: string[]): Promise<RankingRow[]> {
    const active = allRobots.filter((robot) => activeStrategyIds.includes(robot.id));
    const now = new Date().toISOString();
    const candidates: (RankingRow | null)[] = await Promise.all(stockCatalog.map(async (meta): Promise<RankingRow | null> => {
      const baseStock = await stockService.getStock(meta.symbol);
      if (!baseStock || baseStock.prices.length < 240 || !active.length) return null;
      const officialQuote = await getOfficialQuote(baseStock.meta);
      const stock = officialQuote ? mergeOfficialQuote(baseStock, officialQuote) : baseStock;
      const latest = stock.prices.at(-1)!;
      const previous = stock.prices.at(-2)!;
      const indicator = stock.indicators.at(-1)!;
      const evaluated = active.map((robot) => ({ robot, result: robot.analyze(stock, market) }))
        .sort((a, b) => b.result.score - a.result.score)[0];
      const trendScore = indicator.ma20 && indicator.ma60
        ? (latest.close > indicator.ma20 ? 14 : 5) + (indicator.ma20 > indicator.ma60 ? 11 : 4) : 5;
      const momentumScore = indicator.histogram != null ? Math.min(20, 10 + Math.sign(indicator.histogram) * 6 + (indicator.macdSignal === "entry" ? 4 : 0)) : 5;
      const volumeAverage = stock.prices.slice(-20).reduce((sum, item) => sum + item.volume, 0) / 20;
      const volumeScore = Math.min(15, 8 + (latest.volume / volumeAverage - 1) * 8);
      const marketScore = Math.max(0, 5 + market.score / 20);
      const distanceMa20 = indicator.ma20 ? Math.abs((latest.close - indicator.ma20) / indicator.ma20) * 100 : 20;
      const riskScore = Math.max(0, 10 - Math.max(0, distanceMa20 - 7));
      const total = Math.round(Math.max(0, Math.min(100,
        trendScore + momentumScore + volumeScore + evaluated.result.score * .2 + marketScore + riskScore,
      )));
      const risks = [...evaluated.result.risks];
      if (distanceMa20 > 12) risks.push("距離 MA20 過遠");
      if (latest.volume < 500_000) risks.push("成交量偏低");
      const reasons = [
        ...evaluated.result.reasons,
        indicator.macdSignal === "entry" ? "MACD 今日翻紅" : indicator.histogram != null && indicator.histogram >= 0 ? "MACD 柱狀體位於零軸上方" : "MACD 動能仍待確認",
        indicator.ma20 && latest.close > indicator.ma20 ? "股價站上 MA20" : "股價接近關鍵均線",
        `符合${evaluated.robot.name.replace(" Bot", "")}策略 ${evaluated.result.score}%`,
        `目前大盤多空力道 ${market.score >= 0 ? "+" : ""}${market.score}`,
      ].filter((item, index, array) => array.indexOf(item) === index).slice(0, 5);
      return {
        rank: 0, symbol: meta.symbol, name: meta.name, price: latest.close,
        changePercent: ((latest.close - previous.close) / previous.close) * 100,
        volume: latest.volume, strategyId: evaluated.robot.id, strategyName: evaluated.robot.name,
        score: total, strategyFit: evaluated.result.score, marketDirection: market.direction,
        macdState: indicator.macdSignal === "entry" ? "今日翻紅" : indicator.macdSignal === "exit" ? "今日翻綠" : indicator.histogram != null && indicator.histogram >= 0 ? "紅柱" : "綠柱",
        signalStatus: market.marketOpen ? "temporary" : "confirmed",
        triggeredAt: now, reasons, riskTags: risks,
        movement: "new", updatedAt: now,
        priceSource: officialQuote?.source ?? "Mock Provider",
        priceDate: officialQuote?.date ?? latest.date,
        priceTime: officialQuote?.time ?? "展示資料",
        isOfficialPrice: Boolean(officialQuote),
      } satisfies RankingRow;
    }));
    return candidates.filter((row): row is RankingRow => row !== null)
      .filter((row) => row.score >= 55 || row.strategyId === "exit-warning")
      .sort((a, b) => b.score - a.score)
      .slice(0, 12)
      .map((row, index) => ({ ...row, rank: index + 1 }));
  }
}

export const autoScreeningEngine = new AutoScreeningEngine();
