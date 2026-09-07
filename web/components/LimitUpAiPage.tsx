"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Activity, AlertTriangle, Bell, BellRing, CheckCheck, CircleDollarSign, RefreshCw, Settings, ShieldCheck, Target, Volume2, VolumeX, X, Zap } from "lucide-react";
import { finiteNumber, normalizeLimitUpAiStatus, normalizeLimitUpDashboard, normalizeNotificationPayload } from "@/lib/limit-up-ai-normalize";
import type { LimitUpAiNotification, LimitUpAiPerformanceBucket, LimitUpAiSettings, LimitUpAiStatus, LimitUpDashboard, LimitUpPosition, LimitUpTrade } from "@/lib/limit-up-ai-types";
import { isLimitUpTradeNotification, selectOpenLimitUpPositions, selectTodayLimitUpTrades } from "@/lib/limit-up-ai-view";
import { LimitUpAiClientError, limitUpAiClient } from "@/services/limit-up-ai-client";

function money(value: number | null | undefined): string {
  return `NT$${finiteNumber(value).toLocaleString("zh-TW", { maximumFractionDigits: 0 })}`;
}

function signedMoney(value: number | null | undefined): string {
  const amount = finiteNumber(value);
  return `${amount > 0 ? "+" : amount < 0 ? "-" : ""}${money(Math.abs(amount))}`;
}

function percent(value: number | null | undefined): string {
  const amount = finiteNumber(value);
  return `${amount > 0 ? "+" : ""}${amount.toFixed(2)}%`;
}

