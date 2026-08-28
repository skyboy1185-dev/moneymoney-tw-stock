"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  BarChart3,
  Bell,
  Bot,
  CheckCircle2,
  CircleDollarSign,
  Mail,
  RefreshCw,
  ShieldAlert,
  TrendingDown,
  TrendingUp,
  X,
} from "lucide-react";

type Settings = {
  systemName: string;
  enabled: boolean;
  tradingMode: "PAPER" | "LIVE" | string;
  maxCapital: number;
  availableCapital: number;
  riskPerTradePct: number;
  dailyMaxLossPct: number;
  weeklyDrawdownPct: number;
  minAiScoreToTrade: number;
  minAiScoreToWatch: number;
  minRiskReward: number;
  maxPositions: number;
  maxPositionPct: number;
  maxStopDistancePct: number;
  commissionDiscount: number;
  commissionDiscountLabel: string;
  emailEnabled: boolean;
  emailBuyEnabled: boolean;
  emailSellEnabled: boolean;
  emailAddEnabled: boolean;
  emailStopLossEnabled: boolean;
  emailTakeProfitEnabled: boolean;
  emailRiskEnabled: boolean;
  emailDailySummaryEnabled: boolean;
  emailErrorEnabled: boolean;
  stopNewTrades: boolean;
  stopReason: string | null;
  consecutiveStopLosses: number;
  strategyMode?: string;
  strategyModeLabel?: string;
  precisionPolicy?: {
    longOnly: boolean;
    allowedRegimes: string[];
    allowedStrategies: string[];
    minAiScore: number;
    minTotalScore: number;
    minHealthScore: number;
    minIndustryStrength: number;
    minRiskReward: number;
    maxStopDistancePct: number;
    maxNewTradesPerDay: number;
    maxDailyLossPct: number;
    riskPerTradePct: number;
    requiresRealtimeBreakoutProxy: boolean;
  };
  settingsVersion: number;
  updatedAt: string;
};

type MarketState = {
  regime: string;
  label: string;
  longWeight: number;
  shortWeight: number;
};

type RiskState = {
  todayPnl: number;
  dailyMaxLoss: number;
  openTrades: number;
  openedTradesToday?: number;
  maxNewTradesPerDay?: number;
  stopNewTrades: boolean;
  stopReason: string | null;
  consecutiveStopLosses: number;
};

type Status = {
  systemName: string;
  status?: string;
  running?: boolean;
  lastRunAt?: string | null;
  lastSuccessAt?: string | null;
  lastError?: string | null;
  settings: Settings;
  marketState: MarketState;
  risk: RiskState;
};

type Candidate = {
  rank: number;
  stockCode: string;
  stockName: string;
  subIndustry: string;
  strategyType: string;
  strategyName: string;
  totalScore: number;
  healthScore: number;
  currentPrice: number;
  entryPriceLow: number;
  entryPriceHigh: number;
  breakoutPrice: number;
  stopLossPrice: number;
  stopDistancePct?: number;
  maxStopDistancePct?: number;
  stopDistanceCapped?: boolean;
  tradeSide?: "LONG" | "SHORT" | string;
  strategyMode?: string;
  targetPrice1: number;
  targetPrice2: number;
  relativeStrength: number;
  industryStrength: number;
  falseBreakoutRisk: number;
  status: string;
  statusLabel: string;
  selectedReasons: string[];
  riskReasons: string[];
  missingData: string[];
  quoteSource: string;
  quoteTimestamp: string;
};

type Industry = {
  subIndustry: string;
  strengthScore: number;
  strengthRank: number;
  return1d: number | null;
  advanceRatio: number | null;
  volumeGrowth: number | null;
};

type Trade = {
  id: number;
  stockCode: string;
  stockName: string;
  strategyType: string;
  side: "LONG" | "SHORT" | string;
  tradeMode: string;
  quantityShares: number;
  quantityLots: number;
  entryPrice: number;
  entryTime: string;
  entryReasons: string[];
  stopLossPrice: number;
  targetPrice1: number;
  targetPrice2: number;
  lastPrice: number;
  aiScore: number;
  marketRegime: string;
  sectorStatus: string | null;
  riskAmount: number;
  initialR: number;
  realizedR: number;
  status: string;
  exitPrice: number | null;
  exitTime: string | null;
  exitReason: string | null;
  exitReasons: string[];
  grossProfit: number;
  tradingCost: number;
  netProfit: number;
  returnPercentage: number;
  unrealizedProfit: number;
  updatedAt: string;
};

type Performance = {
  systemName: string;
  settings: Pick<Settings, "maxCapital" | "availableCapital" | "riskPerTradePct" | "dailyMaxLossPct" | "tradingMode" | "commissionDiscount" | "commissionDiscountLabel"> & {
    commissionRate?: number;
    taxRate?: number;
    costFormula?: string;
  };
  summary: {
    totalTrades: number;
    closedTrades: number;
    openTrades: number;
    wins: number;
    losses: number;
    breakeven: number;
    winRate: number;
    longWinRate: number;
    shortWinRate: number;
    grossProfit: number;
    tradingCost: number;
    grossCommission: number;
    actualCommission: number;
    commissionRebate: number;
    rebateAccumulated: number;
    netProfit: number;
    unrealizedProfit: number;
    averageProfit: number;
    profitFactor: number;
    averageR: number;
  };
  openPositions: Trade[];
  closedTrades: Trade[];
  strategyAnalytics: Array<{
    strategy: string;
    trades: number;
    winRate: number;
    averageR: number | string;
    profitFactor: number | string;
    recent30ProfitFactor: number | string;
    weightStatus: "ACTIVE" | "REDUCED" | "PAUSED" | string;
  }>;
  timeBucketAnalytics: Array<{
    bucket: string;
    trades: number;
    winRate: number;
    averageR: number | string;
    profitFactor: number | string;
  }>;
};

