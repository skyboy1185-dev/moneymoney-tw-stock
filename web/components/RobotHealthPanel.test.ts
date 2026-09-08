import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { RobotHealthPanel } from "./RobotHealthPanel";
import { TodayRobotNotificationsPanel } from "./TodayRobotNotificationsPanel";
import { robotHealthException, robotModeLabel, type RobotHealthItem } from "@/lib/robot-health";

const base: RobotHealthItem = {
  id: "day-trading-v2", scope: "user",
  execution: { status: "waiting", phase: "after_close", enabled: true, lastRunAt: null, lastSuccessAt: null, error: null, reason: "盤後待命" },
  data: { status: "waiting", tradeDate: "2026-09-07", updatedAt: null, reason: "等待下一交易日" },
  mode: { tradeMode: "PAPER", performanceMode: null },
};

describe("robot health presentation", () => {
  it("does not turn after-close waiting or user pauses into errors", () => {
    expect(robotHealthException(base)).toBeNull();
    expect(robotHealthException({ ...base, execution: { ...base.execution, status: "paused" } })).toBeNull();
  });
  it("reports stale source data independently of a running engine", () => {
    expect(robotHealthException({ ...base, execution: { ...base.execution, status: "running" }, data: { ...base.data, status: "stale", reason: "行情日期落後" } })).toBe("行情日期落後");
  });
  it("keeps engine errors visible even when market data is current", () => {
    expect(robotHealthException({ ...base, execution: { ...base.execution, status: "error", error: "工作程序中斷" }, data: { ...base.data, status: "current" } })).toBe("工作程序中斷");
  });
  it("keeps unknown mode distinct from simulation", () => {
    expect(robotModeLabel(null)).toBe("未取得");
    expect(robotModeLabel("PAPER")).toBe("模擬交易");
    expect(robotModeLabel("BACKTEST")).toBe("歷史回測");
    expect(robotModeLabel("SWING")).toBe("波段模式");
    expect(robotModeLabel("DAY_TRADE")).toBe("當沖模式");
    expect(robotModeLabel("ALERT_ONLY")).toBe("僅提醒模式");
    expect(robotModeLabel("MANUAL_PAPER")).toBe("手動模擬");
  });
  it("starts both disclosures collapsed and labels inaccessible data instead of zero", () => {
    const health = renderToStaticMarkup(createElement(RobotHealthPanel, { userId: "browser-user", onOpen: () => {} }));
    const notifications = renderToStaticMarkup(createElement(TodayRobotNotificationsPanel, { userId: "browser-user", onOpen: () => {} }));
    expect(health).toContain('aria-expanded="false" aria-controls="robot-health-details"');
    expect(health).toContain('id="robot-health-details" hidden=""');
    expect(notifications).toContain('aria-expanded="false" aria-controls="today-robot-details"');
    expect(notifications).toContain('id="today-robot-details" hidden=""');
    expect(notifications).toContain("未取得");
    expect(notifications).not.toContain("未讀 0");
  });
});
