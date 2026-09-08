export const ROBOT_HEALTH_LABELS = {
  "day-trading-v2": "當沖機器人2", "limit-up-ai": "漲停機器人", "pattern-robot": "型態選股機器人",
  "rocket-radar": "飆股雷達", "long-term": "長線選股", "strong-stocks": "強勢股策略",
} as const;
export type RobotHealthId = keyof typeof ROBOT_HEALTH_LABELS;
export interface RobotHealthItem {
  id: RobotHealthId;
  scope: "user" | "shared";
  execution: {
    status: "running" | "waiting" | "paused" | "stopped" | "error" | "unknown";
    phase: string | null; enabled: boolean | null; lastRunAt: string | null; lastSuccessAt: string | null;
    error: string | null; reason: string | null;
  };
  data: {
    status: "current" | "waiting" | "stale" | "unavailable" | "unknown";
    tradeDate: string | null; updatedAt: string | null; reason: string | null;
  };
  mode: { tradeMode: string | null; performanceMode: string | null };
}
export interface RobotHealthResponse {
  generatedAt: string;
  market: { localDate: string; isTradingDay: boolean };
  items: RobotHealthItem[];
}
export const EXECUTION_LABELS: Record<RobotHealthItem["execution"]["status"], string> = {
  running: "運作中", waiting: "待命", paused: "暫停", stopped: "已停止", error: "執行異常", unknown: "未取得",
};
export const DATA_LABELS: Record<RobotHealthItem["data"]["status"], string> = {
  current: "資料正常", waiting: "等待資料", stale: "資料過期", unavailable: "資料無法使用", unknown: "未確認",
};
export function robotHealthException(item: RobotHealthItem): string | null {
  const reasons: string[] = [];
  if (item.execution.status === "error" || item.execution.error) reasons.push(item.execution.error || item.execution.reason || EXECUTION_LABELS.error);
  if (item.data.status === "stale" || item.data.status === "unavailable") reasons.push(item.data.reason || DATA_LABELS[item.data.status]);
  return reasons.length ? [...new Set(reasons)].join("；") : null;
}
export function robotModeLabel(mode: string | null): string {
  if (!mode) return "未取得";
  return ({ PAPER: "模擬交易", PAPER_LIVE: "即時模擬", MANUAL_PAPER: "手動模擬", BACKTEST: "歷史回測", LIVE: "真實交易", DISABLED: "已停用", SCAN_ONLY: "僅掃描", REMINDER_ONLY: "僅提醒", SWING: "波段模式", DAY_TRADE: "當沖模式", ALERT_ONLY: "僅提醒模式" } as Record<string, string>)[mode] ?? mode;
}
