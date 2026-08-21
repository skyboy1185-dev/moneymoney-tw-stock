import type { ChipDefenseResult } from "./chip-defense";

export interface MarketIndexDefenseSnapshot {
  indexName: string;
  currentPrice: number;
  source: string;
  quoteAt: string;
  calculationNote: string;
  defense: ChipDefenseResult;
}

export interface MarketIndexDefenseResponse extends MarketIndexDefenseSnapshot {
  otc: MarketIndexDefenseSnapshot | null;
  otcError?: string;
}
