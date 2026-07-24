import type { MarketContext, MarketForceResult, MarketRegime } from "@/lib/market-types";

export type RegimeInputs = Omit<MarketContext, keyof MarketForceResult | "regime">;

export class MarketRegimeDetector {
  private candidate: MarketRegime | null = null;
  private occurrences = 0;
  private active: MarketRegime = "range";

  detectMarketRegime(force: MarketForceResult, input: RegimeInputs): MarketRegime {
    let detected: MarketRegime;
    const range = input.adx < 20 && Math.abs(input.ma20Slope) < .08 && Math.abs(force.score) < 20;
    const up = input.indexAboveMa20 && input.indexAboveMa60 && input.ma20Slope > 0 && input.macdAboveZero && input.adx > 20 && force.score > 20;
    const down = !input.indexAboveMa20 && !input.indexAboveMa60 && input.ma20Slope < 0 && !input.macdAboveZero && input.adx > 20 && force.score < -20;
    if (range) detected = "range";
    else if (up) detected = "wave_up";
    else if (down) detected = "wave_down";
    else detected = "transition";

    if (detected === this.active) { this.candidate = null; this.occurrences = 0; return this.active; }
    if (this.candidate === detected) this.occurrences += 1;
    else { this.candidate = detected; this.occurrences = 1; }
    if (this.occurrences >= 3) { this.active = detected; this.candidate = null; this.occurrences = 0; }
    return this.active;
  }
}

export const marketRegimeDetector = new MarketRegimeDetector();
