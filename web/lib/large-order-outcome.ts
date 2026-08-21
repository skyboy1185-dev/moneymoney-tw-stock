import type {
  ElectronicChipFlowAlert,
  ElectronicChipFlowPricePoint,
} from "@/lib/electronic-chip-flow-alerts";

export interface LargeOrderOutcomePoint {
  minutes: 5 | 15 | 30;
  status: "pending" | "evaluated" | "unavailable";
  returnPercent: number | null;
}

const HORIZONS = [5, 15, 30] as const;
const MAX_POINT_GAP_MS = 2 * 60 * 1_000;

function firstPointAtOrAfter(
  points: ElectronicChipFlowPricePoint[],
  target: number,
): ElectronicChipFlowPricePoint | undefined {
  return points.find((point) => {
    const timestamp = Date.parse(point.timestamp);
    return timestamp >= target && timestamp - target <= MAX_POINT_GAP_MS;
  });
}

export function evaluateLargeOrderOutcomes(
  alert: ElectronicChipFlowAlert,
  rawPoints: ElectronicChipFlowPricePoint[],
): LargeOrderOutcomePoint[] {
  const signalAt = Date.parse(alert.cycleStartedAt || alert.firstDetectedAt);
  const points = rawPoints
    .filter((point) => point.price > 0 && Number.isFinite(Date.parse(point.timestamp)))
    .sort((left, right) => Date.parse(left.timestamp) - Date.parse(right.timestamp));
  if (!Number.isFinite(signalAt) || points.length === 0) {
    return HORIZONS.map((minutes) => ({ minutes, status: "unavailable", returnPercent: null }));
  }
  const entry = firstPointAtOrAfter(points, signalAt);
  if (!entry) {
    return HORIZONS.map((minutes) => ({ minutes, status: "unavailable", returnPercent: null }));
  }
  const latestAt = Date.parse(points[points.length - 1].timestamp);
  const direction = alert.direction === "short" ? -1 : 1;
  return HORIZONS.map((minutes) => {
    const target = signalAt + minutes * 60 * 1_000;
    if (latestAt < target) return { minutes, status: "pending", returnPercent: null };
    const outcome = firstPointAtOrAfter(points, target);
    if (!outcome) return { minutes, status: "unavailable", returnPercent: null };
    return {
      minutes,
      status: "evaluated",
      returnPercent: Math.round(((outcome.price / entry.price - 1) * 100 * direction) * 100) / 100,
    };
  });
}
