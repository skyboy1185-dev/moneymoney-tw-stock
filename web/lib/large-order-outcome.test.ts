import { describe, expect, it } from "vitest";
import type { ElectronicChipFlowAlert, ElectronicChipFlowPricePoint } from "@/lib/electronic-chip-flow-alerts";
import { evaluateLargeOrderOutcomes } from "@/lib/large-order-outcome";

const alert = {
  firstDetectedAt: "2026-08-20T09:00:00+08:00",
  cycleStartedAt: "2026-08-20T09:00:00+08:00",
  direction: "long",
} as ElectronicChipFlowAlert;

const points = [
  ["09:00", 100], ["09:05", 101], ["09:15", 99], ["09:30", 103],
].map(([time, price]) => ({
  timestamp: `2026-08-20T${time}:00+08:00`, price, isRealtime: true,
})) as ElectronicChipFlowPricePoint[];

describe("evaluateLargeOrderOutcomes", () => {
  it("calculates direction-adjusted 5/15/30 minute returns", () => {
    expect(evaluateLargeOrderOutcomes(alert, points).map((item) => item.returnPercent)).toEqual([1, -1, 3]);
    expect(evaluateLargeOrderOutcomes({ ...alert, direction: "short" }, points).map((item) => item.returnPercent)).toEqual([-1, 1, -3]);
  });

  it("keeps horizons pending until enough time has elapsed", () => {
    expect(evaluateLargeOrderOutcomes(alert, points.slice(0, 2)).map((item) => item.status)).toEqual([
      "evaluated", "pending", "pending",
    ]);
  });
});
