import { describe, expect, it } from "vitest";
import { toTaipeiChartTimestamp } from "./chip-flow-time";

describe("toTaipeiChartTimestamp", () => {
  it("keeps 09:00 Taipei wall-clock time on the chart axis", () => {
    const timestamp = toTaipeiChartTimestamp("2026-07-29T09:00:00+08:00");
    expect(new Date(timestamp * 1_000).toISOString()).toBe("2026-07-29T09:00:00.000Z");
  });

  it("keeps the 13:30 closing-auction label", () => {
    const timestamp = toTaipeiChartTimestamp("2026-07-29T13:30:00+08:00");
    expect(new Date(timestamp * 1_000).toISOString()).toBe("2026-07-29T13:30:00.000Z");
  });

  it("rejects malformed timestamps instead of silently shifting the axis", () => {
    expect(() => toTaipeiChartTimestamp("09:00")).toThrow(RangeError);
  });
});
