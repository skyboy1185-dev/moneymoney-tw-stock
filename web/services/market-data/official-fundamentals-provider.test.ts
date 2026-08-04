import { describe, expect, it } from "vitest";
import type { StockMeta } from "@/lib/types";
import { parseOfficialFundamentals } from "./official-fundamentals-provider";

const listed: StockMeta = {
  symbol: "2317",
  name: "鴻海",
  market: "上市",
  industry: "暫無資料",
  peRatio: null,
  dividendYield: null,
  priceToBook: null,
  eps: null,
  marketCap: null,
};

describe("official fundamentals provider", () => {
  it("parses TWSE valuation, trailing EPS, industry and live market cap", () => {
    const result = parseOfficialFundamentals(listed, {
      valuation: [{
        Date: "1150727", Code: "2317", PEratio: "17.97",
        DividendYield: "2.83", PBratio: "1.99",
      }],
      companies: [{
        公司代號: "2317", 產業別: "31",
        已發行普通股數或TDR原股發行股數: "14028648626",
      }],
      closes: [{ Code: "2317", ClosingPrice: "253.00" }],
    }, 238);

    expect(result).toMatchObject({
      industry: "其他電子業",
      peRatio: 17.97,
      dividendYield: 2.83,
      priceToBook: 1.99,
      fundamentalsDate: "2026-07-27",
      fundamentalsSource: "TWSE 官方基本資料",
    });
    expect(result.eps).toBeCloseTo(14.08, 2);
    expect(result.marketCap).toBeCloseTo(3_338_818_372_988, -3);
  });

  it("keeps unavailable negative-earnings PE and EPS empty without losing other fields", () => {
    const result = parseOfficialFundamentals({ ...listed, symbol: "TEST" }, {
      valuation: [{
        Date: "1150727", Code: "TEST", PEratio: "",
        DividendYield: "0.00", PBratio: "1.25",
      }],
      companies: [{
        公司代號: "TEST", 產業別: "24",
        已發行普通股數或TDR原股發行股數: "1000000",
      }],
      closes: [{ Code: "TEST", ClosingPrice: "50" }],
    }, 48);

    expect(result.peRatio).toBeNull();
    expect(result.eps).toBeNull();
    expect(result.dividendYield).toBe(0);
    expect(result.priceToBook).toBe(1.25);
    expect(result.marketCap).toBe(48_000_000);
  });
});
