import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GET } from "./route";

vi.mock("@/lib/runtime-config", () => ({ getBackendBaseUrl: () => "http://backend.test" }));
afterEach(() => vi.unstubAllGlobals());

describe("read-only robot health proxy", () => {
  it("requires the current user and never substitutes an automation account", async () => {
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    const response = await GET(new NextRequest("http://localhost/api/robot-health"));
    expect(response.status).toBe(400);
    expect(fetch).not.toHaveBeenCalled();
  });
  it("forwards user identity with no-store and preserves upstream failures", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response('{"error":"unavailable"}', { status: 503, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetch);
    const response = await GET(new NextRequest("http://localhost/api/robot-health", { headers: { "x-user-id": "browser-current-user" } }));
    expect(fetch).toHaveBeenCalledWith("http://backend.test/api/v1/robot-health", expect.objectContaining({ headers: { "x-user-id": "browser-current-user" }, cache: "no-store" }));
    expect(response.status).toBe(503);
    expect(response.headers.get("cache-control")).toBe("no-store");
  });
});
