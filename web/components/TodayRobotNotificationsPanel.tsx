"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, BellRing, Bot, ChevronDown, Flame, RefreshCw, Rocket, ScanSearch, Zap } from "lucide-react";
import {
  normalizeTodayRobotNotifications,
  summarizeTodayRobotNotifications,
  TODAY_ROBOT_SOURCE_LABELS,
  type TodayRobotNotification,
  type TodayRobotSource,
} from "@/lib/today-robot-notifications";
import { emptyNotificationPoll, mergeNotificationPoll, notificationSourceState } from "@/lib/robot-notification-poll";

const FILTERS: Array<{ source: TodayRobotSource | "all"; label: string }> = [
  { source: "all", label: "全部" },
  { source: "day-trading-v2", label: TODAY_ROBOT_SOURCE_LABELS["day-trading-v2"] },
  { source: "day-trading", label: TODAY_ROBOT_SOURCE_LABELS["day-trading"] },
  { source: "limit-up-ai", label: TODAY_ROBOT_SOURCE_LABELS["limit-up-ai"] },
  { source: "adaptive-electronic", label: TODAY_ROBOT_SOURCE_LABELS["adaptive-electronic"] },
  { source: "pattern-robot", label: TODAY_ROBOT_SOURCE_LABELS["pattern-robot"] },
  { source: "rocket-radar", label: TODAY_ROBOT_SOURCE_LABELS["rocket-radar"] },
];

function sourceIcon(source: TodayRobotSource) {
  if (source === "day-trading" || source === "day-trading-v2") return <Bot size={15} />;
  if (source === "limit-up-ai") return <Flame size={15} />;
  if (source === "adaptive-electronic") return <Zap size={15} />;
  if (source === "pattern-robot") return <ScanSearch size={15} />;
  return <Rocket size={15} />;
}

function formatTime(value?: string | null): string {
  if (!value) return "尚無";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleTimeString("zh-TW", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    timeZone: "Asia/Taipei",
  });
}

function displayStock(item: TodayRobotNotification): string {
  if (!item.symbol && !item.stockName) return "全市場 / 系統";
  return `${item.symbol ?? ""} ${item.stockName ?? ""}`.trim();
}

