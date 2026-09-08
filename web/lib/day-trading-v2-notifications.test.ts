import { describe, expect, it } from "vitest";
import { selectDayTradingV2Notifications } from "./day-trading-v2-notifications";

const TODAY = "2026-09-08";
const event = (overrides: Record<string, unknown> = {}) => ({
  id: 1, eventId: "fill:order-1", mode: "PAPER", eventType: "BUY_FILLED",
  title: "買進成交", message: "2330｜1,000股", createdAt: `${TODAY}T09:15:00+08:00`, read: false,
  ...overrides,
});

describe("V2 screen notifications", () => {
  it("selects today's PAPER events using Taipei midnight, including UTC timestamps without an offset", () => {
    const items = selectDayTradingV2Notifications({ items: [
      event(),
      event({ eventId: "midnight", createdAt: "2026-09-07T16:00:00" }),
      event({ eventId: "yesterday", createdAt: "2026-09-07T15:59:59Z" }),
      event({ eventId: "tomorrow", createdAt: "2026-09-08T16:00:00Z" }),
      event({ eventId: "live", mode: "LIVE" }),
      event({ eventId: "backtest", mode: "BACKTEST" }),
    ] }, "browser-user", TODAY);
    expect(items.map((item) => item.eventId)).toEqual(["fill:order-1", "midnight"]);
    expect(items[1].createdAt).toBe("2026-09-07T16:00:00.000Z");
  });

  it("deduplicates by eventId within a user and gives other users separate keys", () => {
    const payload = { items: [event(), event({ id: 99 })] };
    const first = selectDayTradingV2Notifications(payload, "first-user", TODAY);
    const repeated = selectDayTradingV2Notifications(payload, "first-user", TODAY);
    const other = selectDayTradingV2Notifications(payload, "second-user", TODAY);
    expect(first).toHaveLength(1);
    expect(repeated[0].key).toBe(first[0].key);
    expect(other[0].key).not.toBe(first[0].key);
    const seen = new Set(first.map((item) => item.key));
    expect(repeated.filter((item) => !seen.has(item.key))).toHaveLength(0);
    expect(other.filter((item) => !seen.has(item.key))).toHaveLength(1);
  });

  it.each([
    ["PREOPEN_READY", "activation"], ["OPENING_RANGE_READY", "activation"],
    ["HOURLY_SUMMARY", "activation"], ["DAILY_REPORT", "activation"],
    ["BUY_FILLED", "buy"], ["SELL_FILLED", "sell"],
    ["MARKET_DATA_INTERRUPTED", "stop"], ["SYSTEM_HEARTBEAT_INTERRUPTED", "stop"],
    ["DAILY_LOSS_LIMIT", "stop"], ["EMERGENCY_STOP", "stop"],
    ["BROKER_DISCONNECTED", "stop"],
  ])("keeps %s and selects the matching toast style", (eventType, kind) => {
    expect(selectDayTradingV2Notifications({ items: [event({ eventType })] }, "browser-user", TODAY)[0].kind).toBe(kind);
  });

  it("ignores malformed payloads and cannot select events without a user", () => {
    for (const payload of [null, {}, { items: null }, { items: [null, event({ createdAt: "bad-date" }), event({ eventId: "" })] }]) {
      expect(selectDayTradingV2Notifications(payload, "browser-user", TODAY)).toEqual([]);
    }
    expect(selectDayTradingV2Notifications({ items: [event()] }, "", TODAY)).toEqual([]);
  });
});
