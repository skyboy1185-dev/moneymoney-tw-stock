"use client";

import { useEffect, useRef, useState } from "react";
import { Bot, CircleDollarSign, ShieldAlert, TrendingDown, TrendingUp, X } from "lucide-react";
import { usePathname } from "next/navigation";
import type { DayTradingAlert, DayTradingSignal, TradingAutomationState } from "@/lib/day-trading-types";
import type { LongTermTradeMessage } from "@/lib/long-term-types";
import type { RocketNotification } from "@/lib/rocket-radar-types";

const AUTOMATION_USER_ID = "system-automation";
const STORAGE_KEY = "day-trading-robot-web-notifications";
type RobotTarget = "day-trading" | "adaptive-electronic" | "rocket-radar" | "long-term";

type RobotToastKind = "activation" | "buy" | "short" | "reduce" | "sell" | "cover" | "stop";

interface RobotToast {
  id: string;
  kind: RobotToastKind;
  target: RobotTarget;
  title: string;
  stock: string;
  message: string;
  reason: string;
  timestamp: string;
}

interface AdaptiveSignal {
  id: number;
  stockCode: string | null;
  stockName: string | null;
  signalType: string;
  action: string;
  price: number | null;
  healthScore: number | null;
  reasons: string[];
  createdAt: string;
}

interface AdaptiveNotification {
  id: number;
  category: string;
  level: string;
  symbol: string | null;
  symbolName: string | null;
  title: string;
  message: string;
  strategy: string | null;
  side: string | null;
  price: number | null;
  quantity: number | null;
  aiScore: number | null;
  riskReward: number | null;
  timestamp: string;
  stockCode?: string | null;
  stockName?: string | null;
  action?: string;
  reasons?: string[];
  healthScore?: number | null;
  createdAt?: string;
}

interface RegimeResponse {
  automation: TradingAutomationState;
  supervisor?: {
    status?: string;
    session?: TradingAutomationState;
  };
}

const headers = { "x-user-id": AUTOMATION_USER_ID };
const ROCKET_TRADE_TYPES = new Set(["BUY", "ADD", "REDUCE", "TAKE_PROFIT", "SELL", "STOP_LOSS"]);
const SUPER_AI_TOAST_CATEGORIES = new Set(["BUY", "SHORT", "ADD", "REDUCE", "STOP_LOSS", "TAKE_PROFIT", "EXIT"]);

function time(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value || "—";
  return parsed.toLocaleTimeString("zh-TW", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    timeZone: "Asia/Taipei",
  });
}

function taipeiDate(value: string): string {
  return new Date(value).toLocaleDateString("sv-SE", { timeZone: "Asia/Taipei" });
}

function alertKind(alert: DayTradingAlert): RobotToastKind {
  const text = `${alert.action} ${alert.reason}`;
  if (alert.level === "emergency" || text.includes("停損") || text.includes("緊急")) return "stop";
  if (text.includes("回補")) return "cover";
  if (text.includes("全部") || text.includes("賣出")) return "sell";
  return "reduce";
}

function ToastIcon({ kind }: { kind: RobotToastKind }) {
  if (kind === "activation") return <Bot />;
  if (kind === "buy") return <TrendingUp />;
  if (kind === "short") return <TrendingDown />;
  if (kind === "cover") return <TrendingUp />;
  if (kind === "reduce") return <TrendingDown />;
  if (kind === "stop") return <ShieldAlert />;
  return <CircleDollarSign />;
}

