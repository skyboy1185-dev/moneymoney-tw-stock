import type { MarketContext, RankingRow } from "@/lib/market-types";
import { allRobots } from "@/robots";
import { thematicStockCatalog } from "@/services/stock-service";
import { getOfficialQuotes } from "@/services/market-data/official-quote-provider";
import { buildOfficialStockPayload } from "@/services/market-data/official-history-provider";
import { calculateRSI } from "@/lib/technical";
import { assessKeyPrice } from "@/lib/key-price";

function taipeiDate() {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Taipei", year: "numeric", month: "2-digit", day: "2-digit",
  }).format(new Date());
}

function round(value: number) {
  return Math.round(value * 100) / 100;
}

export class AutoScreeningEngine {
  async scan(market: MarketContext, activeStrategyIds: string[]): Promise<RankingRow[]> {
    const active = allRobots.filter((robot) => activeStrategyIds.includes(robot.id));
    const now = new Date().toISOString();
    const officialQuotes = await getOfficialQuotes(thematicStockCatalog);
    const candidates: (RankingRow | null)[] = await Promise.all(thematicStockCatalog.map(async (meta): Promise<RankingRow | null> => {
      const officialQuote = officialQuotes.get(meta.symbol) ?? null;
      if (!officialQuote || !active.length) return null;
      let stock;
      try {
        stock = await buildOfficialStockPayload(meta, officialQuote);
      } catch {
        return null;
      }
      if (stock.prices.length < 240 || stock.dataMode !== "official_history") return null;
      const latest = stock.prices.at(-1)!;
      const previous = stock.prices.at(-2)!;
      const indicator = stock.indicators.at(-1)!;
      const rsi = calculateRSI(stock.prices).at(-1) ?? null;
      const evaluations = active.map((robot) => ({ robot, result: robot.analyze(stock, market) }))
        .sort((a, b) => b.result.score - a.result.score
          || Number(b.robot.supportedRegimes.includes(market.direction))
          - Number(a.robot.supportedRegimes.includes(market.direction))
          || Number(a.result.risks.length) - Number(b.result.risks.length));
      const evaluated = evaluations[0];
      const keyPrice = assessKeyPrice(stock.prices);
      const trendScore = indicator.ma20 && indicator.ma60
        ? (latest.close > indicator.ma20 ? 14 : 5) + (indicator.ma20 > indicator.ma60 ? 11 : 4) : 5;
      const momentumScore = indicator.histogram != null ? Math.min(20, 10 + Math.sign(indicator.histogram) * 6 + (indicator.macdSignal === "entry" ? 4 : 0)) : 5;
      const volumeAverage = stock.prices.slice(-20).reduce((sum, item) => sum + item.volume, 0) / 20;
      const volumeScore = Math.max(0, Math.min(15, 8 + (latest.volume / volumeAverage - 1) * 8));
      const keyPriceScore = keyPrice.aboveKeyPrice
        ? latest.volume >= volumeAverage ? 8 : 6
        : 0;
      const marketScore = Math.max(0, 5 + market.score / 20);
      const distanceMa20 = indicator.ma20 ? Math.abs((latest.close - indicator.ma20) / indicator.ma20) * 100 : 20;
      const riskScore = Math.max(0, 10 - Math.max(0, distanceMa20 - 7));
      const marketFit = Math.round(Math.max(0, Math.min(100,
        65 + market.score * (evaluated.robot.supportedRegimes.includes(market.direction) ? .35 : -.35),
      )));
      const strategyScore = evaluated.result.score * .2;
      const scoreBreakdown = {
        trend: round(trendScore),
        momentum: round(momentumScore),
        volume: round(volumeScore),
        keyPrice: keyPriceScore,
        strategy: round(strategyScore),
        market: round(marketScore),
        risk: round(riskScore),
      };
      const total = Math.round(Math.max(0, Math.min(100,
        Object.values(scoreBreakdown).reduce((sum, value) => sum + value, 0),
      )));
      const risks = [...evaluated.result.risks];
      if (distanceMa20 > 12) risks.push("距離 MA20 過遠");
      if (latest.volume < 500_000) risks.push("成交量偏低");
      const trueRanges = stock.prices.slice(-14).map((price, index, values) => {
        const previousClose = index ? values[index - 1].close : price.open;
        return Math.max(price.high - price.low, Math.abs(price.high - previousClose), Math.abs(price.low - previousClose));
      });
      const atr = trueRanges.reduce((sum, value) => sum + value, 0) / Math.max(1, trueRanges.length);
      const entryMin = round(latest.close * .995);
      const entryMax = round(latest.close * 1.005);
      const stopLoss = round(Math.max(.01, latest.close - Math.max(atr * 1.5, latest.close * .025)));
      const perShareRisk = Math.max(.01, latest.close - stopLoss);
      const target1 = round(latest.close + perShareRisk * 1.5);
      const target2 = round(latest.close + perShareRisk * 2.5);
      const riskRewardRatio = round((target2 - latest.close) / perShareRisk);
      const spreadPercentage = officialQuote?.bestBid && officialQuote.bestAsk
        ? round((officialQuote.bestAsk - officialQuote.bestBid) / latest.close * 100)
        : null;
      const quoteFresh = Boolean(officialQuote?.isRealtime && officialQuote.date === taipeiDate());
      const turnover = latest.close * latest.volume;
      const hardRiskFailures: string[] = [];
      if (!officialQuote) hardRiskFailures.push("行情資料缺失");
      if (officialQuote?.source === "TWSE MIS 五檔參考價") {
        hardRiskFailures.push("目前為交易所五檔參考價，尚未取得最新成交價");
      }
      if (!quoteFresh) hardRiskFailures.push("行情時間過期");
      if (latest.volume < 500_000) hardRiskFailures.push("成交量不足");
      if (turnover < 50_000_000) hardRiskFailures.push("成交金額不足");
      if (Math.abs(distanceMa20) > 12) hardRiskFailures.push("距離 MA20 過遠");
      if ((rsi ?? 50) >= 80) hardRiskFailures.push("RSI 嚴重過熱");
      const upperShadow = latest.high - Math.max(latest.open, latest.close);
      if (upperShadow > Math.max(.01, latest.high - latest.low) * .55 && latest.volume > volumeAverage * 1.8) {
        hardRiskFailures.push("爆量長上影線");
      }
      const reasons = [
        keyPrice.keyPrice == null
          ? "關鍵價資料不足"
          : keyPrice.aboveKeyPrice
            ? `收盤站上 20 日關鍵價 ${keyPrice.keyPrice}`
            : `距離 20 日關鍵價 ${Math.abs(keyPrice.keyPriceDistancePct ?? 0).toFixed(2)}%`,
        ...evaluated.result.reasons,
        indicator.macdSignal === "entry" ? "MACD 今日翻紅" : indicator.histogram != null && indicator.histogram >= 0 ? "MACD 柱狀體位於零軸上方" : "MACD 動能仍待確認",
        indicator.ma20 && latest.close > indicator.ma20 ? "股價站上 MA20" : "股價接近關鍵均線",
        `符合${evaluated.robot.name.replace(" Bot", "")}策略 ${evaluated.result.score}%`,
        `目前大盤多空力道 ${market.score >= 0 ? "+" : ""}${market.score}`,
      ].filter((item, index, array) => array.indexOf(item) === index).slice(0, 5);
      return {
        rank: 0, symbol: meta.symbol, name: meta.name, market: meta.market,
        industry: meta.industry, themes: meta.themes ?? [], price: latest.close,
        changePercent: ((latest.close - previous.close) / previous.close) * 100,
        volume: latest.volume, strategyId: evaluated.robot.id, strategyName: evaluated.robot.name,
        score: total, scoreBreakdown, strategyFit: evaluated.result.score,
        secondaryStrategies: evaluations.slice(1, 3).filter((item) => item.result.score >= 55).map((item) => item.robot.name),
        marketFit, healthScore: Math.round(Math.max(0, Math.min(100, total * .55 + evaluated.result.score * .45))),
        riskRewardRatio, entryMin, entryMax, stopLoss, target1, target2,
        turnover, spreadPercentage, distanceMa20, rsi,
        keyPrice: keyPrice.keyPrice,
        aboveKeyPrice: keyPrice.aboveKeyPrice,
        keyPriceDistancePct: keyPrice.keyPriceDistancePct,
        volumeQualified: latest.volume >= 500_000,
        liquidityQualified: latest.volume >= 500_000 && turnover >= 50_000_000,
        quoteFresh, hardRiskFailures, isFeatured: false,
        signalId: `ai-${latest.date}-${meta.symbol}-${evaluated.robot.id}`,
        marketDirection: market.direction,
        macdState: indicator.macdSignal === "entry" ? "今日翻紅" : indicator.macdSignal === "exit" ? "今日翻綠" : indicator.histogram != null && indicator.histogram >= 0 ? "紅柱" : "綠柱",
        signalStatus: market.marketOpen ? "temporary" : "confirmed",
        triggeredAt: now, reasons, riskTags: risks,
        movement: "new", updatedAt: now,
        priceSource: officialQuote?.source ?? "Mock Provider",
        priceDate: officialQuote?.date ?? latest.date,
        priceTime: officialQuote?.time ?? "展示資料",
        isOfficialPrice: Boolean(
          officialQuote
          && ["TWSE MIS", "TWSE OpenAPI", "TPEx OpenAPI"].includes(officialQuote.source),
        ),
      } satisfies RankingRow;
    }));
    return candidates.filter((row): row is RankingRow => row !== null)
      .filter((row) => row.score >= 55)
      .sort((a, b) => b.score - a.score)
      .slice(0, 12)
      .map((row, index) => ({ ...row, rank: index + 1 }));
  }
}

export const autoScreeningEngine = new AutoScreeningEngine();
