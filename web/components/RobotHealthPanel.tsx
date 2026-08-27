"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Bot, Flame, RefreshCw, ShieldAlert, Zap } from "lucide-react";
import { dayTradingClient } from "@/services/day-trading-client";
import { limitUpAiClient } from "@/services/limit-up-ai-client";

type RobotTarget = "day-trading" | "limit-up-ai" | "adaptive-electronic";
type HealthTone = "ok" | "warming" | "paused" | "error";

interface RobotHealth {
  id: RobotTarget;
  title: string;
  subtitle: string;
  tone: HealthTone;
  status: string;
  detail: string;
  updatedAt?: string | null;
}

type LooseRecord = Record<string, unknown>;

function text(value: unknown): string {
  return typeof value === "string" && value.trim() ? value : "";
}

function record(value: unknown): LooseRecord {
  return value && typeof value === "object" && !Array.isArray(value) ? value as LooseRecord : {};
}

function formatTime(value?: string | null): string {
  if (!value) return "尚無";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleTimeString("zh-TW", { hour12: false, timeZone: "Asia/Taipei" });
}

function dayTradingHealth(payload: unknown, error?: string): RobotHealth {
  const body = record(payload);
  const automation = record(body.automation);
  const supervisor = record(body.supervisor);
  const session = Object.keys(record(supervisor.session)).length ? record(supervisor.session) : automation;
  const phase = text(session.phase);
  const statusMessage = text(session.statusMessage) || text(body.recommendationSummary) || text(body.dataNotice);
  const supervisorStatus = text(supervisor.status);
  const dataStatus = text(body.dataStatus);
  const latest = text(session.localTime) || text(body.updatedAt);
  let tone: HealthTone = "warming";
  let status = "等待啟動";

  if (error) {
    tone = "error";
    status = "連線異常";
  } else if (dataStatus === "disconnected" || dataStatus === "source_error" || dataStatus === "severe_delay") {
    tone = "error";
    status = "資料異常";
  } else if (supervisorStatus === "running" || ["warmup", "scanning", "entry_closed", "closing"].includes(phase)) {
    tone = phase === "scanning" ? "ok" : "warming";
    status = phase === "scanning" ? "盤中掃描" : phase === "entry_closed" ? "停止新進場" : "排程運作";
  }

  return {
    id: "day-trading",
    title: "當沖機器人",
    subtitle: "多空正式訊號",
    tone,
    status,
    detail: error || statusMessage || "讀取排程與資料新鮮度中",
    updatedAt: latest,
  };
}

function limitUpHealth(payload: unknown, error?: string): RobotHealth {
  const body = record(payload);
  const statusValue = text(body.status);
  const lastError = text(body.lastError);
  const marketSessionActive = body.marketSessionActive === true;
  let tone: HealthTone = "warming";
  let status = "等待盤中";

  if (error || lastError) {
    tone = "error";
    status = "偵測異常";
  } else if (statusValue === "running") {
    tone = marketSessionActive ? "ok" : "warming";
    status = marketSessionActive ? "背景偵測中" : "保留最後結果";
  } else if (statusValue) {
    tone = "paused";
    status = statusValue;
  }

  return {
    id: "limit-up-ai",
    title: "漲停機器人",
    subtitle: "飆股 / 近漲停",
    tone,
    status,
    detail: error || lastError || (marketSessionActive ? "盤中每 15 秒掃描候選" : "非盤中，等待下一次市場時段"),
    updatedAt: text(body.lastSuccessAt) || text(body.lastRunAt) || null,
  };
}

