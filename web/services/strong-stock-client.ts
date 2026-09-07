import type { StrongDashboard, StrongRanking } from "@/lib/strong-stock-types";

const base = "/api/strong-stocks";

async function request<T>(path: string, userId: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${base}/${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", "x-user-id": userId, ...(init?.headers ?? {}) },
    cache: "no-store",
    signal: init?.signal ?? AbortSignal.timeout(30_000),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail ?? payload.error ?? "強勢股服務暫時無法使用");
  return payload as T;
}

export const strongStockClient = {
  dashboard: (userId: string) => request<StrongDashboard>("dashboard", userId),
  rankings: (userId: string, query = "") => request<{ tradeDate: string | null; items: StrongRanking[] }>(`rankings${query ? `?${query}` : ""}`, userId),
  stock: (userId: string, symbol: string) => request<{ current: StrongRanking; history: StrongRanking[] }>(`stocks/${symbol}`, userId),
  start: (userId: string) => request("paper/start", userId, { method: "POST" }),
  pause: (userId: string) => request("paper/pause", userId, { method: "POST" }),
  saveSettings: (userId: string, paperEnabled: boolean, config: Record<string, unknown>) => request("settings", userId, { method: "PATCH", body: JSON.stringify({ paper_enabled: paperEnabled, config }) }),
  close: (userId: string, positionId: string, price: string, reason: string) => request(`positions/${positionId}/close`, userId, { method: "POST", body: JSON.stringify({ price, reason }) }),
  backtest: (userId: string, startDate: string, endDate: string, benchmark = "0050") => request<Record<string, unknown>>("backtests", userId, { method: "POST", body: JSON.stringify({ start_date: startDate, end_date: endDate, benchmark }) }),
  backtests: (userId: string) => request<{ items: Array<Record<string, unknown>> }>("backtests", userId),
  readNotification: (userId: string, id: number) => request(`notifications/${id}/read`, userId, { method: "POST" }),
};
