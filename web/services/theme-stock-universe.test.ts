import { describe, expect, it } from "vitest";
import { stockCatalog, thematicStockCatalog } from "@/services/stock-service";
import { isTargetThemeSymbol, TARGET_STOCK_THEMES, themesForSymbol } from "@/services/theme-stock-universe";

describe("AI supply-chain and LEO theme stock universe", () => {
  it("preserves AI supply-chain subthemes and low-earth-orbit tags", () => {
    expect(themesForSymbol("2330")).toEqual(["AI"]);
    expect(themesForSymbol("3491")).toEqual(["低軌衛星"]);
    expect(themesForSymbol("6285")).toEqual(["AI", "低軌衛星"]);
    expect(themesForSymbol("3037")).toEqual(["AI", "PCB", "ABF載板"]);
    expect(themesForSymbol("2327")).toEqual(["AI", "被動元件"]);
    expect(themesForSymbol("2408")).toEqual(["AI", "記憶體"]);
    expect(themesForSymbol("1815")).toEqual(["AI", "玻纖布"]);
    expect(themesForSymbol("1303")).toEqual(["玻纖布"]);
    expect(themesForSymbol("2404")).toEqual(["廠務工程"]);
    expect(themesForSymbol("3661")).toEqual(["AI", "IC設計"]);
  });

  it("excludes unrelated stocks from both robot scans", () => {
    expect(isTargetThemeSymbol("2603")).toBe(false);
    expect(stockCatalog.some((stock) => stock.symbol === "2603")).toBe(true);
    expect(thematicStockCatalog.some((stock) => stock.symbol === "2603")).toBe(false);
    expect(thematicStockCatalog.every((stock) => stock.themes?.length)).toBe(true);
    expect(thematicStockCatalog.every((stock) => stock.themes?.some((theme) => TARGET_STOCK_THEMES.includes(theme)))).toBe(true);
  });
});
