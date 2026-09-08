import type { Dashboard, NotificationItem, RegimePerformance, TradingMode } from "@/lib/day-trading-v2-types";

const base = "/api/day-trading-v2";

export class DayTradingV2RequestError extends Error {
  constructor(message: string, public readonly status: number) {
    super(message);
    this.name = "DayTradingV2RequestError";
  }
}

async function request<T>(path: string, userId: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${base}/${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", "x-user-id": userId, ...(init?.headers ?? {}) },
    signal: init?.signal ?? AbortSignal.timeout(15_000),
    cache: "no-store",
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new DayTradingV2RequestError(payload.detail ?? payload.error ?? "當沖機器人2服務暫時無法使用", response.status);
  return payload as T;
}

export const dayTradingV2Client = {
  dashboard: (userId: string) => request<Dashboard>("dashboard", userId),
  notifications: (userId: string) => request<{ unread: number; items: NotificationItem[] }>("notifications", userId),
  saveSettings: (userId: string, mode: TradingMode, config: Dashboard["config"]) => request<{ mode: TradingMode; config: Dashboard["config"] }>("settings", userId, { method: "PUT", body: JSON.stringify({ mode, config }) }),
  updateRobot: (userId: string, strategyId: string, enabled: boolean, allocation?: string) => request(`robots/${strategyId}`, userId, { method: "PATCH", body: JSON.stringify({ enabled, allocation }) }),
  emergencyStop: (userId: string) => request<{ status: string; message: string }>("emergency-stop", userId, { method: "POST" }),
  cancelAll: (userId: string) => request<{ cancelled: number }>("cancel-all", userId, { method: "POST" }),
  scanNow: (userId: string) => request<{ evaluated: number; executed: number; skipped: number }>("scan-now", userId, { method: "POST" }),
  startToday: (userId: string) => request("control/start-today", userId, { method: "POST" }),
  pause: (userId: string) => request("control/pause", userId, { method: "POST" }),
  resume: (userId: string) => request("control/resume", userId, { method: "POST" }),
  stop: (userId: string) => request("control/stop", userId, { method: "POST" }),
  closePosition: (userId: string, positionId: string, fillPrice: string, reason = "手動平倉") => request(`positions/${positionId}/close`, userId, { method: "POST", body: JSON.stringify({ fill_price: fillPrice, reason, percentage: 100 }) }),
  backtest: (userId: string, payload: Record<string, unknown>) => request<Record<string, unknown>>("backtests", userId, { method: "POST", body: JSON.stringify(payload) }),
  backtestJob: (userId: string, jobId: string) => request<Record<string, unknown>>(`backtests/${jobId}`, userId),
  backtests: (userId: string) => request<{ items: Array<Record<string, unknown>> }>("backtests", userId),
  backtestPresets: (userId: string) => request<{
    endDate: string;
    monthToDate: { startDate: string; endDate: string };
    recent20TradingDays: { startDate: string; endDate: string; tradingDays: number };
  }>("backtests/presets", userId),
  controller: (userId: string) => request<Dashboard["controller"]>("controller", userId),
  controllerDecisions: (userId: string, date?: string) => request<{ items: Array<Record<string, unknown>> }>(`controller/decisions${date ? `?trading_date=${date}` : ""}`, userId),
  optimization: (userId: string) => request<Dashboard["optimization"]>("optimization", userId),
  diagnoseStrategies: (userId: string) => request<Dashboard["optimization"]>("optimization/diagnose", userId, { method: "POST" }),
  optimizationHealthHistory: (userId: string, strategyId: string) => request<Record<string, unknown>>(`optimization/health-history?strategy_id=${encodeURIComponent(strategyId)}`, userId),
  createOptimizationJob: (userId: string, strategyId: string, datasetId?: string) => request<Record<string, unknown>>("optimization/jobs", userId, { method: "POST", body: JSON.stringify({ strategy_id: strategyId, dataset_id: datasetId }) }),
  optimizationJob: (userId: string, jobId: string) => request<Record<string, unknown>>(`optimization/jobs/${jobId}`, userId),
  challengerRun: (userId: string, runId: string) => request<Record<string, unknown>>(`optimization/challengers/${runId}`, userId),
  regimePerformance: (userId: string, options: { source: string; period: string; sourceId?: string; role?: string }) => {
    const query = new URLSearchParams({ source: options.source, period: options.period });
    if (options.sourceId) query.set("source_id", options.sourceId);
    if (options.role) query.set("role", options.role);
    return request<RegimePerformance>(`performance/by-regime?${query.toString()}`, userId);
  },
  uploadOptimizationDataset: (userId: string, file: File) => request<Record<string, unknown>>(`optimization/datasets?name=${encodeURIComponent(file.name)}&data_format=${file.name.toLowerCase().endsWith(".parquet") ? "PARQUET" : "CSV"}`, userId, { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file, signal: AbortSignal.timeout(300_000) }),
  approveVersion: (userId: string, strategyId: string, version: string, approvalCode: string) => request<Record<string, unknown>>(`strategy-versions/${strategyId}/${version}/approve`, userId, { method: "POST", body: JSON.stringify({ confirmation_version: version, approval_code: approvalCode }) }),
  rejectVersion: (userId: string, strategyId: string, version: string, reason: string) => request<Record<string, unknown>>(`strategy-versions/${strategyId}/${version}/reject`, userId, { method: "POST", body: JSON.stringify({ confirmation_version: version, reason }) }),
  rollbackVersion: (userId: string, strategyId: string, version: string, reason: string) => request<Record<string, unknown>>(`strategy-versions/${strategyId}/${version}/rollback`, userId, { method: "POST", body: JSON.stringify({ confirmation_version: version, reason }) }),
};