function superAiHealth(payload: unknown, error?: string): RobotHealth {
  const body = record(payload);
  const settings = record(body.settings);
  const risk = record(body.risk);
  const running = body.running === true || text(body.status) === "running";
  const lastError = text(body.lastError);
  const stopReason = text(risk.stopReason) || text(settings.stopReason);
  const stopNewTrades = risk.stopNewTrades === true || settings.stopNewTrades === true;
  let tone: HealthTone = running ? "ok" : "paused";
  let status = running ? "模型運作中" : "未啟動";

  if (error || lastError) {
    tone = "error";
    status = "偵測異常";
  } else if (stopNewTrades) {
    tone = "paused";
    status = "風控暫停新單";
  }

  return {
    id: "adaptive-electronic",
    title: "超強 AI 當沖機器人",
    subtitle: "電子股 AI 風控",
    tone,
    status,
    detail: error || lastError || stopReason || text(record(body.marketState).label) || "監控候選、持倉與風控狀態",
    updatedAt: text(body.lastSuccessAt) || text(body.lastRunAt) || null,
  };
}

async function jsonOrError(response: Response): Promise<unknown> {
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const body = record(payload);
    throw new Error(text(body.error) || text(body.detail) || `HTTP ${response.status}`);
  }
  return payload;
}

export function RobotHealthPanel({
  userId,
  onOpen,
}: {
  userId: string;
  onOpen: (target: RobotTarget) => void;
}) {
  const [items, setItems] = useState<RobotHealth[]>(() => [
    dayTradingHealth(null),
    limitUpHealth(null),
    superAiHealth(null),
  ]);
  const [loading, setLoading] = useState(false);
  const [lastChecked, setLastChecked] = useState<string | null>(null);

  const headers = useMemo(() => ({ "x-user-id": userId || "system-automation" }), [userId]);

  const load = useCallback(async () => {
    setLoading(true);
    const [day, limitUp, superAi] = await Promise.allSettled([
      dayTradingClient.regime(userId || "system-automation"),
      limitUpAiClient.status(userId || "system-automation"),
      fetch("/api/adaptive-electronic/status", { cache: "no-store", headers }).then(jsonOrError),
    ]);
    setItems([
      day.status === "fulfilled"
        ? dayTradingHealth(day.value)
        : dayTradingHealth(null, day.reason instanceof Error ? day.reason.message : "當沖狀態讀取失敗"),
      limitUp.status === "fulfilled"
        ? limitUpHealth(limitUp.value)
        : limitUpHealth(null, limitUp.reason instanceof Error ? limitUp.reason.message : "漲停狀態讀取失敗"),
      superAi.status === "fulfilled"
        ? superAiHealth(superAi.value)
        : superAiHealth(null, superAi.reason instanceof Error ? superAi.reason.message : "超強 AI 狀態讀取失敗"),
    ]);
    setLastChecked(new Date().toISOString());
    setLoading(false);
  }, [headers, userId]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 30_000);
    return () => window.clearInterval(timer);
  }, [load]);

  return <section className="robot-health-strip" aria-label="機器人即時狀態">
    <div className="robot-health-heading">
      <strong><Bot size={15} />機器人狀態</strong>
      <span>{loading ? "更新中" : `最後檢查 ${formatTime(lastChecked)}`}</span>
    </div>
    <div className="robot-health-items">
      {items.map((item) => <button
        type="button"
        className={`robot-health-card ${item.tone}`}
        key={item.id}
        onClick={() => onOpen(item.id)}
      >
        <span className="robot-health-icon">
          {item.id === "day-trading" ? <Bot size={15} /> : item.id === "limit-up-ai" ? <Flame size={15} /> : <Zap size={15} />}
        </span>
        <span className="robot-health-copy">
          <strong>{item.title}</strong>
          <small>{item.subtitle}</small>
        </span>
        <b>{item.status}</b>
        <em>{item.detail}</em>
        <i>{formatTime(item.updatedAt)}</i>
      </button>)}
    </div>
    <button className="robot-health-refresh" type="button" onClick={() => void load()} disabled={loading} aria-label="重新整理機器人狀態">
      {loading ? <span className="spinner small" /> : <RefreshCw size={13} />}
    </button>
    {items.some((item) => item.tone === "error") && <div className="robot-health-alert" role="status">
      <ShieldAlert size={13} />至少一個機器人狀態異常；點卡片進入該頁看詳細錯誤。
    </div>}
  </section>;
}
