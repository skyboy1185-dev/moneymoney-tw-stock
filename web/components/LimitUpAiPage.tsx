"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Activity, AlertTriangle, Bell, BellRing, CheckCheck, CircleDollarSign, Flame, Gauge, RefreshCw, Settings, ShieldCheck, Target, Volume2, VolumeX, X, Zap } from "lucide-react";
import type { ElectronicChipFlowAlert, ElectronicChipFlowAlertsResponse } from "@/lib/electronic-chip-flow-alerts";
import { finiteNumber, normalizeLargeOrderResponse, normalizeLimitUpDashboard, normalizeLimitUpReplay, normalizeNotificationPayload } from "@/lib/limit-up-ai-normalize";
import type { LimitUpAiNotification, LimitUpAiPerformanceBucket, LimitUpAiSettings, LimitUpCandidate, LimitUpDashboard, LimitUpPosition, LimitUpReplay, LimitUpTrade } from "@/lib/limit-up-ai-types";
import { limitUpAiClient } from "@/services/limit-up-ai-client";

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

function lots(value: number | null | undefined): string {
  return `${finiteNumber(value).toLocaleString("zh-TW", { maximumFractionDigits: 1 })} 張`;
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
    ACTIONABLE: "可進場",
    NEAR_LIMIT: "接近漲停",
    OVERNIGHT: "隔日評估",
    WARNING: "警告",
  };
  return labels[action] ?? action;
}

function chipFlowStatusLabel(status?: string): string {
  const labels: Record<string, string> = {
    realtime: "即時排名",
    scanning: "掃描中",
    warming: "暖機中",
    closed: "休市",
    unavailable: "資料暫缺",
    disconnected: "連線中斷",
  };
  return labels[status ?? ""] ?? "等待資料";
}

function CandidateTable({ title, subtitle, items, compact = false }: { title: string; subtitle: string; items: LimitUpCandidate[]; compact?: boolean }) {
  return <section className="rocket-panel">
    <div className="rocket-title">
      <Flame size={17} />
      <div><h2>{title}</h2><p>{subtitle}</p></div>
      <b>{items.length} 檔</b>
    </div>
    {items.length ? <div className="rocket-table-wrap"><table><thead><tr><th>排名</th><th>股票</th><th>現價 / 距漲停</th><th>強度分</th><th>型態</th><th>量比 / 大單</th><th>VWAP / 分時</th><th>判斷</th></tr></thead><tbody>{items.map((item) => {
      const blocker = item.failures[0] ?? item.warnings[0] ?? item.reasons[0] ?? "條件正常";
      return <tr key={`${item.snapshotAt}-${item.id}`}><td>#{item.rank}</td><td><strong>{item.symbol} {item.stockName}</strong><small>{item.market}</small></td><td><b>{price(item.price)}</b><small className={item.limitDistancePercent <= 3 ? "profit" : ""}>距漲停 {item.limitDistancePercent.toFixed(2)}%</small></td><td><strong className="rocket-score">{item.score.toFixed(1)}</strong><small>{item.categoryLabel}</small></td><td>{item.setupLabel}<small>{item.actionable ? "正式可進場" : "等待確認"}</small></td><td>{item.estimatedVolumeRatio20d.toFixed(2)}X<small>大單力道 {item.largeOrderForce.toFixed(0)}</small></td><td>{item.vwapStatus ?? "無資料"}<small>{item.fiveMinuteStructure ?? "等待分時"}</small></td><td><span className={`rocket-status ${item.actionable ? "can_enter" : item.category === "attack" ? "strong_breakout" : "waiting"}`}>{item.actionable ? "正式買進" : item.categoryLabel}</span>{!compact && <small>{blocker}</small>}</td></tr>;
    })}</tbody></table></div> : <div className="rocket-empty compact">目前沒有符合條件的標的。</div>}
  </section>;
}

