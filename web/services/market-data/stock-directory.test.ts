import { describe, expect, it } from "vitest";
import { findStockInDirectory, parseOfficialStockDirectory } from "./stock-directory";

describe("official stock directory", () => {
  const stocks = parseOfficialStockDirectory(
    [
      { Code: "2330", Name: "台積電" },
      { Code: "2412", Name: "中華電" },
    ],
    [
      { SecuritiesCompanyCode: "6488", CompanyName: "環球晶" },
      { SecuritiesCompanyCode: "8299", CompanyName: "群聯" },
      { SecuritiesCompanyCode: "8043", CompanyName: "蜜望實" },
    ],
  );

  it("parses listed and OTC rows", () => {
    expect(stocks).toHaveLength(5);
    expect(stocks.find((stock) => stock.symbol === "2412")).toMatchObject({
      name: "中華電",
      market: "上市",
    });
    expect(stocks.find((stock) => stock.symbol === "8299")).toMatchObject({
      name: "群聯",
      market: "上櫃",
    });
  });

  it("finds stocks by exact symbol or exact name", () => {
    expect(findStockInDirectory(stocks, "2412")?.name).toBe("中華電");
    expect(findStockInDirectory(stocks, "群聯")?.symbol).toBe("8299");
    expect(findStockInDirectory(stocks, "8043")).toMatchObject({
      name: "蜜望實",
      market: "上櫃",
    });
    expect(findStockInDirectory(stocks, "蜜望實")?.symbol).toBe("8043");
  });

  it("supports partial names and ignores spaces", () => {
    expect(findStockInDirectory(stocks, "中華")?.symbol).toBe("2412");
    expect(findStockInDirectory(stocks, " 台 積 電 ")?.symbol).toBe("2330");
  });
});
