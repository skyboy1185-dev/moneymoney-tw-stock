import type { DayTradingSignal } from "./day-trading-types";

export const streamRetryDelay = (attempt: number) => Math.min(30_000, 1_000 * 2 ** Math.min(attempt, 5));

export function isExpired(expiresAt: string, now = Date.now()) {
  return new Date(expiresAt).getTime() <= now;
}

export function canCreateEntry(dataDelaySeconds: number, dailyLossReached: boolean, consecutiveLosses: number, limit: number) {
  return dataDelaySeconds <= 8 && !dailyLossReached && consecutiveLosses < limit;
}

export function longSignalScore(values: Record<string, boolean>) {
  const weights: Record<string, number> = {
    vwapUp: 15, aboveVwap: 15, breakout: 15, volume: 10, activeBuy: 15,
    largeBuy: 10, shortTrend: 10, marketFit: 5, industryFit: 5,
  };
  return Object.entries(weights).reduce((score, [key, weight]) => score + (values[key] ? weight : 0), 0);
}

export function shortSignalScore(values: Record<string, boolean>) {
  const weights: Record<string, number> = {
    vwapDown: 15, belowVwap: 15, breakdown: 15, volume: 10, activeSell: 15,
    largeSell: 10, shortTrend: 10, marketFit: 5, industryFit: 5,
  };
  return Object.entries(weights).reduce((score, [key, weight]) => score + (values[key] ? weight : 0), 0);
}

export function evaluateExit(
  direction: "long" | "short",
  price: number,
  stopLoss: number,
  target1: number,
  target2: number,
  trailingStop?: number | null,
) {
  if (direction === "long") {
    if (price <= stopLoss) return { priority: 0, action: "立即全部賣出" };
    if (trailingStop && price <= trailingStop) return { priority: 1, action: "全部賣出" };
    if (price >= target2) return { priority: 1, action: "全部賣出" };
    if (price >= target1) return { priority: 2, action: "減碼 50%" };
    return { priority: 9, action: "續抱多單" };
  }
  if (price >= stopLoss) return { priority: 0, action: "立即全部回補" };
  if (trailingStop && price >= trailingStop) return { priority: 1, action: "全部回補" };
  if (price <= target2) return { priority: 1, action: "全部回補" };
  if (price <= target1) return { priority: 2, action: "回補 50%" };
  return { priority: 9, action: "續抱空單" };
}

export function filterSignals(signals: DayTradingSignal[], filter: string) {
  if (filter === "long") return signals.filter((item) => item.direction === "long");
  if (filter === "short") return signals.filter((item) => item.direction === "short");
  if (filter === "waiting") return signals.filter((item) => item.action.includes("等待"));
  if (filter === "confirmed") return signals.filter((item) => item.status === "confirmed");
  if (filter === "high") return signals.filter((item) => item.confidenceScore >= 85);
  if (filter === "expiring") return signals.filter((item) => new Date(item.expiresAt).getTime() - Date.now() < 60_000);
  if (filter === "listed") return signals.filter((item) => item.market === "上市");
  if (filter === "otc") return signals.filter((item) => item.market === "上櫃");
  return signals;
}
