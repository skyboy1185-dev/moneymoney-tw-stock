import type { LimitUpAiNotification, LimitUpPosition, LimitUpTrade } from "@/lib/limit-up-ai-types";

export const LIMIT_UP_TRADE_NOTIFICATION_TYPES = ["BUY", "SELL", "TAKE_PROFIT", "STOP_LOSS"] as const;

const notificationTypes = new Set<string>(LIMIT_UP_TRADE_NOTIFICATION_TYPES);

export function isLimitUpTradeNotification(item: LimitUpAiNotification): boolean {
  return notificationTypes.has(item.type);
}

export function taipeiDateKey(value: string | Date): string | null {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Taipei",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date);
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === type)?.value;
  const year = part("year"), month = part("month"), day = part("day");
  return year && month && day ? `${year}-${month}-${day}` : null;
}

export function selectTodayLimitUpTrades(items: LimitUpTrade[], now = new Date()): LimitUpTrade[] {
  const today = taipeiDateKey(now);
  return today ? items.filter((item) => taipeiDateKey(item.executedAt) === today) : [];
}

export function selectOpenLimitUpPositions(items: LimitUpPosition[]): LimitUpPosition[] {
  return items.filter((item) => item.status === "open" && item.remainingQuantity > 0);
}