type NotificationItem = {
  id: number;
  source: string;
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
  stopLoss: number | null;
  takeProfit1: number | null;
  takeProfit2: number | null;
  aiScore: number | null;
  riskReward: number | null;
  emailSent: boolean;
  read: boolean;
  timestamp: string;
};

const SYSTEM_NAME = "超強AI當沖系統";
const USER_WARNING =
  "此為超強AI當沖系統的模擬交易與技術決策結果，不代表實際券商成交，也不保證獲利。AI訊號必須通過市場、流動性、資金、風險、R/R與重複訊號檢查後才允許交易；風控規則永遠高於AI判斷。";

function money(value: number | null | undefined) {
  const safe = Number(value ?? 0);
  return `${safe < 0 ? "-" : ""}NT$${Math.abs(safe).toLocaleString("zh-TW", { maximumFractionDigits: 0 })}`;
}

function price(value: number | null | undefined) {
  return Number(value ?? 0).toLocaleString("zh-TW", { maximumFractionDigits: 2 });
}

function pct(value: number | null | undefined) {
  const safe = Number(value ?? 0);
  return `${safe > 0 ? "+" : ""}${safe.toFixed(2)}%`;
}

function num(value: number | string | null | undefined) {
  const safe = Number(value ?? 0);
  return Number.isFinite(safe) ? safe : 0;
}

function dt(value: string | null | undefined) {
  if (!value) return "尚無";
  return new Date(value).toLocaleString("zh-TW", { hour12: false });
}

function pnlClass(value: number | null | undefined) {
  const safe = Number(value ?? 0);
  return safe > 0 ? "pattern-profit" : safe < 0 ? "pattern-loss" : "";
}

function quoteAgeSeconds(value: string | null | undefined) {
  if (!value) return Number.POSITIVE_INFINITY;
  const timestamp = new Date(value).getTime();
  if (!Number.isFinite(timestamp)) return Number.POSITIVE_INFINITY;
  return Math.max(0, Math.round((Date.now() - timestamp) / 1000));
}

function sideLabel(side: string | null | undefined) {
  return side === "SHORT" ? "🔴 放空" : side === "LONG" ? "🟢 做多" : "—";
}

function categoryIcon(category: string) {
  if (category === "BUY") return "🟢";
  if (category === "SHORT") return "🔴";
  if (category === "ADD") return "🔵";
  if (category === "REDUCE") return "🟠";
  if (category === "TAKE_PROFIT") return "✅";
  if (category === "STOP_LOSS") return "⛔";
  if (category === "RISK") return "⚠️";
  if (category === "ERROR") return "🚨";
  return "🟡";
}

function strategyLabel(strategy: string) {
  const map: Record<string, string> = {
    BREAKOUT: "開盤強勢突破",
    RECOVERY: "VWAP強勢回踩",
    RANGE: "平台壓縮突破",
    CRASH: "弱勢放空/跌破",
    OPEN_STRENGTH_BREAKOUT: "開盤強勢突破",
    VWAP_PULLBACK: "VWAP強勢回踩",
    RANGE_BREAKDOWN: "平台跌破",
    FAKE_BREAKOUT_REVERSAL: "假突破反轉",
  };
  return map[strategy] ?? strategy;
}

async function api<T>(path: string, userId: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/adaptive-electronic${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      "x-user-id": userId,
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  });
  const body = (await response.json().catch(() => ({}))) as { error?: string; detail?: string };
  if (!response.ok) throw new Error(body.detail ?? body.error ?? `HTTP ${response.status}`);
  return body as T;
}

function StatCard({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <article>
      <span>{label}</span>
      <b className={tone}>{value}</b>
    </article>
  );
}

function DecisionReasonList({ reasons }: { reasons: string[] }) {
  if (!reasons.length) return <small>尚無AI決策說明</small>;
  return (
    <ol className="pattern-reasons">
      {reasons.slice(0, 7).map((reason) => (
        <li key={reason}>{reason}</li>
      ))}
    </ol>
  );
}

