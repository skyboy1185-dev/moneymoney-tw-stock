import { calculateSMA } from "./indicators";
import type { DailyPrice, TechnicalIndicator } from "./types";

export type TrendLabel = "強勢多頭" | "多頭整理" | "盤整" | "空頭反彈" | "弱勢空頭";
export type VolumeStatus = "極度量縮" | "量縮" | "正常量" | "溫和放量" | "明顯放量" | "爆量";
export type CompositeSignal = "observe" | "entry" | "add" | "reduce" | "exit" | "neutral";

export interface VolumeAnalysisPoint {
  date: string;
  ma5: number | null;
  ma20: number | null;
  ratio5: number | null;
  ratio20: number | null;
  status: VolumeStatus;
}

export interface MacdAnalysisPoint {
  date: string;
  histogramChange: number | null;
  state: string;
  cross: "黃金交叉" | "死亡交叉" | "無交叉";
  expandingCount: number;
  shrinkingCount: number;
}

export interface TechnicalMarker {
  date: string;
  position: "aboveBar" | "belowBar";
  color: string;
  shape: "arrowUp" | "arrowDown" | "circle" | "square";
  text: string;
}

export interface CompositeTradeSignal {
  date: string;
  type: "entry" | "exit";
  price: number;
  reasons: string[];
}

export interface TechnicalSummary {
  trend: TrendLabel;
  healthScore: number;
  klineStatus: string;
  volumeStatus: string;
  volumeExplanation: string;
  macdStatus: string;
  support: number | null;
  resistance: number | null;
  operation: string;
  operationReasons: string[];
  risk: string;
  signal: CompositeSignal;
  topDivergence: boolean;
  bottomDivergence: boolean;
  dataSufficient: boolean;
}

export interface TechnicalAnalysis {
  summary: TechnicalSummary;
  volume: VolumeAnalysisPoint[];
  macd: MacdAnalysisPoint[];
  markers: TechnicalMarker[];
  tradeSignals: CompositeTradeSignal[];
}

function finite(value: number | null | undefined): value is number {
  return value != null && Number.isFinite(value);
}

function average(values: number[]): number | null {
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
}

function volumeStatus(ratio: number | null): VolumeStatus {
  if (ratio == null) return "正常量";
  if (ratio < 0.5) return "極度量縮";
  if (ratio < 0.8) return "量縮";
  if (ratio <= 1.2) return "正常量";
  if (ratio <= 1.6) return "溫和放量";
  if (ratio <= 2.5) return "明顯放量";
  return "爆量";
}

function localPivots(prices: DailyPrice[], kind: "high" | "low", radius = 2): number[] {
  const result: number[] = [];
  for (let index = radius; index < prices.length - radius; index += 1) {
    const value = prices[index][kind];
    const window = prices.slice(index - radius, index + radius + 1).map((price) => price[kind]);
    if (kind === "high" ? value === Math.max(...window) : value === Math.min(...window)) result.push(index);
  }
  return result;
}

function countHistogramMove(
  indicators: TechnicalIndicator[],
  index: number,
  direction: "expand" | "shrink",
): number {
  let count = 0;
  for (let cursor = index; cursor > 0; cursor -= 1) {
    const current = indicators[cursor].histogram;
    const previous = indicators[cursor - 1].histogram;
    if (!finite(current) || !finite(previous) || current * previous <= 0) break;
    const matched = direction === "expand"
      ? Math.abs(current) > Math.abs(previous)
      : Math.abs(current) < Math.abs(previous);
    if (!matched) break;
    count += 1;
  }
  return count;
}

