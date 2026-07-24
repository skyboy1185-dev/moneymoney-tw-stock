import { latestTechnical } from "@/lib/technical";
import type { MarketContext, MarketDirection, RobotResult } from "@/lib/market-types";
import type { StockPayload } from "@/lib/types";
import { BaseRobot } from "./BaseRobot";

type Facts = ReturnType<typeof facts>;
const average = (values: number[]) => values.reduce((sum, value) => sum + value, 0) / Math.max(1, values.length);

function facts(stock: StockPayload) {
  const prices = stock.prices;
  const latest = prices.at(-1)!;
  const previous = prices.at(-2)!;
  const { indicator, previousIndicator, kd, previousKD, rsi, adx } = latestTechnical(prices);
  const ma20Slope = indicator.ma20 && stock.indicators.at(-6)?.ma20
    ? ((indicator.ma20 - stock.indicators.at(-6)!.ma20!) / stock.indicators.at(-6)!.ma20!) * 100 : 0;
  const ma60Slope = indicator.ma60 && stock.indicators.at(-11)?.ma60
    ? ((indicator.ma60 - stock.indicators.at(-11)!.ma60!) / stock.indicators.at(-11)!.ma60!) * 100 : 0;
  const volume5 = average(prices.slice(-5).map((item) => item.volume));
  const volume20 = average(prices.slice(-20).map((item) => item.volume));
  const high20 = Math.max(...prices.slice(-21, -1).map((item) => item.high));
  const high60 = Math.max(...prices.slice(-61, -1).map((item) => item.high));
  const low20 = Math.min(...prices.slice(-20).map((item) => item.low));
  const distanceMa20 = indicator.ma20 ? ((latest.close - indicator.ma20) / indicator.ma20) * 100 : 99;
  return {
    latest, previous, indicator, previousIndicator, kd, previousKD, rsi: rsi ?? 50, adx: adx ?? 20,
    ma20Slope, ma60Slope, volume5, volume20, high20, high60, low20, distanceMa20,
    macdEntry: indicator.macdSignal === "entry",
    macdExit: indicator.macdSignal === "exit",
    negativeShrinking: indicator.histogram != null && previousIndicator.histogram != null && indicator.histogram < 0 && indicator.histogram > previousIndicator.histogram,
  };
}

function result(conditions: [boolean, string][], risks: string[] = [], threshold = .55): RobotResult {
  const matched = conditions.filter(([condition]) => condition);
  const score = Math.round((matched.length / conditions.length) * 100);
  return { matched: score >= threshold * 100, score, reasons: matched.map(([, reason]) => reason).slice(0, 5), risks };
}

abstract class FactsRobot extends BaseRobot {
  protected withFacts(stock: StockPayload, market: MarketContext, evaluator: (fact: Facts) => RobotResult) {
    return evaluator(facts(stock));
  }
}

export class SidewaysBreakoutRobot extends FactsRobot {
  id = "sideways-breakout"; name = "盤整突破 Bot";
  supportedRegimes: MarketDirection[] = ["sideways", "bull"];
  protected evaluate(stock: StockPayload, market: MarketContext) {
    return this.withFacts(stock, market, (f) => result([
      [f.adx < 25, "ADX 低於 25，原區間趨勢收斂"],
      [f.latest.close >= f.high20, "股價突破 20 日高點"],
      [f.macdEntry, "MACD 今日翻紅"],
      [f.latest.volume > f.volume5 * 1.5, "成交量大於 5 日均量 1.5 倍"],
      [f.indicator.ma20 != null && f.latest.close > f.indicator.ma20, "收盤站上 MA20"],
    ]));
  }
}

export class RangeReboundRobot extends FactsRobot {
  id = "range-rebound"; name = "區間回彈 Bot";
  supportedRegimes: MarketDirection[] = ["sideways", "bear"];
  protected evaluate(stock: StockPayload, market: MarketContext) {
    return this.withFacts(stock, market, (f) => result([
      [f.latest.close <= f.low20 * 1.05, "股價接近 20 日區間下緣"],
      [f.rsi < 40, "RSI 位於 40 以下"],
      [f.kd.goldenCross, "KD 低檔黃金交叉"],
      [f.negativeShrinking, "MACD 負柱縮短"],
      [f.latest.close >= f.low20, "尚未跌破近期支撐"],
    ]));
  }
}

