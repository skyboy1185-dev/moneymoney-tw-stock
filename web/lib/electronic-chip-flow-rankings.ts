import type { ElectronicChipFlowAlert, ElectronicChipFlowAlertsResponse } from "@/lib/electronic-chip-flow-alerts";

function withDisplayRanks(alerts: ElectronicChipFlowAlert[]): ElectronicChipFlowAlert[] {
  return alerts.map((alert, index) => {
    if (Number.isFinite(alert.rank) && (alert.rank ?? 0) > 0) return alert;
    return { ...alert, rank: index + 1 };
  });
}

export function selectLargeOrderRankings(
  data: ElectronicChipFlowAlertsResponse | null | undefined,
  direction: "long" | "short",
): ElectronicChipFlowAlert[] {
  if (!data) return [];
  if (direction === "short") {
    const rankings = Array.isArray(data.shortRankings)
      ? data.shortRankings
      : Array.isArray(data.shortAlerts) ? data.shortAlerts : [];
    return withDisplayRanks(rankings);
  }
  const rankings = Array.isArray(data.longRankings)
    ? data.longRankings
    : Array.isArray(data.alerts) ? data.alerts : [];
  return withDisplayRanks(rankings);
}
