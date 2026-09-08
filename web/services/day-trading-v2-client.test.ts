import { afterEach, describe, expect, it, vi } from "vitest";
import { dayTradingV2Client, DayTradingV2RequestError } from "./day-trading-v2-client";

afterEach(() => vi.unstubAllGlobals());

describe("V2 notification requests", () => {
  it("sends the browser's user scope and disables cached notification responses", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ unread: 0, items: [] })));
    vi.stubGlobal("fetch", fetchMock);
    await expect(dayTradingV2Client.notifications("browser-user")).resolves.toEqual({ unread: 0, items: [] });
    expect(fetchMock).toHaveBeenCalledWith("/api/day-trading-v2/notifications", expect.objectContaining({
      cache: "no-store", headers: expect.objectContaining({ "x-user-id": "browser-user" }),
    }));
  });

  it.each([401, 503])("preserves HTTP %s so login expiry is distinguished from a service interruption", async (status) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: "request failed" }), { status })));
    const error = await dayTradingV2Client.notifications("browser-user").catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(DayTradingV2RequestError);
    expect(error).toMatchObject({ status, message: "request failed" });
  });
});
