import { describe, expect, it } from "vitest";

import { resolveAdaptiveIndustryCode } from "@/services/adaptive-electronic-service";

describe("resolveAdaptiveIndustryCode", () => {
  it("keeps official codes and derives a fallback code from the industry", () => {
    expect(resolveAdaptiveIndustryCode("24", "半導體")).toBe("24");
    expect(resolveAdaptiveIndustryCode("", "半導體")).toBe("24");
  });

  it("uses an explicit unknown code instead of sending an invalid empty value", () => {
    expect(resolveAdaptiveIndustryCode(undefined, "跨產業題材")).toBe("00");
  });
});