function detectDivergence(prices: DailyPrice[], indicators: TechnicalIndicator[]) {
  const start = Math.max(0, prices.length - 80);
  const sliced = prices.slice(start);
  const lows = localPivots(sliced, "low").slice(-2).map((index) => index + start);
  const highs = localPivots(sliced, "high").slice(-2).map((index) => index + start);
  let bottom = false;
  let top = false;
  if (lows.length === 2) {
    const [first, second] = lows;
    const firstMacd = indicators[first]?.dif ?? indicators[first]?.histogram;
    const secondMacd = indicators[second]?.dif ?? indicators[second]?.histogram;
    bottom = prices[second].low < prices[first].low
      && finite(firstMacd) && finite(secondMacd) && secondMacd > firstMacd;
  }
  if (highs.length === 2) {
    const [first, second] = highs;
    const firstMacd = indicators[first]?.dif ?? indicators[first]?.histogram;
    const secondMacd = indicators[second]?.dif ?? indicators[second]?.histogram;
    top = prices[second].high > prices[first].high
      && finite(firstMacd) && finite(secondMacd) && secondMacd < firstMacd;
  }
  return { bottom, top, lowPivots: lows, highPivots: highs };
}

function trendLabel(
  prices: DailyPrice[],
  indicators: TechnicalIndicator[],
  volumeRatio: number | null,
): TrendLabel {
  const latest = prices.at(-1);
  const current = indicators.at(-1);
  if (!latest || !current || !finite(current.ma5) || !finite(current.ma10) || !finite(current.ma20)) return "盤整";
  const ma20Past = indicators.at(-6)?.ma20;
  const ma60 = current.ma60;
  const close = latest.close;
  const recentHigh = Math.max(...prices.slice(-20).map((price) => price.high));
  const recentLow = Math.min(...prices.slice(-20).map((price) => price.low));
  const ma20Up = finite(ma20Past) && current.ma20 > ma20Past;
  const ma20Down = finite(ma20Past) && current.ma20 < ma20Past;
  const maSpread = (Math.max(current.ma5, current.ma10, current.ma20) - Math.min(current.ma5, current.ma10, current.ma20)) / close;
  const nearHigh = close >= recentHigh * 0.97;
  if (close > current.ma5 && close > current.ma10 && close > current.ma20
    && current.ma5 > current.ma10 && current.ma10 > current.ma20 && ma20Up && nearHigh) return "強勢多頭";
  if (close > current.ma20 && close > recentLow && (maSpread < 0.03 || !ma20Down) && (volumeRatio ?? 1) <= 1.2) return "多頭整理";
  if (close < current.ma5 && close < current.ma10 && close < current.ma20
    && current.ma5 < current.ma10 && current.ma10 < current.ma20 && ma20Down) return "弱勢空頭";
  if ((close > current.ma5 || close > current.ma10) && (close < current.ma20 || (finite(ma60) && close < ma60)) && ma20Down) return "空頭反彈";
  if (maSpread < 0.025) return "盤整";
  return close >= current.ma20 ? "多頭整理" : "空頭反彈";
}

function buildMarkers(
  prices: DailyPrice[],
  indicators: TechnicalIndicator[],
  volumePoints: VolumeAnalysisPoint[],
  divergence: ReturnType<typeof detectDivergence>,
): TechnicalMarker[] {
  const markers: TechnicalMarker[] = [];
  const start = Math.max(1, prices.length - 160);
  const recentLows = divergence.lowPivots.slice(-1);
  const recentHighs = divergence.highPivots.slice(-1);
  for (const index of recentHighs) markers.push({ date: prices[index].date, position: "aboveBar", color: "#f4bd61", shape: "circle", text: "波段高" });
  for (const index of recentLows) markers.push({ date: prices[index].date, position: "belowBar", color: "#62a8ff", shape: "circle", text: "波段低" });
  for (let index = start; index < prices.length; index += 1) {
    const price = prices[index];
    const previous = prices[index - 1];
    const volumeRatio = volumePoints[index]?.ratio20 ?? 1;
    const bodyRatio = Math.abs(price.close - price.open) / previous.close;
    const prior = prices.slice(Math.max(0, index - 20), index);
    const priorHigh = prior.length ? Math.max(...prior.map((item) => item.high)) : price.high;
    const priorLow = prior.length ? Math.min(...prior.map((item) => item.low)) : price.low;
    if (price.low > previous.high * 1.002) markers.push({ date: price.date, position: "belowBar", color: "#9f8cff", shape: "square", text: "向上缺口" });
    else if (price.high < previous.low * 0.998) markers.push({ date: price.date, position: "aboveBar", color: "#9f8cff", shape: "square", text: "向下缺口" });
    if (volumeRatio >= 1.6 && bodyRatio >= 0.025) {
      const bullish = price.close > price.open;
      markers.push({ date: price.date, position: bullish ? "belowBar" : "aboveBar", color: bullish ? "#ff6467" : "#2bce7f", shape: bullish ? "arrowUp" : "arrowDown", text: bullish ? "大量長紅" : "大量長黑" });
    } else if (price.close > priorHigh && volumeRatio >= 1.2) {
      markers.push({ date: price.date, position: "belowBar", color: "#ff7678", shape: "arrowUp", text: "突破" });
    } else if (price.close < priorLow && volumeRatio >= 1.2) {
      markers.push({ date: price.date, position: "aboveBar", color: "#36cb83", shape: "arrowDown", text: "跌破" });
    }
  }
  if (divergence.bottom && recentLows.length) {
    const index = recentLows[0];
    markers.push({ date: prices[index].date, position: "belowBar", color: "#aa92ff", shape: "arrowUp", text: "底背離" });
  }
  if (divergence.top && recentHighs.length) {
    const index = recentHighs[0];
    markers.push({ date: prices[index].date, position: "aboveBar", color: "#ff9f43", shape: "arrowDown", text: "頂背離" });
  }
  return markers.slice(-16);
}

