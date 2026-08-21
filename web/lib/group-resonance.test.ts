import { describe, expect, it } from "vitest";
import type { ElectronicChipFlowAlert, ElectronicChipFlowQuote } from "@/lib/electronic-chip-flow-alerts";
import { detectGroupResonances } from "@/lib/group-resonance";

function alert(symbol: string, name: string, themes: string[]): ElectronicChipFlowAlert {
  return { symbol, name, themes, industry: "半導體" } as ElectronicChipFlowAlert;
}

function quote(symbol: string, changePercent: number): ElectronicChipFlowQuote {
  return { symbol, changePercent } as ElectronicChipFlowQuote;
}

describe("detectGroupResonances", () => {
  it("flags two stocks in the same specific theme moving up together", () => {
    const alerts = [alert("2337", "旺宏", ["AI", "記憶體"]), alert("2344", "華邦電", ["AI", "記憶體"])];
    const result = detectGroupResonances(alerts, {
      "2337": quote("2337", 1.2),
      "2344": quote("2344", 0.8),
    });
    expect(result[0]).toMatchObject({ group: "記憶體", direction: "up", count: 2 });
  });

  it("does not flag a mixed-direction group", () => {
    const alerts = [alert("2337", "旺宏", ["記憶體"]), alert("2344", "華邦電", ["記憶體"])];
    expect(detectGroupResonances(alerts, {
      "2337": quote("2337", 1.2),
      "2344": quote("2344", -0.8),
    })).toEqual([]);
  });

  it("ignores insignificant synchronized movement", () => {
    const alerts = [alert("2337", "旺宏", ["記憶體"]), alert("2344", "華邦電", ["記憶體"])];
    expect(detectGroupResonances(alerts, {
      "2337": quote("2337", 0.2),
      "2344": quote("2344", 0.3),
    })).toEqual([]);
  });
});
