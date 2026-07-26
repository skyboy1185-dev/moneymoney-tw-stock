import { analyzeTechnicalData } from "./technical-analysis";
import { calculateKD, calculateRSI } from "./technical";
import type { DailyPrice, TechnicalIndicator } from "./types";

export interface PowerScoreContext {
  foreignBuy?: boolean;
  investmentTrustBuy?: boolean;
  majorPlayerBuy?: boolean;
  institutionalBuy?: boolean;
  marginHealthy?: boolean;
  institutionalBuyStreak?: boolean;
  sectorTopThree?: boolean;
  marketAligned?: boolean;
}

export interface PowerScoreSection {
  name: string;
  score: number;
  maxScore: number;
}

export interface PriceZone {
  min: number;
  max: number;
}

export interface PowerScoreResult {
  healthScore: number;
  powerValue: number;
  stars: number;
  starLabel: string;
  support: number | null;
  resistance: number | null;
  suggestion: string;
  buyPoint: PriceZone | null;
  addPoint: number | null;
  stopLoss: number | null;
  takeProfit: number | null;
  isBreakout: boolean;
  isBullAttack: boolean;
  canBuy: boolean;
  canAdd: boolean;
  needsTakeProfit: boolean;
  needsStopLoss: boolean;
  deductions: string[];
  highlights: string[];
  sections: PowerScoreSection[];
  dataCoverage: number;
  quoteDate: string;
}

function finite(value: number | null | undefined): value is number {
  return value != null && Number.isFinite(value);
}

function average(values: number[]): number | null {
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
}

