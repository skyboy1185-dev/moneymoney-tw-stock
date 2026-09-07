import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DailyPrice, StockMeta } from "@/lib/types";

const { getOfficialDeductionHistory, getOfficialQuotes } = vi.hoisted(() => ({
  getOfficialDeductionHistory: vi.fn(),
  getOfficialQuotes: vi.fn(),
}));

vi.mock("@/services/market-data/official-history-provider", () => ({
  getOfficialDeductionHistory,
  mergeOfficialHistoryWithQuote: (history: DailyPrice[]) => history,
}));

vi.mock("@/services/market-data/official-quote-provider", () => ({
  getOfficialQuotes,
}));

import { POST } from "./route";

function risingPrices(meta: StockMeta): DailyPrice[] {
  return Array.from({ length: 500 }, (_, index) => {
    const date = new Date(Date.UTC(2024, 0, index + 1)).toISOString().slice(0, 10);
    const close = index + 100;
    return {
      symbol: meta.symbol,
      name: meta.name,
      date,
      open: close,
      high: close,
      low: close,
      close,
      volume: 1_000_000,
    };
  });
}

describe("POST /api/manual-screener/matches", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getOfficialQuotes.mockResolvedValue(new Map());
    getOfficialDeductionHistory.mockImplementation(async (meta: StockMeta) => {
      if (meta.symbol === "9999") throw new Error("history unavailable");
      return risingPrices(meta);
    });
  });

  it("股票去重後一次回傳所有符合策略，單檔失敗不影響其他結果", async () => {
    const request = new Request("http://localhost/api/manual-screener/matches", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        asOfDate: "2025-05-14",
        items: [
          { symbol: "2330", name: "台積電", market: "上市" },
          { symbol: "2330", name: "台積電", market: "上市" },
          { symbol: "9999", name: "測試股", market: "上櫃" },
        ],
      }),
    });
    const response = await POST(request);
    const payload = await response.json();

    expect(response.status).toBe(200);
    expect(payload.items).toHaveLength(2);
    expect(payload.items.find((item: { symbol: string }) => item.symbol === "2330").matches)
      .toEqual(expect.arrayContaining([expect.objectContaining({ strategyId: "day-ma5-ma10-ma20-up" })]));
    expect(payload.items.find((item: { symbol: string }) => item.symbol === "9999")).toMatchObject({
      matches: [],
      error: "此股票的策略行情暫時無法取得",
    });
  });
});
