import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

beforeEach(() => vi.resetModules());
afterEach(() => vi.unstubAllGlobals());

describe("shared browser user ID", () => {
  it("replaces a host-specific ID with the private site's restored owner", async () => {
    const getItem = vi.fn(() => "existing-browser-user");
    const setItem = vi.fn();
    vi.stubGlobal("window", { localStorage: { getItem, setItem } });
    const { getBrowserUserId, BROWSER_USER_ID_KEY, PRIVATE_SITE_OWNER_ID } = await import("./browser-user-id");
    expect(getBrowserUserId()).toBe(PRIVATE_SITE_OWNER_ID);
    expect(getItem).not.toHaveBeenCalled();
    expect(setItem).toHaveBeenCalledWith(BROWSER_USER_ID_KEY, PRIVATE_SITE_OWNER_ID);
  });

  it("uses one owner ID when the global notifier initializes before the page", async () => {
    let stored: string | null = null;
    vi.stubGlobal("window", { localStorage: {
      getItem: () => stored,
      setItem: (_key: string, value: string) => { stored = value; },
    } });
    const { getBrowserUserId, PRIVATE_SITE_OWNER_ID } = await import("./browser-user-id");
    const notifierId = getBrowserUserId();
    expect(notifierId).toBe(PRIVATE_SITE_OWNER_ID);
    expect(getBrowserUserId()).toBe(notifierId);
    expect(stored).toBe(notifierId);
  });

  it("keeps the restored owner when storage is blocked", async () => {
    vi.stubGlobal("window", { get localStorage() { throw new Error("storage denied"); } });
    const { getBrowserUserId, PRIVATE_SITE_OWNER_ID } = await import("./browser-user-id");
    expect(getBrowserUserId()).toBe(PRIVATE_SITE_OWNER_ID);
  });
});