function LargeOrderRankingTable({
  title,
  subtitle,
  direction,
  items,
  candidateSymbols,
  onSelectStock,
}: {
  title: string;
  subtitle: string;
  direction: "long" | "short";
  items: ElectronicChipFlowAlert[];
  candidateSymbols: Set<string>;
  onSelectStock: (symbol: string) => void;
}) {
  return <section className={`rocket-panel limit-up-large-order-table ${direction}`}>
    <div className="rocket-title">
      {direction === "long" ? <Zap size={17} /> : <Activity size={17} />}
      <div><h2>{title}</h2><p>{subtitle}</p></div>
      <b>Top {Math.min(items.length, 10)}</b>
    </div>
    {items.length ? <div className="rocket-table-wrap"><table><thead><tr><th>排名</th><th>股票</th><th>{direction === "long" ? "大單淨買" : "大單淨賣"}</th><th>連續次數</th><th>{direction === "long" ? "買賣比" : "賣買比"}</th><th>趨勢</th><th>資料</th><th>漲停AI</th></tr></thead><tbody>{items.slice(0, 10).map((item, index) => {
      const rank = item.rank ?? index + 1;
      const netLots = direction === "long"
        ? Math.max(0, item.recentNetBuyLots)
        : Math.max(0, item.recentNetSellLots ?? -item.recentNetBuyLots);
      const steps = direction === "long" ? item.positiveSteps : item.negativeSteps ?? 0;
      const ratio = direction === "long" ? item.buySellRatio : item.sellBuyRatio ?? 0;
      const matched = candidateSymbols.has(item.symbol);
      return <tr key={`${direction}-${item.symbol}`} className={rank <= 3 ? "top-rank" : ""}>
        <td><span className="chip-alert-rank">#{rank}</span></td>
        <td><button className="limit-up-stock-link" onClick={() => onSelectStock(item.symbol)}><strong>{item.symbol} {item.name}</strong><small>{item.market}・{item.industry}</small></button></td>
        <td><b className={direction === "long" ? "profit" : "loss"}>{lots(netLots)}</b><small>日大單買 {lots(item.dayLargeBuyLots)} / 賣 {lots(item.dayLargeSellLots)}</small></td>
        <td><strong>{steps}</strong><small>{item.currentQualifies ? "目前仍符合" : "等待下一筆確認"}</small></td>
        <td><strong>{ratio ? ratio.toFixed(2) : "—"}</strong><small>門檻 {item.effectiveNetThresholdLots ? lots(item.effectiveNetThresholdLots) : "估算中"}</small></td>
        <td>{item.trendLabel}<small>{item.message}</small></td>
        <td>{item.dataStateLabel ?? "即時"}<small>{item.lastLargeOrderAt ? time(item.lastLargeOrderAt) : item.time}</small></td>
        <td><span className={`rocket-status ${matched ? "can_enter" : "waiting"}`}>{matched ? "已進漲停AI候選" : "大單強，待漲停條件"}</span></td>
      </tr>;
    })}</tbody></table></div> : <div className="rocket-empty compact">目前沒有 {direction === "long" ? "多方大單買入" : "空方大單賣出"} Top10 資料。</div>}
  </section>;
}

function LargeOrderTop10Panel({
  data,
  currentSymbol,
  candidateSymbols,
  onSelectStock,
}: {
  data: ElectronicChipFlowAlertsResponse | null;
  currentSymbol?: string;
  candidateSymbols: Set<string>;
  onSelectStock: (symbol: string) => void;
}) {
  const rankingLimit = data?.rankingLimit ?? 10;
  const longAlerts = Array.isArray(data?.alerts) ? data.alerts : [];
  const shortAlerts = Array.isArray(data?.shortAlerts) ? data.shortAlerts : [];
  const longRank = currentSymbol ? longAlerts.find((item) => item.symbol === currentSymbol) : undefined;
  const shortRank = currentSymbol ? shortAlerts.find((item) => item.symbol === currentSymbol) : undefined;
  const longRankNumber = longRank ? longRank.rank ?? longAlerts.indexOf(longRank) + 1 : null;
  const shortRankNumber = shortRank ? shortRank.rank ?? shortAlerts.indexOf(shortRank) + 1 : null;
  return <section className="limit-up-large-order-ranking">
    <header className="rocket-panel limit-up-large-order-header">
      <div><span>這裡就是大單動能前10排名</span><h2>連續大單動能 Top{rankingLimit}</h2><p>同一套即時大單排名：多方看連續大單買入，空方看連續大單賣出；漲停 AI 會另外標示是否已通過完整條件。</p></div>
      <strong>{chipFlowStatusLabel(data?.status)}</strong>
    </header>
    {currentSymbol && <article className="rocket-panel limit-up-current-rank-card">
      <span>目前查詢股票</span>
      <strong>{currentSymbol}</strong>
      {longRank ? <p className="profit">目前多方大單買入第 {longRankNumber} 名，近 {data?.windowMinutes ?? 5} 分鐘淨買 {lots(Math.max(0, longRank.recentNetBuyLots))}。</p>
        : shortRank ? <p className="loss">目前空方大單賣出第 {shortRankNumber} 名，近 {data?.windowMinutes ?? 5} 分鐘淨賣 {lots(Math.max(0, shortRank.recentNetSellLots ?? -shortRank.recentNetBuyLots))}。</p>
          : <p>目前不在大單動能 Top{rankingLimit}；代表不是當下前 10 強的連續大單方向標的。</p>}
    </article>}
    {data?.notice && <div className="data-anomaly-banner"><ShieldCheck /><div><strong>大單資料狀態</strong><span>{data.notice}</span></div></div>}
    <div className="limit-up-large-order-grid">
      <LargeOrderRankingTable
        title={`多方大單買入 Top${rankingLimit}`}
        subtitle={`最近 ${data?.windowMinutes ?? 5} 分鐘連續主動買盤排名。`}
        direction="long"
        items={longAlerts}
        candidateSymbols={candidateSymbols}
        onSelectStock={onSelectStock}
      />
      <LargeOrderRankingTable
        title={`空方大單賣出 Top${rankingLimit}`}
        subtitle={`最近 ${data?.windowMinutes ?? 5} 分鐘連續主動賣盤排名。`}
        direction="short"
        items={shortAlerts}
        candidateSymbols={candidateSymbols}
        onSelectStock={onSelectStock}
      />
    </div>
  </section>;
}

