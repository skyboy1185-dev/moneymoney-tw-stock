import { describe, expect, it } from "vitest";
import { MockStockDataProvider } from "./stock-service";
import type { ScreenerFilters } from "@/lib/types";

const emptyFilters: ScreenerFilters = {
  minPrice: "", maxPrice: "", minVolume: "", minChange: "", maxChange: "",
  industry: "", market: "", technical: [],
};

describe("MockStockDataProvider", () => {
  const provider = new MockStockDataProvider();

  it("可用代號或中文名稱搜尋並回傳完整行情與指標", async () => {
    const byName = await provider.search("台積電");
    const bySymbol = await provider.search("2330");
    expect(byName?.symbol).toBe("2330");
    expect(bySymbol?.name).toBe("台積電");

    const stock = await provider.getStock("2330");
    expect(stock?.prices).toHaveLength(5280);
    expect(stock?.indicators).toHaveLength(5280);
    expect(stock?.prices.at(-1)?.date).toBe("2026-07-24");
  });

  it("今日 MACD 翻紅與翻綠使用同一套訊號欄位", async () => {
    const all = await provider.screen(emptyFilters);
    const entries = await provider.screen({ ...emptyFilters, technical: ["macdEntryToday"] });
    const exits = await provider.screen({ ...emptyFilters, technical: ["macdExitToday"] });
    expect(entries.length).toBeGreaterThan(0);
    expect(exits.length).toBeGreaterThan(0);
    expect(entries.every((row) => row.flags.includes("macdEntryToday"))).toBe(true);
    expect(exits.every((row) => row.flags.includes("macdExitToday"))).toBe(true);
    expect(all.length).toBeGreaterThanOrEqual(entries.length + exits.length);
  });

  it("同時套用基本條件與技術條件", async () => {
    const rows = await provider.screen({
      ...emptyFilters,
      minPrice: "100",
      market: "上市",
      technical: ["aboveMa5"],
    });
    expect(rows.every((row) => row.price >= 100 && row.market === "上市" && row.flags.includes("aboveMa5"))).toBe(true);
  });
});
