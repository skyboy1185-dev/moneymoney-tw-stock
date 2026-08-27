import { describe, expect, it, vi } from "vitest";
import type { DailyPrice } from "@/lib/types";
import { POST } from "./route";
import { getOfficialDeductionHistory } from "@/services/market-data/official-history-provider";
import { getOfficialQuotes } from "@/services/market-data/official-quote-provider";

vi.mock("@/services/market-data/official-history-provider", () => ({
  getOfficialDeductionHistory: vi.fn(),
  mergeOfficialHistoryWithQuote: vi.fn((history: DailyPrice[]) => history),
}));

vi.mock("@/services/market-data/official-quote-provider", () => ({
  getOfficialQuotes: vi.fn(),
}));

function price(date: string, close: number): DailyPrice {
  return {
    symbol: "2330",
    name: "台積電",
    date,
    open: close,
    high: close,
    low: close,
    close,
    volume: 1_000_000,
  };
}

describe("POST /api/market-data/deduction-signals", () => {
  it("裁切 asOfDate 之後的未來 K 棒再計算扣抵訊號", async () => {
    const prices = Array.from({ length: 20 }, (_, index) => {
      const day = `${index + 1}`.padStart(2, "0");
      return price(`2026-08-${day}`, 100 + index);
    });
    prices.push(price("2026-08-21", 9_999));
    vi.mocked(getOfficialDeductionHistory).mockResolvedValue(prices);
    vi.mocked(getOfficialQuotes).mockResolvedValue(new Map());

    const response = await POST(new Request("http://localhost/api/market-data/deduction-signals", {
      method: "POST",
      body: JSON.stringify({
        asOfDate: "2026-08-20",
        items: [{ symbol: "2330", name: "台積電", market: "上市" }],
      }),
    }));
    const payload = await response.json();
    const item = payload.items[0];

    expect(item.currentPrice).toBe(119);
    expect(item.latestPriceDate).toBe("2026-08-20");
    expect(item.previousClose).toBe(118);
    expect(item.matches.every((match: { signalDate: string }) => match.signalDate <= "2026-08-20")).toBe(true);
  });
});
