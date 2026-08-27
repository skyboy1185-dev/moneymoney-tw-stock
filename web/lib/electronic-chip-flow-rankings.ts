import type { ElectronicChipFlowAlert, ElectronicChipFlowAlertsResponse } from "@/lib/electronic-chip-flow-alerts";

export function selectLargeOrderRankings(
  data: ElectronicChipFlowAlertsResponse | null | undefined,
  direction: "long" | "short",
): ElectronicChipFlowAlert[] {
  if (!data) return [];
  if (direction === "short") {
    return Array.isArray(data.shortRankings)
      ? data.shortRankings
      : Array.isArray(data.shortAlerts) ? data.shortAlerts : [];
  }
  return Array.isArray(data.longRankings)
    ? data.longRankings
    : Array.isArray(data.alerts) ? data.alerts : [];
}
