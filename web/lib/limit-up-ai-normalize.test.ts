import { describe, expect, it } from "vitest";
import { normalizeLargeOrderResponse, normalizeLimitUpDashboard, normalizeNotificationPayload } from "@/lib/limit-up-ai-normalize";

describe("limit-up AI normalizers", () => {
  it("supplies safe dashboard defaults when backend fields are missing", () => {
    const dashboard = normalizeLimitUpDashboard({
      summary: { candidateCount: 1 },
      candidates: [{ symbol: "2408", score: "88.5", failures: null }],
    });

    expect(dashboard.summary.candidateCount).toBe(1);
    expect(dashboard.performance.today.totalPnl).toBe(0);
    expect(dashboard.performance.month.winRate).toBe(0);
    expect(dashboard.settings.capital).toBe(3_000_000);
    expect(dashboard.candidates[0].score).toBe(88.5);
    expect(dashboard.candidates[0].failures).toEqual([]);
    expect(dashboard.candidates[0].largeOrderSource).toBe("unavailable");
    expect(dashboard.summary.alertableCount).toBe(0);
    expect(dashboard.summary.limitBoardCount).toBe(0);
    expect(dashboard.limitBoard).toEqual([]);
    expect(dashboard.alerts).toEqual([]);
    expect(dashboard.positions).toEqual([]);
    expect(dashboard.trades).toEqual([]);
  });

  it("normalizes limit-up board and alert metadata", () => {
    const dashboard = normalizeLimitUpDashboard({
      summary: { alertableCount: 1, limitBoardCount: 1 },
      limitBoard: [{ symbol: "1709", alertable: true, isLockedLimitUp: true, largeOrderSource: "quote_proxy", entryBlockReason: "只通知不買" }],
      alerts: [{ symbol: "1709", alertable: true }],
    });

    expect(dashboard.limitBoard[0].symbol).toBe("1709");
    expect(dashboard.limitBoard[0].alertable).toBe(true);
    expect(dashboard.limitBoard[0].isLockedLimitUp).toBe(true);
    expect(dashboard.limitBoard[0].largeOrderSource).toBe("quote_proxy");
    expect(dashboard.limitBoard[0].entryBlockReason).toBe("只通知不買");
    expect(dashboard.alerts).toHaveLength(1);
    expect(dashboard.summary.alertableCount).toBe(1);
    expect(dashboard.summary.limitBoardCount).toBe(1);
  });

  it("keeps large-order Top10 safe when the API returns an error object", () => {
    const payload = normalizeLargeOrderResponse({ error: "FastAPI 回應 503" });

    expect(payload.status).toBe("unavailable");
    expect(payload.rankingLimit).toBe(10);
    expect(payload.alerts).toEqual([]);
    expect(payload.shortAlerts).toEqual([]);
    expect(payload.lastError).toBe("FastAPI 回應 503");
  });

  it("normalizes notification payloads before reading ids", () => {
    const payload = normalizeNotificationPayload({
      items: [{ id: "42", type: "BUY", isRead: false }],
    });

    expect(payload.items[0].id).toBe(42);
    expect(payload.unreadCount).toBe(1);
  });
});
