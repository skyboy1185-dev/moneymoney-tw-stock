import { describe, expect, it } from "vitest";
import type { StockMeta } from "@/lib/types";
import { findStockInDirectory } from "./stock-directory";

const stocks: StockMeta[] = [
  {
    symbol: "2308",
    name: "台達電",
    market: "上市",
    industry: "電子零組件",
    peRatio: null,
    dividendYield: null,
    priceToBook: null,
    eps: null,
    marketCap: null,
  },
  {
    symbol: "2330",
    name: "台積電",
    market: "上市",
    industry: "半導體",
    peRatio: null,
    dividendYield: null,
    priceToBook: null,
    eps: null,
    marketCap: null,
  },
];

describe("findStockInDirectory", () => {
  it("resolves an exact Chinese stock name", () => {
    expect(findStockInDirectory(stocks, "台達電")?.symbol).toBe("2308");
  });

  it("resolves a partial Chinese stock name", () => {
    expect(findStockInDirectory(stocks, "台積")?.symbol).toBe("2330");
  });

  it("keeps stock-symbol lookup working", () => {
    expect(findStockInDirectory(stocks, "2308")?.name).toBe("台達電");
  });
});
