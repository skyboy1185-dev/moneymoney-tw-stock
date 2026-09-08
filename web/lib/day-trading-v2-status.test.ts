import { describe, expect, it } from "vitest";
import type { RuntimeState } from "./day-trading-v2-types";
import { v2Headline, v2QuoteLabel, v2SourceLabel, v2SourceStatus } from "./day-trading-v2-status";

const runtime = { running: true, status: "RUNNING", heartbeatStale: false, quoteStale: false, orderAllowed: true } as RuntimeState;

describe("V2 execution and quote labels", () => {
  it("shows a live worker with unavailable quotes as paused new entries", () => {
    expect(v2Headline("NORMAL", { ...runtime, quoteStale: true, orderAllowed: false })).toBe("運作中，行情不足暫停新交易");
  });
  it("keeps risk, explicit stops and execution exceptions distinct", () => {
    expect(v2Headline("HALTED", runtime)).toBe("風控暫停");
    expect(v2Headline("HALTED", { ...runtime, status: "EMERGENCY_STOP" })).toBe("系統已停止");
    expect(v2Headline("HALTED", { ...runtime, heartbeatStale: true })).toBe("背景執行異常");
  });
  it("shows partially updated quotes without claiming every stock is fresh", () => {
    expect(v2QuoteLabel({ ...runtime, receivingQuotes: true, quoteHealth: { observedAt: "now", lastReceivedAt: "now", trackedCount: 3, freshCount: 1, staleCount: 2, overCapacity: false } })).toBe("部分行情更新中");
  });
  it("does not label shadow quotes as enabled backup", () => {
    const shadow = { ...runtime, quoteHealth: { providerMode: "shadow", ready: false, activeSource: "NONE", entitlementReady: false, entitlementReason: "quota_insufficient" } } as RuntimeState;
    expect(v2SourceStatus(shadow)).toBe("備援驗證中，帳號額度尚未達標");
    expect(v2SourceStatus({ ...shadow, quoteHealth: { ...shadow.quoteHealth!, entitlementReady: true, entitlementReason: "備援驗證中，尚未啟用 Fugle 正式報價" } })).toBe("備援驗證中，尚未啟用");
    expect(v2SourceLabel(shadow)).toBe("尚未取得行情");
    expect(v2SourceStatus({ ...shadow, dataStatus: "waiting" })).toBe("等待交易時段");
  });
  it("distinguishes primary, backup and all unavailable sources", () => {
    const primary = { ...runtime, quoteHealth: { providerMode: "primary", ready: true, activeSource: "FUGLE_WS" } } as RuntimeState;
    expect(v2SourceLabel(primary)).toBe("Fugle 即時串流");
    expect(v2SourceStatus(primary)).toBe("主要行情連線正常");
    expect(v2SourceStatus({ ...primary, quoteHealth: { ...primary.quoteHealth!, activeSource: "FUGLE_REST", providerMode: "degraded" } })).toBe("使用備援行情，自動重連中");
    expect(v2SourceStatus({ ...primary, quoteHealth: { ...primary.quoteHealth!, activeSource: "NONE", ready: false } })).toBe("全部行情來源暫時不可用");
  });
});