function round(value: number, digits = 2) {
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

export function calculatePowerScore(
  prices: DailyPrice[],
  indicators: TechnicalIndicator[],
  context: PowerScoreContext = {},
): PowerScoreResult {
  const latest = prices.at(-1);
  const previous = prices.at(-2);
  const current = indicators.at(-1);
  const previousIndicator = indicators.at(-2);
  if (!latest || !previous || !current || prices.length < 120 || indicators.length !== prices.length) {
    return {
      healthScore: 0, powerValue: 0, stars: 1, starLabel: "★☆☆☆☆",
      support: null, resistance: null, suggestion: "資料不足，暫不提供操作結論。",
      buyPoint: null, addPoint: null, stopLoss: null, takeProfit: null,
      isBreakout: false, isBullAttack: false, canBuy: false, canAdd: false,
      needsTakeProfit: false, needsStopLoss: false,
      deductions: ["歷史資料不足 120 個交易日，無法完成馬力評分。"],
      highlights: [], sections: [], dataCoverage: 0, quoteDate: latest?.date ?? "",
    };
  }

  const deductions: string[] = [];
  const highlights: string[] = [];
  const sections: PowerScoreSection[] = [];
  let availablePoints = 75;
  const scoreSection = (name: string, maxScore: number, checks: Array<{
    matched: boolean | undefined;
    points: number;
    pass: string;
    fail: string;
  }>) => {
    let score = 0;
    for (const check of checks) {
      if (check.matched === true) {
        score += check.points;
        highlights.push(check.pass);
      } else if (check.matched === undefined) {
        deductions.push(`${check.fail}（資料未串接，未取得 ${check.points} 分）`);
      } else {
        deductions.push(`${check.fail}（-${check.points}）`);
      }
    }
    sections.push({ name, score: round(score, 1), maxScore });
    return score;
  };

  const closes = prices.map((price) => price.close);
  const volumes = prices.map((price) => price.volume);
  const volumeMa5 = average(volumes.slice(-5));
  const volumeMa20 = average(volumes.slice(-20));
  const previous20High = Math.max(...prices.slice(-21, -1).map((price) => price.high));
  const previous60High = Math.max(...prices.slice(-61, -1).map((price) => price.high));
  const ma20Past = indicators.at(-6)?.ma20;
  const ma60Past = indicators.at(-11)?.ma60;
  const histogramIncreasing = finite(current.histogram) && finite(previousIndicator?.histogram)
    && current.histogram > previousIndicator.histogram;
  const macdGoldenCross = finite(current.dif) && finite(current.signal)
    && finite(previousIndicator?.dif) && finite(previousIndicator?.signal)
    && previousIndicator.dif <= previousIndicator.signal && current.dif > current.signal;
  const breakout20 = latest.close > previous20High;
  const breakout60 = latest.close > previous60High;
  const volumeRatio20 = volumeMa20 ? latest.volume / volumeMa20 : 1;
  const volumeRatio5 = volumeMa5 ? latest.volume / volumeMa5 : 1;
  const priceUp = latest.close > previous.close;
  const consolidation = Math.abs((latest.close - previous.close) / previous.close) < 0.02;
  const allMaValues = [current.ma5, current.ma10, current.ma20, current.ma60, current.ma120].filter(finite);
  const aboveAllMa = allMaValues.length === 5 && allMaValues.every((ma) => latest.close > ma);
  const bullishAlignment = finite(current.ma5) && finite(current.ma10) && finite(current.ma20) && finite(current.ma60)
    && current.ma5 > current.ma10 && current.ma10 > current.ma20 && current.ma20 > current.ma60;
  const rsi = calculateRSI(prices).at(-1);
  const kd = calculateKD(prices);
  const latestKD = kd.at(-1);
  const previousKD = kd.at(-2);
  const kdGoldenCross = finite(latestKD?.k) && finite(latestKD?.d) && finite(previousKD?.k) && finite(previousKD?.d)
    && previousKD.k <= previousKD.d && latestKD.k > latestKD.d;

  let totalScore = 0;
  totalScore += scoreSection("趨勢", 25, [
    { matched: finite(current.ma5) && latest.close > current.ma5, points: 3, pass: "收盤站上 MA5", fail: "收盤尚未站上 MA5" },
    { matched: finite(current.ma5) && finite(current.ma10) && current.ma5 > current.ma10, points: 3, pass: "MA5 大於 MA10", fail: "MA5 尚未高於 MA10" },
    { matched: finite(current.ma10) && finite(current.ma20) && current.ma10 > current.ma20, points: 3, pass: "MA10 大於 MA20", fail: "MA10 尚未高於 MA20" },
    { matched: finite(current.ma20) && finite(current.ma60) && current.ma20 > current.ma60, points: 3, pass: "MA20 大於 MA60", fail: "MA20 尚未高於 MA60" },
    { matched: finite(current.ma20) && finite(ma20Past) && current.ma20 > ma20Past, points: 3, pass: "MA20 斜率向上", fail: "MA20 尚未明確向上" },
    { matched: finite(current.ma60) && finite(ma60Past) && current.ma60 > ma60Past, points: 3, pass: "MA60 斜率向上", fail: "MA60 尚未明確向上" },
    { matched: breakout20, points: 3.5, pass: "創 20 日新高", fail: "尚未創 20 日新高" },
    { matched: breakout60, points: 3.5, pass: "創 60 日新高", fail: "尚未創 60 日新高" },
  ]);
  totalScore += scoreSection("MACD", 15, [
    { matched: finite(current.dif) && finite(current.signal) && current.dif > current.signal, points: 3, pass: "DIF 高於 DEA", fail: "DIF 尚未高於 DEA" },
    { matched: histogramIncreasing, points: 3, pass: "MACD 柱狀增加", fail: "MACD 柱狀未增加" },
    { matched: macdGoldenCross, points: 3, pass: "MACD 黃金交叉", fail: "MACD 今日未形成黃金交叉" },
    { matched: finite(current.dif) && finite(current.signal) && current.dif > 0 && current.signal > 0, points: 3, pass: "MACD 位於零軸以上", fail: "MACD 尚未站上零軸" },
    { matched: finite(current.histogram) && current.histogram > 0 && histogramIncreasing, points: 3, pass: "MACD 紅柱增加", fail: "MACD 紅柱未持續增加" },
  ]);
  totalScore += scoreSection("成交量", 15, [
    { matched: volumeMa5 != null && latest.volume > volumeMa5, points: 3, pass: "成交量高於 5 日均量", fail: "成交量未高於 5 日均量" },
    { matched: breakout20 && volumeRatio5 >= 1.2, points: 3, pass: "突破伴隨量能", fail: "尚未形成有效突破量" },
    { matched: priceUp && latest.volume > previous.volume, points: 3, pass: "量價齊揚", fail: "今日未形成量價齊揚" },
    { matched: volumeRatio20 <= 2.5, points: 3, pass: "成交量未達爆天量", fail: "成交量超過 20 日均量 2.5 倍，爆量風險升高" },
    { matched: consolidation && volumeMa20 != null && latest.volume < volumeMa20, points: 3, pass: "縮量整理", fail: "目前不是縮量整理型態" },
  ]);
  totalScore += scoreSection("均線", 10, [
    { matched: aboveAllMa, points: 5, pass: "股價站上所有主要均線", fail: "股價尚未站上所有主要均線" },
    { matched: bullishAlignment, points: 5, pass: "均線呈多頭排列", fail: "均線尚未形成完整多頭排列" },
  ]);
  totalScore += scoreSection("RSI", 5, [
    { matched: finite(rsi) && rsi >= 55 && rsi <= 75, points: 5, pass: "RSI 位於 55～75 強勢區", fail: finite(rsi) && rsi > 75 ? "RSI 超過 75，短線過熱" : "RSI 未位於 55～75 最佳區間" },
  ]);
  totalScore += scoreSection("KD", 5, [
    { matched: kdGoldenCross, points: 5, pass: "KD 今日黃金交叉", fail: "KD 今日未形成黃金交叉" },
  ]);

  const chipChecks = [
    [context.foreignBuy, "外資買超", "外資未買超"],
    [context.investmentTrustBuy, "投信買超", "投信未買超"],
    [context.majorPlayerBuy, "大額交易力道偏多", "大額交易力道未偏多"],
    [context.institutionalBuy, "法人合計買超", "法人合計未買超"],
    [context.marginHealthy, "融資變化健康", "融資變化不利"],
  ] as const;
  totalScore += scoreSection("籌碼", 10, chipChecks.map(([matched, pass, fail]) => ({ matched, points: 2, pass, fail })));
  totalScore += scoreSection("法人", 5, [
    { matched: context.institutionalBuyStreak, points: 5, pass: "法人連續買超", fail: "法人尚未連續買超" },
  ]);
  totalScore += scoreSection("族群熱度", 5, [
    { matched: context.sectorTopThree, points: 5, pass: "所屬族群位居今日前三強", fail: "所屬族群未進入今日前三強" },
  ]);
  totalScore += scoreSection("大盤方向", 5, [
    { matched: context.marketAligned, points: 5, pass: "加權、櫃買、期貨與台積電方向一致", fail: "四項市場方向尚未一致" },
  ]);

  const unavailableContextCount = Object.values(context).filter((value) => value !== undefined).length;
  availablePoints += unavailableContextCount === 0 ? 0 : (
    [context.foreignBuy, context.investmentTrustBuy, context.majorPlayerBuy, context.institutionalBuy, context.marginHealthy]
      .filter((value) => value !== undefined).length * 2
    + (context.institutionalBuyStreak !== undefined ? 5 : 0)
    + (context.sectorTopThree !== undefined ? 5 : 0)
    + (context.marketAligned !== undefined ? 5 : 0)
  );
  const healthScore = Math.round(clamp(totalScore, 0, 100));
  const powerValue = Math.round(healthScore / 100 * 17);
  const stars = healthScore >= 80 ? 5 : healthScore >= 65 ? 4 : healthScore >= 50 ? 3 : healthScore >= 35 ? 2 : 1;
  const technical = analyzeTechnicalData(prices, indicators);
  const support = technical.summary.support;
  const resistance = technical.summary.resistance;
  const baseSupport = [support, current.ma20, current.ma10].filter(finite).filter((value) => value <= latest.close * 1.03).sort((a, b) => b - a)[0];
  const stopLoss = round((finite(baseSupport) && baseSupport < latest.close ? baseSupport : latest.close * 0.95) * 0.98);
  const buyReference = finite(baseSupport) ? baseSupport : latest.close;
  const buyPoint = healthScore >= 50 ? { min: round(buyReference * 0.995), max: round(Math.min(latest.close, buyReference * 1.015)) } : null;
  const addPoint = finite(resistance) ? round(Math.max(resistance * 1.005, latest.close * 1.01)) : round(latest.close * 1.02);
  const riskPerShare = Math.max(latest.close - stopLoss, latest.close * 0.01);
  const takeProfit = round(Math.max(finite(resistance) ? resistance : 0, latest.close + riskPerShare * 2));
  const isBreakout = breakout20 && volumeRatio5 >= 1.2;
  const isBullAttack = healthScore >= 65 && isBreakout && finite(current.histogram) && current.histogram > 0 && histogramIncreasing;
  const needsStopLoss = latest.close <= stopLoss || (finite(support) && latest.close < support)
    || (finite(current.histogram) && current.histogram < 0 && finite(current.dif) && finite(current.signal) && current.dif < current.signal && latest.close < (current.ma20 ?? latest.close));
  const needsTakeProfit = (finite(rsi) && rsi > 75) || technical.summary.topDivergence
    || (finite(current.histogram) && current.histogram > 0 && finite(previousIndicator?.histogram) && current.histogram < previousIndicator.histogram && latest.close >= takeProfit * 0.98);
  const canBuy = !needsStopLoss && healthScore >= 65 && finite(rsi) && rsi <= 75 && volumeRatio20 <= 2.5;
  const canAdd = canBuy && healthScore >= 70 && isBreakout && !needsTakeProfit;
  let suggestion = "中性觀察，等待趨勢、量能與動能進一步確認。";
  if (needsStopLoss) suggestion = "技術結構轉弱，應優先執行停損或降低部位。";
  else if (needsTakeProfit) suggestion = "短線過熱或動能減速，可分批停利並提高保護。";
  else if (canAdd) suggestion = "多方攻擊成立，可等待突破確認後分批加碼。";
  else if (canBuy) suggestion = "馬力偏強，可在買點區分批布局，避免追價。";
  else if (healthScore >= 50) suggestion = "具部分多方條件，但資料或確認條件不足，先觀察。";
  else if (healthScore < 35) suggestion = "馬力偏弱，避免進場並留意支撐失守風險。";

  return {
    healthScore,
    powerValue,
    stars,
    starLabel: `${"★".repeat(stars)}${"☆".repeat(5 - stars)}`,
    support: finite(support) ? round(support) : null,
    resistance: finite(resistance) ? round(resistance) : null,
    suggestion,
    buyPoint,
    addPoint,
    stopLoss,
    takeProfit,
    isBreakout,
    isBullAttack,
    canBuy,
    canAdd,
    needsTakeProfit,
    needsStopLoss,
    deductions,
    highlights: highlights.slice(0, 10),
    sections,
    dataCoverage: Math.round(availablePoints),
    quoteDate: latest.date,
  };
}