export function DayTradingRobotNotifier({ onOpen }: { onOpen?: (target: RobotTarget) => void }) {
  const pathname = usePathname();
  const [toasts, setToasts] = useState<RobotToast[]>([]);
  const seenEvents = useRef(new Set<string>());
  const timers = useRef<number[]>([]);

  useEffect(() => {
    if (pathname === "/login") return;
    try {
      const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "[]") as string[];
      seenEvents.current = new Set(stored);
    } catch { /* start with an empty browser-local deduplication set */ }

    let stopped = false;

    const remember = (ids: string[]) => {
      ids.forEach((id) => seenEvents.current.add(id));
      const recent = Array.from(seenEvents.current).slice(-300);
      try { localStorage.setItem(STORAGE_KEY, JSON.stringify(recent)); } catch { /* storage is optional */ }
    };

    const show = (items: RobotToast[]) => {
      const fresh = items.filter((item) => !seenEvents.current.has(item.id));
      if (!fresh.length) return;
      remember(fresh.map((item) => item.id));
      setToasts((current) => [...fresh, ...current].slice(0, 8));
      fresh.forEach((item) => {
        const duration = item.kind === "activation" ? 8_000 : item.kind === "stop" ? 15_000 : 12_000;
        timers.current.push(window.setTimeout(() => {
          setToasts((current) => current.filter((toast) => toast.id !== item.id));
        }, duration));
      });
    };

    const load = async () => {
      if (document.visibilityState === "hidden") return;
      try {
        const [
          regimeResponse, signalResponse, alertResponse, adaptiveNotificationResponse,
          rocketResponse, longOnlyResponse, focusedLongResponse,
        ] = await Promise.all([
          fetch("/api/day-trading/market-regime", { cache: "no-store", headers }),
          fetch("/api/day-trading/signals/today", { cache: "no-store", headers }),
          fetch("/api/day-trading/alerts", { cache: "no-store", headers }),
          fetch("/api/adaptive-electronic/notifications?source=SUPER_AI_DAYTRADE&limit=80", { cache: "no-store" }),
          fetch("/api/rocket-radar/notifications?period=today&limit=100", { cache: "no-store" }),
          fetch("/api/long-term/events?mode=long_only&afterId=0&limit=100", { cache: "no-store" }),
          fetch("/api/long-term/events?mode=focused_long&afterId=0&limit=100", { cache: "no-store" }),
        ]);
        const today = taipeiDate(new Date().toISOString());
        const regime = regimeResponse.ok ? await regimeResponse.json() as RegimeResponse : null;
        const signals = signalResponse.ok
          ? await signalResponse.json() as { tradingDate: string; items: DayTradingSignal[] }
          : { tradingDate: today, items: [] };
        const alerts = alertResponse.ok
          ? await alertResponse.json() as { items: DayTradingAlert[] }
          : { items: [] };
        const adaptiveNotificationPayload = adaptiveNotificationResponse.ok
          ? await adaptiveNotificationResponse.json() as { items: AdaptiveNotification[] }
          : { items: [] };
        const adaptiveNotifications = {
          items: adaptiveNotificationPayload.items.map((item) => ({
            ...item,
            stockCode: item.symbol,
            stockName: item.symbolName,
            action: item.category,
            reasons: [
              item.strategy ? `策略 ${item.strategy}` : "",
              item.aiScore == null ? "" : `AI ${item.aiScore.toFixed(0)}`,
              item.riskReward == null ? "" : `R/R 1:${item.riskReward.toFixed(2)}`,
            ].filter(Boolean),
            healthScore: item.aiScore,
            createdAt: item.timestamp,
          })),
        };
        const rocketSignals = rocketResponse.ok
          ? await rocketResponse.json() as { items: RocketNotification[] }
          : { items: [] };
        const longOnlySignals = longOnlyResponse.ok
          ? await longOnlyResponse.json() as { items: LongTermTradeMessage[] }
          : { items: [] };
        const focusedLongSignals = focusedLongResponse.ok
          ? await focusedLongResponse.json() as { items: LongTermTradeMessage[] }
          : { items: [] };
        const session = regime?.supervisor?.session ?? regime?.automation;
        const supervisorRunning = regime?.supervisor?.status === "running";
        const activePhase = session
          ? ["warmup", "scanning", "entry_closed", "closing"].includes(session.phase)
          : false;
        const items: RobotToast[] = [];

        if (session && supervisorRunning && activePhase) {
          items.push({
            id: `robot-activation:${session.tradingDate}`,
            kind: "activation",
            target: "day-trading",
            title: "AI 當沖機器人已啟動",
            stock: "今日自動監控運作中",
            message: session.phase === "scanning"
              ? "正在掃描正式進場訊號。"
              : session.phase === "entry_closed"
                ? "已停止新進場，持續監控現有部位。"
                : "當沖排程與自動持倉監控正常運作。",
            reason: session.statusMessage,
            timestamp: session.localTime,
          });
        }

        signals.items.filter((signal) => signal.isOfficialRecommendation).forEach((signal) => {
          items.push({
            id: `robot-entry:${signal.id}`,
            kind: signal.direction === "long" ? "buy" : "short",
            target: "day-trading",
            title: signal.direction === "long" ? "AI 當沖機器人｜模擬買進" : "AI 當沖機器人｜模擬放空",
            stock: `${signal.symbol} ${signal.stockName}`,
            message: `${signal.action}・進場 ${signal.entryMin.toFixed(2)}～${signal.entryMax.toFixed(2)}・停損 ${signal.stopLoss.toFixed(2)}`,
            reason: `信心 ${signal.confidenceScore}・RR ${signal.riskRewardRatio}`,
            timestamp: signal.recommendedAt ?? signal.generatedAt,
          });
        });

        alerts.items.filter((alert) => taipeiDate(alert.createdAt) === signals.tradingDate).forEach((alert) => {
          const kind = alertKind(alert);
          const isCover = alert.action.includes("回補");
          items.push({
            id: `robot-alert:${alert.id}`,
            kind,
            target: "day-trading",
            title: kind === "stop" ? `AI 當沖機器人｜${isCover ? "空單停損回補" : "多單停損賣出"}`
              : kind === "cover" ? "AI 當沖機器人｜模擬回補"
                : kind === "sell" ? "AI 當沖機器人｜模擬賣出"
                : "AI 當沖機器人｜模擬減碼",
            stock: alert.message,
            message: `${alert.action}・價格 ${alert.price.toFixed(2)}`,
            reason: alert.reason,
            timestamp: alert.createdAt,
          });
        });

        adaptiveNotifications.items
          .filter((signal) => (
            SUPER_AI_TOAST_CATEGORIES.has(signal.category)
            && taipeiDate(signal.timestamp) === signals.tradingDate
          ))
          .forEach((signal) => {
            const isEntry = ["BUY", "SHORT", "ADD"].includes(signal.category);
            const price = signal.price == null ? "—" : signal.price.toFixed(2);
            items.push({
              id: `adaptive-notification:${signal.id}`,
              kind: signal.category === "STOP_LOSS" ? "stop" : isEntry ? (signal.category === "SHORT" ? "short" : "buy") : "sell",
              target: "adaptive-electronic",
              title: isEntry ? "超強AI當沖系統｜模擬買進" : "超強AI當沖系統｜模擬賣出",
              stock: `${signal.stockCode ?? "—"} ${signal.stockName ?? ""}`.trim(),
              message: `${signal.action}・價格 ${price}`,
              reason: signal.reasons.slice(0, 2).join("；")
                || (signal.healthScore == null ? "正式策略訊號" : `健康度 ${signal.healthScore.toFixed(1)}`),
              timestamp: signal.createdAt,
            });
          });

        rocketSignals.items
          .filter((signal) => (
            ROCKET_TRADE_TYPES.has(signal.notificationType)
            && taipeiDate(signal.timestamp) === signals.tradingDate
          ))
          .forEach((signal) => {
            const labels: Record<string, string> = {
              BUY: "買進", ADD: "加碼", REDUCE: "減碼", TAKE_PROFIT: "停利",
              SELL: "賣出", STOP_LOSS: "停損",
            };
            const kind: RobotToastKind = signal.notificationType === "STOP_LOSS" ? "stop"
              : ["SELL", "TAKE_PROFIT"].includes(signal.notificationType) ? "sell"
                : signal.notificationType === "REDUCE" ? "reduce" : "buy";
            items.push({
              id: `rocket-signal:${signal.notificationId}`,
              kind,
              target: "rocket-radar",
              title: `飆股雷達｜${labels[signal.notificationType] ?? signal.notificationType}`,
              stock: `${signal.stockCode ?? "—"} ${signal.stockName ?? ""}`.trim(),
              message: signal.message,
              reason: signal.reason,
              timestamp: signal.timestamp,
            });
          });

        const addLongTermSignals = (rows: LongTermTradeMessage[], modeLabel: string) => {
          rows.filter((signal) => signal.tradeDate === signals.tradingDate).forEach((signal) => {
            const replacement = signal.reason.includes("汰換") || signal.reason.includes("換股");
            const action = signal.eventType === "BUY" ? "買進" : "賣出";
            items.push({
              id: `long-term-signal:${signal.id}`,
              kind: signal.eventType === "BUY" ? "buy" : "sell",
              target: "long-term",
              title: `長線選股｜${replacement ? `換股${action}` : `模擬${action}`}（${modeLabel}）`,
              stock: `${signal.stockCode} ${signal.stockName}`,
              message: `價格 ${signal.price.toFixed(2)}・${signal.quantity.toLocaleString("zh-TW")} 股`,
              reason: signal.reason,
              timestamp: signal.timestamp,
            });
          });
        };
        addLongTermSignals(longOnlySignals.items, "10 檔穩健組");
        addLongTermSignals(focusedLongSignals.items, "3 檔精選組");

        if (!stopped) show(items.sort((left, right) => {
          const priority = (kind: RobotToastKind) => kind === "stop" ? 0
            : ["sell", "cover"].includes(kind) ? 1
              : kind === "reduce" ? 2
                : ["buy", "short"].includes(kind) ? 3 : 4;
          return priority(left.kind) - priority(right.kind);
        }));
      } catch { /* polling retries automatically */ }
    };

    const syncSeenEvents = (event: StorageEvent) => {
      if (event.key !== STORAGE_KEY || !event.newValue) return;
      try {
        const stored = JSON.parse(event.newValue) as string[];
        stored.forEach((id) => seenEvents.current.add(id));
      } catch { /* ignore malformed browser storage */ }
    };

    void load();
    const timer = window.setInterval(() => void load(), 10_000);
    window.addEventListener("storage", syncSeenEvents);
    return () => {
      stopped = true;
      window.clearInterval(timer);
      window.removeEventListener("storage", syncSeenEvents);
      timers.current.forEach((toastTimer) => window.clearTimeout(toastTimer));
      timers.current = [];
    };
  }, [pathname]);

  if (!toasts.length) return null;
  return <div className="day-bot-toast-stack" aria-live="assertive">
    {toasts.map((item) => <article className={`day-bot-toast ${item.kind} ${item.target}`} key={item.id} role="alert">
      <button className="day-bot-toast-body" type="button" onClick={() => {
        if (onOpen) onOpen(item.target);
        else window.location.assign(`/?view=${item.target}`);
      }}>
        <span><ToastIcon kind={item.kind} /></span>
        <div>
          <strong>{item.title}</strong>
          <h4>{item.stock}</h4>
          <p>{item.message}</p>
          <footer>{item.reason}<time>{time(item.timestamp)}</time></footer>
        </div>
      </button>
      <button className="day-bot-toast-close" type="button" aria-label="關閉機器人通知" onClick={() => setToasts((current) => current.filter((toast) => toast.id !== item.id))}><X /></button>
    </article>)}
  </div>;
}
