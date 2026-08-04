import { describe, expect, it } from "vitest";
import {
  parseTpexDailyInvestors,
  parseTwseCompanyInvestors,
} from "./official-stock-institutional-provider";

describe("official stock institutional provider", () => {
  it("解析證交所單一公司近月三大法人張數", () => {
    const items = parseTwseCompanyInvestors({
      info: { status: "success" },
      chart: {
        foreign: {
          categories: ["2026/07/27", "2026/07/28"],
          series: [
            { name: "外資", data: [-2_661.75, -14_659.33] },
            { name: "投信", data: [566.86, 280.4] },
            { name: "自營商", data: [1_410.35, 5_123.4] },
            { name: "總買賣超", data: [-684.55, -9_255.54] },
          ],
        },
      },
    });
    expect(items).toEqual([
      { date: "2026-07-27", foreign: -2_661.75, trust: 566.86, dealer: 1_410.35, total: -684.55 },
      { date: "2026-07-28", foreign: -14_659.33, trust: 280.4, dealer: 5_123.4, total: -9_255.54 },
    ]);
  });

  it("解析櫃買中心每日個股三大法人股數並換算為張", () => {
    const row = [
      "8299", "群聯",
      "1,143,784", "1,513,350", "-369,566",
      "0", "0", "0",
      "1,143,784", "1,513,350", "-369,566",
      "5,000", "27,000", "-22,000",
      "31,800", "48,253", "-16,453",
      "219,607", "301,056", "-81,449",
      "251,407", "349,309", "-97,902",
      "-489,468",
    ];
    expect(parseTpexDailyInvestors(
      { tables: [{ date: "115/07/28", data: [row] }] },
      "8299",
      "2026-07-28",
    )).toEqual({
      date: "2026-07-28",
      foreign: -369.566,
      trust: -22,
      dealer: -97.902,
      total: -489.468,
    });
  });
});
