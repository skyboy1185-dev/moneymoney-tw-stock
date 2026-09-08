export const BROWSER_USER_ID_KEY = "moneymoney-user-id";

let temporaryUserId = "";

/** Shared by the page and notifications, including when browser storage is unavailable. */
export function getBrowserUserId(): string {
  try {
    const stored = window.localStorage.getItem(BROWSER_USER_ID_KEY);
    if (stored) return stored;
  } catch { /* keep a stable ID for this page session */ }
  temporaryUserId ||= globalThis.crypto?.randomUUID?.()
    ?? `local-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  try { window.localStorage.setItem(BROWSER_USER_ID_KEY, temporaryUserId); } catch { /* storage is optional */ }
  return temporaryUserId;
}
