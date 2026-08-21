import type { ManualStrategy } from "./market-types";

export const MANUAL_STRATEGIES: ManualStrategy[] = [
  { id: "day-macd", name: "日 K MACD 翻紅，成交量大於 500 張", timeframe: "day", volumeThreshold: 500_000, requiresKD: false, signalMode: "confirmed" },
  { id: "week-macd", name: "週 K MACD 翻紅，成交量大於 3,500 張", timeframe: "week", volumeThreshold: 3_500_000, requiresKD: false, signalMode: "confirmed" },
  { id: "month-macd", name: "月 K MACD 翻紅，成交量大於 10,000 張", timeframe: "month", volumeThreshold: 10_000_000, requiresKD: false, signalMode: "confirmed" },
  { id: "day-macd-kd", name: "日 K MACD 翻紅且 KD 低檔金叉，成交量大於 500 張", timeframe: "day", volumeThreshold: 500_000, requiresKD: true, signalMode: "confirmed" },
  { id: "week-macd-kd", name: "週 K MACD 翻紅且 KD 低檔金叉，成交量大於 3,500 張", timeframe: "week", volumeThreshold: 3_500_000, requiresKD: true, signalMode: "confirmed" },
  { id: "month-macd-kd", name: "月 K MACD 翻紅且 KD 低檔金叉，成交量大於 10,000 張", timeframe: "month", volumeThreshold: 10_000_000, requiresKD: true, signalMode: "confirmed" },
  { id: "day-kd-below-8", name: "日 K、D 同時低於 8", timeframe: "day", volumeThreshold: 0, requiresKD: false, signalMode: "kd-below", kdThreshold: 8 },
  { id: "week-kd-below-8", name: "週 K、D 同時低於 8", timeframe: "week", volumeThreshold: 0, requiresKD: false, signalMode: "kd-below", kdThreshold: 8 },
  { id: "month-kd-below-8", name: "月 K、D 同時低於 8", timeframe: "month", volumeThreshold: 0, requiresKD: false, signalMode: "kd-below", kdThreshold: 8 },
  { id: "day-kd-single-bullish-divergence", name: "日 KD 一次低檔背離", timeframe: "day", volumeThreshold: 0, requiresKD: false, signalMode: "kd-bullish-divergence", divergenceLookback: 30 },
  { id: "day-kd-double-bullish-divergence", name: "日 KD 二度低檔背離", timeframe: "day", volumeThreshold: 0, requiresKD: false, signalMode: "kd-double-bullish-divergence", divergenceLookback: 45 },
  { id: "day-macd-forecast", name: "日 K MACD 預測即將翻紅，成交量大於 500 張", timeframe: "day", volumeThreshold: 500_000, requiresKD: false, signalMode: "forecast" },
  { id: "week-macd-forecast", name: "週 K MACD 預測即將翻紅，成交量大於 3,500 張", timeframe: "week", volumeThreshold: 3_500_000, requiresKD: false, signalMode: "forecast" },
  { id: "month-macd-forecast", name: "月 K MACD 預測即將翻紅，成交量大於 10,000 張", timeframe: "month", volumeThreshold: 10_000_000, requiresKD: false, signalMode: "forecast" },
  { id: "day-macd-kd-forecast", name: "日 K MACD 預測即將翻紅且 KD 低檔金叉，成交量大於 500 張", timeframe: "day", volumeThreshold: 500_000, requiresKD: true, signalMode: "forecast" },
  { id: "week-macd-kd-forecast", name: "週 K MACD 預測即將翻紅且 KD 低檔金叉，成交量大於 3,500 張", timeframe: "week", volumeThreshold: 3_500_000, requiresKD: true, signalMode: "forecast" },
  { id: "month-macd-kd-forecast", name: "月 K MACD 預測即將翻紅且 KD 低檔金叉，成交量大於 10,000 張", timeframe: "month", volumeThreshold: 10_000_000, requiresKD: true, signalMode: "forecast" },
  { id: "day-deduction-three-low", name: "日 K 即將扣三低", timeframe: "day", volumeThreshold: 0, requiresKD: false, signalMode: "deduction-low", maPeriod: 20, deductionDirection: "low" },
  { id: "day-deduction-three-high", name: "日 K 即將扣三高", timeframe: "day", volumeThreshold: 0, requiresKD: false, signalMode: "deduction-high", maPeriod: 20, deductionDirection: "high" },
  { id: "week-deduction-three-low", name: "週 K 即將扣三低", timeframe: "week", volumeThreshold: 0, requiresKD: false, signalMode: "deduction-low", maPeriod: 20, deductionDirection: "low" },
  { id: "week-deduction-three-high", name: "週 K 即將扣三高", timeframe: "week", volumeThreshold: 0, requiresKD: false, signalMode: "deduction-high", maPeriod: 20, deductionDirection: "high" },
  { id: "month-deduction-three-low", name: "月 K 即將扣三低", timeframe: "month", volumeThreshold: 0, requiresKD: false, signalMode: "deduction-low", maPeriod: 20, deductionDirection: "low" },
  { id: "month-deduction-three-high", name: "月 K 即將扣三高", timeframe: "month", volumeThreshold: 0, requiresKD: false, signalMode: "deduction-high", maPeriod: 20, deductionDirection: "high" },
];

export const CONFIRMED_MANUAL_STRATEGIES = MANUAL_STRATEGIES.filter((strategy) => strategy.signalMode === "confirmed");
export const FORECAST_MANUAL_STRATEGIES = MANUAL_STRATEGIES.filter((strategy) => strategy.signalMode === "forecast");
export const DEDUCTION_MANUAL_STRATEGIES = MANUAL_STRATEGIES.filter((strategy) => strategy.deductionDirection != null);
export const KD_MANUAL_STRATEGIES = MANUAL_STRATEGIES.filter((strategy) => strategy.signalMode === "kd-below");
export const DIVERGENCE_MANUAL_STRATEGIES = MANUAL_STRATEGIES.filter((strategy) => strategy.signalMode === "kd-bullish-divergence" || strategy.signalMode === "kd-double-bullish-divergence");