function price(value: number | null | undefined): string {
  return finiteNumber(value).toLocaleString("zh-TW", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function time(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "時間待確認";
  return parsed.toLocaleTimeString("zh-TW", { hour12: false, timeZone: "Asia/Taipei" });
}

function pnlClass(value: number | null | undefined): string {
  const amount = finiteNumber(value);
  return amount > 0 ? "profit" : amount < 0 ? "loss" : "";
}

function actionLabel(action: string): string {
  const labels: Record<string, string> = {
    BUY: "買進",
    SELL: "出場",
    REDUCE: "減碼",
    TAKE_PROFIT: "停利",
    STOP_LOSS: "停損",
    OVERNIGHT: "隔日評估",
    WARNING: "警告",
  };
  return labels[action] ?? action;
}

function robotStatusLabel(status?: string): string {
  const labels: Record<string, string> = {
    running: "背景偵測中",
    error: "偵測異常",
    stopped: "已停止",
    unknown: "狀態確認中",
  };
  return labels[status ?? ""] ?? status ?? "狀態確認中";
}

function limitUpErrorMessage(reason: unknown, fallback: string): string {
  if (reason instanceof LimitUpAiClientError && reason.status === 401) {
    return "請先登入後再使用漲停機器人。";
  }
  return reason instanceof Error ? reason.message : fallback;
}

function isLoginRequiredMessage(message: string): boolean {
  return message.includes("請先登入") || message.includes("非公開模式");
}

function PositionTable({ items }: { items: LimitUpPosition[] }) {
  return <section className="rocket-panel">
    <div className="rocket-title"><Target size={17} /><div><h2>目前留倉</h2><p>只顯示仍持有的部位，包含未實現損益、停損與隔日留倉評估。</p></div><b>{items.length} 檔</b></div>
    {items.length ? <div className="rocket-table-wrap"><table><thead><tr><th>股票</th><th>型態</th><th>進場 / 現價</th><th>剩餘股數</th><th>未實現</th><th>最高 / 停損</th><th>停利階段</th><th>隔日分</th><th>狀態</th></tr></thead><tbody>{items.map((item) => <tr key={item.id}><td><strong>{item.symbol} {item.stockName}</strong><small>{time(item.entryAt)}</small></td><td>{item.setupType}</td><td>{price(item.entryPrice)}<small>{price(item.currentPrice)}</small></td><td>{item.remainingQuantity.toLocaleString("zh-TW")}</td><td className={pnlClass(item.unrealizedPnl)}>{signedMoney(item.unrealizedPnl)}<small>{percent(item.returnPercent)}</small></td><td>{price(item.highestPrice)}<small className="loss">停損 {price(item.stopLoss)}</small></td><td>{item.takeProfitStage}/2</td><td>{item.overnightScore.toFixed(0)}<small>留 {Math.round(item.overnightHoldPct * 100)}%</small></td><td>{item.latestAction}</td></tr>)}</tbody></table></div> : <div className="rocket-empty compact">目前沒有留倉部位。</div>}
  </section>;
}

function TradeTable({ items }: { items: LimitUpTrade[] }) {
  return <section className="rocket-panel">
    <div className="rocket-title"><Activity size={17} /><div><h2>今日買進／賣出紀錄</h2><p>只顯示今日模擬買進、分批停利、停損與出場；完整歷史仍納入績效。</p></div></div>
    {items.length ? <div className="rocket-table-wrap"><table><thead><tr><th>時間</th><th>股票</th><th>動作</th><th>價格</th><th>股數</th><th>金額</th><th>已實現</th><th>原因</th></tr></thead><tbody>{items.map((item) => <tr key={item.id}><td>{time(item.executedAt)}</td><td>{item.symbol} {item.stockName}</td><td>{actionLabel(item.action)}</td><td>{price(item.price)}</td><td>{item.quantity.toLocaleString("zh-TW")}</td><td>{money(item.grossAmount)}</td><td className={pnlClass(item.realizedPnl)}>{signedMoney(item.realizedPnl)}</td><td>{item.reason}</td></tr>)}</tbody></table></div> : <div className="rocket-empty compact">今日尚無買賣紀錄。</div>}
  </section>;
}

function PerformancePanel({ bucket, title, subtitle }: { bucket: LimitUpAiPerformanceBucket; title: string; subtitle: string }) {
  return <section className="rocket-panel limit-up-performance-panel">
    <div className="rocket-title"><CircleDollarSign size={17} /><div><h2>{title}</h2><p>{subtitle}</p></div><strong className={pnlClass(bucket.totalPnl)}>{signedMoney(bucket.totalPnl)}</strong></div>
    <div className="rocket-performance-grid">
      <article><span>完成交易</span><strong>{bucket.tradeCount}</strong><small>買進 {bucket.buyCount} / 賣出 {bucket.sellCount}</small></article>
      <article><span>勝率</span><strong>{bucket.winRate.toFixed(1)}%</strong><small>{bucket.winCount} 勝 / {bucket.lossCount} 敗</small></article>
      <article><span>獲利合計</span><strong className="profit">{signedMoney(bucket.grossProfit)}</strong><small>所有獲利賣出</small></article>
      <article><span>虧損合計</span><strong className="loss">-{money(bucket.grossLoss)}</strong><small>所有虧損賣出</small></article>
      <article><span>已實現</span><strong className={pnlClass(bucket.realizedPnl)}>{signedMoney(bucket.realizedPnl)}</strong><small>未實現 {signedMoney(bucket.unrealizedPnl)}</small></article>
      <article><span>總報酬率</span><strong className={pnlClass(bucket.totalReturnPct)}>{percent(bucket.totalReturnPct)}</strong><small>持倉 {bucket.openPositionCount}</small></article>
    </div>
  </section>;
}

function NotificationCenter({ items, unreadCount, filter, onFilterChange, onRead, onReadAll }: { items: LimitUpAiNotification[]; unreadCount: number; filter: string; onFilterChange: (value: string) => void; onRead: (id: number) => void; onReadAll: () => void }) {
  const options = [["全部", ""], ["買進", "BUY"], ["停利／減碼", "TAKE_PROFIT"], ["停損", "STOP_LOSS"], ["出場", "SELL"]];
  return <section className="rocket-panel rocket-message-center">
    <div className="rocket-title"><BellRing size={18} /><div><h2>買賣訊息通知中心</h2><p>只保存正式買進、停利、減碼、停損與出場訊息；最新在上。</p></div><b>未讀 {unreadCount}</b></div>
    <div className="rocket-message-controls">
      <div>{options.map(([label, value]) => <button key={label} className={filter === value ? "active" : ""} onClick={() => onFilterChange(value)}>{label}</button>)}</div>
      <button onClick={onReadAll} disabled={!unreadCount}><CheckCheck size={14} />全部已讀</button>
    </div>
    <div className="rocket-messages">{items.length ? items.map((item) => <article key={item.id} className={`${item.type.toLowerCase()} ${item.isRead ? "read" : "unread"}`} onClick={() => !item.isRead && onRead(item.id)}>
      <time>{time(item.createdAt)}</time>
      <span>{item.type === "BUY" ? "⚡" : item.type === "STOP_LOSS" ? "⚠️" : "🔔"}</span>
      <div><strong>{actionLabel(item.type)}｜{item.symbol ?? "-"} {item.stockName ?? ""}</strong><p>{item.message}</p><small>{item.reason}</small></div>
      {!item.isRead && <i>未讀</i>}
    </article>) : <div className="rocket-empty compact"><Bell size={22} />此篩選條件目前沒有訊息。</div>}</div>
  </section>;
}

function SettingsPanel({ settings, onChange, onSave }: { settings: LimitUpAiSettings; onChange: (settings: LimitUpAiSettings) => void; onSave: () => void }) {
  const update = (key: keyof LimitUpAiSettings, value: number | boolean) => onChange({ ...settings, [key]: value });
  return <section className="rocket-settings">
    <div><Settings size={17} /><span><strong>專抓漲停飆股 AI 設定</strong><small>預設用 300 萬模擬資金，嚴格控管單檔與隔夜風險。</small></span></div>
    <label>資金<input type="number" value={settings.capital} onChange={(event) => update("capital", Number(event.target.value))} /></label>
    <label>最低股價<input type="number" value={settings.minPrice} onChange={(event) => update("minPrice", Number(event.target.value))} /></label>
    <label>最高股價<input type="number" value={settings.maxPrice} onChange={(event) => update("maxPrice", Number(event.target.value))} /></label>
    <label>最低量比<input type="number" step="0.1" value={settings.minVolumeRatio20d} onChange={(event) => update("minVolumeRatio20d", Number(event.target.value))} /></label>
    <label>最多持倉<input type="number" value={settings.maxPositions} onChange={(event) => update("maxPositions", Number(event.target.value))} /></label>
    <label><input type="checkbox" checked={settings.excludeLockedLimitUp} onChange={(event) => update("excludeLockedLimitUp", event.target.checked)} />排除已鎖漲停</label>
    <label><input type="checkbox" checked={settings.soundEnabled} onChange={(event) => update("soundEnabled", event.target.checked)} />網頁通知音效</label>
    <button onClick={onSave}>儲存設定</button>
  </section>;
}

export function LimitUpAiPage({ userId }: { userId: string }) {
  const [data, setData] = useState<LimitUpDashboard>(() => normalizeLimitUpDashboard({
    dataNotice: "漲停機器人頁面已啟動；等待背景掃描產生正式買賣與留倉資料。",
  }));
  const [robotStatus, setRobotStatus] = useState<LimitUpAiStatus | null>(null);
  const [settingsDraft, setSettingsDraft] = useState<LimitUpAiSettings | null>(null);
  const [messageFilter, setMessageFilter] = useState("");
  const [messages, setMessages] = useState<LimitUpAiNotification[]>([]);
  const [toasts, setToasts] = useState<LimitUpAiNotification[]>([]);
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState("");
  const initializedMessages = useRef(false);
  const lastNotificationId = useRef(0);

  const playTone = useCallback(() => {
    const audioContext = new AudioContext();
    const oscillator = audioContext.createOscillator();
    const gain = audioContext.createGain();
    oscillator.type = "sine";
    oscillator.frequency.value = 880;
    gain.gain.setValueAtTime(0.001, audioContext.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.12, audioContext.currentTime + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.001, audioContext.currentTime + 0.45);
    oscillator.connect(gain);
    gain.connect(audioContext.destination);
    oscillator.start();
    oscillator.stop(audioContext.currentTime + 0.5);
  }, []);

  const load = useCallback(async (quiet = false) => {
    if (!userId) return;
    if (!quiet) setLoading(true);
    const failures: string[] = [];
    try {
      const [statusResult, dashboardResult, notificationResult] = await Promise.allSettled([
        limitUpAiClient.status(userId),
        limitUpAiClient.dashboard(userId),
        limitUpAiClient.notifications(userId, messageFilter),
      ]);

      let soundEnabled = true;
      if (statusResult.status === "fulfilled") {
        setRobotStatus(normalizeLimitUpAiStatus(statusResult.value));
      } else {
        failures.push(limitUpErrorMessage(statusResult.reason, "機器人狀態暫時無法取得"));
      }

      if (dashboardResult.status === "fulfilled") {
        const safeDashboard = normalizeLimitUpDashboard(dashboardResult.value);
        soundEnabled = safeDashboard.settings.soundEnabled;
        setData(safeDashboard);
        setSettingsDraft((current) => current ?? safeDashboard.settings);
      } else {
        failures.push(limitUpErrorMessage(dashboardResult.reason, "漲停機器人主資料讀取失敗"));
      }

      if (notificationResult.status === "fulfilled") {
        const safeNotifications = normalizeNotificationPayload(notificationResult.value);
        const tradeNotifications = safeNotifications.items.filter(isLimitUpTradeNotification);
        setMessages(tradeNotifications);
        setData((current) => ({
          ...current,
          notifications: tradeNotifications,
          unreadCount: safeNotifications.unreadCount,
        }));
        const newestId = Math.max(0, ...tradeNotifications.map((item) => item.id));
        if (!initializedMessages.current) {
          initializedMessages.current = true;
          lastNotificationId.current = newestId;
        } else {
          const fresh = tradeNotifications
            .filter((item) => item.id > lastNotificationId.current && !item.isRead)
            .sort((a, b) => a.id - b.id);
          if (fresh.length) {
            setToasts((current) => [...fresh, ...current].slice(0, 4));
            if (soundEnabled) {
              try { playTone(); } catch { /* browser may block sound until first interaction */ }
            }
          }
          lastNotificationId.current = Math.max(lastNotificationId.current, newestId);
        }
      } else {
        failures.push("買賣通知暫時無法取得");
      }

      setError(failures[0] ?? "");
    } finally {
      setLoading(false);
    }
  }, [messageFilter, playTone, userId]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!userId) return;
    const timer = window.setInterval(() => void load(true), 15_000);
    return () => window.clearInterval(timer);
  }, [load, userId]);

  const saveSettings = async () => {
    if (!settingsDraft || !userId) return;
    try {
      const saved = await limitUpAiClient.saveSettings(userId, settingsDraft);
      setSettingsDraft(saved);
      await load(true);
    } catch (reason) {
      setError(limitUpErrorMessage(reason, "設定儲存失敗"));
    }
  };

  const runManualScan = async () => {
    if (!userId) return;
    setScanning(true);
    try {
      const payload = await limitUpAiClient.scan(userId);
      const safeDashboard = normalizeLimitUpDashboard(payload);
      setData(safeDashboard);
      setSettingsDraft((current) => current ?? safeDashboard.settings);
      await load(true);
    } catch (reason) {
      setError(limitUpErrorMessage(reason, "漲停機器人手動掃描失敗"));
    } finally {
      setScanning(false);
    }
  };

  const markRead = async (id: number) => {
    if (!userId) return;
    await limitUpAiClient.markNotificationRead(userId, id).catch(() => undefined);
    setMessages((current) => current.map((item) => item.id === id ? { ...item, isRead: true } : item));
    setData((current) => current ? { ...current, unreadCount: Math.max(0, current.unreadCount - 1) } : current);
  };

  const markAllRead = async () => {
    if (!userId) return;
    await limitUpAiClient.markAllNotificationsRead(userId).catch(() => undefined);
    setMessages((current) => current.map((item) => ({ ...item, isRead: true })));
    setData((current) => current ? { ...current, unreadCount: 0 } : current);
  };

  const visibleMessages = useMemo(() => messages.filter(isLimitUpTradeNotification), [messages]);
  const todayTrades = useMemo(() => selectTodayLimitUpTrades(data.trades), [data.trades]);
  const openPositions = useMemo(() => selectOpenLimitUpPositions(data.positions), [data.positions]);

  return <div className="rocket-page limit-up-ai-page">
    <div className="limit-up-toast-stack">{toasts.map((item) => <article key={item.id} className={`limit-up-toast ${item.type.toLowerCase()}`}>
      <button aria-label="關閉通知" onClick={() => setToasts((current) => current.filter((row) => row.id !== item.id))}><X size={13} /></button>
      <strong>{item.title}</strong>
      <span>{item.message}</span>
      <small>{time(item.createdAt)}・{item.reason}</small>
    </article>)}</div>

    <header className="rocket-heading limit-up-heading">
      <div><p>LIMIT-UP MOMENTUM DAYTRADE AI</p><h1><Zap size={27} />專抓漲停飆股AI</h1><span>背景持續掃描；本頁只呈現今日買賣、目前留倉、績效與成交訊息。</span></div>
      <div className="rocket-heading-actions">
        <label>{data.settings.soundEnabled ? <Volume2 size={14} /> : <VolumeX size={14} />}<input type="checkbox" checked={settingsDraft?.soundEnabled ?? data.settings.soundEnabled} onChange={(event) => settingsDraft && setSettingsDraft({ ...settingsDraft, soundEnabled: event.target.checked })} />通知音效</label>
        <button onClick={() => void saveSettings()} disabled={!settingsDraft}>儲存音效</button>
        <button onClick={() => void load()} disabled={loading}><RefreshCw className={loading ? "spin-icon" : ""} size={15} />重新讀取</button>
        <button onClick={() => void runManualScan()} disabled={scanning}><RefreshCw className={scanning ? "spin-icon" : ""} size={15} />立即掃描</button>
      </div>
    </header>
    {error && <div className="error-banner"><AlertTriangle size={16} />{error}{isLoginRequiredMessage(error) && <a href="/login">前往登入</a>}</div>}
    <div className="data-anomaly-banner"><ShieldCheck /><div><strong>模擬交易提醒</strong><span>{data.dataNotice}</span></div></div>

    <section className="rocket-dashboard">
      <article><span>機器人狀態</span><strong>{robotStatusLabel(robotStatus?.status)}</strong><small>{robotStatus?.marketSessionActive ? "盤中每 15 秒自動偵測" : "非盤中，保留最後結果"}</small></article>
      <article><span>今日買進</span><strong>{data.performance.today.buyCount}</strong><small>筆成交</small></article>
      <article><span>今日賣出</span><strong>{data.performance.today.sellCount}</strong><small>含停利、減碼與停損</small></article>
      <article><span>目前留倉</span><strong>{openPositions.length}</strong><small>未實現 {signedMoney(data.performance.today.unrealizedPnl)}</small></article>
      <article><span>今日賺賠</span><strong className={pnlClass(data.performance.today.totalPnl)}>{signedMoney(data.performance.today.totalPnl)}</strong><small>已實現＋未實現</small></article>
      <article><span>今日勝率</span><strong>{data.performance.today.winRate.toFixed(1)}%</strong><small>{data.performance.today.winCount} 勝 / {data.performance.today.lossCount} 敗</small></article>
      <article><span>買賣通知</span><strong>{data.unreadCount}</strong><small>未讀訊息</small></article>
      <article><span>最後掃描</span><strong>{robotStatus?.lastSuccessAt ? time(robotStatus.lastSuccessAt) : "尚未成功"}</strong><small>{robotStatus?.lastError ? `錯誤：${robotStatus.lastError}` : `累計 ${robotStatus?.cycleCount ?? 0} 輪`}</small></article>
    </section>

    <PositionTable items={openPositions} />
    <TradeTable items={todayTrades} />

    <section className="limit-up-performance-grid">
      <PerformancePanel title="今日績效" subtitle="以今日台北時間交易紀錄計算。" bucket={data.performance.today} />
      <PerformancePanel title="本月績效" subtitle={`統計月份 ${data.performance.period}。`} bucket={data.performance.month} />
      <PerformancePanel title="全部績效" subtitle="專抓漲停飆股 AI 累積模擬結果。" bucket={data.performance.all} />
    </section>

    <NotificationCenter items={visibleMessages} unreadCount={data.unreadCount} filter={messageFilter} onFilterChange={setMessageFilter} onRead={(id) => void markRead(id)} onReadAll={() => void markAllRead()} />
    {settingsDraft && <SettingsPanel settings={settingsDraft} onChange={setSettingsDraft} onSave={() => void saveSettings()} />}
  </div>;
}
