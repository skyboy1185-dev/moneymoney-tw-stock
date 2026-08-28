"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, BellRing, Bot, Flame, RefreshCw, Rocket, ScanSearch, Zap } from "lucide-react";
import {
  normalizeTodayRobotNotifications,
  summarizeTodayRobotNotifications,
  TODAY_ROBOT_SOURCE_LABELS,
  type TodayRobotNotification,
  type TodayRobotNotificationPayloads,
  type TodayRobotSource,
} from "@/lib/today-robot-notifications";

const AUTOMATION_USER_ID = "system-automation";
const PATTERN_USER_ID = "system-pattern-robot";
const FILTERS: Array<{ source: TodayRobotSource | "all"; label: string }> = [
  { source: "all", label: "全部" },
  { source: "day-trading", label: TODAY_ROBOT_SOURCE_LABELS["day-trading"] },
  { source: "limit-up-ai", label: TODAY_ROBOT_SOURCE_LABELS["limit-up-ai"] },
  { source: "adaptive-electronic", label: TODAY_ROBOT_SOURCE_LABELS["adaptive-electronic"] },
  { source: "pattern-robot", label: TODAY_ROBOT_SOURCE_LABELS["pattern-robot"] },
  { source: "rocket-radar", label: TODAY_ROBOT_SOURCE_LABELS["rocket-radar"] },
];

function sourceIcon(source: TodayRobotSource) {
  if (source === "day-trading") return <Bot size={15} />;
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

function fulfilled<T>(result: PromiseSettledResult<T>): T | undefined {
  return result.status === "fulfilled" ? result.value : undefined;
}

function failureCount(results: PromiseSettledResult<unknown>[]): number {
  return results.filter((result) => result.status === "rejected").length;
}

export function TodayRobotNotificationsPanel({
  userId,
  onOpen,
}: {
  userId: string;
  onOpen: (target: TodayRobotSource) => void;
}) {
  const [items, setItems] = useState<TodayRobotNotification[]>([]);
  const [activeSource, setActiveSource] = useState<TodayRobotSource | "all">("all");
  const [loading, setLoading] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);
  const [failedSources, setFailedSources] = useState(0);

  const load = useCallback(async () => {
    setLoading(true);
    const dayHeaders = { "x-user-id": userId || AUTOMATION_USER_ID };
    const patternHeaders = { "x-user-id": userId || PATTERN_USER_ID };
    const results = await Promise.allSettled([
      jsonOrThrow("/api/day-trading/signals/today", { headers: dayHeaders }),
      jsonOrThrow("/api/day-trading/alerts", { headers: dayHeaders }),
      jsonOrThrow("/api/limit-up-ai/notifications?limit=100", { headers: dayHeaders }),
      jsonOrThrow("/api/adaptive-electronic/notifications?source=SUPER_AI_DAYTRADE&limit=120", { headers: dayHeaders }),
      jsonOrThrow("/api/pattern-robot/messages?pageSize=100", { headers: patternHeaders }),
      jsonOrThrow("/api/rocket-radar/notifications?period=today&limit=100"),
    ]);
    const payloads: TodayRobotNotificationPayloads = {
      dayTradingSignals: fulfilled(results[0]),
      dayTradingAlerts: fulfilled(results[1]),
      limitUp: fulfilled(results[2]),
      superAi: fulfilled(results[3]),
      pattern: fulfilled(results[4]),
      rocket: fulfilled(results[5]),
    };
    setItems(normalizeTodayRobotNotifications(payloads));
    setFailedSources(failureCount(results));
    setLastUpdated(new Date().toISOString());
    setLoading(false);
  }, [userId]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 30_000);
    return () => window.clearInterval(timer);
  }, [load]);

  const summaries = useMemo(() => summarizeTodayRobotNotifications(items), [items]);
  const visibleItems = useMemo(() => (
    activeSource === "all" ? items : items.filter((item) => item.source === activeSource)
  ).slice(0, 80), [activeSource, items]);
  const unreadCount = items.filter((item) => item.isRead === false).length;

  return <section className="today-robot-notifications" aria-label="今日機器人通知">
    <header className="today-robot-heading">
      <div>
        <strong><BellRing size={16} />今日機器人通知</strong>
        <span>集中顯示各機器人今天產生的訊息，最新在上</span>
      </div>
      <div>
        <b>{items.length} 則</b>
        <small>未讀 {unreadCount}・更新 {formatTime(lastUpdated)}</small>
      </div>
      <button type="button" onClick={() => void load()} disabled={loading} aria-label="重新整理今日機器人通知">
        {loading ? <span className="spinner small" /> : <RefreshCw size={13} />}
      </button>
    </header>

    <div className="today-robot-summary">
      {summaries.map((summary) => <button
        type="button"
        key={summary.source}
        className={activeSource === summary.source ? "active" : ""}
        onClick={() => setActiveSource(summary.source)}
        onDoubleClick={() => onOpen(summary.source)}
      >
        <span>{sourceIcon(summary.source)}</span>
        <strong>{summary.sourceLabel}</strong>
        <b>{summary.count}</b>
        <small>未讀 {summary.unreadCount}・最後 {formatTime(summary.lastTimestamp)}</small>
      </button>)}
    </div>

    <div className="today-robot-filters">
      {FILTERS.map((filter) => <button
        type="button"
        key={filter.source}
        className={activeSource === filter.source ? "active" : ""}
        onClick={() => setActiveSource(filter.source)}
      >
        {filter.label}
      </button>)}
      {failedSources > 0 && <span><AlertTriangle size={13} />{failedSources} 個來源暫時讀取失敗</span>}
    </div>

    <div className="today-robot-list">
      {visibleItems.length ? visibleItems.map((item) => <article className={`${item.level} ${item.isRead === false ? "unread" : ""}`} key={item.id}>
        <time>{formatTime(item.timestamp)}</time>
        <span className={`today-robot-source source-${item.source}`}>{sourceIcon(item.source)}{item.sourceLabel}</span>
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
        <strong>今日尚無通知</strong>
        <span>若開盤後有正式訊號、出場、停損或掃描完成，會集中顯示在這裡。</span>
      </div>}
    </div>
  </section>;
}