function PositionTable({ items }: { items: LimitUpPosition[] }) {
  return <section className="rocket-panel">
    <div className="rocket-title"><Target size={17} /><div><h2>已進場監控區</h2><p>模擬進場後追蹤停損、分批停利、移動停利與隔日沖評估。</p></div><b>{items.filter((item) => item.status === "open").length} 未平倉</b></div>
    {items.length ? <div className="rocket-table-wrap"><table><thead><tr><th>股票</th><th>型態</th><th>進場 / 現價</th><th>剩餘股數</th><th>未實現</th><th>最高 / 停損</th><th>停利階段</th><th>隔日分</th><th>狀態</th></tr></thead><tbody>{items.map((item) => <tr key={item.id}><td><strong>{item.symbol} {item.stockName}</strong><small>{time(item.entryAt)}</small></td><td>{item.setupType}</td><td>{price(item.entryPrice)}<small>{price(item.currentPrice)}</small></td><td>{item.remainingQuantity.toLocaleString("zh-TW")}</td><td className={pnlClass(item.unrealizedPnl)}>{signedMoney(item.unrealizedPnl)}<small>{percent(item.returnPercent)}</small></td><td>{price(item.highestPrice)}<small className="loss">停損 {price(item.stopLoss)}</small></td><td>{item.takeProfitStage}/2</td><td>{item.overnightScore.toFixed(0)}<small>留 {Math.round(item.overnightHoldPct * 100)}%</small></td><td>{item.latestAction}</td></tr>)}</tbody></table></div> : <div className="rocket-empty compact">目前沒有模擬持倉，系統持續掃描中。</div>}
  </section>;
}

function TradeTable({ items }: { items: LimitUpTrade[] }) {
  return <section className="rocket-panel">
    <div className="rocket-title"><Activity size={17} /><div><h2>買賣交易紀錄</h2><p>所有模擬買進、分批停利、停損與出場都會保存，並納入績效。</p></div></div>
    {items.length ? <div className="rocket-table-wrap"><table><thead><tr><th>時間</th><th>股票</th><th>動作</th><th>價格</th><th>股數</th><th>金額</th><th>已實現</th><th>原因</th></tr></thead><tbody>{items.map((item) => <tr key={item.id}><td>{time(item.executedAt)}</td><td>{item.symbol} {item.stockName}</td><td>{actionLabel(item.action)}</td><td>{price(item.price)}</td><td>{item.quantity.toLocaleString("zh-TW")}</td><td>{money(item.grossAmount)}</td><td className={pnlClass(item.realizedPnl)}>{signedMoney(item.realizedPnl)}</td><td>{item.reason}</td></tr>)}</tbody></table></div> : <div className="rocket-empty compact">目前沒有交易紀錄。</div>}
  </section>;
}

