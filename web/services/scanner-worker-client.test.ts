import { describe, expect, it } from "vitest";

import { scannerWorkerErrorPayload } from "./scanner-worker-client";

describe("scannerWorkerErrorPayload", () => {
  it("marks scanner worker failures as explicit scanner errors", () => {
    expect(scannerWorkerErrorPayload(new Error("fetch failed"))).toEqual({
      error: "fetch failed",
      status: "scanner_error",
      statusCode: 503,
    });
  });
});
