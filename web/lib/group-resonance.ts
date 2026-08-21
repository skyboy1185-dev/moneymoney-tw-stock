import type {
  ElectronicChipFlowAlert,
  ElectronicChipFlowQuote,
} from "@/lib/electronic-chip-flow-alerts";

export interface GroupResonance {
  group: string;
  direction: "up" | "down";
  count: number;
  symbols: string[];
  names: string[];
  averageChangePercent: number;
}

const BROAD_THEMES = new Set(["AI"]);
const MIN_GROUP_STOCKS = 2;
const MIN_AVERAGE_MOVE_PERCENT = 0.5;

function groupsFor(alert: ElectronicChipFlowAlert): string[] {
  const specificThemes = (alert.themes ?? []).filter((theme) => !BROAD_THEMES.has(theme));
  return specificThemes.length ? specificThemes : [`${alert.industry}族群`];
}

export function detectGroupResonances(
  alerts: ElectronicChipFlowAlert[],
  quotes: Record<string, ElectronicChipFlowQuote>,
): GroupResonance[] {
  const groups = new Map<string, Map<string, ElectronicChipFlowAlert>>();
  alerts.forEach((alert) => {
    if (!quotes[alert.symbol]) return;
    groupsFor(alert).forEach((group) => {
      const members = groups.get(group) ?? new Map<string, ElectronicChipFlowAlert>();
      members.set(alert.symbol, alert);
      groups.set(group, members);
    });
  });

  const resonances: GroupResonance[] = [];
  groups.forEach((members, group) => {
    const stocks = [...members.values()];
    if (stocks.length < MIN_GROUP_STOCKS) return;
    const changes = stocks.map((stock) => quotes[stock.symbol].changePercent);
    const allUp = changes.every((change) => change > 0);
    const allDown = changes.every((change) => change < 0);
    if (!allUp && !allDown) return;
    const averageChangePercent = changes.reduce((sum, change) => sum + change, 0) / changes.length;
    if (Math.abs(averageChangePercent) < MIN_AVERAGE_MOVE_PERCENT) return;
    resonances.push({
      group,
      direction: allUp ? "up" : "down",
      count: stocks.length,
      symbols: stocks.map((stock) => stock.symbol),
      names: stocks.map((stock) => stock.name),
      averageChangePercent,
    });
  });

  return resonances.sort((left, right) =>
    right.count - left.count
    || Math.abs(right.averageChangePercent) - Math.abs(left.averageChangePercent),
  );
}
