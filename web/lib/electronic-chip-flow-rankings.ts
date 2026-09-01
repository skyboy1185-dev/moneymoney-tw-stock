import type { ElectronicChipFlowAlert, ElectronicChipFlowAlertsResponse } from "@/lib/electronic-chip-flow-alerts";

export type LargeOrderRankChangeType = "new" | "up" | "down" | "out";

export interface LargeOrderRankChangeEvent {
  type: LargeOrderRankChangeType;
  direction: "long" | "short";
  alert: ElectronicChipFlowAlert;
  symbol: string;
  name: string;
  previousRank?: number;
  currentRank?: number;
  rankDelta: number;
}

function withDisplayRanks(alerts: ElectronicChipFlowAlert[]): ElectronicChipFlowAlert[] {
  return alerts.map((alert, index) => {
    if (Number.isFinite(alert.rank) && (alert.rank ?? 0) > 0) return alert;
    return { ...alert, rank: index + 1 };
  });
}

function numeric(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function matchesNetDirection(alert: ElectronicChipFlowAlert, direction: "long" | "short"): boolean {
  if (alert.largeOrderOffsetting) return false;
  const sessionNet = numeric(alert.sessionNetBuyLots) - numeric(alert.sessionNetSellLots);
  const netLots = sessionNet !== 0 ? sessionNet : numeric(alert.largeNetLots);
  return direction === "short" ? netLots < 0 : netLots > 0;
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
    return withDisplayRanks(rankings.filter((alert) => matchesNetDirection(alert, "short")));
  }
  const rankings = Array.isArray(data.longRankings)
    ? data.longRankings
    : Array.isArray(data.alerts) ? data.alerts : [];
  return withDisplayRanks(rankings.filter((alert) => matchesNetDirection(alert, "long")));
}

function rankMap(alerts: ElectronicChipFlowAlert[]): Map<string, ElectronicChipFlowAlert> {
  return new Map(withDisplayRanks(alerts).map((alert) => [alert.symbol, alert]));
}

export function annotateLargeOrderRankChanges(
  current: ElectronicChipFlowAlert[],
  previous: ElectronicChipFlowAlert[],
): ElectronicChipFlowAlert[] {
  const previousBySymbol = rankMap(previous);
  return withDisplayRanks(current).map((alert) => {
    const previousAlert = previousBySymbol.get(alert.symbol);
    const currentRank = alert.rank ?? 0;
    const previousRank = previousAlert?.rank;
    if (!previousRank || !currentRank) {
      return { ...alert, rankChangeType: previousRank ? "same" : "new" };
    }
    const rankDelta = previousRank - currentRank;
    const rankChangeType = rankDelta > 0 ? "up" : rankDelta < 0 ? "down" : "same";
    return { ...alert, previousRank, rankDelta, rankChangeType };
  });
}

export function selectMajorLargeOrderRankChangeEvents(
  current: ElectronicChipFlowAlert[],
  previous: ElectronicChipFlowAlert[],
  direction: "long" | "short",
  {
    minimumRankDelta = 3,
    outPreviousRankLimit = 5,
  }: {
    minimumRankDelta?: number;
    outPreviousRankLimit?: number;
  } = {},
): LargeOrderRankChangeEvent[] {
  const currentRanked = annotateLargeOrderRankChanges(current, previous);
  const previousRanked = withDisplayRanks(previous);
  const currentBySymbol = rankMap(currentRanked);
  const events: LargeOrderRankChangeEvent[] = [];

  for (const alert of currentRanked) {
    const rankDelta = alert.rankDelta ?? 0;
    if (alert.rankChangeType === "new") {
      events.push({
        type: "new", direction, alert, symbol: alert.symbol, name: alert.name,
        currentRank: alert.rank, rankDelta: 0,
      });
    } else if (alert.rankChangeType === "up" && rankDelta >= minimumRankDelta) {
      events.push({
        type: "up", direction, alert, symbol: alert.symbol, name: alert.name,
        previousRank: alert.previousRank, currentRank: alert.rank, rankDelta,
      });
    } else if (alert.rankChangeType === "down" && Math.abs(rankDelta) >= minimumRankDelta) {
      events.push({
        type: "down", direction, alert, symbol: alert.symbol, name: alert.name,
        previousRank: alert.previousRank, currentRank: alert.rank, rankDelta,
      });
    }
  }

  for (const alert of previousRanked) {
    if (currentBySymbol.has(alert.symbol)) continue;
    const previousRank = alert.rank;
    if (!previousRank || previousRank > outPreviousRankLimit) continue;
    events.push({
      type: "out", direction, alert: { ...alert, rankChangeType: "out", previousRank, rankDelta: -999 },
      symbol: alert.symbol, name: alert.name, previousRank, rankDelta: -999,
    });
  }

  return events.sort((left, right) => {
    const priority = { new: 4, up: 3, out: 2, down: 1 } as const;
    if (priority[left.type] !== priority[right.type]) return priority[right.type] - priority[left.type];
    return (left.currentRank ?? left.previousRank ?? 99) - (right.currentRank ?? right.previousRank ?? 99);
  });
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
