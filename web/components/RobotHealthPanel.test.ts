import { describe, expect, it } from "vitest";

import { dayTradingHealth } from "./RobotHealthPanel";

describe("dayTradingHealth", () => {
  it("does not report after-close summary cache as a robot error", () => {
    const health = dayTradingHealth({
      dataStatus: "severe_delay",
      recommendationSummary: "今日無正式訊號",
      supervisor: {
        status: "running",
        session: {
          phase: "summary",
          statusMessage: "盤後彙整完成",
          localTime: "2026-08-28T16:01:46+08:00",
        },
      },
    });

    expect(health.tone).toBe("paused");
    expect(health.status).toBe("盤後完成");
  });

  it("still reports severe delay as an error during active scanning", () => {
    const health = dayTradingHealth({
      dataStatus: "severe_delay",
      quoteCoverageCount: 0,
      candidateUniverseCount: 292,
      automation: {
        phase: "scanning",
        statusMessage: "行情資料異常，暫停產生新交易訊號。",
      },
    });

    expect(health.tone).toBe("error");
    expect(health.status).toBe("資料異常");
    expect(health.detail).toBe("報價覆蓋 0/292");
  });

  it("reports index delay as degraded instead of a hard error when signals remain usable", () => {
    const health = dayTradingHealth({
      dataStatus: "normal",
      dataQualityMode: "index_delay",
      quoteCoverageCount: 240,
      candidateUniverseCount: 292,
      automation: {
        phase: "scanning",
        statusMessage: "指數延遲但個股報價足夠",
      },
    });

    expect(health.tone).toBe("warming");
    expect(health.status).toBe("資料降級");
  });
});
