"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Bell, Bot, CheckCheck, CircleDollarSign, ShieldAlert, TrendingDown, TrendingUp, X } from "lucide-react";
import { usePathname } from "next/navigation";
import type { DayTradingAlert, DayTradingSignal, TradingAutomationState } from "@/lib/day-trading-types";
import type { LongTermTradeMessage } from "@/lib/long-term-types";
import type { RocketNotification } from "@/lib/rocket-radar-types";
import { getBrowserUserId } from "@/lib/browser-user-id";
import { selectDayTradingV2Notifications } from "@/lib/day-trading-v2-notifications";
import { dayTradingV2Client, DayTradingV2RequestError } from "@/services/day-trading-v2-client";
import { compactNotificationTitle, IN_APP_NOTIFICATION_EVENT, type InAppNotification } from "@/lib/in-app-notifications";

const AUTOMATION_USER_ID = "system-automation";
const STORAGE_KEY = "day-trading-robot-web-notifications";
const READ_STORAGE_KEY = "day-trading-robot-read-notifications";
const INBOX_STORAGE_KEY = "day-trading-robot-notification-inbox";
type RobotTarget = "day-trading-v2" | "day-trading" | "adaptive-electronic" | "rocket-radar" | "long-term";

type RobotToastKind = "activation" | "buy" | "short" | "reduce" | "sell" | "cover" | "stop" | "skip";

interface RobotToast {
  id: string;
  kind: RobotToastKind;
  target: RobotTarget;
  title: string;
  stock: string;
  message: string;
  reason: string;
  timestamp: string;
  href?: string;
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

function time(value?: string | null): string {
  if (!value) return "—";
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

function taipeiDate(value?: string | null): string {
  if (!value) return "";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "";
  return parsed.toLocaleDateString("sv-SE", { timeZone: "Asia/Taipei" });
}

function dateTime(value?: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString("zh-TW", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    timeZone: "Asia/Taipei",
  });
}

function fixed(value: unknown, digits = 2): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : "—";
}

function integer(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("zh-TW") : "—";
}

function okResponse(result: PromiseSettledResult<Response>): Response | null {
  return result.status === "fulfilled" && result.value.ok ? result.value : null;
}