function superAiEntryPhase() {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Taipei",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(new Date());
  const value = (type: string) => parts.find((part) => part.type === type)?.value ?? "";
  const weekday = value("weekday");
  const hour = Number(value("hour"));
  const minute = Number(value("minute"));
  const minutes = hour * 60 + minute;
  if (weekday === "Sat" || weekday === "Sun" || !Number.isFinite(minutes)) {
    return {
      label: "非交易日",
      tone: "off",
      message: "今天不開新當沖倉；系統只保留既有資料與觀察名單。",
    };
  }
  if (minutes < 9 * 60 + 15) {
    return {
      label: "開盤前",
      tone: "off",
      message: "09:15 前不開新倉；先避開開盤亂流，只觀察不進場。",
    };
  }
  if (minutes < 12 * 60) {
    return {
      label: "可進場時段",
      tone: "on",
      message: "09:15～12:00 允許符合少量精準突破風控的新進場。",
    };
  }
  if (minutes <= 13 * 60 + 30) {
    return {
      label: "停止新進場",
      tone: "off",
      message: "12:00 後不新增當沖倉，只監控既有持倉與隔日觀察名單。",
    };
  }
  return {
    label: "收盤後",
    tone: "off",
    message: "今日不再開新倉；強勢標的只保留為隔日觀察。",
  };
}

