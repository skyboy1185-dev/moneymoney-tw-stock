import type { DayTradingSettings } from "@/lib/day-trading-types";

const base = "/api/day-trading";

async function request<T>(path: string, userId: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${base}/${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "x-user-id": userId,
      ...(init?.headers ?? {}),
    },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail ?? payload.error ?? "操作失敗");
  return payload as T;
}

export const dayTradingClient = {
  regime: (userId: string) => request<unknown>("market-regime", userId),
  signals: (userId: string) => request<{ items: unknown[] }>("signals", userId),
  rankings: (userId: string) => request<{ items: unknown[] }>("rankings", userId),
  positions: (userId: string) => request<{ items: unknown[] }>("positions", userId),
  alerts: (userId: string) => request<{ items: unknown[]; unread: number }>("alerts", userId),
  trades: (userId: string) => request<{ items: unknown[] }>("trades", userId),
  performance: (userId: string) => request<unknown>("performance", userId),
  settings: (userId: string) => request<DayTradingSettings>("settings", userId),
  saveSettings: (userId: string, settings: DayTradingSettings) => request<DayTradingSettings>("settings", userId, {
    method: "PUT",
    body: JSON.stringify({
      capital: settings.capital,
      max_risk_per_trade: settings.maxRiskPerTrade,
      max_daily_loss: settings.maxDailyLoss,
      max_daily_trades: settings.maxDailyTrades,
      max_position_percentage: settings.maxPositionPercentage,
      max_consecutive_losses: settings.maxConsecutiveLosses,
      minimum_risk_reward: settings.minimumRiskReward,
      maximum_spread: settings.maximumSpread,
      minimum_volume: settings.minimumVolume,
      minimum_turnover: settings.minimumTurnover,
      latest_entry_time: settings.latestEntryTime,
      close_reminder_time: settings.closeReminderTime,
      notification_enabled: settings.notificationEnabled,
      sound_enabled: settings.soundEnabled,
      entry_notification: settings.entryNotification,
      exit_notification: settings.exitNotification,
      stop_notification: settings.stopNotification,
      target_notification: settings.targetNotification,
      data_alert_notification: settings.dataAlertNotification,
      high_confidence_only: settings.highConfidenceOnly,
      minimum_confidence: settings.minimumConfidence,
      notification_cooldown: settings.notificationCooldown,
      repeat_count: settings.repeatCount,
    }),
  }),
  createPosition: (userId: string, signalId: string, direction: "long" | "short", entryPrice: number, quantity = 1) =>
    request<unknown>("positions", userId, {
      method: "POST", body: JSON.stringify({ signal_id: signalId, direction, entry_price: entryPrice, quantity }),
    }),
  updatePosition: (userId: string, id: number, body: Record<string, unknown>) =>
    request<unknown>(`positions/${id}`, userId, { method: "PATCH", body: JSON.stringify(body) }),
  closePosition: (userId: string, id: number, percentage: number, reason: string) =>
    request<unknown>(`positions/${id}/close`, userId, {
      method: "POST", body: JSON.stringify({ percentage, reason }),
    }),
  readAlert: (userId: string, id: number) => request<unknown>(`alerts/${id}/read`, userId, { method: "PATCH" }),
  scenario: (userId: string, scenario: string) => request<unknown>(`scenarios/${scenario}`, userId, { method: "POST" }),
};