export function generateCompositeTradeSignals(
  prices: DailyPrice[],
  indicators: TechnicalIndicator[],
): CompositeTradeSignal[] {
  if (prices.length < 30 || indicators.length !== prices.length) return [];
  const volumeMa5 = calculateSMA(prices.map((price) => price.volume), 5);
  const signals: CompositeTradeSignal[] = [];
  let holding = false;
  for (let index = 20; index < prices.length; index += 1) {
    const price = prices[index];
    const previousPrice = prices[index - 1];
    const current = indicators[index];
    const previous = indicators[index - 1];
    if (!current || !previous || !finite(current.histogram) || !finite(previous.histogram)) continue;
    const priorFiveHigh = Math.max(...prices.slice(index - 5, index).map((item) => item.high));
    const priorTwentyLow = Math.min(...prices.slice(index - 20, index).map((item) => item.low));
    const histogramTurnedPositive = previous.histogram < 0 && current.histogram >= 0;
    const histogramTurnedNegative = previous.histogram >= 0 && current.histogram < 0;
    const goldenCross = finite(current.dif) && finite(current.signal) && finite(previous.dif) && finite(previous.signal)
      && previous.dif <= previous.signal && current.dif > current.signal;
    const deathCross = finite(current.dif) && finite(current.signal) && finite(previous.dif) && finite(previous.signal)
      && previous.dif >= previous.signal && current.dif < current.signal;
    const aboveShortMa = finite(current.ma5) && finite(current.ma10) && price.close > current.ma5 && price.close > current.ma10;
    const belowShortMa = (finite(current.ma10) && price.close < current.ma10)
      || (finite(current.ma20) && price.close < current.ma20);
    const breakout = price.close > priorFiveHigh;
    const supportBreak = price.close < priorTwentyLow;
    const averageVolume5 = volumeMa5[index];
    const previousMa5 = indicators[index - 2]?.ma5;
    const volumeConfirmed = finite(averageVolume5) && price.volume > averageVolume5 * 1.2;
    const downVolumeConfirmed = price.close < previousPrice.close && finite(averageVolume5) && price.volume > averageVolume5;
    const ma5TurningUp = finite(current.ma5) && finite(previousMa5) && current.ma5 > previousMa5;
    const macdBothDown = finite(current.dif) && finite(current.signal) && finite(previous.dif) && finite(previous.signal)
      && current.dif < previous.dif && current.signal < previous.signal;

    if (!holding) {
      const checks = [
        [histogramTurnedPositive, "MACD 柱狀由負翻正"],
        [goldenCross, "DIF 向上穿越 DEA"],
        [aboveShortMa, "收盤站上 MA5 與 MA10"],
        [breakout, "收盤突破近 5 日高點"],
        [volumeConfirmed, "成交量大於 5 日均量 1.2 倍"],
        [ma5TurningUp, "MA5 開始向上"],
      ] as const;
      const reasons = checks.filter(([matched]) => matched).map(([, reason]) => reason);
      if ((histogramTurnedPositive || goldenCross) && reasons.length >= 4) {
        signals.push({ date: price.date, type: "entry", price: price.close, reasons });
        holding = true;
      }
      continue;
    }

    const checks = [
      [histogramTurnedNegative, "MACD 柱狀由正翻負"],
      [deathCross, "DIF 向下跌破 DEA"],
      [belowShortMa, "收盤跌破 MA10 或 MA20"],
      [supportBreak, "收盤跌破近 20 日支撐"],
      [downVolumeConfirmed, "下跌成交量大於 5 日均量"],
      [macdBothDown, "DIF 與 DEA 同時向下"],
    ] as const;
    const reasons = checks.filter(([matched]) => matched).map(([, reason]) => reason);
    if ((histogramTurnedNegative || deathCross || supportBreak) && reasons.length >= 2) {
      signals.push({ date: price.date, type: "exit", price: price.close, reasons });
      holding = false;
    }
  }
  return signals;
}