function PerformancePanel({ bucket, title, subtitle }: { bucket: LimitUpAiPerformanceBucket; title: string; subtitle: string }) {
  return <section className="rocket-panel limit-up-performance-panel">
    <div className="rocket-title"><CircleDollarSign size={17} /><div><h2>{title}</h2><p>{subtitle}</p></div><strong className={pnlClass(bucket.totalPnl)}>{signedMoney(bucket.totalPnl)}</strong></div>
    <div className="rocket-performance-grid">
      <article><span>完成交易</span><strong>{bucket.tradeCount}</strong><small>買進 {bucket.buyCount} / 賣出 {bucket.sellCount}</small></article>
      <article><span>勝率</span><strong>{bucket.winRate.toFixed(1)}%</strong><small>{bucket.winCount} 勝 / {bucket.lossCount} 敗</small></article>
      <article><span>已實現</span><strong className={pnlClass(bucket.realizedPnl)}>{signedMoney(bucket.realizedPnl)}</strong><small>未實現 {signedMoney(bucket.unrealizedPnl)}</small></article>
      <article><span>總報酬率</span><strong className={pnlClass(bucket.totalReturnPct)}>{percent(bucket.totalReturnPct)}</strong><small>持倉 {bucket.openPositionCount}</small></article>
      <article><span>平均獲利</span><strong className="profit">{signedMoney(bucket.averageWin)}</strong><small>平均虧損 {signedMoney(bucket.averageLoss)}</small></article>
      <article><span>最大單筆虧損</span><strong className={pnlClass(bucket.maximumSingleLoss)}>{signedMoney(bucket.maximumSingleLoss)}</strong><small>風控觀察</small></article>
    </div>
  </section>;
}

