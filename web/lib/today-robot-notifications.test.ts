import { describe, expect, it } from "vitest";
import {
  normalizeTodayRobotNotifications,
  summarizeTodayRobotNotifications,
  todayTaipeiDate,
} from "@/lib/today-robot-notifications";

const TODAY = "2026-08-28";
const todayAt = (time: string) => `${TODAY}T${time}+08:00`;

describe("today robot notifications", () => {
  it("normalizes every robot payload into one clear today-only list", () => {
    const items = normalizeTodayRobotNotifications({
      dayTradingSignals: {
        tradingDate: TODAY,
        items: [
          {
            id: "dt-1",
            isOfficialRecommendation: true,
            direction: "long",
            symbol: "2330",
            stockName: "台積電",
            action: "買進",
            entryMin: 100,
            entryMax: 101,
            stopLoss: 98,
            confidenceScore: 88,
            riskRewardRatio: 2.4,
            reasons: ["大單買盤增加"],
            recommendedAt: todayAt("09:20:00"),
          },
          {
            id: "dt-ignored",
            isOfficialRecommendation: false,
            direction: "long",
            symbol: "2317",
            stockName: "鴻海",
            generatedAt: todayAt("09:21:00"),
          },
        ],
      },
      dayTradingAlerts: {
        items: [
          {
            id: 7,
            type: "STOP_LOSS",
            title: "當沖停損",
            message: "2330 觸發停損",
            reason: "跌破停損價",
            price: 98,
            createdAt: todayAt("09:40:00"),
            readAt: null,
          },
        ],
      },
      limitUp: {
        items: [
          {
            id: 11,
            type: "BUY",
            title: "漲停機器人買進",
            message: "接近漲停攻擊",
            symbol: "2408",
            stockName: "南亞科",
            reason: "距漲停 2%",
            isRead: false,
            createdAt: todayAt("10:00:00"),
          },
        ],
      },
      superAi: {
        items: [
          {
            id: 21,
            category: "SHORT",
            title: "超強 AI 放空",
            message: "短線轉弱",
            symbol: "2303",
            symbolName: "聯電",
            reasons: ["VWAP 下方", "大單偏空"],
            isRead: true,
            timestamp: todayAt("10:10:00"),
          },
        ],
      },
      pattern: {
        items: [
          {
            id: 31,
            messageType: "SCAN_COMPLETED",
            title: "型態掃描完成",
            message: "找到 3 檔接近突破",
            isRead: true,
            createdAt: todayAt("08:50:00"),
          },
        ],
      },
      rocket: {
        items: [
          {
            notificationId: 41,
            notificationType: "TAKE_PROFIT",
            title: "飆股雷達停利",
            message: "第一段停利",
            stockCode: "3661",
            stockName: "世芯-KY",
            reason: "達停利目標",
            isRead: false,
            timestamp: todayAt("10:20:00"),
          },
          {
            notificationId: 42,
            notificationType: "BUY",
            title: "昨天訊息",
            message: "不應顯示",
            timestamp: "2026-08-27T10:00:00+08:00",
          },
        ],
      },
    }, TODAY);

    expect(items.map((item) => [item.source, item.action, item.symbol])).toEqual([
      ["rocket-radar", "take_profit", "3661"],
      ["adaptive-electronic", "short", "2303"],
      ["limit-up-ai", "buy", "2408"],
      ["day-trading", "stop_loss", null],
      ["day-trading", "buy", "2330"],
      ["pattern-robot", "scan", null],
    ]);
    expect(items.find((item) => item.source === "adaptive-electronic")?.reason).toBe("VWAP 下方、大單偏空");
  });

  it("keeps working when some sources are missing or failed upstream", () => {
    const items = normalizeTodayRobotNotifications({
      rocket: {
        items: [{
          notificationId: 1,
          notificationType: "BUY",
          title: "飆股雷達買進",
          message: "突破",
          reason: "Rocket 分數通過",
          timestamp: todayAt("09:30:00"),
        }],
      },
    }, TODAY);

    expect(items).toHaveLength(1);
    expect(items[0].source).toBe("rocket-radar");
  });

  it("summarizes all configured robots even when there is no notification", () => {
    const summary = summarizeTodayRobotNotifications([]);

    expect(summary.map((item) => [item.source, item.count, item.unreadCount, item.lastTimestamp])).toEqual([
      ["day-trading", 0, 0, null],
      ["limit-up-ai", 0, 0, null],
      ["adaptive-electronic", 0, 0, null],
      ["pattern-robot", 0, 0, null],
      ["rocket-radar", 0, 0, null],
    ]);
  });

  it("resolves the current Taipei date explicitly", () => {
    expect(todayTaipeiDate(new Date("2026-08-27T16:30:00.000Z"))).toBe(TODAY);
  });
});
