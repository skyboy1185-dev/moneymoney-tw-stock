"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity, Bell, BellRing, ChevronRight, CircleDollarSign, Crosshair, Gauge,
  LineChart, RefreshCw, Rocket, ShieldAlert, Target, TrendingUp, Trophy, Volume2, X,
} from "lucide-react";
import type {
  RocketBacktest, RocketCandidate, RocketDashboard, RocketNotification, RocketStatRow,
} from "@/lib/rocket-radar-types";

const POPUP_TYPES = new Set(["BREAKOUT", "BUY", "ADD", "REDUCE", "TAKE_PROFIT", "SELL", "STOP_LOSS", "WARNING"]);
const SOUND_TYPES = new Set(["BUY", "SELL", "STOP_LOSS", "TAKE_PROFIT"]);
const BASE_ALLOCATIONS = [25, 20, 15, 10, 10];

function money(value: number): string {
  const text = new Intl.NumberFormat("zh-TW", { maximumFractionDigits: 0 }).format(Math.abs(value));
  return `${value < 0 ? "-" : value > 0 ? "+" : ""}NT$${text}`;
}

function plainMoney(value: number): string {
  return `NT$${new Intl.NumberFormat("zh-TW", { maximumFractionDigits: 0 }).format(value)}`;
}

function percent(value: number): string { return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`; }
function price(value: number): string { return value.toLocaleString("zh-TW", { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
function pnlClass(value: number): string { return value > 0 ? "profit" : value < 0 ? "loss" : ""; }
function time(value: string): string { return new Date(value).toLocaleTimeString("zh-TW", { hour12: false }); }

function eventClass(type: string): string {
  if (type === "BUY") return "buy";
  if (type === "ADD") return "add";
  if (type === "HOLD" || type === "BREAKOUT") return "hold";
  if (type === "REDUCE") return "reduce";
  if (type === "TAKE_PROFIT") return "take-profit";
  if (type === "SELL" || type === "STOP_LOSS") return "sell";
  if (type === "WARNING") return "warning";
  return "info";
}

function eventIcon(type: string): string {
  return ({ BUY: "🟢", ADD: "🟣", HOLD: "🔵", BREAKOUT: "🚀", REDUCE: "🟠", TAKE_PROFIT: "💰", SELL: "🔴", STOP_LOSS: "🔴", WARNING: "⚠️", MARKET: "📊", SECTOR: "🔥", WATCH: "🟡" } as Record<string, string>)[type] ?? "🔔";
}

function EquityCurve({ rows }: { rows: RocketDashboard["equityCurve"] }) {
  if (!rows.length) return <div className="rocket-empty compact"><LineChart size={25} /><span>首個交易日後開始累積資產曲線</span></div>;
  const values = rows.map((row) => row.totalEquity);
  const min = Math.min(...values), max = Math.max(...values);
  const range = Math.max(1, max - min);
  const points = rows.map((row, index) => `${rows.length === 1 ? 500 : index / (rows.length - 1) * 960 + 20},${200 - (row.totalEquity - min) / range * 160}`).join(" ");
  return <div className="rocket-equity-chart">
    <svg viewBox="0 0 1000 230" role="img" aria-label="每日總資產曲線">
      <defs><linearGradient id="rocket-equity-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#7f68ff" stopOpacity=".34" /><stop offset="1" stopColor="#7f68ff" stopOpacity="0" /></linearGradient></defs>
      <polyline points={`20,210 ${points} 980,210`} fill="url(#rocket-equity-fill)" stroke="none" />
      <polyline points={points} fill="none" stroke="#9b8aff" strokeWidth="5" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
    <div><span>{rows[0].date}<strong>{plainMoney(rows[0].totalEquity)}</strong></span><span>{rows.at(-1)?.date}<strong>{plainMoney(rows.at(-1)?.totalEquity ?? 0)}</strong></span></div>
  </div>;
}

function AnalyticsTable({ rows, label }: { rows: RocketStatRow[]; label: string }) {
  if (!rows.length) return <div className="rocket-empty compact">尚未累積已完成交易，{label}會在平倉後開始統計。</div>;
  return <div className="rocket-table-wrap"><table className="rocket-analytics-table"><thead><tr><th>{label}</th><th>交易數</th><th>勝率</th><th>平均報酬</th><th>平均獲利</th><th>平均虧損</th><th>Profit Factor</th><th>最大虧損</th><th>最大回撤</th><th>總報酬</th></tr></thead><tbody>
    {rows.map((row) => <tr key={row.key}><td><strong>{row.key}</strong></td><td>{row.tradeCount}</td><td>{percent(row.winRate)}</td><td className={pnlClass(row.averageReturnPercent)}>{percent(row.averageReturnPercent)}</td><td className="profit">{percent(row.averageWinPercent)}</td><td className="loss">{percent(row.averageLossPercent)}</td><td>{row.profitFactor?.toFixed(2) ?? "—"}</td><td className="loss">{percent(row.maximumLossPercent)}</td><td className="loss">{percent(row.maximumDrawdownPercent)}</td><td className={pnlClass(row.totalReturnPercent)}>{percent(row.totalReturnPercent)}</td></tr>)}
  </tbody></table></div>;
}

function CandidateDetail({ item, data, onClose, onSelectStock }: { item: RocketCandidate; data: RocketDashboard; onClose: () => void; onSelectStock: (symbol: string) => void }) {
  const tradeRank = data.top5.findIndex((candidate) => candidate.stockCode === item.stockCode);
  const rankIndex = tradeRank >= 0 ? tradeRank : Math.min(Math.max(item.rank - 1, 0), 4);
  const allocationPct = BASE_ALLOCATIONS[rankIndex] * data.market.maximumExposurePercent / 80;
  const allocation = data.account.totalEquity * allocationPct / 100;
  const first = allocation * .4, second = allocation * .3;
  const riskShares = Math.floor(data.account.totalEquity * .01 / Math.max(.01, item.breakoutPrice - item.stopLossPrice));
  const budgetShares = Math.floor(first / item.breakoutPrice);
  const shares = Math.max(0, Math.min(riskShares, budgetShares));
  return <div className="rocket-modal-backdrop" onMouseDown={onClose}><section className="rocket-detail" onMouseDown={(event) => event.stopPropagation()}>
    <header><div><p>ROCKET CANDIDATE DETAIL</p><h2>🚀 {item.stockCode} {item.stockName}</h2><span>{item.statusLabel}・{item.patternType}</span></div><button onClick={onClose} aria-label="關閉"><X size={18} /></button></header>
    <div className="rocket-detail-hero"><article><span>Rocket Score</span><strong>{item.rocketScore.toFixed(1)} / 100</strong></article><article><span>CHASE Risk</span><strong className={item.chaseRiskScore >= 60 ? "loss" : "profit"}>{item.chaseRiskScore.toFixed(1)}</strong></article><article><span>目前價格</span><strong>{price(item.currentPrice)}</strong></article><article><span>市場／族群</span><strong>{data.market.label}</strong><small>{item.sectorName} #{item.sectorRank}</small></article></div>
    <div className="rocket-detail-columns">
      <article><h3>分數拆解</h3>{Object.entries(item.scoreBreakdown).map(([key, value]) => <p key={key}><span>{key}</span><strong>{value === null ? "資料暫無" : value.toFixed(1)}</strong></p>)}<footer>資料可用度 {item.dataAvailabilityPercent.toFixed(0)}%</footer></article>
      <article><h3>交易計畫</h3><p><span>Entry</span><strong>{price(item.breakoutPrice)}～{price(item.breakoutPrice * 1.01)}</strong></p><p><span>Stop</span><strong className="loss">{price(item.stopLossPrice)}</strong></p><p><span>Target 1</span><strong>{price(item.targetPrice1)}</strong></p><p><span>Target 2</span><strong>{price(item.targetPrice2)}</strong></p><p><span>Trailing</span><strong>MA10 / 2 ATR</strong></p><p><span>RR</span><strong>{item.riskRewardRatio.toFixed(2)}</strong></p></article>
      <article><h3>100 萬資金配置</h3><p><span>最終配置</span><strong>{plainMoney(allocation)}（{allocationPct.toFixed(1)}%）</strong></p><p><span>第一筆 40%</span><strong>{plainMoney(first)}</strong></p><p><span>第二筆 30%</span><strong>{plainMoney(second)}</strong></p><p><span>第三筆 30%</span><strong>{plainMoney(second)}</strong></p><p><span>1% 風險估算</span><strong>{shares.toLocaleString("zh-TW")} 股</strong></p><footer>實際成交仍受現金、滑價、費用與每股停損風險限制。</footer></article>
    </div>
    <div className="rocket-detail-reasons"><h3>模型依據</h3>{item.reasons.map((reason) => <span key={reason}>✓ {reason}</span>)}{item.missingData.map((reason) => <span className="missing" key={reason}>ℹ {reason}，已重新分配可用指標權重</span>)}</div>
    <button className="rocket-open-stock" onClick={() => onSelectStock(item.stockCode)}>開啟個股完整分析 <ChevronRight size={15} /></button>
  </section></div>;
}

export function RocketRadarPage({ onSelectStock, onUnreadChange }: { onSelectStock: (symbol: string) => void; onUnreadChange?: (count: number) => void }) {
  const [data, setData] = useState<RocketDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<RocketCandidate | null>(null);
  const [analysis, setAnalysis] = useState<"strategy" | "score" | "holding" | "regime">("strategy");
  const [messageFilter, setMessageFilter] = useState("");
  const [messagePeriod, setMessagePeriod] = useState("today");
  const [messages, setMessages] = useState<RocketNotification[]>([]);
  const [backtestPeriod, setBacktestPeriod] = useState("3m");
  const [backtest, setBacktest] = useState<RocketBacktest | null>(null);
  const [backtesting, setBacktesting] = useState(false);
  const [tradeFilter, setTradeFilter] = useState("");
  const [feeDiscount, setFeeDiscount] = useState(.6);
  const [slippage, setSlippage] = useState(.001);
  const [soundEnabled, setSoundEnabled] = useState(false);
  const lastEventId = useRef(0);
  const eventReady = useRef(false);

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const response = await fetch("/api/rocket-radar/dashboard", { cache: "no-store" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error ?? "飆股雷達資料讀取失敗");
      setData(payload);
      setMessages((current) => current.length ? current : payload.notifications);
      setFeeDiscount(payload.settings.brokerFeeDiscount);
      setSlippage(payload.settings.slippageRate);
      setSoundEnabled(payload.settings.soundEnabled);
      onUnreadChange?.(payload.unreadCount);
      if (!eventReady.current) {
        lastEventId.current = Math.max(0, ...payload.notifications.map((item: RocketNotification) => item.notificationId));
        eventReady.current = true;
      }
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "飆股雷達資料讀取失敗");
    } finally { setLoading(false); }
  }, [onUnreadChange]);

  const playTone = useCallback(() => {
    try {
      const AudioContextClass = window.AudioContext || (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (!AudioContextClass) return;
      const context = new AudioContextClass(), oscillator = context.createOscillator(), gain = context.createGain();
      oscillator.frequency.value = 660; gain.gain.setValueAtTime(.045, context.currentTime);
      gain.gain.exponentialRampToValueAtTime(.001, context.currentTime + .16);
      oscillator.connect(gain); gain.connect(context.destination); oscillator.start(); oscillator.stop(context.currentTime + .17);
    } catch { /* Browser may require an earlier user gesture. */ }
  }, []);

  const markRead = useCallback(async (id: number) => {
    await fetch(`/api/rocket-radar/notifications/${id}/read`, { method: "POST" }).catch(() => undefined);
    setMessages((rows) => rows.map((row) => row.notificationId === id ? { ...row, isRead: true } : row));
    setData((current) => current ? { ...current, unreadCount: Math.max(0, current.unreadCount - 1) } : current);
  }, []);

  const pollEvents = useCallback(async () => {
    if (!eventReady.current) return;
    try {
      const response = await fetch(`/api/rocket-radar/events?afterId=${lastEventId.current}`, { cache: "no-store" });
      const payload = await response.json() as { items: RocketNotification[]; lastEventId: number };
      if (!response.ok) return;
      lastEventId.current = Math.max(lastEventId.current, payload.lastEventId);
      const popup = payload.items.filter((item) => POPUP_TYPES.has(item.notificationType));
      if (popup.length) {
        popup.forEach((item) => {
          if (soundEnabled && SOUND_TYPES.has(item.notificationType)) playTone();
          if (!item.isRead) void markRead(item.notificationId);
        });
      }
      if (payload.items.length) { setMessages((rows) => [...payload.items.reverse(), ...rows]); void load(true); }
    } catch { /* next polling cycle retries */ }
  }, [load, markRead, playTone, soundEnabled]);

  useEffect(() => { void load(); const timer = window.setInterval(() => void load(true), 15_000); return () => window.clearInterval(timer); }, [load]);
  useEffect(() => { const timer = window.setInterval(() => void pollEvents(), 5_000); return () => window.clearInterval(timer); }, [pollEvents]);

  const loadMessages = useCallback(async () => {
    const params = new URLSearchParams({ period: messagePeriod });
    if (messageFilter) params.set("type", messageFilter);
    const response = await fetch(`/api/rocket-radar/notifications?${params}`, { cache: "no-store" });
    const payload = await response.json();
    if (response.ok) { setMessages(payload.items); onUnreadChange?.(payload.unreadCount); }
  }, [messageFilter, messagePeriod, onUnreadChange]);
  useEffect(() => { if (data) void loadMessages(); }, [data?.updatedAt, loadMessages]); // eslint-disable-line react-hooks/exhaustive-deps

  const saveSettings = async () => {
    const response = await fetch("/api/rocket-radar/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ broker_fee_discount: feeDiscount, slippage_rate: slippage, sound_enabled: soundEnabled }) });
    if (!response.ok) setError("交易成本設定儲存失敗"); else void load(true);
  };
  const runBacktest = async () => {
    setBacktesting(true);
    try { const response = await fetch(`/api/rocket-radar/backtest?period=${backtestPeriod}`, { cache: "no-store" }); setBacktest(await response.json()); }
    finally { setBacktesting(false); }
  };

  const analyticsRows = data ? ({ strategy: data.strategyStats, score: data.scoreStats, holding: data.holdingStats, regime: data.regimeStats } as const)[analysis] : [];
  const analyticsLabel = ({ strategy: "策略類型", score: "Rocket Score", holding: "持有天數", regime: "市場環境" } as const)[analysis];
  const filteredTrades = useMemo(() => data?.trades.filter((row) => !tradeFilter || row.action === tradeFilter) ?? [], [data?.trades, tradeFilter]);

  if (loading && !data) return <div className="table-loading"><span className="spinner" /><span>正在啟動全市場飆股雷達…</span></div>;
  if (!data) return <div className="error-banner">{error || "飆股雷達暫時無資料"}</div>;

  return <div className="rocket-page">
    <header className="rocket-heading"><div><p>ROCKET MOMENTUM DETECTION SYSTEM</p><h1><Rocket size={27} />飆股雷達</h1><span>尋找尚未大幅噴出、即將進入主升段的標的；正式買進會同步寄送 Gmail，不發送 LINE。</span></div><div className="rocket-heading-actions"><label><Volume2 size={14} /><input type="checkbox" checked={soundEnabled} onChange={(event) => setSoundEnabled(event.target.checked)} />通知音效</label><button onClick={() => void load()} disabled={loading}><RefreshCw className={loading ? "spin-icon" : ""} size={15} />立即更新</button></div></header>
    {error && <div className="error-banner">{error}</div>}

    <section className="rocket-dashboard">
      <article className="regime"><span>目前市場</span><strong>{data.market.label}</strong><small>{data.market.strategy}</small></article>
      <article><span>候選／可進場</span><strong>{data.candidates.length}／{data.top5.length}</strong><small>觀察池最多 20・交易最多 5</small></article>
      <article><span>持倉</span><strong>{data.account.positionCount} / 5</strong><small>最高曝險 {data.market.maximumExposurePercent.toFixed(0)}%</small></article>
      <article><span>現金</span><strong>{plainMoney(data.account.cash)}</strong><small>股票市值 {plainMoney(data.account.marketValue)}</small></article>
      <article><span>總資產</span><strong>{plainMoney(data.account.totalEquity)}</strong><small className={pnlClass(data.account.cumulativePnl)}>{money(data.account.cumulativePnl)}</small></article>
      <article><span>累積報酬</span><strong className={pnlClass(data.account.returnPercent)}>{percent(data.account.returnPercent)}</strong><small>今日 {money(data.account.todayPnl)}</small></article>
      <article><span>已實現／未實現</span><strong className={pnlClass(data.account.realizedPnl)}>{money(data.account.realizedPnl)}</strong><small className={pnlClass(data.account.unrealizedPnl)}>未實現 {money(data.account.unrealizedPnl)}</small></article>
      <article><span>歷史勝率</span><strong>{data.performance.winRate.toFixed(1)}%</strong><small>{data.performance.winningTrades} 勝／{data.performance.losingTrades} 敗</small></article>
      <article><span>Profit Factor</span><strong>{data.performance.profitFactor?.toFixed(2) ?? "—"}</strong><small>Expectancy {percent(data.performance.expectancyPercent)}</small></article>
      <article><span>最大回撤</span><strong className="loss">{percent(data.performance.maximumDrawdownPercent)}</strong><small>依每日資產高點計算</small></article>
    </section>

    <div className="rocket-market-grid"><section className="rocket-panel"><div className="rocket-title"><Activity size={17} /><div><h2>市場狀態</h2><p>動態決定最大資金曝險</p></div><b>{data.market.score.toFixed(0)} / 100</b></div><div className="rocket-regime-body"><strong>{data.market.label}</strong><span>最大曝險 {data.market.maximumExposurePercent.toFixed(0)}%</span>{data.market.reasons.map((reason) => <p key={reason}>✓ {reason}</p>)}</div></section>
      <section className="rocket-panel"><div className="rocket-title"><TrendingUp size={17} /><div><h2>TOP 5 強勢族群</h2><p>價格、量能、寬度、法人與大戶</p></div></div><div className="rocket-sectors">{data.sectors.map((sector) => <article key={sector.name}><b>#{sector.rank}</b><strong>{sector.name}</strong><span>{sector.score.toFixed(1)}</span><small>1日 {sector.return1d?.toFixed(1) ?? "—"}%・5日 {sector.return5d?.toFixed(1) ?? "—"}%</small></article>)}</div></section></div>

    <section className="rocket-panel"><div className="rocket-title"><Trophy size={17} /><div><h2>今日飆股 TOP 5</h2><p>必須同時通過 Rocket、CHASE Risk、突破量能與 RR；不為湊滿五檔降標準</p></div></div>{data.top5.length ? <div className="rocket-top5">{data.top5.map((item) => <button key={item.id} onClick={() => setSelected(item)}><i>#{item.rank}</i><div><strong>{item.stockCode} {item.stockName}</strong><span>{item.sectorName} #{item.sectorRank}・{item.patternType}</span></div><b>{item.rocketScore.toFixed(1)}</b><small className={item.chaseRiskScore >= 60 ? "loss" : "profit"}>CHASE {item.chaseRiskScore.toFixed(0)}</small><em>{item.statusLabel}</em></button>)}</div> : <div className="rocket-empty"><ShieldAlert size={28} /><strong>今日無符合風險報酬條件的飆股，維持現金。</strong><span>系統不會為了湊滿 5 檔而降低進場門檻。</span></div>}</section>

    <section className="rocket-panel"><div className="rocket-title"><Crosshair size={17} /><div><h2>飆股觀察池 TOP 20</h2><p>不是今日漲幅排行；優先尋找整理完成、量價確認但尚未過熱的標的</p></div></div>{data.candidates.length ? <div className="rocket-table-wrap"><table><thead><tr><th>排名</th><th>股票</th><th>價格／漲跌</th><th>Rocket</th><th>族群</th><th>型態</th><th>量比</th><th>法人／大戶</th><th>CHASE</th><th>突破／停損</th><th>RR</th><th>狀態</th></tr></thead><tbody>{data.candidates.map((item) => <tr key={item.id} onClick={() => setSelected(item)}><td>#{item.rank}</td><td><strong>{item.stockCode} {item.stockName}</strong><small>{item.marketType}</small></td><td>{price(item.currentPrice)}<small className={pnlClass(item.changePercent)}>{percent(item.changePercent)}</small></td><td><strong className="rocket-score">{item.rocketScore.toFixed(1)}</strong></td><td>{item.sectorName}<small>#{item.sectorRank}</small></td><td>{item.patternType}</td><td>{item.volumeRatio.toFixed(2)}X</td><td>{item.scoreBreakdown["法人"]?.toFixed(1) ?? "暫無"}／{item.scoreBreakdown["籌碼強度"]?.toFixed(1) ?? "暫無"}</td><td className={item.chaseRiskScore >= 60 ? "loss" : ""}>{item.chaseRiskScore.toFixed(0)}</td><td>{price(item.breakoutPrice)}<small className="loss">Stop {price(item.stopLossPrice)}</small></td><td>{item.riskRewardRatio.toFixed(2)}</td><td><span className={`rocket-status ${item.status}`}>{item.statusLabel}</span></td></tr>)}</tbody></table></div> : <div className="rocket-empty">{data.candidateMessage}</div>}</section>

    <section className="rocket-panel"><div className="rocket-title"><Target size={17} /><div><h2>目前持倉</h2><p>持倉不會再次成為新買進推薦，只能加碼、減碼、停利或出場</p></div></div>{data.positions.length ? <div className="rocket-table-wrap"><table><thead><tr><th>股票</th><th>均價／現價</th><th>股數</th><th>成本／市值</th><th>未實現損益</th><th>最高浮盈</th><th>Stop／Trailing</th><th>持有</th><th>Rocket變化</th><th>操作建議</th></tr></thead><tbody>{data.positions.map((item) => <tr key={item.id}><td><strong>{item.stockCode} {item.stockName}</strong></td><td>{price(item.averageCost)}<small>{price(item.currentPrice)}</small></td><td>{item.quantity.toLocaleString("zh-TW")}</td><td>{plainMoney(item.cost)}<small>{plainMoney(item.marketValue)}</small></td><td className={pnlClass(item.unrealizedPnl)}>{money(item.unrealizedPnl)}<small>{percent(item.returnPercent)}</small></td><td className="profit">{money(item.highestProfit)}</td><td>{price(item.stopLoss)}<small>{item.trailingStop ? price(item.trailingStop) : "尚未啟動"}</small></td><td>{item.holdingDays} 天<small>加碼階段 {item.addStage}/3</small></td><td>{item.rocketScoreEntry.toFixed(0)} → {item.rocketScoreCurrent.toFixed(0)}</td><td>{item.latestAction}</td></tr>)}</tbody></table></div> : <div className="rocket-empty compact">目前沒有模擬持倉，保留現金等待正式突破。</div>}</section>

    <section className="rocket-panel"><div className="rocket-title"><Gauge size={17} /><div><h2>策略績效</h2><p>勝率只計算已完成交易；Realized 與 Unrealized 分開</p></div></div><div className="rocket-performance-grid">{[
      ["總交易", data.performance.totalTrades], ["勝率", `${data.performance.winRate.toFixed(1)}%`], ["平均獲利", percent(data.performance.averageWinPercent)], ["平均虧損", percent(data.performance.averageLossPercent)], ["盈虧比", data.performance.payoffRatio?.toFixed(2) ?? "—"], ["Profit Factor", data.performance.profitFactor?.toFixed(2) ?? "—"], ["Expected Value", percent(data.performance.expectancyPercent)], ["最大連勝／連敗", `${data.performance.maximumWinningStreak}／${data.performance.maximumLosingStreak}`], ["最大回撤", percent(data.performance.maximumDrawdownPercent)], ["總報酬", percent(data.performance.totalReturnPercent)],
    ].map(([label, value]) => <article key={String(label)}><span>{label}</span><strong>{value}</strong></article>)}</div><div className="rocket-backtest"><strong>策略回測</strong><select value={backtestPeriod} onChange={(event) => setBacktestPeriod(event.target.value)}><option value="1m">近1個月</option><option value="3m">近3個月</option><option value="6m">近6個月</option><option value="1y">近1年</option><option value="2y">近2年</option><option value="all">全部</option></select><button onClick={() => void runBacktest()} disabled={backtesting}>{backtesting ? <span className="spinner small" /> : "執行無前視驗證"}</button>{backtest && <span className={backtest.status === "completed" ? "profit" : ""}>{backtest.status === "completed" ? `${backtest.tradeCount} 筆・報酬 ${percent(backtest.totalReturnPercent ?? 0)}・勝率 ${(backtest.winRate ?? 0).toFixed(1)}%` : backtest.message}</span>}</div></section>

    <section className="rocket-panel"><div className="rocket-title"><LineChart size={17} /><div><h2>每日資產曲線</h2><p>每日保存 Cash、Market Value、Total Equity、Daily P&L 與高點回撤</p></div></div><EquityCurve rows={data.equityCurve} /></section>

    <section className="rocket-panel"><div className="rocket-title"><Activity size={17} /><div><h2>勝率與策略分析</h2><p>蒐集真實模擬績效，不讓 AI 自行修改規則</p></div></div><div className="rocket-subtabs"><button className={analysis === "strategy" ? "active" : ""} onClick={() => setAnalysis("strategy")}>策略拆分</button><button className={analysis === "score" ? "active" : ""} onClick={() => setAnalysis("score")}>Score區間</button><button className={analysis === "holding" ? "active" : ""} onClick={() => setAnalysis("holding")}>持有天數</button><button className={analysis === "regime" ? "active" : ""} onClick={() => setAnalysis("regime")}>市場環境</button></div><AnalyticsTable rows={analyticsRows} label={analyticsLabel} /></section>

    <section className="rocket-panel"><div className="rocket-title"><CircleDollarSign size={17} /><div><h2>交易紀錄 Trade History</h2><p>完成交易摘要與每一筆買進、加碼、減碼、停利、停損、費用及稅</p></div><select value={tradeFilter} onChange={(event) => setTradeFilter(event.target.value)}><option value="">全部交易</option><option value="BUY">買進</option><option value="ADD">加碼</option><option value="REDUCE">減碼</option><option value="TAKE_PROFIT">停利</option><option value="STOP_LOSS">停損</option><option value="SELL">賣出</option></select></div>
      {data.completedTrades.length > 0 && <><h3 className="rocket-ledger-title">已完成交易（勝率統計來源）</h3><div className="rocket-table-wrap"><table><thead><tr><th>股票</th><th>訊號／出場</th><th>策略／市場</th><th>均價／出場</th><th>股數／金額</th><th>持有</th><th>損益</th><th>最高／最低</th><th>最大浮盈／浮虧</th><th>出場原因</th></tr></thead><tbody>{data.completedTrades.map((item) => <tr key={item.id}><td><strong>{item.stockCode} {item.stockName}</strong><small>{item.sectorName}・Rocket {item.rocketScore.toFixed(0)}</small></td><td>{item.signalDate}<small>{item.exitDate}</small></td><td>{item.strategyType}<small>{item.marketRegime}</small></td><td>{price(item.averageCost)}<small>{price(item.exitPrice)}</small></td><td>{item.quantity.toLocaleString("zh-TW")} 股<small>{plainMoney(item.investedAmount)}</small></td><td>{item.holdingDays} 天</td><td className={pnlClass(item.profit)}>{money(item.profit)}<small>{percent(item.returnPercent)}</small></td><td>{price(item.highestPrice)}<small>{price(item.lowestPrice)}</small></td><td className={pnlClass(item.maximumFavorableExcursion)}>{money(item.maximumFavorableExcursion)}<small className="loss">{money(item.maximumAdverseExcursion)}</small></td><td>{item.exitReason}</td></tr>)}</tbody></table></div></>}
      <h3 className="rocket-ledger-title">成交明細</h3>{filteredTrades.length ? <div className="rocket-table-wrap"><table><thead><tr><th>時間</th><th>股票</th><th>動作</th><th>策略</th><th>價格</th><th>股數</th><th>成交金額</th><th>手續費／稅</th><th>已實現損益</th><th>原因</th></tr></thead><tbody>{filteredTrades.map((item) => <tr key={item.id}><td>{new Date(item.timestamp).toLocaleString("zh-TW", { hour12: false })}</td><td>{item.stockCode} {item.stockName}</td><td><span className={`event-pill ${eventClass(item.action)}`}>{item.action}</span></td><td>{item.strategyType}</td><td>{price(item.price)}</td><td>{item.quantity.toLocaleString("zh-TW")}</td><td>{plainMoney(item.grossAmount)}</td><td>{plainMoney(item.fee)}／{plainMoney(item.tax)}</td><td className={pnlClass(item.realizedPnl)}>{money(item.realizedPnl)}</td><td>{item.reason}</td></tr>)}</tbody></table></div> : <div className="rocket-empty compact">尚無交易紀錄。</div>}</section>

    <section className="rocket-settings"><div><ShieldAlert size={17} /><span><strong>模擬交易成本設定</strong><small>買賣已納入手續費、證交稅與滑價</small></span></div><label>手續費折數<input type="number" min="0" max="1" step="0.05" value={feeDiscount} onChange={(event) => setFeeDiscount(Number(event.target.value))} /></label><label>滑價率<input type="number" min="0" max="0.02" step="0.0005" value={slippage} onChange={(event) => setSlippage(Number(event.target.value))} /></label><label><input type="checkbox" checked={soundEnabled} onChange={(event) => setSoundEnabled(event.target.checked)} />網頁通知音效</label><button onClick={() => void saveSettings()}>儲存設定</button></section>

    <section className="rocket-panel rocket-message-center"><div className="rocket-title"><BellRing size={18} /><div><h2>🔔 飆股雷達訊息中心</h2><p>所有事件保存於資料庫；最新 → 最舊</p></div><b>今日訊息 {messages.length}・未讀 {data.unreadCount}</b></div><div className="rocket-message-controls"><div>{[["全部", ""], ["買進", "BUY"], ["加碼", "ADD"], ["減碼", "REDUCE"], ["停利", "TAKE_PROFIT"], ["停損", "STOP_LOSS"], ["市場", "MARKET"], ["警告", "WARNING"]].map(([label, value]) => <button key={label} className={messageFilter === value ? "active" : ""} onClick={() => setMessageFilter(value)}>{label}</button>)}</div><select value={messagePeriod} onChange={(event) => setMessagePeriod(event.target.value)}><option value="today">今日</option><option value="3d">近3日</option><option value="7d">近7日</option><option value="30d">近30日</option><option value="all">全部</option></select></div><div className="rocket-messages">{messages.length ? messages.map((item) => <article key={item.notificationId} className={`${eventClass(item.notificationType)} ${item.isRead ? "read" : "unread"}`} onClick={() => !item.isRead && void markRead(item.notificationId)}><time>{time(item.timestamp)}</time><span>{eventIcon(item.notificationType)}</span><div><strong>{item.notificationType}｜{item.stockCode} {item.stockName}</strong><p>{item.message}</p><small>{item.reason}</small></div>{!item.isRead && <i>未讀</i>}</article>) : <div className="rocket-empty compact"><Bell size={22} />此篩選條件尚無訊息。</div>}</div></section>

    {selected && <CandidateDetail item={selected} data={data} onClose={() => setSelected(null)} onSelectStock={onSelectStock} />}
  </div>;
}
