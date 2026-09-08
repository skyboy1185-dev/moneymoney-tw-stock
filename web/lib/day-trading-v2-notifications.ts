import type { NotificationItem } from "@/lib/day-trading-v2-types";

export type DayTradingV2ToastKind = "activation" | "buy" | "sell" | "stop";
export type DayTradingV2ScreenNotification = NotificationItem & { key: string; kind: DayTradingV2ToastKind };

function toastKind(eventType: string): DayTradingV2ToastKind {
  if (eventType === "BUY_FILLED") return "buy";
  if (eventType === "SELL_FILLED") return "sell";
  if (/INTERRUPTED|DISCONNECTED|LOSS_LIMIT|HALTED|EMERGENCY|RISK_REDUCED/.test(eventType)) return "stop";
  return "activation";
}

export function selectDayTradingV2Notifications(
  payload: unknown,
  userId: string,
  today = new Date().toLocaleDateString("sv-SE", { timeZone: "Asia/Taipei" }),
): DayTradingV2ScreenNotification[] {
  if (!userId || !payload || typeof payload !== "object" || !("items" in payload) || !Array.isArray(payload.items)) return [];
  const events = new Map<string, DayTradingV2ScreenNotification>();
  for (const raw of payload.items) {
    if (!raw || typeof raw !== "object") continue;
    const item = raw as Partial<NotificationItem>;
    if (item.mode !== "PAPER" || typeof item.eventId !== "string" || !item.eventId
      || typeof item.eventType !== "string" || typeof item.createdAt !== "string"
      || typeof item.title !== "string" || typeof item.message !== "string") continue;
    // The backend stores UTC; SQLite-backed responses can omit the offset.
    const timestamp = /(?:Z|[+-]\d{2}:\d{2})$/i.test(item.createdAt) ? item.createdAt : `${item.createdAt}Z`;
    const date = new Date(timestamp);
    if (!Number.isFinite(date.getTime()) || date.toLocaleDateString("sv-SE", { timeZone: "Asia/Taipei" }) !== today) continue;
    const key = `day-trading-v2:${JSON.stringify([userId, item.eventId])}`;
    if (events.has(key)) continue;
    events.set(key, {
      id: typeof item.id === "number" ? item.id : 0, eventId: item.eventId, mode: "PAPER",
      eventType: item.eventType, title: item.title, message: item.message,
      read: item.read === true, createdAt: date.toISOString(), key, kind: toastKind(item.eventType),
    });
  }
  return [...events.values()].sort((left, right) => Date.parse(right.createdAt) - Date.parse(left.createdAt));
}