export function AdaptiveElectronicPage({
  userId,
  onSelectStock,
}: {
  userId: string;
  onSelectStock: (symbol: string) => void;
}) {
  const [status, setStatus] = useState<Status | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [industries, setIndustries] = useState<Industry[]>([]);
  const [performance, setPerformance] = useState<Performance | null>(null);
  const [notifications, setNotifications] = useState<NotificationItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [minimumScore, setMinimumScore] = useState(70);
  const [sideFilter, setSideFilter] = useState("");
  const [query, setQuery] = useState("");
  const [notificationSource, setNotificationSource] = useState("SUPER_AI_DAYTRADE");

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const currentStatus = await api<Status>("/status", userId);
      const [candidatePayload, industryPayload, perfPayload, notificationPayload] = await Promise.all([
        api<{ items: Candidate[] }>(`/candidates?minimumScore=${minimumScore}`, userId),
        api<{ items: Industry[] }>("/industries", userId),
        api<Performance>("/performance", userId),
        api<{ items: NotificationItem[] }>(`/notifications?source=${notificationSource}&limit=120`, userId),
      ]);
      setStatus(currentStatus);
      setSettings(currentStatus.settings);
      setCandidates(candidatePayload.items ?? []);
      setIndustries(industryPayload.items ?? []);
      setPerformance(perfPayload);
      setNotifications(notificationPayload.items ?? []);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "載入失敗");
    } finally {
      setLoading(false);
    }
  }, [minimumScore, notificationSource, userId]);

  useEffect(() => {
    void load();
    const refreshMs = (performance?.openPositions?.length ?? 0) > 0 ? 5_000 : 15_000;
    const timer = window.setInterval(() => void load(true), refreshMs);
    return () => window.clearInterval(timer);
  }, [load, performance?.openPositions?.length]);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(""), 4500);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const saveSettings = async (patch?: Partial<Settings>) => {
    const next = { ...(settings ?? status?.settings), ...(patch ?? {}) };
    if (!next.systemName) return;
    setWorking(true);
    try {
      const saved = await api<Settings>("/settings", userId, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(next),
      });
      setSettings(saved);
      setStatus((old) => old ? { ...old, settings: saved } : old);
      setToast("設定已保存到後端資料庫");
      await load(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "設定保存失敗");
    } finally {
      setWorking(false);
    }
  };

  const sendTestEmail = async () => {
    setWorking(true);
    try {
      const result = await api<{ sent: boolean }>("/email/test", userId, { method: "POST" });
      setToast(result.sent ? "測試 Email 已送出" : "Email 未送出，請檢查 Gmail 設定");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "測試 Email 失敗");
    } finally {
      setWorking(false);
    }
  };

  const markAllRead = async () => {
    setWorking(true);
    try {
      await api("/notifications/mark-all-read", userId, { method: "POST" });
      await load(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "通知更新失敗");
    } finally {
      setWorking(false);
    }
  };

  const marketRegime = status?.marketState.regime;
  const visibleCandidates = useMemo(() => candidates.filter((item) => {
    const textMatch = !query || `${item.stockCode}${item.stockName}${item.subIndustry}${item.strategyName}`.includes(query);
    const side = item.tradeSide ?? (marketRegime === "CRASH" || item.strategyType === "CRASH" || item.relativeStrength < -3 ? "SHORT" : "LONG");
    const sideMatch = !sideFilter || sideFilter === side;
    return textMatch && sideMatch;
  }), [candidates, query, sideFilter, marketRegime]);

  const usedCapital = (settings?.maxCapital ?? 0) - (settings?.availableCapital ?? 0);
  const summary = performance?.summary;
  const market = status?.marketState;
  const risk = status?.risk;
  const openPositions = performance?.openPositions ?? [];
  const closedTrades = performance?.closedTrades ?? [];
  const entryPhase = useMemo(() => superAiEntryPhase(), []);
  const precisionPolicy = settings?.precisionPolicy;
  const stopNewTrades = risk?.stopNewTrades === true || settings?.stopNewTrades === true;
  const strategyModeLabel = settings?.strategyModeLabel ?? "少量精準突破模式";

  if (loading && !status) {
    return (
      <div className="pattern-loading">
        <span className="spinner" />
        <p>載入超強AI當沖系統...</p>
      </div>
    );
  }

  return (
    <div className="pattern-page">
      <header className="pattern-hero">
        <div>
          <small>SUPER AI DAYTRADE SYSTEM</small>
          <h1><Bot />{SYSTEM_NAME}</h1>
          <p>多空雙向、即時監控、風控優先、動態資金控管、模擬/實盤模式、Email與網頁通知分流。</p>
        </div>
        <div className="pattern-hero-actions">
          <span className={`pattern-system-state ${settings?.enabled ? "on" : "off"}`}>
            {settings?.enabled ? "系統啟用" : "系統停止"}
          </span>
          <button disabled={working || !settings} onClick={() => void saveSettings({ enabled: !settings?.enabled })}>
            {settings?.enabled ? "停止" : "啟動"}
          </button>
          <button
            disabled={working || !settings}
            onClick={() => void saveSettings({
              stopNewTrades: !settings?.stopNewTrades,
              stopReason: settings?.stopNewTrades ? null : "precision_observation_mode",
            })}
          >
            {settings?.stopNewTrades ? "恢復新單" : "停新單"}
          </button>
          <button disabled={working} onClick={() => void load()}>
            <RefreshCw className={working ? "spin-icon" : ""} />重新整理
          </button>
        </div>
      </header>

      <p className="pattern-risk-notice"><ShieldAlert />{USER_WARNING}</p>
      <p className="pattern-risk-notice">
        <ShieldAlert />
        {strategyModeLabel}：只做強勢盤高分突破；AI ≥ {precisionPolicy?.minAiScore ?? 88}、健康度 ≥ {precisionPolicy?.minHealthScore ?? 75}、族群強度 ≥ {precisionPolicy?.minIndustryStrength ?? 65}、R/R ≥ {precisionPolicy?.minRiskReward ?? 2.5}、停損 ≤ {precisionPolicy?.maxStopDistancePct ?? 2}%。
        {stopNewTrades ? "目前停止新增 paper trade，掃描與排行照常更新。" : "目前允許符合條件的新 paper trade。"}
      </p>
      <p className="pattern-risk-notice">
        <Activity />
        <span className={`pattern-system-state ${entryPhase.tone}`}>{entryPhase.label}</span>
        {entryPhase.message}
      </p>
      <p className="pattern-risk-notice">
        <Bot />策略隔離：本頁是「超強AI當沖系統」，使用 SUPER_AI_DAYTRADE 來源、AI 評分、多空權重、R/R 與獨立模擬績效；可共用行情資料，但不沿用「當沖機器人」的 VWAP/5 分 K 原版當沖策略與績效。
      </p>
      {error && <div className="error-banner">{error}<button onClick={() => setError("")}><X /></button></div>}
      {toast && <div className="pattern-toast">{toast}</div>}

      <section className="pattern-stats">
        <StatCard label="市場狀態" value={`${market?.label ?? "盤整"} (${market?.regime ?? "UNCERTAIN"})`} />
        <StatCard label="多方權重" value={`${market?.longWeight ?? 50}%`} />
        <StatCard label="空方權重" value={`${market?.shortWeight ?? 50}%`} />
        <StatCard label="今日已使用資金" value={money(usedCapital)} />
        <StatCard label="剩餘資金" value={money(settings?.availableCapital)} />
        <StatCard label="今日損益" value={money(risk?.todayPnl)} tone={pnlClass(risk?.todayPnl)} />
        <StatCard label="今日最大允許虧損" value={money(risk?.dailyMaxLoss)} />
        <StatCard label="目前風險" value={risk?.stopNewTrades ? `停止新交易：${risk.stopReason ?? "風控觸發"}` : "可接受新訊號"} tone={risk?.stopNewTrades ? "pattern-loss" : "pattern-profit"} />
      </section>

      <section className="pattern-stats">
        <StatCard label="策略模式" value={strategyModeLabel} />
        <StatCard label="今日新單" value={`${risk?.openedTradesToday ?? 0}/${risk?.maxNewTradesPerDay ?? precisionPolicy?.maxNewTradesPerDay ?? 2}`} />
        <StatCard label="精準門檻" value={`AI ${precisionPolicy?.minAiScore ?? 88} / 健康 ${precisionPolicy?.minHealthScore ?? 75}`} />
        <StatCard label="停損上限" value={`${precisionPolicy?.maxStopDistancePct ?? 2}%`} />
      </section>

      <section className="pattern-panel">
        <div className="pattern-title">
          <CircleDollarSign />
          <div>
            <h2>資金與風控設定</h2>
            <p>資金上限不得超過500萬元；下單張數由可用資金、停損距離、單筆最大風險與R/R自動計算。</p>
          </div>
          <button disabled={working || !settings} onClick={() => void saveSettings()}>保存設定</button>
        </div>
        {settings && (
          <div className="pattern-settings-grid">
            <label>交易模式
              <select value={settings.tradingMode} onChange={(e) => setSettings({ ...settings, tradingMode: e.target.value })}>
                <option value="PAPER">模擬盤</option>
                <option value="LIVE">實盤</option>
              </select>
            </label>
            <label>最大操作資金
              <input type="number" min="100000" max="5000000" step="100000" value={settings.maxCapital} onChange={(e) => setSettings({ ...settings, maxCapital: Number(e.target.value) })} />
            </label>
            <label>目前可用資金
              <input type="number" min="0" max="5000000" step="10000" value={settings.availableCapital} onChange={(e) => setSettings({ ...settings, availableCapital: Number(e.target.value) })} />
            </label>
            <label>單筆最大風險 %
              <input type="number" min="0.1" max="1" step="0.05" value={settings.riskPerTradePct} onChange={(e) => setSettings({ ...settings, riskPerTradePct: Number(e.target.value) })} />
            </label>
            <label>單日最大虧損 %
              <input type="number" min="0.3" max="3" step="0.1" value={settings.dailyMaxLossPct} onChange={(e) => setSettings({ ...settings, dailyMaxLossPct: Number(e.target.value) })} />
            </label>
            <label>單週最大回撤 %
              <input type="number" min="1" max="5" step="0.1" value={settings.weeklyDrawdownPct} onChange={(e) => setSettings({ ...settings, weeklyDrawdownPct: Number(e.target.value) })} />
            </label>
            <label>交易最低AI分數
              <input type="number" min="70" max="100" value={settings.minAiScoreToTrade} onChange={(e) => setSettings({ ...settings, minAiScoreToTrade: Number(e.target.value) })} />
            </label>
            <label>觀察最低AI分數
              <input type="number" min="0" max="100" value={settings.minAiScoreToWatch} onChange={(e) => setSettings({ ...settings, minAiScoreToWatch: Number(e.target.value) })} />
            </label>
            <label>最低R/R
              <input type="number" min="1" max="5" step="0.1" value={settings.minRiskReward} onChange={(e) => setSettings({ ...settings, minRiskReward: Number(e.target.value) })} />
            </label>
            <label>最大停損距離 %
              <input type="number" min="0.3" max="3" step="0.1" value={settings.maxStopDistancePct ?? 1} onChange={(e) => setSettings({ ...settings, maxStopDistancePct: Number(e.target.value) })} />
            </label>
            <label>最多持股
              <input type="number" min="1" max="10" value={settings.maxPositions} onChange={(e) => setSettings({ ...settings, maxPositions: Number(e.target.value) })} />
            </label>
            <label>單檔資金上限 %
              <input type="number" min="5" max="50" value={settings.maxPositionPct} onChange={(e) => setSettings({ ...settings, maxPositionPct: Number(e.target.value) })} />
            </label>
            <label>退水折扣
              <input type="number" min="0" max="1" step="0.05" value={settings.commissionDiscount} onChange={(e) => setSettings({ ...settings, commissionDiscount: Number(e.target.value) })} />
            </label>
            <label className="check"><input type="checkbox" checked={settings.emailEnabled} onChange={(e) => setSettings({ ...settings, emailEnabled: e.target.checked })} />啟用Email通知</label>
            <label className="check"><input type="checkbox" checked={settings.emailBuyEnabled} onChange={(e) => setSettings({ ...settings, emailBuyEnabled: e.target.checked })} />買進/放空通知</label>
            <label className="check"><input type="checkbox" checked={settings.emailSellEnabled} onChange={(e) => setSettings({ ...settings, emailSellEnabled: e.target.checked })} />出場通知</label>
            <label className="check"><input type="checkbox" checked={settings.emailStopLossEnabled} onChange={(e) => setSettings({ ...settings, emailStopLossEnabled: e.target.checked })} />停損通知</label>
            <label className="check"><input type="checkbox" checked={settings.emailTakeProfitEnabled} onChange={(e) => setSettings({ ...settings, emailTakeProfitEnabled: e.target.checked })} />停利通知</label>
            <label className="check"><input type="checkbox" checked={settings.emailRiskEnabled} onChange={(e) => setSettings({ ...settings, emailRiskEnabled: e.target.checked })} />風控通知</label>
            <label className="check"><input type="checkbox" checked={settings.emailDailySummaryEnabled} onChange={(e) => setSettings({ ...settings, emailDailySummaryEnabled: e.target.checked })} />每日結算通知</label>
            <label className="check"><input type="checkbox" checked={settings.emailErrorEnabled} onChange={(e) => setSettings({ ...settings, emailErrorEnabled: e.target.checked })} />異常通知</label>
          </div>
        )}
        <div className="pattern-run-strip">
          <span>交易模式 <b className={settings?.tradingMode === "LIVE" ? "pattern-loss" : ""}>{settings?.tradingMode === "LIVE" ? "實盤" : "模擬盤"}</b></span>
          <span>單筆風險金額 <b>{money((settings?.maxCapital ?? 0) * (settings?.riskPerTradePct ?? 0) / 100)}</b></span>
          <span>新單停損上限 <b>{(settings?.maxStopDistancePct ?? 1).toFixed(2)}%</b></span>
          <span>退水計算 <b>{settings?.commissionDiscountLabel ?? `${((settings?.commissionDiscount ?? 0.2) * 10).toFixed(1)}折`}</b></span>
          <span>連續停損 <b>{risk?.consecutiveStopLosses ?? 0}/3</b></span>
          <span>設定版本 <b>v{settings?.settingsVersion ?? 0}</b></span>
          <span>最後成功執行 <b>{dt(status?.lastSuccessAt)}</b></span>
          <button disabled={working} onClick={() => void sendTestEmail()}><Mail />發送測試Email</button>
        </div>
        <div className="super-ai-email-policy">
          <article>
            <h3><Mail />會寄 Email 的正式訊號</h3>
            <p>BUY 做多買進、SHORT 放空、ADD 加碼、REDUCE 減碼、STOP_LOSS 停損、TAKE_PROFIT 停利、EXIT 出場、RISK 風控警告、ERROR 系統異常。</p>
          </article>
          <article>
            <h3><Bell />只做網頁通知，不寄 Email</h3>
            <p>WATCH 觀察、候選股掃描、接近條件但尚未通過風控、尚未形成正式交易動作的提醒。</p>
          </article>
          <article>
            <h3><ShieldAlert />判斷規則</h3>
            <p>Email 代表正式交易/風控/異常訊號；所有訊號仍先經過市場、流動性、資金、風險、R/R 與重複訊號檢查，且目前預設為模擬盤。</p>
          </article>
        </div>
        {settings?.tradingMode === "LIVE" && (
          <p className="pattern-risk-notice"><AlertTriangle />目前為實盤交易模式。此模式保存在後端，不會因重新整理自動切回模擬盤。</p>
        )}
      </section>

      {summary && (
        <section className="pattern-stats">
          <StatCard label="累積損益" value={money(summary.netProfit)} tone={pnlClass(summary.netProfit)} />
          <StatCard label="未實現損益" value={money(summary.unrealizedProfit)} tone={pnlClass(summary.unrealizedProfit)} />
          <StatCard label="交易次數" value={`${summary.closedTrades} 已結 / ${summary.openTrades} 持倉`} />
          <StatCard label="勝率" value={pct(summary.winRate)} />
          <StatCard label="多單勝率" value={pct(summary.longWinRate)} />
          <StatCard label="空單勝率" value={pct(summary.shortWinRate)} />
          <StatCard label="Profit Factor" value={summary.profitFactor >= 900 ? "∞" : summary.profitFactor.toFixed(2)} />
          <StatCard label="平均R" value={summary.averageR.toFixed(2)} tone={pnlClass(summary.averageR)} />
          <StatCard label="退水折扣" value={performance.settings.commissionDiscountLabel ?? `${(performance.settings.commissionDiscount * 10).toFixed(1)}折`} />
          <StatCard label="退水累積" value={money(summary.commissionRebate)} />
          <StatCard label="交易成本" value="手續費+證交稅" />
        </section>
      )}

      {performance?.settings.costFormula && (
        <p className="pattern-risk-notice">
          <CircleDollarSign />
          交易成本公式：{performance.settings.costFormula}；目前退水折扣 {performance.settings.commissionDiscountLabel ?? `${(performance.settings.commissionDiscount * 10).toFixed(1)}折`}；退水累積 {money(summary?.commissionRebate)}。
        </p>
      )}

      <section className="pattern-panel">
        <div className="pattern-title">
          <Activity />
          <div>
            <h2>即時候選與AI交易評分</h2>
            <p>70以下禁止交易，70-79觀察，80-89允許交易，90以上A+。進場前仍需通過風控與R/R。</p>
          </div>
          <div className="pattern-filters">
            <input placeholder="股票/族群/策略" value={query} onChange={(e) => setQuery(e.target.value)} />
            <select value={sideFilter} onChange={(e) => setSideFilter(e.target.value)}>
              <option value="">多空全部</option>
              <option value="LONG">做多</option>
              <option value="SHORT">放空</option>
            </select>
            <select value={minimumScore} onChange={(e) => setMinimumScore(Number(e.target.value))}>
              <option value={0}>全部</option>
              <option value={70}>70分以上</option>
              <option value={80}>80分以上</option>
              <option value={90}>90分以上</option>
            </select>
          </div>
        </div>
        <div className="pattern-table-wrap">
          <table>
            <thead>
              <tr>
                <th>股票</th>
                <th>方向/策略</th>
                <th>AI評分</th>
                <th>價格/進場區</th>
                <th>停損/目標</th>
                <th>族群/量價</th>
                <th>原因</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {visibleCandidates.map((item) => {
                const inferredSide = item.tradeSide ?? (market?.regime === "CRASH" || item.strategyType === "CRASH" || item.relativeStrength < -3 ? "SHORT" : "LONG");
                const aiScore = Math.min(100, item.totalScore * 0.6 + item.healthScore * 0.4 + (market?.regime === "BREAKOUT" || market?.regime === "RECOVERY" ? 10 : 4));
                const risk = Math.max(0.01, Math.abs(item.currentPrice - item.stopLossPrice));
                const reward = Math.max(0, Math.abs(item.targetPrice2 - item.currentPrice));
                const rr = reward / risk;
                return (
                  <tr key={`${item.stockCode}-${item.strategyType}`}>
                    <td><b>{item.stockCode}</b><small>{item.stockName}</small></td>
                    <td><b>{sideLabel(inferredSide)}</b><small>{strategyLabel(item.strategyType)}</small></td>
                    <td><strong>{aiScore.toFixed(0)}</strong><small>{item.statusLabel}</small></td>
                    <td>{price(item.currentPrice)}<small>{price(item.entryPriceLow)} - {price(item.entryPriceHigh)}</small></td>
                    <td>
                      <span className="pattern-loss">{price(item.stopLossPrice)}（{(item.stopDistancePct ?? (risk / item.currentPrice * 100)).toFixed(2)}%）</span>
                      <small className="pattern-profit">TP1 {price(item.targetPrice1)} / TP2 {price(item.targetPrice2)}</small>
                      {item.stopDistanceCapped && <small className="pattern-loss">停損已套用上限 {(item.maxStopDistancePct ?? settings?.maxStopDistancePct ?? 1).toFixed(2)}%</small>}
                    </td>
                    <td>{item.subIndustry}<small>族群 {item.industryStrength.toFixed(0)}｜RS {item.relativeStrength.toFixed(1)}｜R/R {rr.toFixed(2)}</small></td>
                    <td><DecisionReasonList reasons={[...item.selectedReasons, ...item.riskReasons]} /></td>
                    <td><button onClick={() => onSelectStock(item.stockCode)}>查看個股</button></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {!visibleCandidates.length && <div className="pattern-empty">目前沒有符合篩選條件的候選訊號。</div>}
        </div>
      </section>

      <div className="pattern-two-columns wide">
        <section className="pattern-panel">
          <div className="pattern-title"><TrendingUp /><div><h2>目前持倉</h2><p>虧損單禁止攤平；只有獲利部位才允許金字塔加碼。</p></div></div>
          <div className="pattern-table-wrap">
            <table>
              <thead><tr><th>股票</th><th>方向/策略</th><th>成本/現價</th><th>股數</th><th>損益</th><th>R倍數</th><th>停損/停利</th><th>AI建議</th></tr></thead>
              <tbody>
                {openPositions.map((item) => {
                  const age = quoteAgeSeconds(item.updatedAt);
                  const quoteDelayed = age > 20;
                  const referencePrice = Math.max(0.01, item.lastPrice > 0 ? item.lastPrice : item.entryPrice);
                  const stopDistancePct = item.side === "SHORT"
                    ? (item.stopLossPrice - referencePrice) / referencePrice * 100
                    : (referencePrice - item.stopLossPrice) / referencePrice * 100;
                  const targetDistancePct = item.side === "SHORT"
                    ? (referencePrice - item.targetPrice2) / referencePrice * 100
                    : (item.targetPrice2 - referencePrice) / referencePrice * 100;
                  const actionText = quoteDelayed
                    ? "行情延遲，等待即時報價"
                    : item.unrealizedProfit > item.riskAmount
                      ? "獲利中，監控移動停利"
                      : item.unrealizedProfit < 0
                        ? "接近風控，停損即時監控"
                        : "持倉監控中";
                  return (
                  <tr key={item.id}>
                    <td><b>{item.stockCode}</b><small>{item.stockName}</small></td>
                    <td>{sideLabel(item.side)}<small>{strategyLabel(item.strategyType)}</small></td>
                    <td>
                      {price(item.entryPrice)}
                      <small className={quoteDelayed ? "pattern-loss" : "pattern-profit"}>
                        Last {price(item.lastPrice)} · {quoteDelayed ? "DELAY" : "LIVE"} {Number.isFinite(age) ? `${age}s` : ""}
                      </small>
                    </td>
                    <td>{item.quantityShares.toLocaleString()}<small>{item.quantityLots.toFixed(2)} lots</small></td>
                    <td className={pnlClass(item.unrealizedProfit)}>{money(item.unrealizedProfit)}<small>{pct(item.returnPercentage)}</small></td>
                    <td className={pnlClass(item.realizedR)}>{item.realizedR.toFixed(2)}R</td>
                    <td>
                      <span className="pattern-loss">{price(item.stopLossPrice)}（距停損 {stopDistancePct.toFixed(2)}%）</span>
                      <small className="pattern-profit">{price(item.targetPrice1)} / {price(item.targetPrice2)}・目標距離 {targetDistancePct.toFixed(2)}%</small>
                    </td>
                    <td>{actionText}</td>
                  </tr>
                  );
                })}
              </tbody>
            </table>
            {!openPositions.length && <div className="pattern-empty">目前沒有超強AI當沖系統持倉。</div>}
          </div>
        </section>

        <section className="pattern-panel">
          <div className="pattern-title"><TrendingDown /><div><h2>最近交易紀錄</h2><p>超強AI當沖系統獨立績效，不與其他機器人混算。</p></div></div>
          <div className="pattern-table-wrap">
            <table>
              <thead><tr><th>股票</th><th>方向</th><th>進出場</th><th>損益</th><th>R</th><th>原因</th></tr></thead>
              <tbody>
                {closedTrades.slice(0, 30).map((item) => (
                  <tr key={item.id}>
                    <td><b>{item.stockCode}</b><small>{item.stockName}</small></td>
                    <td>{sideLabel(item.side)}<small>{strategyLabel(item.strategyType)}</small></td>
                    <td>{price(item.entryPrice)}<small>{item.exitPrice ? price(item.exitPrice) : "尚未出場"}</small></td>
                    <td className={pnlClass(item.netProfit)}>{money(item.netProfit)}<small>{pct(item.returnPercentage)}</small></td>
                    <td className={pnlClass(item.realizedR)}>{item.realizedR.toFixed(2)}R</td>
                    <td>{item.exitReason ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!closedTrades.length && <div className="pattern-empty">尚無已完成交易，因此勝率與Profit Factor仍為0。</div>}
          </div>
        </section>
      </div>

      <div className="pattern-two-columns">
        <section className="pattern-panel">
          <div className="pattern-title"><BarChart3 /><div><h2>策略績效自動分析</h2><p>最近30筆Profit Factor低於1會降權，低於0.8會暫停策略。</p></div></div>
          <div className="pattern-table-wrap">
            <table>
              <thead><tr><th>策略</th><th>交易</th><th>勝率</th><th>平均R</th><th>PF</th><th>近30 PF</th><th>權重</th></tr></thead>
              <tbody>
                {(performance?.strategyAnalytics ?? []).map((item) => (
                  <tr key={item.strategy}>
                    <td>{strategyLabel(item.strategy)}</td>
                    <td>{item.trades}</td>
                    <td>{pct(item.winRate)}</td>
                    <td className={pnlClass(num(item.averageR))}>{num(item.averageR).toFixed(2)}</td>
                    <td>{num(item.profitFactor) >= 900 ? "∞" : num(item.profitFactor).toFixed(2)}</td>
                    <td>{num(item.recent30ProfitFactor) >= 900 ? "∞" : num(item.recent30ProfitFactor).toFixed(2)}</td>
                    <td>{item.weightStatus}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="pattern-panel">
          <div className="pattern-title"><Activity /><div><h2>交易時間分析</h2><p>統計不同時段的勝率、Profit Factor與平均R。</p></div></div>
          <div className="pattern-table-wrap">
            <table>
              <thead><tr><th>時段</th><th>交易</th><th>勝率</th><th>平均R</th><th>PF</th></tr></thead>
              <tbody>
                {(performance?.timeBucketAnalytics ?? []).map((item) => (
                  <tr key={item.bucket}>
                    <td>{item.bucket}</td>
                    <td>{item.trades}</td>
                    <td>{pct(item.winRate)}</td>
                    <td className={pnlClass(num(item.averageR))}>{num(item.averageR).toFixed(2)}</td>
                    <td>{num(item.profitFactor) >= 900 ? "∞" : num(item.profitFactor).toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      <section className="pattern-panel">
        <div className="pattern-title">
          <Bell />
          <div>
            <h2>通知中心</h2>
            <p>所有通知保存source，不靠文字判斷來源。超強AI當沖系統、當沖機器人、型態選股機器人、飆股雷達可分流。</p>
          </div>
          <div className="pattern-filters">
            <select value={notificationSource} onChange={(e) => setNotificationSource(e.target.value)}>
              <option value="SUPER_AI_DAYTRADE">超強AI當沖系統</option>
              <option value="DAYTRADE_BOT">當沖機器人</option>
              <option value="PATTERN_BOT">型態選股機器人</option>
              <option value="MOMENTUM_RADAR">飆股雷達</option>
              <option value="SYSTEM">系統通知</option>
              <option value="MARKET">大盤通知</option>
            </select>
            <button disabled={working} onClick={() => void markAllRead()}><CheckCircle2 />全部已讀</button>
          </div>
        </div>
        <div className="pattern-message-list">
          {notifications.map((item) => (
            <article key={item.id} className={`${item.read ? "" : "unread"} ${item.level}`}>
              <time>{dt(item.timestamp)}</time>
              <b>{categoryIcon(item.category)} 【{item.source === "SUPER_AI_DAYTRADE" ? SYSTEM_NAME : item.source}｜{item.category}】 {item.symbol} {item.symbolName}</b>
              <p>{item.message}</p>
              <small>
                {item.strategy ? `策略：${strategyLabel(item.strategy)}｜` : ""}
                {item.side ? `方向：${sideLabel(item.side)}｜` : ""}
                {item.price ? `價格：${price(item.price)}｜` : ""}
                {item.quantity ? `股數：${item.quantity.toLocaleString()}｜` : ""}
                {item.aiScore ? `AI評分：${item.aiScore.toFixed(0)}｜` : ""}
                {item.riskReward ? `R/R：1:${item.riskReward.toFixed(2)}｜` : ""}
                Email：{item.emailSent ? "已送出" : "未送出"}
              </small>
            </article>
          ))}
          {!notifications.length && <div className="pattern-empty">目前沒有通知。</div>}
        </div>
      </section>

      <section className="pattern-panel">
        <div className="pattern-title"><BarChart3 /><div><h2>強弱族群</h2><p>用於多空權重與個股AI評分。</p></div></div>
        <div className="pattern-table-wrap">
          <table>
            <thead><tr><th>排名</th><th>族群</th><th>強度</th><th>今日漲跌</th><th>上漲家數</th><th>量能</th></tr></thead>
            <tbody>
              {industries.slice(0, 20).map((item) => (
                <tr key={item.subIndustry}>
                  <td>{item.strengthRank}</td>
                  <td>{item.subIndustry}</td>
                  <td>{item.strengthScore.toFixed(1)}</td>
                  <td className={pnlClass(item.return1d)}>{pct(item.return1d)}</td>
                  <td>{pct(item.advanceRatio)}</td>
                  <td>{pct(item.volumeGrowth)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
