import type { ManualSignalMode, Timeframe } from "./market-types";

export interface ManualStrategyMatch {
  strategyId: string;
  strategyName: string;
  timeframe: Timeframe;
  signalMode: ManualSignalMode;
  signalDate: string;
  signalStatus: "temporary" | "confirmed";
}

export interface StockManualStrategyMatches {
  symbol: string;
  matches: ManualStrategyMatch[];
  calculatedAt: string;
  asOfDate?: string | null;
  latestPriceDate?: string | null;
  error?: string | null;
}

export function selectVisibleManualStrategyMatches(
  result: StockManualStrategyMatches | undefined,
  asOfDate: string,
  limit = 3,
): { visible: ManualStrategyMatch[]; hiddenCount: number; all: ManualStrategyMatch[] } {
  const all = (result?.matches ?? []).filter((match) => match.signalDate <= asOfDate);
  return {
    visible: all.slice(0, Math.max(0, limit)),
    hiddenCount: Math.max(0, all.length - Math.max(0, limit)),
    all,
  };
}