async function jsonOr<T>(response: Response | null, fallback: T): Promise<T> {
  if (!response) return fallback;
  try {
    return await response.json() as T;
  } catch {
    return fallback;
  }
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
  const [inbox, setInbox] = useState<RobotToast[]>([]);
  const [readEvents, setReadEvents] = useState<Set<string>>(new Set());
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [loginExpired, setLoginExpired] = useState(false);
  const seenEvents = useRef(new Set<string>());
  const timers = useRef<number[]>([]);
  const inboxHydrated = useRef(false);

  const show = useCallback((items: RobotToast[]) => {
    setInbox((current) => [...items, ...current.filter((old) => !items.some((item) => item.id === old.id))]
      .sort((left, right) => Date.parse(right.timestamp) - Date.parse(left.timestamp)).slice(0, 100));
    const fresh = items.filter((item) => !seenEvents.current.has(item.id));
    if (!fresh.length) return;
    fresh.forEach((item) => seenEvents.current.add(item.id));
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(Array.from(seenEvents.current).slice(-300))); } catch { /* storage is optional */ }
    const critical = fresh.filter((item) => item.kind === "stop");
    const selected = critical[0] ?? fresh[0];
    const count = critical.length || fresh.length;
    const visible = count > 1 ? {
      ...selected,
      id: `notification-burst:${Date.now()}`,
      title: compactNotificationTitle(count, critical.length > 0),
      stock: "訊息已完整保留在通知中心",
      message: selected.title,
      reason: "點擊查看全部通知",
    } : selected;
    timers.current.forEach((timer) => window.clearTimeout(timer));
    timers.current = [];
    setToasts([visible]);
    const duration = visible.kind === "stop" ? 10_000 : 4_000;
    timers.current.push(window.setTimeout(() => setToasts([]), duration));
  }, []);

  const persistRead = useCallback((next: Set<string>) => {
    setReadEvents(new Set(next));
    try { localStorage.setItem(READ_STORAGE_KEY, JSON.stringify(Array.from(next).slice(-500))); } catch { /* storage is optional */ }
  }, []);

  const openItem = useCallback((item: RobotToast) => {
    persistRead(new Set(readEvents).add(item.id));
    setDrawerOpen(false);
    if (item.href) window.location.assign(item.href);
    else if (onOpen) onOpen(item.target);
    else window.location.assign(`/?view=${item.target}`);
  }, [onOpen, persistRead, readEvents]);

  useEffect(() => {
    if (pathname === "/login") return;
    try {
      const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "[]") as string[];
      seenEvents.current = new Set(stored);
      const storedRead = JSON.parse(localStorage.getItem(READ_STORAGE_KEY) ?? "[]") as string[];
      setReadEvents(new Set(storedRead));
      const storedInbox = JSON.parse(localStorage.getItem(INBOX_STORAGE_KEY) ?? "[]") as RobotToast[];
      if (Array.isArray(storedInbox)) {
        const today = taipeiDate(new Date().toISOString());
        setInbox(storedInbox.filter((item) => (
          item && typeof item.id === "string" && typeof item.title === "string"
          && typeof item.timestamp === "string" && taipeiDate(item.timestamp) === today
        )).slice(0, 500));
      }
    } catch { /* start with an empty browser-local deduplication set */ }
    queueMicrotask(() => { inboxHydrated.current = true; });

    let stopped = false;

    const load = async () => {
      if (document.visibilityState === "hidden") return;
      try {
        const [
          regimeResponse, signalResponse, alertResponse, adaptiveNotificationResponse,
          rocketResponse, longOnlyResponse, focusedLongResponse,
        ] = await Promise.allSettled([
          fetch("/api/day-trading/market-regime", { cache: "no-store", headers }),
          fetch("/api/day-trading/signals/today", { cache: "no-store", headers }),
          fetch("/api/day-trading/alerts", { cache: "no-store", headers }),
          fetch("/api/adaptive-electronic/notifications?source=SUPER_AI_DAYTRADE&limit=80", { cache: "no-store" }),
          fetch("/api/rocket-radar/notifications?period=today&limit=100", { cache: "no-store" }),
          fetch("/api/long-term/events?mode=long_only&afterId=0&limit=100", { cache: "no-store" }),
          fetch("/api/long-term/events?mode=focused_long&afterId=0&limit=100", { cache: "no-store" }),
        ]);
        const today = taipeiDate(new Date().toISOString());
        const regime = await jsonOr<RegimeResponse | null>(okResponse(regimeResponse), null);
        const signals = await jsonOr<{ tradingDate: string; items: DayTradingSignal[] }>(
          okResponse(signalResponse),
          { tradingDate: today, items: [] },
        );
        if (!Array.isArray(signals.items)) signals.items = [];
        if (!signals.tradingDate) signals.tradingDate = today;
        const alerts = await jsonOr<{ items: DayTradingAlert[] }>(okResponse(alertResponse), { items: [] });
        if (!Array.isArray(alerts.items)) alerts.items = [];
        const adaptiveNotificationPayload = await jsonOr<{ items: AdaptiveNotification[] }>(
          okResponse(adaptiveNotificationResponse),
          { items: [] },
        );
        if (!Array.isArray(adaptiveNotificationPayload.items)) adaptiveNotificationPayload.items = [];
        const adaptiveNotifications = {
          items: adaptiveNotificationPayload.items.map((item) => ({
            ...item,
            stockCode: item.symbol,
            stockName: item.symbolName,
            action: item.category,
            reasons: [
              item.strategy ? `策略 ${item.strategy}` : "",
              item.aiScore == null ? "" : `AI ${fixed(item.aiScore, 0)}`,
              item.riskReward == null ? "" : `R/R 1:${fixed(item.riskReward, 2)}`,
            ].filter(Boolean),
            healthScore: item.aiScore,
            createdAt: item.timestamp,
          })),
        };
        const rocketSignals = await jsonOr<{ items: RocketNotification[] }>(okResponse(rocketResponse), { items: [] });
        if (!Array.isArray(rocketSignals.items)) rocketSignals.items = [];
        const longOnlySignals = await jsonOr<{ items: LongTermTradeMessage[] }>(okResponse(longOnlyResponse), { items: [] });
        if (!Array.isArray(longOnlySignals.items)) longOnlySignals.items = [];
        const focusedLongSignals = await jsonOr<{ items: LongTermTradeMessage[] }>(okResponse(focusedLongResponse), { items: [] });
        if (!Array.isArray(focusedLongSignals.items)) focusedLongSignals.items = [];
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
            message: `${signal.action}・進場 ${fixed(signal.entryMin)}～${fixed(signal.entryMax)}・停損 ${fixed(signal.stopLoss)}`,
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
            message: `${alert.action}・價格 ${fixed(alert.price)}`,
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
            const price = fixed(signal.price);
            items.push({
              id: `adaptive-notification:${signal.id}`,
              kind: signal.category === "STOP_LOSS" ? "stop" : isEntry ? (signal.category === "SHORT" ? "short" : "buy") : "sell",
              target: "adaptive-electronic",
              title: isEntry ? "超強AI當沖系統｜模擬買進" : "超強AI當沖系統｜模擬賣出",
              stock: `${signal.stockCode ?? "—"} ${signal.stockName ?? ""}`.trim(),
              message: `${signal.action}・價格 ${price}`,
              reason: signal.reasons.slice(0, 2).join("；")
                || (signal.healthScore == null ? "正式策略訊號" : `健康度 ${fixed(signal.healthScore, 1)}`),
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
            const isSkip = signal.eventType === "SKIP";
            const action = signal.eventType === "BUY" ? "買進" : isSkip ? "未成交" : "賣出";
            const actionTimeLabel = signal.eventType === "BUY" ? "買入行情時間" : isSkip ? "判定時間" : "賣出行情時間";
            items.push({
              id: `long-term-signal:${signal.id}`,
              kind: signal.eventType === "BUY" ? "buy" : isSkip ? "skip" : "sell",
              target: "long-term",
              title: `長線選股｜${isSkip ? action : replacement ? `換股${action}` : `模擬${action}`}（${modeLabel}）`,
              stock: `${signal.stockCode} ${signal.stockName}`,
              message: isSkip
                ? `不列績效・參考價 ${fixed(signal.price)}・${actionTimeLabel} ${dateTime(signal.timestamp)}`
                : `行情模擬成交價 ${fixed(signal.price)}・${integer(signal.quantity)} 股・${actionTimeLabel} ${dateTime(signal.timestamp)}`,
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
      if (!event.newValue) return;
      try {
        const stored = JSON.parse(event.newValue) as string[];
        if (event.key === STORAGE_KEY) stored.forEach((id) => seenEvents.current.add(id));
        if (event.key === READ_STORAGE_KEY) setReadEvents(new Set(stored));
        if (event.key === INBOX_STORAGE_KEY) {
          const today = taipeiDate(new Date().toISOString());
          setInbox((stored as unknown as RobotToast[]).filter((item) => item && taipeiDate(item.timestamp) === today).slice(0, 500));
        }
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
  }, [pathname, show]);

  useEffect(() => {
    if (!inboxHydrated.current) return;
    try { localStorage.setItem(INBOX_STORAGE_KEY, JSON.stringify(inbox.slice(0, 500))); } catch { /* storage is optional */ }
  }, [inbox]);

  useEffect(() => {
    const receive = (event: Event) => {
      const item = (event as CustomEvent<InAppNotification>).detail;
      if (!item?.id || !item.title || !item.timestamp) return;
      show([{
        id: item.id,
        kind: item.severity === "critical" ? "stop" : "activation",
        target: "adaptive-electronic",
        title: item.title,
        stock: item.stock ?? "盤中訊息",
        message: item.message,
        reason: item.reason ?? "",
        timestamp: item.timestamp,
        href: item.href,
      }]);
    };
    window.addEventListener(IN_APP_NOTIFICATION_EVENT, receive);
    return () => window.removeEventListener(IN_APP_NOTIFICATION_EVENT, receive);
  }, [show]);

  useEffect(() => {
    if (pathname === "/login") return;
    let stopped = false;
    let loading = false;
    const load = async () => {
      if (loading || document.visibilityState === "hidden") return;
      loading = true;
      try {
        const userId = getBrowserUserId();
        const payload = await dayTradingV2Client.notifications(userId);
        if (stopped) return;
        setLoginExpired(false);
        if (document.hidden) return;
        show(selectDayTradingV2Notifications(payload, userId).map((item) => ({
          id: item.key, kind: item.kind, target: "day-trading-v2",
          title: item.title, stock: "當沖機器人2｜模擬交易", message: item.message,
          reason: "", timestamp: item.createdAt,
        })));
      } catch (error) {
        if (!stopped && error instanceof DayTradingV2RequestError && error.status === 401) setLoginExpired(true);
      } finally {
        loading = false;
      }
    };
    const resume = () => { if (document.visibilityState === "visible") void load(); };
    void load();
    const timer = window.setInterval(() => void load(), 10_000);
    document.addEventListener("visibilitychange", resume);
    return () => {
      stopped = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", resume);
    };
  }, [pathname, show]);

  if (pathname === "/login") return null;
  const unread = inbox.filter((item) => !readEvents.has(item.id)).length;
  return <>
    <button className="notification-bell" type="button" aria-label={`通知中心，${unread} 則未讀`} aria-expanded={drawerOpen} onClick={() => setDrawerOpen((open) => !open)}>
      <Bell />{unread > 0 && <b>{unread > 99 ? "99+" : unread}</b>}
    </button>
    {drawerOpen && <aside className="notification-drawer" aria-label="盤中通知中心">
      <header><div><strong>盤中通知中心</strong><small>今日訊息都會保留在這裡</small></div><button type="button" aria-label="關閉通知中心" onClick={() => setDrawerOpen(false)}><X /></button></header>
      <div className="notification-drawer-actions"><span>{unread} 則未讀</span><button type="button" onClick={() => persistRead(new Set(inbox.map((item) => item.id)))} disabled={!unread}><CheckCheck />全部標為已讀</button></div>
      <div className="notification-inbox">
        {inbox.map((item) => <button type="button" className={`${item.kind === "stop" ? "critical" : ""} ${readEvents.has(item.id) ? "read" : "unread"}`} key={item.id} onClick={() => openItem(item)}>
          <span><ToastIcon kind={item.kind} /></span><div><strong>{item.title}</strong><h4>{item.stock}</h4><p>{item.message}</p><footer>{item.reason}<time>{time(item.timestamp)}</time></footer></div>
        </button>)}
        {!inbox.length && <p className="notification-inbox-empty">目前沒有盤中通知</p>}
      </div>
    </aside>}
    {(toasts.length > 0 || loginExpired) && <div className="day-bot-toast-stack" aria-live="polite">
    {loginExpired && <article className="day-bot-toast stop" role="alert">
      <button className="day-bot-toast-body" type="button" onClick={() => {
        window.location.assign(`/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`);
      }}>
        <span><ShieldAlert /></span><div><strong>登入已逾時，畫面通知已暫停</strong><p>請重新登入以恢復當沖機器人2通知。</p><h4>重新登入</h4></div>
      </button>
    </article>}
    {toasts.map((item) => <article className={`day-bot-toast ${item.kind} ${item.target}`} key={item.id} role={item.kind === "stop" ? "alert" : "status"}>
      <button className="day-bot-toast-body" type="button" onClick={() => openItem(item)}>
        <span><ToastIcon kind={item.kind} /></span>
        <div>
          <strong>{item.title}</strong>
          <h4>{item.stock}</h4>
          <p>{item.message}</p>
          <footer>{item.reason}<time>{time(item.timestamp)}</time></footer>
        </div>
      </button>
    </article>)}
    </div>}
  </>;
}
