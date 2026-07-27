import { beforeEach, describe, expect, it } from "vitest";
import {
  canCreateEntry, evaluateExit, filterSignals, isExpired, longSignalScore,
  shortSignalScore, signalRemainingMs, streamRetryDelay,
} from "./day-trading-engine";
import type { DayTradingSignal, EmergencyEvent } from "./day-trading-types";
import { useDayTradingStore } from "@/stores/day-trading-store";

const signal = (overrides: Partial<DayTradingSignal> = {}): DayTradingSignal => ({
  id: "s1", rank: 1, symbol: "2330", stockName: "台積電", market: "上市",
  direction: "long", directionLabel: "做多", action: "等待突破", price: 100,
  changePercent: 1, volume: 1_000_000, turnover: 100_000_000,
  entryMin: 99, entryMax: 100, stopLoss: 95, target1: 105, target2: 110,
  confidenceScore: 88, healthScore: 82, riskRewardRatio: 2,
  vwapStatus: "站上", volumeStatus: "量增", largeOrderForce: 70,
  industryStrength: "強勢", reasons: ["站上 VWAP"], warnings: [],
  generatedAt: new Date().toISOString(), expiresAt: new Date(Date.now() + 60_000).toISOString(),
  quoteTimestamp: new Date().toISOString(), status: "confirmed", dataSource: "mock_stream",
  spreadPercentage: 0.1, tradingEligible: true, shortEligible: false,
  shortAvailabilityKnown: true, chaseBlocked: false, stopDistancePercent: 1,
  marketAlignment: 90, confirmationScore: 85, isOfficialRecommendation: true,
  recommendationLabel: "AI 正式推薦", qualificationFailures: [],
  ...overrides,
});

describe("當沖訊號與風控", () => {
  it("做多分數使用九項權重", () => {
    expect(longSignalScore({
      vwapUp: true, aboveVwap: true, breakout: true, volume: true,
      activeBuy: true, largeBuy: true, shortTrend: true, marketFit: true, industryFit: true,
    })).toBe(100);
  });

  it("放空分數不會只依賴跌幅", () => {
    expect(shortSignalScore({ breakdown: true })).toBe(15);
    expect(shortSignalScore({
      vwapDown: true, belowVwap: true, breakdown: true, volume: true,
      activeSell: true, largeSell: true, shortTrend: true, marketFit: true, industryFit: true,
    })).toBe(100);
  });

  it("辨認訊號有效期限與失效", () => {
    expect(isExpired(new Date(Date.now() - 1).toISOString())).toBe(true);
    expect(isExpired(new Date(Date.now() + 10_000).toISOString())).toBe(false);
  });

  it("有效倒數使用伺服器時間校準，不受瀏覽器時鐘影響", () => {
    expect(signalRemainingMs(
      "2026-07-27T11:45:00+08:00",
      "2026-07-27T11:40:00+08:00",
      1_000,
    )).toBe(299_000);
  });

  it("多單停損優先於健康度", () => {
    expect(evaluateExit("long", 94, 95, 105, 110)).toEqual({ priority: 0, action: "立即全部賣出" });
  });

  it("空單停損優先於健康度", () => {
    expect(evaluateExit("short", 106, 105, 95, 90)).toEqual({ priority: 0, action: "立即全部回補" });
  });

  it("第一停利產生減碼或部分回補", () => {
    expect(evaluateExit("long", 105, 95, 105, 110).action).toBe("減碼 50%");
    expect(evaluateExit("short", 95, 105, 95, 90).action).toBe("回補 50%");
  });

  it("第二停利產生全部出場", () => {
    expect(evaluateExit("long", 110, 95, 105, 110).action).toBe("全部賣出");
    expect(evaluateExit("short", 90, 105, 95, 90).action).toBe("全部回補");
  });

  it("移動停利可先於固定目標出場", () => {
    expect(evaluateExit("long", 102, 95, 105, 110, 103).action).toBe("全部賣出");
    expect(evaluateExit("short", 98, 105, 95, 90, 97).action).toBe("全部回補");
  });

  it("資料延遲停止新訊號", () => {
    expect(canCreateEntry(12, false, 0, 3)).toBe(false);
  });

  it("每日最大虧損停止新訊號", () => {
    expect(canCreateEntry(1, true, 0, 3)).toBe(false);
  });

  it("連續虧損達限制停止新訊號", () => {
    expect(canCreateEntry(1, false, 3, 3)).toBe(false);
  });

  it("SSE 斷線重連採上限 30 秒退避", () => {
    expect(streamRetryDelay(0)).toBe(1000);
    expect(streamRetryDelay(4)).toBe(16_000);
    expect(streamRetryDelay(10)).toBe(30_000);
  });

  it("排行榜支援多空、高信心與市場篩選", () => {
    const rows = [signal(), signal({ id: "s2", direction: "short", directionLabel: "放空", market: "上櫃", confidenceScore: 70 })];
    expect(filterSignals(rows, "long")).toHaveLength(1);
    expect(filterSignals(rows, "short")).toHaveLength(1);
    expect(filterSignals(rows, "high")).toHaveLength(1);
    expect(filterSignals(rows, "otc")).toHaveLength(1);
  });
});

describe("即時事件優先級與去重", () => {
  beforeEach(() => {
    useDayTradingStore.setState({ signals: [], positions: [], alerts: [], emergency: null, eventIds: [] });
  });

  it("重複事件 ID 不重複更新", () => {
    useDayTradingStore.getState().handleEvent("signal_update", "same-id", [signal()]);
    useDayTradingStore.getState().handleEvent("signal_update", "same-id", [signal({ id: "ignored" })]);
    expect(useDayTradingStore.getState().signals[0].id).toBe("s1");
  });

  it("緊急出場不會被後續進場訊號覆蓋", () => {
    const emergency: EmergencyEvent = {
      type: "emergency_exit", level: "emergency", id: "e1", title: "緊急出場",
      message: "立即處理", action: "立即全部賣出", reason: "跌破停損", price: 94,
    };
    useDayTradingStore.getState().handleEvent("emergency_exit", "e1", emergency);
    useDayTradingStore.getState().handleEvent("new_signal", "s-event", [signal()]);
    expect(useDayTradingStore.getState().emergency?.id).toBe("e1");
  });
});
