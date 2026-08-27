import type { StockDeductionSignals, DeductionSignalMatch } from "@/lib/deduction-signals";
import type { ElectronicChipFlowAlert } from "@/lib/electronic-chip-flow-alerts";

export interface DingSelectionRow {
  symbol: string;
  name: string;
  market: ElectronicChipFlowAlert["market"];
  sourceRank: number;
  currentPrice: number | null;
  matches: DeductionSignalMatch[];
  calculatedAt: string;
  asOfDate?: string | null;
  latestPriceDate?: string | null;
}

function validAsOfDate(value: string | undefined, asOfDate: string): boolean {
  return Boolean(value) && value! <= asOfDate;
}

export function buildDingSelectionRows(
  signals: StockDeductionSignals[],
  sourceAlerts: ElectronicChipFlowAlert[],
  asOfDate: string,
  limit = 10,
): DingSelectionRow[] {
  const alertsBySymbol = new Map(sourceAlerts.map((alert, index) => [
    alert.symbol,
    { alert, sourceRank: alert.rank ?? index + 1 },
  ]));
  return signals.flatMap((signal): DingSelectionRow[] => {
    const source = alertsBySymbol.get(signal.symbol);
    if (!source) return [];
    const matches = signal.matches.filter((match) => validAsOfDate(match.signalDate, asOfDate));
    if (!matches.length) return [];
    return [{
      symbol: signal.symbol,
      name: source.alert.name,
      market: source.alert.market,
      sourceRank: source.sourceRank,
      currentPrice: signal.currentPrice,
      matches,
      calculatedAt: signal.calculatedAt,
      asOfDate: signal.asOfDate,
      latestPriceDate: signal.latestPriceDate,
    }];
  }).sort((left, right) => {
    if (left.sourceRank !== right.sourceRank) return left.sourceRank - right.sourceRank;
    return right.matches.length - left.matches.length || left.symbol.localeCompare(right.symbol);
  }).slice(0, limit);
}
