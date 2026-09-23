export const IN_APP_NOTIFICATION_EVENT = "twse:in-app-notification";

export type InAppNotificationSeverity = "normal" | "critical";

export interface InAppNotification {
  id: string;
  severity: InAppNotificationSeverity;
  title: string;
  stock?: string;
  message: string;
  reason?: string;
  timestamp: string;
  href?: string;
}

export function publishInAppNotification(notification: InAppNotification): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent<InAppNotification>(IN_APP_NOTIFICATION_EVENT, { detail: notification }));
}

export function compactNotificationTitle(count: number, critical: boolean): string {
  if (count <= 1) return "";
  return critical ? `新增 ${count} 則重大盤中訊息` : `新增 ${count} 則盤中訊息`;
}
