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
    expect(dashboard.positions).toEqual([]);
    expect(dashboard.trades).toEqual([]);
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
