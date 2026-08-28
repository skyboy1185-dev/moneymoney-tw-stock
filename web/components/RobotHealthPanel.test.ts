import { describe, expect, it } from "vitest";

import { dayTradingHealth } from "./RobotHealthPanel";

describe("dayTradingHealth", () => {
  it("does not report after-close summary cache as a robot error", () => {
    const health = dayTradingHealth({
      dataStatus: "severe_delay",
      recommendationSummary: "目前沒有符合風控條件的股票，持續掃描中",
      supervisor: {
        status: "running",
        session: {
          phase: "summary",
          statusMessage: "今日新訊號已停止，系統已產生交易摘要。",
          localTime: "2026-08-28T16:01:46+08:00",
        },
      },
    });

    expect(health.tone).toBe("paused");
    expect(health.status).toBe("今日掃描完成");
  });

  it("still reports severe delay as an error during active scanning", () => {
    const health = dayTradingHealth({
      dataStatus: "severe_delay",
      automation: {
        phase: "scanning",
        statusMessage: "行情資料異常，暫停產生新交易訊號。",
      },
    });

    expect(health.tone).toBe("error");
    expect(health.status).toBe("資料異常");
  });
});
