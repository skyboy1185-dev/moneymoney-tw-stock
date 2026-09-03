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

function numeric(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function record(value: unknown): LooseRecord {
  return value && typeof value === "object" && !Array.isArray(value) ? value as LooseRecord : {};
}

function formatTime(value?: string | null): string {
  if (!value) return "未更新";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleTimeString("zh-TW", { hour12: false, timeZone: "Asia/Taipei" });
}

function robotLabel(id: RobotTarget): string {
  if (id === "day-trading") return "AI 當沖";
  if (id === "limit-up-ai") return "漲停機器人";
  return "超強 AI 當沖";
}

export function dayTradingHealth(payload: unknown, error?: string): RobotHealth {
  const body = record(payload);
  const automation = record(body.automation);
  const supervisor = record(body.supervisor);
  const supervisorSession = record(supervisor.session);
  const session = Object.keys(supervisorSession).length ? supervisorSession : automation;
  const phase = text(session.phase);
  const supervisorStatus = text(supervisor.status);
  const dataStatus = text(body.dataStatus);
  const dataQualityMode = text(body.dataQualityMode);
  const formalBlockReason = text(body.formalBlockReason);
  const statusMessage = text(session.statusMessage) || text(body.recommendationSummary) || text(body.dataNotice);
  const quoteCoverageCount = numeric(body.quoteCoverageCount) ?? numeric(supervisor.quoteCoverageCount);
  const candidateUniverseCount = numeric(body.candidateUniverseCount) ?? numeric(supervisor.candidateUniverseCount);
  const latest = text(session.localTime) || text(body.updatedAt);
  const runningSupervisor = supervisorStatus === "running";
  const activePhase = ["warmup", "scanning", "long_only"].includes(phase);
  const completedPhase = ["summary", "entry_closed", "closing", "non_trading"].includes(phase);
  const badDataStatus = dataStatus === "disconnected" || dataStatus === "source_error" || dataStatus === "severe_delay";
  const degradedButUsable = dataQualityMode === "index_delay";

  let tone: HealthTone = "warming";
  let status = "待啟動";

  if (error) {
    tone = "error";
    status = "連線異常";
  } else if (runningSupervisor && completedPhase) {
    tone = "paused";
    status = phase === "summary" ? "盤後完成" : phase === "entry_closed" || phase === "closing" ? "停止進場" : "非交易時段";
  } else if (degradedButUsable && activePhase) {
    tone = "warming";
    status = "資料降級";
  } else if (badDataStatus && activePhase) {
    tone = "error";
    status = "資料異常";
  } else if (badDataStatus && !runningSupervisor) {
    tone = "error";
    status = "資料異常";
  } else if (runningSupervisor || ["warmup", "scanning", "long_only", "entry_closed", "closing", "summary"].includes(phase)) {
    tone = phase === "scanning" || phase === "long_only" ? "ok" : "warming";
    status = phase === "scanning" || phase === "long_only" ? "掃描中" : phase === "entry_closed" ? "停止進場" : "準備中";
  }

  const coverageDetail = quoteCoverageCount !== null && candidateUniverseCount !== null
    ? `報價覆蓋 ${quoteCoverageCount}/${candidateUniverseCount}`
    : "";

  return {
    id: "day-trading",
    title: "AI 當沖多空",
    subtitle: "盤中訊號 / 持倉監控",
    tone,
    status,
    detail: error || formalBlockReason || (badDataStatus ? coverageDetail : "") || statusMessage || coverageDetail || "等待盤中資料更新",
    updatedAt: latest,
  };
}

function limitUpHealth(payload: unknown, error?: string): RobotHealth {
  const body = record(payload);
  const statusValue = text(body.status);
  const lastError = text(body.lastError);
  const marketSessionActive = body.marketSessionActive === true;
  let tone: HealthTone = "warming";
  let status = "待啟動";

  if (error || lastError) {
    tone = "error";
    status = "偵測異常";
  } else if (statusValue === "running") {
    tone = marketSessionActive ? "ok" : "warming";
    status = marketSessionActive ? "偵測中" : "盤後待命";
  } else if (statusValue) {
    tone = "paused";
    status = statusValue;
  }

  return {
    id: "limit-up-ai",
    title: "漲停機器人",
    subtitle: "鎖漲停 / 雷達通知",
    tone,
    status,
    detail: error || lastError || (marketSessionActive ? "盤中每 15 秒自動偵測" : "非盤中，保留最後結果"),
    updatedAt: text(body.lastSuccessAt) || text(body.lastRunAt) || null,
  };
}

export function superAiHealth(payload: unknown, error?: string): RobotHealth {
  const body = record(payload);
  const settings = record(body.settings);
  const risk = record(body.risk);
  const running = body.running === true || text(body.status) === "running";
  const lastError = text(body.lastError);
  const stopReason = text(risk.stopReason) || text(settings.stopReason);
  const stopNewTrades = risk.stopNewTrades === true || settings.stopNewTrades === true;
  const scannerPaused = body.newTradesPausedByScanner === true || body.candidateDataStale === true;
  const latestCandidateTradeDate = text(body.latestCandidateTradeDate);
  const scannerDetail = scannerPaused
    ? lastError || `掃描資料過期${latestCandidateTradeDate ? `：候選股日期 ${latestCandidateTradeDate}` : ""}`
    : "";
  let tone: HealthTone = running ? "ok" : "paused";
  let status = running ? "運作中" : "暫停";

  if (error || lastError) {
    tone = "error";
    status = "偵測異常";
  } else if (scannerPaused) {
    tone = "error";
    status = "掃描資料過期";
  } else if (stopNewTrades) {
    tone = "paused";
    status = "風控暫停";
  }

  return {
    id: "adaptive-electronic",
    title: "超強 AI 當沖",
    subtitle: "電子股 AI 風控",
    tone,
    status,
    detail: error || scannerDetail || lastError || stopReason || text(record(body.marketState).label) || "依風控與 AI 評分自動調整",
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
        : dayTradingHealth(null, day.reason instanceof Error ? day.reason.message : "AI 當沖狀態讀取失敗"),
      limitUp.status === "fulfilled"
        ? limitUpHealth(limitUp.value)
        : limitUpHealth(null, limitUp.reason instanceof Error ? limitUp.reason.message : "漲停機器人狀態讀取失敗"),
      superAi.status === "fulfilled"
        ? superAiHealth(superAi.value)
        : superAiHealth(null, superAi.reason instanceof Error ? superAi.reason.message : "超強 AI 當沖狀態讀取失敗"),
    ]);
    setLastChecked(new Date().toISOString());
    setLoading(false);
  }, [headers, userId]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 30_000);
    return () => window.clearInterval(timer);
  }, [load]);

  const errorSummary = items
    .filter((item) => item.tone === "error")
    .map((item) => `${robotLabel(item.id)}：${item.detail || item.status}`)
    .join("；");

  return <section className="robot-health-strip" aria-label="機器人狀態總覽">
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
    {errorSummary && <div className="robot-health-alert" role="status">
      <ShieldAlert size={13} />{errorSummary}
    </div>}
  </section>;
}