export class TrendStartRobot extends FactsRobot {
  id = "trend-start"; name = "波段起漲 Bot";
  supportedRegimes: MarketDirection[] = ["bull", "strong_bull"];
  protected evaluate(stock: StockPayload, market: MarketContext) {
    return this.withFacts(stock, market, (f) => result([
      [f.ma20Slope > 0, "MA20 斜率向上"],
      [f.indicator.ma20 != null && f.indicator.ma60 != null && f.indicator.ma20 > f.indicator.ma60, "MA20 高於 MA60"],
      [f.indicator.ma20 != null && f.latest.close > f.indicator.ma20, "股價站上 MA20"],
      [f.macdEntry, "MACD 今日翻紅"],
      [f.indicator.dif != null && f.previousIndicator.dif != null && f.indicator.dif > f.previousIndicator.dif, "DIF 持續向上"],
      [f.latest.volume > f.volume5, "成交量高於 5 日均量"],
      [f.rsi >= 50 && f.rsi <= 75, "RSI 位於健康動能區"],
    ]));
  }
}

export class BullPullbackRobot extends FactsRobot {
  id = "bull-pullback"; name = "多頭回檔 Bot";
  supportedRegimes: MarketDirection[] = ["bull", "strong_bull"];
  protected evaluate(stock: StockPayload, market: MarketContext) {
    return this.withFacts(stock, market, (f) => result([
      [f.ma20Slope > 0 && f.ma60Slope > 0, "MA20 與 MA60 同步向上"],
      [Math.abs(f.distanceMa20) < 3, "股價距離 MA20 小於 3%"],
      [f.indicator.dif != null && f.indicator.dif > 0, "MACD 位於零軸上方"],
      [f.kd.goldenCross, "KD 黃金交叉"],
      [f.latest.volume >= f.volume5, "反彈成交量回升"],
    ]));
  }
}

export class StrongBreakoutRobot extends FactsRobot {
  id = "strong-breakout"; name = "強勢突破 Bot";
  supportedRegimes: MarketDirection[] = ["strong_bull"];
  protected evaluate(stock: StockPayload, market: MarketContext) {
    return this.withFacts(stock, market, (f) => result([
      [f.indicator.ma5 != null && f.indicator.ma10 != null && f.indicator.ma20 != null && f.indicator.ma60 != null && f.indicator.ma5 > f.indicator.ma10 && f.indicator.ma10 > f.indicator.ma20 && f.indicator.ma20 > f.indicator.ma60, "MA5 > MA10 > MA20 > MA60"],
      [f.latest.close >= f.high60, "股價創 60 日新高"],
      [f.latest.volume > f.volume20 * 1.5, "成交量大於 20 日均量 1.5 倍"],
      [f.adx > 25, "ADX 大於 25"],
      [f.indicator.dif != null && f.indicator.dif > 0, "MACD 位於零軸上方"],
      [f.distanceMa20 < 12, "股價未過度偏離 MA20"],
    ], f.distanceMa20 > 10 ? ["距離 MA20 偏遠"] : []));
  }
}

export class BearReboundRobot extends FactsRobot {
  id = "bear-rebound"; name = "空頭反彈 Bot";
  supportedRegimes: MarketDirection[] = ["bear", "strong_bear"];
  protected evaluate(stock: StockPayload, market: MarketContext) {
    return this.withFacts(stock, market, (f) => result([
      [f.indicator.ma20 != null && f.indicator.ma60 != null && f.indicator.ma20 < f.indicator.ma60, "MA20 低於 MA60"],
      [f.rsi < 35, "RSI 低於 35"],
      [f.kd.goldenCross, "KD 低檔黃金交叉"],
      [f.macdEntry || f.negativeShrinking, "MACD 翻紅或負柱縮短"],
      [f.latest.low >= Math.min(...stock.prices.slice(-5, -1).map((item) => item.low)), "股價未再創短期新低"],
      [f.latest.volume > f.volume5, "成交量出現止跌訊號"],
    ], ["逆勢反彈策略，風險較高", "高風險"]));
  }
}

export class ExitWarningRobot extends FactsRobot {
  id = "exit-warning"; name = "出場警戒 Bot";
  supportedRegimes: MarketDirection[] = ["bear", "strong_bear", "transition"];
  protected evaluate(stock: StockPayload, market: MarketContext) {
    return this.withFacts(stock, market, (f) => result([
      [f.macdExit, "MACD 今日翻綠"],
      [f.indicator.ma20 != null && f.latest.close < f.indicator.ma20, "股價跌破 MA20"],
      [f.previousKD.k != null && f.previousKD.d != null && f.kd.k != null && f.kd.d != null && f.previousKD.k >= f.previousKD.d && f.kd.k < f.kd.d, "KD 死亡交叉"],
      [f.latest.close < f.low20, "跌破近期支撐"],
      [market.score < -20, "大盤多空力道轉弱"],
    ], ["出場警戒"], .2));
  }
}

export const allRobots = [
  new SidewaysBreakoutRobot(),
  new RangeReboundRobot(),
  new TrendStartRobot(),
  new BullPullbackRobot(),
  new StrongBreakoutRobot(),
  new BearReboundRobot(),
  new ExitWarningRobot(),
];