function NotificationCenter({ items, unreadCount, filter, onFilterChange, onRead, onReadAll }: { items: LimitUpAiNotification[]; unreadCount: number; filter: string; onFilterChange: (value: string) => void; onRead: (id: number) => void; onReadAll: () => void }) {
  const options = [["全部", ""], ["買進", "BUY"], ["可進場", "ACTIONABLE"], ["接近漲停", "NEAR_LIMIT"], ["停利", "TAKE_PROFIT"], ["停損", "STOP_LOSS"], ["出場", "SELL"]];
  return <section className="rocket-panel rocket-message-center">
    <div className="rocket-title"><BellRing size={18} /><div><h2>買賣訊息通知中心</h2><p>正式買進、停利、停損、出場與接近漲停候選會保存；最新在上。</p></div><b>未讀 {unreadCount}</b></div>
    <div className="rocket-message-controls">
      <div>{options.map(([label, value]) => <button key={label} className={filter === value ? "active" : ""} onClick={() => onFilterChange(value)}>{label}</button>)}</div>
      <button onClick={onReadAll} disabled={!unreadCount}><CheckCheck size={14} />全部已讀</button>
    </div>
    <div className="rocket-messages">{items.length ? items.map((item) => <article key={item.id} className={`${item.type.toLowerCase()} ${item.isRead ? "read" : "unread"}`} onClick={() => !item.isRead && onRead(item.id)}>
      <time>{time(item.createdAt)}</time>
      <span>{item.type === "BUY" || item.type === "ACTIONABLE" ? "⚡" : item.type === "STOP_LOSS" ? "⚠️" : "🔔"}</span>
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

function LoadErrorState({ error, loading, onRetry }: { error: string; loading: boolean; onRetry: () => void }) {
  return <div className="rocket-page limit-up-ai-page">
    <header className="rocket-heading limit-up-heading">
      <div><p>LIMIT-UP MOMENTUM DAYTRADE AI</p><h1><Zap size={27} />專抓漲停飆股AI</h1><span>頁面已載入，但後端資料暫時無法取得；不會再讓整個畫面白屏。</span></div>
      <div className="rocket-heading-actions">
        <button onClick={onRetry} disabled={loading}><RefreshCw className={loading ? "spin-icon" : ""} size={15} />重試載入</button>
      </div>
    </header>
    <div className="error-banner"><AlertTriangle size={16} />{error || "專抓漲停飆股 AI 資料讀取失敗"}</div>
    <div className="data-anomaly-banner"><ShieldCheck /><div><strong>目前狀態</strong><span>可能是 Railway 後端尚未部署完成、服務暖機中，或資料庫表尚未建立。前端已進入安全降級模式。</span></div></div>
  </div>;
}

export function LimitUpAiPage({ symbol }: { symbol?: string }) {
  const [userId, setUserId] = useState("");
  const [data, setData] = useState<LimitUpDashboard | null>(null);
  const [largeOrderData, setLargeOrderData] = useState<ElectronicChipFlowAlertsResponse | null>(null);
  const [replay, setReplay] = useState<LimitUpReplay | null>(null);
  const [settingsDraft, setSettingsDraft] = useState<LimitUpAiSettings | null>(null);
  const [messageFilter, setMessageFilter] = useState("");
  const [messages, setMessages] = useState<LimitUpAiNotification[]>([]);
  const [toasts, setToasts] = useState<LimitUpAiNotification[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const initializedMessages = useRef(false);
  const lastNotificationId = useRef(0);

  useEffect(() => {
    let id = localStorage.getItem("moneymoney-user-id");
    if (!id) {
      id = globalThis.crypto?.randomUUID?.() ?? `local-${Date.now()}-${Math.random().toString(36).slice(2)}`;
      localStorage.setItem("moneymoney-user-id", id);
    }
    setUserId(id);
  }, []);

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
    try {
      const [dashboard, replayToday, notificationPayload, largeOrderPayload] = await Promise.all([
        limitUpAiClient.dashboard(userId),
        limitUpAiClient.replayToday(userId),
        limitUpAiClient.notifications(userId, messageFilter),
        fetch("/api/chip-flow/electronic-alerts?clientId=limit-up-ai-page", { cache: "no-store" })
          .then(async (response) => {
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) return normalizeLargeOrderResponse({ ...payload, status: "disconnected" });
            return normalizeLargeOrderResponse(payload);
          })
          .catch(() => normalizeLargeOrderResponse({ status: "disconnected", error: "大單資料暫時無法取得。" })),
      ]);
      const safeDashboard = normalizeLimitUpDashboard(dashboard);
      const safeReplay = normalizeLimitUpReplay(replayToday);
      const safeNotifications = normalizeNotificationPayload(notificationPayload);
      setData(safeDashboard);
      setLargeOrderData(largeOrderPayload);
      setReplay(safeReplay);
      setSettingsDraft((current) => current ?? safeDashboard.settings);
      setMessages(safeNotifications.items);
      const newestId = Math.max(0, ...safeNotifications.items.map((item) => item.id));
      if (!initializedMessages.current) {
        initializedMessages.current = true;
        lastNotificationId.current = newestId;
      } else {
        const fresh = safeNotifications.items
          .filter((item) => item.id > lastNotificationId.current && !item.isRead && ["BUY", "SELL", "TAKE_PROFIT", "STOP_LOSS", "ACTIONABLE", "NEAR_LIMIT"].includes(item.type))
          .sort((a, b) => a.id - b.id);
        if (fresh.length) {
          setToasts((current) => [...fresh, ...current].slice(0, 4));
          if (safeDashboard.settings.soundEnabled) {
            try { playTone(); } catch { /* browser may block sound until first interaction */ }
          }
        }
        lastNotificationId.current = Math.max(lastNotificationId.current, newestId);
      }
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "專抓漲停飆股 AI 資料讀取失敗");
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
      setError(reason instanceof Error ? reason.message : "設定儲存失敗");
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

  const visibleMessages = useMemo(() => messages, [messages]);
  const candidateSymbols = useMemo(() => new Set([
    ...(data?.candidates ?? []).map((item) => item.symbol),
    ...(data?.nearEntries ?? []).map((item) => item.symbol),
    ...(data?.limitMonitors ?? []).map((item) => item.symbol),
  ]), [data]);
  const selectStock = (symbol: string) => {
    window.location.assign(`/?symbol=${encodeURIComponent(symbol)}&view=analysis`);
  };

  if (loading && !data) return <div className="table-loading"><span className="spinner" /><span>載入專抓漲停飆股 AI...</span></div>;
  if (!data) return <LoadErrorState error={error || "專抓漲停飆股 AI 目前沒有資料"} loading={loading} onRetry={() => void load()} />;

  return <div className="rocket-page limit-up-ai-page">
    <div className="limit-up-toast-stack">{toasts.map((item) => <article key={item.id} className={`limit-up-toast ${item.type.toLowerCase()}`}>
      <button aria-label="關閉通知" onClick={() => setToasts((current) => current.filter((row) => row.id !== item.id))}><X size={13} /></button>
      <strong>{item.title}</strong>
      <span>{item.message}</span>
      <small>{time(item.createdAt)}・{item.reason}</small>
    </article>)}</div>

    <header className="rocket-heading limit-up-heading">
      <div><p>LIMIT-UP MOMENTUM DAYTRADE AI</p><h1><Zap size={27} />專抓漲停飆股AI</h1><span>只在量價、族群、突破、分時與大單力道同時轉強時列入正式候選；沒出現高品質訊號就不交易。</span></div>
      <div className="rocket-heading-actions">
        <label>{data.settings.soundEnabled ? <Volume2 size={14} /> : <VolumeX size={14} />}<input type="checkbox" checked={settingsDraft?.soundEnabled ?? data.settings.soundEnabled} onChange={(event) => settingsDraft && setSettingsDraft({ ...settingsDraft, soundEnabled: event.target.checked })} />通知音效</label>
        <button onClick={() => void saveSettings()} disabled={!settingsDraft}>儲存音效</button>
        <button onClick={() => void load()} disabled={loading}><RefreshCw className={loading ? "spin-icon" : ""} size={15} />立即更新</button>
      </div>
    </header>
    {error && <div className="error-banner"><AlertTriangle size={16} />{error}</div>}
    <div className="data-anomaly-banner"><ShieldCheck /><div><strong>模擬交易提醒</strong><span>{data.dataNotice}</span></div></div>

    <LargeOrderTop10Panel data={largeOrderData} currentSymbol={symbol} candidateSymbols={candidateSymbols} onSelectStock={selectStock} />

    <section className="rocket-dashboard">
      <article><span>候選股</span><strong>{data.summary.candidateCount}</strong><small>攻擊 {data.summary.attackCount} / 可進場 {data.summary.actionableCount}</small></article>
      <article><span>模擬持倉</span><strong>{data.summary.openPositionCount}</strong><small>最多 {data.settings.maxPositions} 檔</small></article>
      <article><span>今日績效</span><strong className={pnlClass(data.performance.today.totalPnl)}>{signedMoney(data.performance.today.totalPnl)}</strong><small>勝率 {data.performance.today.winRate.toFixed(1)}%</small></article>
      <article><span>本月績效</span><strong className={pnlClass(data.performance.month.totalPnl)}>{signedMoney(data.performance.month.totalPnl)}</strong><small>{data.performance.period}</small></article>
      <article><span>買賣通知</span><strong>{data.unreadCount}</strong><small>未讀訊息</small></article>
      <article><span>今日回推</span><strong>{replay?.attackTotal ?? 0}</strong><small>可進場 {replay?.actionableTotal ?? 0} / {replay?.total ?? 0}</small></article>
    </section>

    <section className="limit-up-performance-grid">
      <PerformancePanel title="今日績效" subtitle="以今日台北時間交易紀錄計算。" bucket={data.performance.today} />
      <PerformancePanel title="本月績效" subtitle={`統計月份 ${data.performance.period}。`} bucket={data.performance.month} />
      <PerformancePanel title="全部績效" subtitle="專抓漲停飆股 AI 累積模擬結果。" bucket={data.performance.all} />
    </section>

    <NotificationCenter items={visibleMessages} unreadCount={data.unreadCount} filter={messageFilter} onFilterChange={setMessageFilter} onRead={(id) => void markRead(id)} onReadAll={() => void markAllRead()} />

    <CandidateTable title="強勢候選區" subtitle="依 100 分模型排序；85 分以上為漲停攻擊候選。" items={data.candidates} />
    <CandidateTable title="接近買點區" subtitle="符合 A/B/C 型態或已達漲停攻擊等級，才會列入正式觀察。" items={data.nearEntries} />
    <PositionTable items={data.positions} />
    <CandidateTable title="漲停 / 炸板監控區" subtitle="距離漲停 3% 內的標的，重點看主動買盤、委買承接與是否開板。" items={data.limitMonitors} compact />
    <section className="rocket-panel">
      <div className="rocket-title"><Gauge size={17} /><div><h2>隔日續抱評估區</h2><p>13:10～13:25 重新評分；80 分以上最多留 50%，70 分以下全部當日平倉。</p></div><b>{data.overnightEvaluations.length} 檔</b></div>
      {data.overnightEvaluations.length ? <div className="rocket-top5">{data.overnightEvaluations.map((item) => <button key={item.id}><i>{item.overnightScore.toFixed(0)}</i><div><strong>{item.symbol} {item.stockName}</strong><span>建議留 {Math.round(item.overnightHoldPct * 100)}%</span></div><b>{percent(item.returnPercent)}</b><em>{item.latestAction}</em></button>)}</div> : <div className="rocket-empty compact">目前沒有需要隔日續抱評估的持倉。</div>}
    </section>
    <TradeTable items={data.trades} />
    {settingsDraft && <SettingsPanel settings={settingsDraft} onChange={setSettingsDraft} onSave={() => void saveSettings()} />}
    <CandidateTable title="今日候選回推" subtitle="用今天保存的掃描快照回看：哪些股票曾進入攻擊 / 可進場候選。" items={replay?.items ?? []} compact />
  </div>;
}
