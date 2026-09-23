import { describe, expect, it } from "vitest";
import { compactNotificationTitle } from "./in-app-notifications";

describe("compact in-app notifications", () => {
  it("uses a quiet aggregate label for ordinary bursts", () => {
    expect(compactNotificationTitle(3, false)).toBe("新增 3 則盤中訊息");
  });

  it("keeps critical bursts explicit", () => {
    expect(compactNotificationTitle(2, true)).toBe("新增 2 則重大盤中訊息");
  });

  it("does not aggregate one item", () => {
    expect(compactNotificationTitle(1, false)).toBe("");
  });
});
