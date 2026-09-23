export const BROWSER_USER_ID_KEY = "moneymoney-user-id";

// This is a private, single-account installation.  The previous Railway site
// stored all user-owned records under this id.  Keeping it stable across hosts
// prevents a new LAN/Funnel origin from silently creating an empty account.
export const PRIVATE_SITE_OWNER_ID = "0db66ac7-d683-4294-8c1c-bf43fe16a454";

/** Shared by the page and notifications, including when browser storage is unavailable. */
export function getBrowserUserId(): string {
  try {
    window.localStorage.setItem(BROWSER_USER_ID_KEY, PRIVATE_SITE_OWNER_ID);
  } catch { /* storage is optional */ }
  return PRIVATE_SITE_OWNER_ID;
}
