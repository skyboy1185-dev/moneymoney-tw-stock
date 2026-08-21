import { describe, expect, it } from "vitest";
import { normalizeAIStockUniverse } from "@/services/ai-stock-universe-service";

describe("AI stock universe", () => {
  it("accepts every supported AI supply-chain theme and rejects malformed rows", () => {
    const rows = normalizeAIStockUniverse({ items: [
      { symbol: "4979", name: "華星光", market: "上櫃", industry: "通信網路", themes: ["CPO／矽光子"] },
      { symbol: "6239", name: "力成", market: "上市", industry: "半導體", themes: ["半導體封測"] },
      { symbol: "2301", name: "光寶科", market: "上市", industry: "電腦及週邊", themes: ["電源／電力"] },
      { symbol: "2603", name: "長榮", market: "上市", industry: "航運", themes: ["航運"] },
    ] });

    expect(rows.map((row) => row.symbol)).toEqual(["4979", "6239", "2301"]);
    expect(rows.flatMap((row) => row.themes ?? [])).toEqual([
      "CPO／矽光子", "半導體封測", "電源／電力",
    ]);
  });
});
