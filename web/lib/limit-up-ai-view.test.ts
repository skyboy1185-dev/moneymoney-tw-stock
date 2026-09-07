import { describe, expect, it } from "vitest";
import type { LimitUpAiNotification, LimitUpPosition, LimitUpTrade } from "@/lib/limit-up-ai-types";
import {
  isLimitUpTradeNotification,
  selectOpenLimitUpPositions,
  selectTodayLimitUpTrades,
  taipeiDateKey,
} from "./limit-up-ai-view";

describe("limit-up AI simplified view", () => {
  it("uses the Taipei calendar date across the UTC day boundary", () => {
    expect(taipeiDateKey("2026-08-27T15:59:00Z")).toBe("2026-08-27");
    expect(taipeiDateKey("2026-08-27T16:01:00Z")).toBe("2026-08-28");
  });

  it("keeps only trades from today in Taipei", () => {
    const rows = [
      { id: 1, executedAt: "2026-08-27T15:59:00Z" },
      { id: 2, executedAt: "2026-08-27T16:01:00Z" },
    ] as LimitUpTrade[];

    expect(selectTodayLimitUpTrades(rows, new Date("2026-08-28T03:00:00Z")).map((item) => item.id)).toEqual([2]);
  });

  it("keeps only open positions with remaining shares", () => {
    const rows = [
      { id: 1, status: "open", remainingQuantity: 1000 },
      { id: 2, status: "closed", remainingQuantity: 0 },
      { id: 3, status: "open", remainingQuantity: 0 },
    ] as LimitUpPosition[];

    expect(selectOpenLimitUpPositions(rows).map((item) => item.id)).toEqual([1]);
  });

  it.each(["BUY", "SELL", "TAKE_PROFIT", "STOP_LOSS"])("keeps %s notifications", (type) => {
    expect(isLimitUpTradeNotification({ type } as LimitUpAiNotification)).toBe(true);
  });

  it.each(["ACTIONABLE", "NEAR_LIMIT", "WARNING"])("hides %s observation notifications", (type) => {
    expect(isLimitUpTradeNotification({ type } as LimitUpAiNotification)).toBe(false);
  });
});
