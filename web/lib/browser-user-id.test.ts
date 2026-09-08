import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

beforeEach(() => vi.resetModules());
afterEach(() => vi.unstubAllGlobals());

describe("shared browser user ID", () => {
  it("reuses the page's existing persisted ID for the notifier", async () => {
    const getItem = vi.fn(() => "existing-browser-user");
    const setItem = vi.fn();
    vi.stubGlobal("window", { localStorage: { getItem, setItem } });
    const { getBrowserUserId, BROWSER_USER_ID_KEY } = await import("./browser-user-id");
    expect(getBrowserUserId()).toBe("existing-browser-user");
    expect(getItem).toHaveBeenCalledWith(BROWSER_USER_ID_KEY);
    expect(setItem).not.toHaveBeenCalled();
  });

  it("creates one ID even if the global notifier initializes before the page", async () => {
    let stored: string | null = null;
    vi.stubGlobal("window", { localStorage: {
      getItem: () => stored,
      setItem: (_key: string, value: string) => { stored = value; },
    } });
    const { getBrowserUserId } = await import("./browser-user-id");
    const notifierId = getBrowserUserId();
    expect(notifierId.length).toBeGreaterThanOrEqual(8);
    expect(getBrowserUserId()).toBe(notifierId);
    expect(stored).toBe(notifierId);
  });

  it("shares a stable session fallback when storage is blocked", async () => {
    vi.stubGlobal("window", { get localStorage() { throw new Error("storage denied"); } });
    const { getBrowserUserId } = await import("./browser-user-id");
    expect(getBrowserUserId()).toBe(getBrowserUserId());
  });
});
