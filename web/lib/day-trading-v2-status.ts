import type { RuntimeState } from "./day-trading-v2-types";

export function v2Headline(systemStatus: string, runtime: RuntimeState): string {
  if (runtime.status === "EMERGENCY_STOP" || runtime.status === "STOPPED") return "系統已停止";
  if (runtime.heartbeatStale || runtime.status === "ALERT" || runtime.executionError) return "背景執行異常";
  if (runtime.status === "RISK_HALTED" || systemStatus === "HALTED") return "風控暫停";
  if (runtime.status === "PAUSED") return "已暫停新交易";
  if (!runtime.running) return "等待交易時段";
  if (runtime.quoteStale || runtime.dataStatus === "unavailable" || runtime.dataStatus === "stale") return "運作中，行情不足暫停新交易";
  if (!runtime.orderAllowed) return "運作中，暫停新交易";
  if (systemStatus === "REDUCED") return "運作中，風險減半";
  return "系統運作中";
}

export function v2QuoteLabel(runtime: RuntimeState): string {
  if (runtime.dataStatus === "waiting") return "等待交易時段";
  const health = runtime.quoteHealth;
  if (health && health.trackedCount > 0 && health.freshCount > 0 && health.staleCount > 0) return "部分行情更新中";
  return runtime.receivingQuotes && !runtime.quoteStale ? "接收中" : "行情不足";
}

export function v2SourceLabel(runtime: RuntimeState, fallback = "尚未取得行情"): string {
  const source = runtime.quoteHealth?.activeSource;
  const labels: Record<string, string> = {
    FUGLE_WS: "Fugle 即時串流", FUGLE_REST: "Fugle 備援報價",
    TWSE_MIS: "TWSE MIS", MIXED: "多來源報價", NONE: "尚未取得行情",
  };
  return source ? labels[source] ?? "行情來源待確認" : fallback;
}

export function v2SourceStatus(runtime: RuntimeState): string | null {
  const health = runtime.quoteHealth;
  if (!health?.providerMode) return null;
  if (runtime.dataStatus === "waiting") return "等待交易時段";
  if (health.providerMode === "shadow") {
    return health.entitlementReady === false ? "備援驗證中，帳號額度尚未達標" : "備援驗證中，尚未啟用";
  }
  if (health.activeSource === "NONE") return "全部行情來源暫時不可用";
  if (health.providerMode === "degraded" || health.activeSource === "FUGLE_REST") return "使用備援行情，自動重連中";
  if (health.providerMode === "mis_only") return "使用 MIS 行情";
  return health.ready ? "主要行情連線正常" : "行情連線檢查中";
}
