import { describe, expect, it } from "vitest";

import { dayTradingHealth, superAiHealth } from "./RobotHealthPanel";

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

describe("superAiHealth", () => {
  it("reports stale scanner data as a robot error with the stale trade date", () => {
    const health = superAiHealth({
      status: "running",
      latestCandidateTradeDate: "2026-09-02",
      candidateDataStale: true,
      newTradesPausedByScanner: true,
      settings: {},
      risk: {},
      marketState: { label: "突破" },
    });

    expect(health.tone).toBe("error");
    expect(health.status).toBe("掃描資料過期");
    expect(health.detail).toContain("2026-09-02");
  });
});
