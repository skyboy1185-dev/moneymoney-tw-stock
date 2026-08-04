import { describe, expect, it } from "vitest";
import type { PowerScoreResult } from "@/lib/power-score";
import type { StockMeta } from "@/lib/types";
import {
  electronicPowerUniverse,
  rankElectronicPowerRows,
  type LeaderPowerRow,
} from "./leader-power-service";

function stock(symbol: string, industry: string): StockMeta {
  return {
    symbol,
    name: symbol,
    industry,
    market: "上市",
    peRatio: null,
    dividendYield: null,
    priceToBook: null,
    eps: null,
    marketCap: null,
  };
}

function row(
  symbol: string,
  powerValue: number,
  healthScore: number,
  changePercent: number,
): LeaderPowerRow {
  return {
    rank: 99,
    symbol,
    name: symbol,
    weight: null,
    industry: "半導體",
    price: 100,
    changePercent,
    quoteSource: "test",
    quoteTime: "test",
    score: { powerValue, healthScore } as PowerScoreResult,
  };
}

describe("electronic leader power ranking", () => {
  it("keeps only electronic industries", () => {
    const result = electronicPowerUniverse([
      stock("2330", "半導體"),
      stock("2308", "電子零組件"),
      stock("2881", "金融保險"),
      stock("2603", "航運業"),
    ]);

    expect(result.map((item) => item.symbol)).toEqual(["2330", "2308"]);
  });

  it("sorts by power, health and daily change before assigning rank", () => {
    const result = rankElectronicPowerRows([
      row("A", 12, 80, 1),
      row("B", 13, 70, 1),
      row("C", 13, 75, -1),
      row("D", 13, 75, 2),
    ]);

    expect(result.map((item) => item.symbol)).toEqual(["D", "C", "B", "A"]);
    expect(result.map((item) => item.rank)).toEqual([1, 2, 3, 4]);
  });

  it("returns at most fifteen rows", () => {
    const result = rankElectronicPowerRows(
      Array.from({ length: 20 }, (_, index) =>
        row(String(index).padStart(2, "0"), 17 - index % 4, 90 - index, index),
      ),
    );

    expect(result).toHaveLength(15);
  });
});
