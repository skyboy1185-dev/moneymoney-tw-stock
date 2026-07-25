import type {
  LineIntegrationStatus,
  LineNotificationSettings,
} from "@/lib/day-trading-types";

const base = "/api/integrations/line";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${base}/${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail ?? payload.error ?? "LINE 通知操作失敗");
  return payload as T;
}

export const lineNotificationClient = {
  status: () => request<LineIntegrationStatus>("status"),
  test: () => request<{ ok: boolean; sentGroups: number }>("test", { method: "POST" }),
  unbind: (groupRecordId: number) => request<{ ok: boolean }>(`groups/${groupRecordId}`, { method: "DELETE" }),
  saveSettings: (settings: LineNotificationSettings) => request<LineNotificationSettings>("settings", {
    method: "PUT",
    body: JSON.stringify({
      opening_enabled: settings.openingEnabled,
      long_entry_enabled: settings.longEntryEnabled,
      short_entry_enabled: settings.shortEntryEnabled,
      long_exit_enabled: settings.longExitEnabled,
      short_cover_enabled: settings.shortCoverEnabled,
      stop_loss_enabled: settings.stopLossEnabled,
      data_alert_enabled: settings.dataAlertEnabled,
      closing_summary_enabled: settings.closingSummaryEnabled,
    }),
  }),
};
