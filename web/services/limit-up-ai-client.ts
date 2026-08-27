import type { LimitUpAiNotification, LimitUpAiPerformance, LimitUpAiSettings, LimitUpAiStatus, LimitUpDashboard, LimitUpReplay } from "@/lib/limit-up-ai-types";

const base = "/api/limit-up-ai";

export class LimitUpAiClientError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "LimitUpAiClientError";
  }
}

async function request<T>(path: string, userId: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${base}/${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "x-user-id": userId,
      ...(init?.headers ?? {}),
    },
    signal: init?.signal ?? AbortSignal.timeout(15_000),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = response.status === 401
      ? "請先登入後再使用漲停機器人。"
      : payload.error ?? payload.detail ?? "專抓漲停飆股 AI 讀取失敗";
    throw new LimitUpAiClientError(message, response.status);
  }
  return payload as T;
}

export const limitUpAiClient = {
  dashboard: (userId: string) => request<LimitUpDashboard>("dashboard", userId),
  scan: (userId: string) => request<LimitUpDashboard>("scan", userId, { method: "POST", signal: AbortSignal.timeout(30_000) }),
  status: (userId: string) => request<LimitUpAiStatus>("status", userId),
  performance: (userId: string) => request<LimitUpAiPerformance>("performance", userId),
  notifications: (userId: string, type = "", unreadOnly = false) => {
    const params = new URLSearchParams();
    if (type) params.set("type", type);
    if (unreadOnly) params.set("unreadOnly", "true");
    return request<{ items: LimitUpAiNotification[]; unreadCount: number }>(`notifications?${params}`, userId);
  },
  unreadNotifications: (userId: string) => request<{ count: number }>("notifications/unread", userId),
  markNotificationRead: (userId: string, id: number) =>
    request<{ status: string; id: number }>(`notifications/${id}/read`, userId, { method: "POST" }),
  markAllNotificationsRead: (userId: string) =>
    request<{ status: string; count: number }>("notifications/read-all", userId, { method: "POST" }),
  replayToday: (userId: string) => request<LimitUpReplay>("replay/today", userId),
  settings: (userId: string) => request<LimitUpAiSettings>("settings", userId),
  saveSettings: (userId: string, settings: LimitUpAiSettings) =>
    request<LimitUpAiSettings>("settings", userId, {
      method: "PUT",
      body: JSON.stringify(settings),
    }),
};
