import type { Dashboard, NotificationItem, TradingMode } from "@/lib/day-trading-v2-types";

const base = "/api/day-trading-v2";

async function request<T>(path: string, userId: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${base}/${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", "x-user-id": userId, ...(init?.headers ?? {}) },
    signal: init?.signal ?? AbortSignal.timeout(15_000),
    cache: "no-store",
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail ?? payload.error ?? "當沖機器人2服務暫時無法使用");
  return payload as T;
}

export const dayTradingV2Client = {
  dashboard: (userId: string) => request<Dashboard>("dashboard", userId),
  notifications: (userId: string) => request<{ unread: number; items: NotificationItem[] }>("notifications", userId),
  saveSettings: (userId: string, mode: TradingMode, config: Dashboard["config"]) => request<{ mode: TradingMode; config: Dashboard["config"] }>("settings", userId, { method: "PUT", body: JSON.stringify({ mode, config }) }),
  updateRobot: (userId: string, strategyId: string, enabled: boolean, allocation?: string) => request(`robots/${strategyId}`, userId, { method: "PATCH", body: JSON.stringify({ enabled, allocation }) }),
  emergencyStop: (userId: string) => request<{ status: string; message: string }>("emergency-stop", userId, { method: "POST" }),
  cancelAll: (userId: string) => request<{ cancelled: number }>("cancel-all", userId, { method: "POST" }),
  scanNow: (userId: string) => request<{ evaluated: number; executed: number; skipped: number }>("scan-now", userId, { method: "POST" }),
  closePosition: (userId: string, positionId: string, fillPrice: string, reason = "手動平倉") => request(`positions/${positionId}/close`, userId, { method: "POST", body: JSON.stringify({ fill_price: fillPrice, reason, percentage: 100 }) }),
  backtest: (userId: string, payload: Record<string, unknown>) => request<Record<string, unknown>>("backtests", userId, { method: "POST", body: JSON.stringify(payload) }),
  backtests: (userId: string) => request<{ items: Array<Record<string, unknown>> }>("backtests", userId),
};