async function jsonOrThrow(url: string, init?: RequestInit): Promise<unknown> {
  const response = await fetch(url, {
    ...init,
    cache: "no-store",
    signal: init?.signal ?? AbortSignal.timeout(15_000),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = body && typeof body === "object" && "error" in body && typeof body.error === "string"
      ? body.error
      : `HTTP ${response.status}`;
    throw new Error(message);
  }
  return body;
}

export function TodayRobotNotificationsPanel({
  userId,
  onOpen,
}: {
  userId: string;
  onOpen: (target: TodayRobotSource) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [poll, setPoll] = useState(() => emptyNotificationPoll(userId));
  const [activeSource, setActiveSource] = useState<TodayRobotSource | "all">("all");
  const [loading, setLoading] = useState(false);
  const requestId = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const cancelRequests = useCallback(() => { ++requestId.current; controller.current?.abort(); }, []);

  const load = useCallback(async () => {
    if (!userId) return;
    const current = ++requestId.current;
    controller.current?.abort();
    const abort = new AbortController();
    controller.current = abort;
    const init = { headers: { "x-user-id": userId }, signal: AbortSignal.any([abort.signal, AbortSignal.timeout(15_000)]) };
    setLoading(true);
    const results = await Promise.allSettled([
      jsonOrThrow("/api/day-trading/signals/today", init),
      jsonOrThrow("/api/day-trading/alerts", init),
      jsonOrThrow("/api/limit-up-ai/notifications?limit=100", init),
      jsonOrThrow("/api/adaptive-electronic/notifications?source=SUPER_AI_DAYTRADE&limit=120", init),
      jsonOrThrow("/api/pattern-robot/messages?pageSize=100", init),
      jsonOrThrow("/api/rocket-radar/notifications?period=today&limit=100", init),
      jsonOrThrow("/api/day-trading-v2/notifications", init),
    ]);
    if (current !== requestId.current) return;
    setPoll((previous) => mergeNotificationPoll(previous, userId, results, new Date().toISOString()));
    setLoading(false);
  }, [userId]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 30_000);
    const resume = () => { if (document.visibilityState === "visible") void load(); };
    document.addEventListener("visibilitychange", resume);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", resume);
      cancelRequests();
    };
  }, [load, cancelRequests]);

  const currentPoll = useMemo(() => poll.ownerId === userId ? poll : emptyNotificationPoll(userId), [poll, userId]);
  const items = useMemo(() => normalizeTodayRobotNotifications(currentPoll.payloads), [currentPoll]);
  const sourceStates = Object.fromEntries(FILTERS.filter((filter) => filter.source !== "all").map((filter) => [filter.source, notificationSourceState(currentPoll, filter.source as TodayRobotSource)]));
  const failedSources = Object.values(sourceStates).filter((state) => state.status === "stale" || state.status === "unavailable").length;
  const hasTodayData = Object.values(sourceStates).some((state) => state.hasTodayData);
  const lastUpdated = currentPoll.attemptedAt;
  const summaries = useMemo(() => summarizeTodayRobotNotifications(items), [items]);
  const visibleItems = useMemo(() => (
    activeSource === "all" ? items : items.filter((item) => item.source === activeSource)
  ).slice(0, 80), [activeSource, items]);
  const unreadCount = items.filter((item) => item.isRead === false).length;

  return <section className="today-robot-notifications compact-notifications" aria-label="今日機器人通知">
    <div className="robot-compact-heading">
      <button type="button" className="robot-panel-toggle" aria-expanded={expanded} aria-controls="today-robot-details" onClick={() => setExpanded((value) => !value)}>
        <BellRing size={16} /><strong>今日機器人通知</strong>
        <span>{hasTodayData ? `${failedSources ? "已知 " : ""}${items.length} 則・未讀 ${unreadCount}` : loading ? "讀取中" : "未取得"}{failedSources > 0 ? `・${failedSources} 個來源更新失敗` : ""}</span>
        <ChevronDown size={16} className={expanded ? "expanded" : ""} />
      </button>
      <button type="button" className="robot-panel-refresh" onClick={() => void load()} disabled={loading || !userId} aria-label="重新整理今日機器人通知"><RefreshCw size={15} className={loading ? "spin-icon" : ""} /></button>
    </div>
    <div id="today-robot-details" hidden={!expanded}>
    <p className="robot-health-updated">最後嘗試更新 {formatTime(lastUpdated)}{failedSources ? "・失敗來源保留上次取得的通知，數量可能不完整。" : "・最新通知在上"}</p>
    <div className="today-robot-summary">
      {summaries.map((summary) => <button
        type="button"
        key={summary.source}
        className={activeSource === summary.source ? "active" : ""}
        onClick={() => setActiveSource(summary.source)}
        aria-pressed={activeSource === summary.source}
      >
        <span>{sourceIcon(summary.source)}</span>
        <strong>{summary.sourceLabel}</strong>
        <b>{sourceStates[summary.source].hasTodayData ? summary.count : "—"}</b>
        <small>{sourceStates[summary.source].status === "stale" ? "更新失敗・保留舊資料" : sourceStates[summary.source].status === "unavailable" ? "未取得資料" : sourceStates[summary.source].status === "waiting" ? "等待讀取" : `未讀 ${summary.unreadCount}・最後 ${formatTime(summary.lastTimestamp)}`}</small>
      </button>)}
    </div>

    <div className="today-robot-filters">
      {FILTERS.map((filter) => <button
        type="button"
        key={filter.source}
        className={activeSource === filter.source ? "active" : ""}
        onClick={() => setActiveSource(filter.source)}
        aria-pressed={activeSource === filter.source}
      >
        {filter.label}
      </button>)}
      {activeSource !== "all" && <button type="button" onClick={() => onOpen(activeSource)}>開啟策略</button>}
      {failedSources > 0 && <span><AlertTriangle size={13} />{failedSources} 個來源更新失敗</span>}
    </div>

    <div className="today-robot-list">
      {visibleItems.length ? visibleItems.map((item) => <article className={`${item.level} ${item.isRead === false ? "unread" : ""}`} key={item.id}>
        <time>{formatTime(item.timestamp)}</time>
        <span className={`today-robot-source source-${item.source}`}>{sourceIcon(item.source)}{item.sourceLabel}{sourceStates[item.source].status === "stale" && <small>上次取得</small>}</span>
        <span className={`today-robot-action action-${item.action}`}>{item.actionLabel}</span>
        <button type="button" onClick={() => onOpen(item.source)}>
          <strong>{displayStock(item)}</strong>
          <b>{item.title}</b>
          <p>{item.message || "沒有額外訊息"}</p>
          <small>{item.reason || item.rawType}</small>
        </button>
        {item.isRead === false && <i>未讀</i>}
      </article>) : <div className="today-robot-empty">
        <BellRing size={24} />
        <strong>{loading ? "正在讀取通知" : !hasTodayData || failedSources ? "目前沒有可顯示的通知，部分來源未更新" : "今日尚無通知"}</strong>
        <span>若開盤後有正式訊號、出場、停損或掃描完成，會集中顯示在這裡。</span>
      </div>}
    </div>
    </div>
  </section>;
}
