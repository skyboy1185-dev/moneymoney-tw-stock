import { describe, expect, it } from "vitest";
import {
  buildRetailFuturesPosition,
  combineInstitutionPeriods,
  parseInstitutionTable,
  parseTaifexInstitutionalOpenInterest,
  parseTaifexInstitutionalCsv,
  parseTaifexForeignNetCsv,
  parseTaifexMarketOpenInterest,
  parseTaifexMarketOpenInterestCsv,
  periodEndFromTitle,
} from "./institutional-investor-service";

const twseRows = [
  ["自營商(自行買賣)", "10", "13", "-3"],
  ["自營商(避險)", "20", "15", "5"],
  ["投信", "30", "22", "8"],
  ["外資及陸資(不含外資自營商)", "40", "51", "-11"],
];

const tpexRows = [
  ["　外資及陸資(不含自營商)", "9", "4", "5"],
  ["投信", "6", "8", "-2"],
  ["自營商合計", "7", "10", "-3"],
  ["　自營商(自行買賣)", "2", "4", "-2"],
  ["　自營商(避險)", "5", "6", "-1"],
];

describe("institutional-investor-service", () => {
  it("解析上市資料並合併自營商自行買賣與避險", () => {
    expect(parseInstitutionTable(twseRows)).toEqual({
      foreign: { buy: 40, sell: 51, net: -11 },
      trust: { buy: 30, sell: 22, net: 8 },
      dealer: { buy: 30, sell: 28, net: 2 },
    });
  });

  it("解析上櫃資料時使用自營商合計", () => {
    expect(parseInstitutionTable(tpexRows).dealer).toEqual({ buy: 7, sell: 10, net: -3 });
  });

  it("合併上市與上櫃並重新計算三大法人合計", () => {
    const listed = parseInstitutionTable(twseRows);
    const otc = parseInstitutionTable(tpexRows);
    const rows = combineInstitutionPeriods(listed, otc, listed, otc, listed, otc);
    expect(rows.find(({ id }) => id === "foreign")?.day.total.net).toBe(-6);
    expect(rows.find(({ id }) => id === "total")?.day.total).toEqual({
      buy: 122,
      sell: 123,
      net: -1,
    });
  });

  it("從證交所區間標題取得最後一個交易日", () => {
    expect(periodEndFromTitle("115年07月01日至115年07月28日 三大法人買賣金額統計表"))
      .toBe("2026-07-28");
  });

  it("從期交所行情表加總各月份全市場未平倉量並排除價差契約", () => {
    const html = `<table>
      <tr><td>MTX</td><td>202608</td>${"<td>0</td>".repeat(10)}<td>30,000</td></tr>
      <tr><td>MTX</td><td>202609</td>${"<td>0</td>".repeat(10)}<td>5,000</td></tr>
      <tr><td>MTX</td><td>202608/202609</td>${"<td>0</td>".repeat(10)}<td>999</td></tr>
    </table>`;
    expect(parseTaifexMarketOpenInterest(html, "MTX")).toBe(35_000);
  });

  it("從期交所三大法人表加總小台多空未平倉量", () => {
    const row = (cells: Array<string | number>) => `<tr>${cells.map((cell) => `<td>${cell}</td>`).join("")}</tr>`;
    const html = `<table>
      ${row([4, "小型臺指期貨", "自營商", 0, 0, 0, 0, 0, 0, "1,500", 0, "4,000", 0])}
      ${row(["投信", 0, 0, 0, 0, 0, 0, "100", 0, "200", 0])}
      ${row(["外資", 0, 0, 0, 0, 0, 0, "400", 0, "800", 0])}
    </table>`;
    expect(parseTaifexInstitutionalOpenInterest(html, "小型臺指期貨"))
      .toEqual({ long: 2_000, short: 5_000 });
  });

  it("依全市場與法人未平倉量推算散戶多空比", () => {
    expect(buildRetailFuturesPosition("mini", 10_000, { long: 2_000, short: 4_000 }))
      .toMatchObject({
        retailLong: 8_000,
        retailShort: 6_000,
        retailNet: 2_000,
        ratioPct: 20,
        bias: "偏多",
      });
  });

  it("解析期交所區間下載的逐日法人未平倉資料", () => {
    const csv = [
      "日期,商品名稱,身份別,交易多,金額,交易空,金額,淨額,金額,多方未平倉,金額,空方未平倉",
      "2026/07/01,小型臺指期貨,自營商,0,0,0,0,0,0,100,0,300",
      "2026/07/01,小型臺指期貨,投信,0,0,0,0,0,0,20,0,10",
      "2026/07/01,小型臺指期貨,外資及陸資,0,0,0,0,0,0,30,0,40",
      "2026/07/02,小型臺指期貨,自營商,0,0,0,0,0,0,90,0,280",
    ].join("\n");
    expect(parseTaifexInstitutionalCsv(csv, "小型臺指期貨").get("2026-07-01"))
      .toEqual({ long: 150, short: 350 });
  });

  it("解析區間行情並逐日加總各月份未平倉量", () => {
    const row = (date: string, month: string, openInterest: string, session = "一般") =>
      [date, "MTX", month, 0, 0, 0, 0, 0, 0, 0, 0, openInterest, 0, 0, 0, 0, "", session].join(",");
    const csv = [
      "交易日期,契約,到期月份,開盤,高,低,收,漲跌,%,量,結算,未沖銷,買,賣,高,低,暫停,交易時段",
      row("2026/07/01", "202607", "1000"),
      row("2026/07/01", "202608", "200"),
      row("2026/07/01", "202607/202608", "999"),
      row("2026/07/01", "202607", "-", "盤後"),
    ].join("\n");
    expect(parseTaifexMarketOpenInterestCsv(csv, "MTX").get("2026-07-01")).toBe(1_200);
  });

  it("解析外資臺股期貨多空淨額", () => {
    const csv = [
      "日期,商品名稱,身份別,交易多,金額,交易空,金額,淨額,金額,多方未平倉,金額,空方未平倉",
      "2026/07/27,臺股期貨,外資及陸資,0,0,0,0,0,0,6870,0,85569",
      "2026/07/28,臺股期貨,外資及陸資,0,0,0,0,0,0,7401,0,89656",
    ].join("\n");
    expect([...parseTaifexForeignNetCsv(csv).entries()]).toEqual([
      ["2026-07-27", { long: 6_870, short: 85_569, net: -78_699 }],
      ["2026-07-28", { long: 7_401, short: 89_656, net: -82_255 }],
    ]);
  });
});
