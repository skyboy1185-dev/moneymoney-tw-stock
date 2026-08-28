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

export type LargeOrderMomentumToastKind = "reinforced" | "joint" | "surge";

export interface LargeOrderMomentumToastCandidate {
  alert: ElectronicChipFlowAlert;
  kind: LargeOrderMomentumToastKind;
}

function momentumToastKind(alert: ElectronicChipFlowAlert): LargeOrderMomentumToastKind | null {
  if (alert.isWarning || alert.trend === "weakening" || alert.trend === "fading" || alert.alertLevel === "critical") {
    return null;
  }
  if (alert.simultaneousIncrease) return "joint";
  if (alert.reinforced) return "reinforced";
  if (alert.currentQualifies) return "surge";
  return null;
}

export function selectLargeOrderMomentumToastCandidates(
  data: ElectronicChipFlowAlertsResponse | null | undefined,
): LargeOrderMomentumToastCandidate[] {
  if (!data) return [];
  const candidates = new Map<string, LargeOrderMomentumToastCandidate>();

  data.alerts.forEach((alert) => {
    const kind = momentumToastKind(alert);
    if (!kind) return;
    candidates.set(alert.symbol, { alert, kind });
  });

  return [...candidates.values()];
}