export function analyzeTechnicalData(
  prices: DailyPrice[],
  indicators: TechnicalIndicator[],
  intraday = false,
): TechnicalAnalysis {
  const volumes = prices.map((price) => price.volume);
  const volumeMa5 = calculateSMA(volumes, 5);
  const volumeMa20 = calculateSMA(volumes, 20);
  const volume = prices.map((price, index): VolumeAnalysisPoint => {
    const ma5 = volumeMa5[index];
    const ma20 = volumeMa20[index];
    const ratio5 = finite(ma5) && ma5 > 0 ? price.volume / ma5 : null;
    const ratio20 = finite(ma20) && ma20 > 0 ? price.volume / ma20 : null;
    return { date: price.date, ma5, ma20, ratio5, ratio20, status: volumeStatus(ratio20) };
  });
  const macd = indicators.map((point, index): MacdAnalysisPoint => {
    const previous = indicators[index - 1];
    const histogramChange = finite(point.histogram) && finite(previous?.histogram) ? point.histogram - previous.histogram : null;
    const cross = finite(point.dif) && finite(point.signal) && finite(previous?.dif) && finite(previous?.signal)
      ? previous.dif <= previous.signal && point.dif > point.signal ? "黃金交叉"
        : previous.dif >= previous.signal && point.dif < point.signal ? "死亡交叉" : "無交叉"
      : "無交叉";
    const expandingCount = countHistogramMove(indicators, index, "expand");
    const shrinkingCount = countHistogramMove(indicators, index, "shrink");
    let state = "MACD 資料不足";
    if (finite(point.histogram)) {
      if (point.histogram < 0) state = shrinkingCount >= 2 ? `負柱連續縮短 ${shrinkingCount} 根` : expandingCount >= 1 ? "負柱持續放大" : "零軸下方震盪";
      else state = shrinkingCount >= 1 ? `正柱連續縮短 ${shrinkingCount} 根` : expandingCount >= 1 ? "正柱持續放大" : "零軸上方震盪";
      if (finite(previous?.histogram) && previous.histogram < 0 && point.histogram >= 0) state = "負柱翻正";
      if (finite(previous?.histogram) && previous.histogram >= 0 && point.histogram < 0) state = "正柱翻負";
    }
    return { date: point.date, histogramChange, state, cross, expandingCount, shrinkingCount };
  });

  const dataSufficient = prices.length >= 120 && indicators.length === prices.length;
  const latest = prices.at(-1);
  const previous = prices.at(-2);
  const current = indicators.at(-1);
  const previousIndicator = indicators.at(-2);
  const latestVolume = volume.at(-1);
  const latestMacd = macd.at(-1);
  const divergence = detectDivergence(prices, indicators);
  const lowPivots = localPivots(prices.slice(-120), "low").map((index) => index + Math.max(0, prices.length - 120));
  const highPivots = localPivots(prices.slice(-120), "high").map((index) => index + Math.max(0, prices.length - 120));
  const support = lowPivots.length ? prices[lowPivots.at(-1)!].low : prices.length ? Math.min(...prices.slice(-20).map((price) => price.low)) : null;
  const resistance = highPivots.length ? prices[highPivots.at(-1)!].high : prices.length ? Math.max(...prices.slice(-20).map((price) => price.high)) : null;

  if (!dataSufficient || !latest || !previous || !current || !latestVolume || !latestMacd) {
    return {
      volume,
      macd,
      markers: [],
      tradeSignals: [],
      summary: {
        trend: "盤整", healthScore: 50, klineStatus: "資料不足，暫不判斷趨勢",
        volumeStatus: "資料不足", volumeExplanation: "至少需要 120 個交易日資料。",
        macdStatus: "資料不足，暫不產生交易結論", support, resistance,
        operation: "資料不足，請等待完整資料後再判斷。", operationReasons: ["關鍵技術資料尚未形成"],
        risk: "資料不足時不可依賴技術指標進行交易。", signal: "neutral",
        topDivergence: false, bottomDivergence: false, dataSufficient: false,
      },
    };
  }

  const trend = trendLabel(prices, indicators, latestVolume.ratio20);
  const change = (latest.close - previous.close) / previous.close;
  const amplitude = (latest.high - latest.low) / previous.close;
  const prior20 = prices.slice(-21, -1);
  const priorHigh = Math.max(...prior20.map((price) => price.high));
  const priorLow = Math.min(...prior20.map((price) => price.low));
  const breakout = latest.close > priorHigh;
  const breakdown = latest.close < priorLow;
  const volumeUp = (latestVolume.ratio20 ?? 1) > 1.2;
  const highVolumeStall = (latestVolume.ratio20 ?? 0) > 2.5 && Math.abs(change) < 0.01;
  const longUpperShadow = latest.high - Math.max(latest.open, latest.close) > Math.abs(latest.close - latest.open) * 1.5;
  const candleRange = Math.max(latest.high - latest.low, Number.EPSILON);
  const candleBody = Math.abs(latest.close - latest.open);
  const explosiveVolume = (latestVolume.ratio20 ?? 0) > 2.5;
  const highArea = latest.close >= Math.max(...prices.slice(-60).map((price) => price.high)) * 0.97;
  const lowArea = latest.close <= Math.min(...prices.slice(-60).map((price) => price.low)) * 1.03;
  const volumeContracting = prices.slice(-4).every((price, index, items) => index === 0 || price.volume < items[index - 1].volume);
  const priceHighVolumeLower = latest.high >= Math.max(...prices.slice(-21, -1).map((price) => price.high))
    && latest.volume < (latestVolume.ma5 ?? latest.volume);
  const recentDirectionVolume = prices.slice(-7).reduce((result, price, index, items) => {
    if (index === 0) return result;
    if (price.close >= items[index - 1].close) result.up.push(price.volume);
    else result.down.push(price.volume);
    return result;
  }, { up: [] as number[], down: [] as number[] });
  const upVolume = average(recentDirectionVolume.up);
  const downVolume = average(recentDirectionVolume.down);
  const ma20Past = indicators.at(-6)?.ma20;
  const ma5Past = indicators.at(-3)?.ma5;
  const ma20Up = finite(current.ma20) && finite(ma20Past) && current.ma20 > ma20Past;
  const ma5Up = finite(current.ma5) && finite(ma5Past) && current.ma5 > ma5Past;
  const aboveMa5 = finite(current.ma5) && latest.close > current.ma5;
  const aboveMa10 = finite(current.ma10) && latest.close > current.ma10;
  const aboveMa20 = finite(current.ma20) && latest.close > current.ma20;
  const belowMa5 = finite(current.ma5) && latest.close < current.ma5;
  const belowMa10 = finite(current.ma10) && latest.close < current.ma10;
  const belowMa20 = finite(current.ma20) && latest.close < current.ma20;
  const histogramTurnedPositive = finite(previousIndicator?.histogram) && finite(current.histogram) && previousIndicator.histogram < 0 && current.histogram >= 0;
  const histogramTurnedNegative = finite(previousIndicator?.histogram) && finite(current.histogram) && previousIndicator.histogram >= 0 && current.histogram < 0;
  const difUp = finite(current.dif) && finite(previousIndicator?.dif) && current.dif > previousIndicator.dif;
  const deaUp = finite(current.signal) && finite(previousIndicator?.signal) && current.signal > previousIndicator.signal;
  const difDown = finite(current.dif) && finite(previousIndicator?.dif) && current.dif < previousIndicator.dif;
  const deaDown = finite(current.signal) && finite(previousIndicator?.signal) && current.signal < previousIndicator.signal;
  const noNewLow = latest.low >= Math.min(...prices.slice(-6, -1).map((price) => price.low));
  const nearSupport = support != null && latest.close <= support * 1.03;
  const volumeShrinking = latest.volume < previous.volume && (latestVolume.ratio20 ?? 1) < 1;
  const volumeAbove5 = (latestVolume.ratio5 ?? 0) >= 1.2;
  const brokeFiveDayHigh = latest.close > Math.max(...prices.slice(-6, -1).map((price) => price.high));
  const positiveReexpand = finite(current.histogram) && current.histogram > 0 && latestMacd.expandingCount >= 1
    && macd.slice(-5, -1).some((item) => item.shrinkingCount >= 1);
  const platformBreakout = breakout && volumeUp;
  const supportHeld = support != null && latest.low >= support * 0.99;

  let trendScore = 0;
  if (aboveMa5) trendScore += 5;
  if (aboveMa10) trendScore += 5;
  if (aboveMa20) trendScore += 5;
  if (finite(current.ma5) && finite(current.ma10) && current.ma5 > current.ma10) trendScore += 5;
  if (finite(current.ma10) && finite(current.ma20) && current.ma10 > current.ma20) trendScore += 5;
  if (ma20Up) trendScore += 5;

  let volumeScore = 10;
  if (change > 0 && latest.volume > previous.volume) volumeScore += 10;
  if (change < 0 && latest.volume < previous.volume) volumeScore += 8;
  if (platformBreakout) volumeScore += 10;
  if (highVolumeStall) volumeScore -= 15;
  if (breakdown && volumeUp) volumeScore -= 20;
  volumeScore = Math.max(0, Math.min(25, volumeScore));

  let macdScore = 10;
  if (finite(current.histogram) && current.histogram < 0 && latestMacd.shrinkingCount >= 2) macdScore += latestMacd.shrinkingCount >= 3 ? 8 : 5;
  if (histogramTurnedPositive) macdScore += 10;
  if (latestMacd.cross === "黃金交叉") macdScore += 8;
  if (positiveReexpand) macdScore += 8;
  if (divergence.bottom) macdScore += 10;
  if (finite(current.histogram) && current.histogram > 0 && latestMacd.shrinkingCount >= 3) macdScore -= 8;
  if (histogramTurnedNegative) macdScore -= 10;
  if (latestMacd.cross === "死亡交叉") macdScore -= 8;
  if (divergence.top) macdScore -= 12;
  macdScore = Math.max(0, Math.min(30, macdScore));

  let patternScore = 5;
  if (platformBreakout) patternScore += 10;
  if (supportHeld) patternScore += 5;
  if (breakdown) patternScore -= 10;
  if (support != null && latest.close < support) patternScore -= 15;
  patternScore = Math.max(0, Math.min(15, patternScore));
  const healthScore = Math.max(0, Math.min(100, trendScore + volumeScore + macdScore + patternScore));

  const observeConditions = [
    finite(current.histogram) && current.histogram < 0 && latestMacd.shrinkingCount >= 2,
    noNewLow, aboveMa5, volumeShrinking, divergence.bottom, nearSupport,
  ];
  const entryConditions = [
    histogramTurnedPositive, latestMacd.cross === "黃金交叉", aboveMa5 && aboveMa10,
    brokeFiveDayHigh, volumeAbove5, ma5Up,
  ];
  const addConditions = [aboveMa10 || aboveMa20, positiveReexpand, platformBreakout, (latestVolume.ratio20 ?? 0) >= 1.2 && (latestVolume.ratio20 ?? 0) <= 1.8, !divergence.top];
  const reduceConditions = [finite(current.histogram) && current.histogram > 0 && latestMacd.shrinkingCount >= 3, divergence.top, highVolumeStall, belowMa5, longUpperShadow];
  const exitConditions = [histogramTurnedNegative, latestMacd.cross === "死亡交叉", belowMa10 || belowMa20, support != null && latest.close < support, change < 0 && volumeAbove5, difDown && deaDown];
  const countTrue = (items: boolean[]) => items.filter(Boolean).length;

  let signal: CompositeSignal = "neutral";
  let operation = "技術面中性，等待價格、成交量與 MACD 形成一致方向。";
  let operationReasons = ["目前尚未形成足夠一致的技術條件"];
  if (countTrue(exitConditions) >= 2) {
    signal = "exit";
    operation = "趨勢轉弱，建議減碼或出場。";
    operationReasons = ["MACD 與價格弱勢條件同時成立", belowMa10 || belowMa20 ? "收盤跌破短中期均線" : "動能明顯轉弱"];
  } else if (countTrue(reduceConditions) >= 2) {
    signal = "reduce";
    operation = "短線動能減弱，可考慮減碼 20%～30%。";
    operationReasons = ["至少兩項減碼警訊成立", latestMacd.state];
  } else if (addConditions.every(Boolean)) {
    signal = "add";
    operation = "趨勢重新加速，可考慮分批加碼。";
    operationReasons = ["股價守住中短期均線", "MACD 正柱重新放大且量價配合"];
  } else if (countTrue(entryConditions) >= 4) {
    signal = "entry";
    operation = "初步進場訊號，可考慮建立 30%～40% 試單部位。";
    operationReasons = ["至少四項進場確認條件成立", "價格、成交量與 MACD 同步轉強"];
  } else if (countTrue(observeConditions) >= 3) {
    signal = "observe";
    operation = "止跌觀察，尚未正式進場。";
    operationReasons = ["至少三項止跌觀察條件成立", "仍需等待收盤突破與成交量確認"];
  } else if (trend === "強勢多頭" && finite(current.histogram) && current.histogram > 0) {
    operation = "趨勢仍偏多，已有持股可續抱；未突破前不建議追高。";
    operationReasons = ["均線維持多頭排列", latestMacd.state];
  }
  if (intraday && signal !== "neutral") operation = `盤中尚未確認：${operation}`;

  let macdStatus = latestMacd.state;
  if (divergence.bottom) macdStatus += "；出現底背離，仍需等待股價突破確認";
  if (divergence.top) macdStatus += "；出現頂背離，建議提高風險警戒";
  if (latestMacd.state === "負柱持續放大") macdStatus += breakdown && volumeUp ? "，偏空賣出訊號" : "，空方動能增強";
  if (latestMacd.state.startsWith("負柱連續縮短")) macdStatus += "，跌勢動能減弱，進入止跌觀察";
  if (latestMacd.state === "負柱翻正" && countTrue(entryConditions) < 4) macdStatus += "，初步轉強但等待突破確認";
  if (latestMacd.state === "正柱持續放大") macdStatus += aboveMa5 && aboveMa10 && aboveMa20 ? "，多方動能持續增強" : "，留意短線乖離";
  if (latestMacd.state.startsWith("正柱連續縮短")) macdStatus += latestMacd.shrinkingCount >= 2 ? "，多方動能明顯減弱" : "，上漲動能開始減速";
  if (latestMacd.state === "正柱翻負" && countTrue(exitConditions) < 2) macdStatus += "，短線轉弱但尚未形成明確出場訊號";

  const ratioText = latestVolume.ratio20 == null ? "尚無 20 日均量" : `${latestVolume.ratio20.toFixed(2)} 倍`;
  const volumeExplanation = `今日成交量為 20 日均量的 ${ratioText}，屬於${latestVolume.status}。`;
  let volumeSignal = "量價表現中性";
  if (explosiveVolume && candleBody / candleRange <= 0.15) volumeSignal = "爆量十字線，多空分歧明顯";
  else if (explosiveVolume && candleBody / previous.close >= 0.02 && latest.close > latest.open) volumeSignal = "爆量長紅";
  else if (explosiveVolume && candleBody / previous.close >= 0.02 && latest.close < latest.open) volumeSignal = "爆量長黑";
  else if (highVolumeStall && highArea) volumeSignal = "高檔爆量滯漲";
  else if (highVolumeStall && lowArea) volumeSignal = "低檔爆量止跌觀察";
  else if (priceHighVolumeLower) volumeSignal = "價格創高但成交量下降";
  else if (platformBreakout) volumeSignal = "整理後量增突破";
  else if (breakdown && volumeUp) volumeSignal = "跌破支撐並放量";
  else if (volumeContracting) volumeSignal = "連續量縮整理";
  else if (change > 0 && latest.volume > previous.volume) volumeSignal = "價漲量增";
  else if (change < 0 && latest.volume > previous.volume) volumeSignal = "價跌量增";
  else if (change < 0 && volumeShrinking) volumeSignal = "拉回量縮";
  else if (change > 0 && volumeShrinking) volumeSignal = "上漲量縮，突破力道不足";
  else if (upVolume != null && downVolume != null && upVolume > downVolume * 1.15) volumeSignal = "上漲量增、下跌量縮";
  else if (upVolume != null && downVolume != null && downVolume > upVolume * 1.15) volumeSignal = "上漲量縮、下跌量增";

  const klineStatus = breakout ? "收盤突破近期壓力"
    : breakdown ? "收盤跌破近期支撐"
      : trend === "盤整" ? "短期均線靠攏，股價維持區間震盪"
        : aboveMa20 ? "股價守住 MA20，趨勢結構尚未破壞"
          : "股價位於 MA20 下方，反彈仍需確認";
  const risk = signal === "exit" ? "若無法迅速站回支撐，技術弱勢可能延續。"
    : divergence.top || highVolumeStall ? "高檔動能與量價出現警訊，應提高停利保護。"
      : amplitude > 0.06 ? "當日振幅偏大，請留意滑價與追高風險。"
        : !aboveMa20 ? "股價尚未站穩 MA20，中期趨勢仍有轉弱風險。"
          : "若收盤跌破 MA20 且成交量放大，技術面將明顯轉弱。";

  const tradeSignals = generateCompositeTradeSignals(prices, indicators);
  const markers = buildMarkers(prices, indicators, volume, divergence);
  for (const tradeSignal of tradeSignals.slice(-80)) {
    const entry = tradeSignal.type === "entry";
    markers.push({
      date: tradeSignal.date,
      position: entry ? "belowBar" : "aboveBar",
      color: entry ? "#ff6467" : "#2bce7f",
      shape: entry ? "arrowUp" : "arrowDown",
      text: entry ? "進場" : "出場",
    });
  }
  if (signal === "observe" || signal === "add" || signal === "reduce") {
    const markerBySignal = {
      observe: { text: "止跌觀察", color: "#f1bd5d", position: "belowBar" as const, shape: "circle" as const },
      add: { text: "加碼觀察", color: "#ff7678", position: "belowBar" as const, shape: "arrowUp" as const },
      reduce: { text: "減碼觀察", color: "#ff9f43", position: "aboveBar" as const, shape: "arrowDown" as const },
    }[signal];
    markers.push({ date: latest.date, ...markerBySignal });
  }
  return {
    volume,
    macd,
    markers,
    tradeSignals,
    summary: {
      trend, healthScore, klineStatus, volumeStatus: `${latestVolume.status}・${volumeSignal}`,
      volumeExplanation, macdStatus, support, resistance, operation, operationReasons,
      risk, signal, topDivergence: divergence.top, bottomDivergence: divergence.bottom, dataSufficient,
    },
  };
}
