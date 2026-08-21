"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Activity, BellRing, Eye, Radio, RefreshCw, ShieldAlert, X } from "lucide-react";
import { streamRetryDelay } from "@/lib/day-trading-engine";
import type {
  DayTradingAlert, DayTradingPerformance, DayTradingPosition, DayTradingSignal,
  DayTradingTrade, LineTradeDelivery, MarketRegime,
} from "@/lib/day-trading-types";
import { dayTradingClient } from "@/services/day-trading-client";
import { useDayTradingStore } from "@/stores/day-trading-store";
import {
  AlertCenter, DayTradingDisclaimer, EmergencyExitModal,
  DayTradingPerformancePanel, LiveSignalCard, MarketDataDelayBadge, MarketRegimeCard, PositionMonitorCard,
  StreamConnectionStatus, TradeTimeline,
} from "./DayTradingComponents";
import { LineNotificationPanel } from "./LineNotificationPanel";

const EVENT_TYPES = [
  "market_update", "quote_update", "new_signal", "signal_update", "signal_expired",
  "position_update", "exit_warning", "emergency_exit", "data_delay", "data_disconnected",
];
const AUTOMATION_PERFORMANCE_USER_ID = "system-automation";

function getUserId() {
  let id = localStorage.getItem("moneymoney-user-id");
  if (!id) {
    id = globalThis.crypto?.randomUUID?.() ?? `local-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    localStorage.setItem("moneymoney-user-id", id);
  }
  return id;
}

function currentTaipeiMonth() {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Taipei", year: "numeric", month: "2-digit",
  }).formatToParts(new Date());
  const year = parts.find((part) => part.type === "year")?.value;
  const month = parts.find((part) => part.type === "month")?.value;
  return `${year}-${month}`;
}

const displayNumber = (value: number, digits = 2) => value.toLocaleString("zh-TW", {
  minimumFractionDigits: digits, maximumFractionDigits: digits,
});
const displayLotsAndShares = (lots?: number) => lots != null && Number.isFinite(lots)
  ? `${lots.toLocaleString("zh-TW", { maximumFractionDigits: 3 })} 張（${Math.round(lots * 1_000).toLocaleString("zh-TW")} 股）`
  : "張數資料不足";
const displayTime = (value: string) => new Date(value).toLocaleString("zh-TW", {
  hour12: false, timeZone: "Asia/Taipei",
});

type StrategyAllocation = NonNullable<DayTradingSignal["strategyAllocations"]>[string];

function StrategyAllocationCell({
  allocation, direction,
}: {
  allocation?: StrategyAllocation;
  direction: DayTradingSignal["direction"];
}) {
  const quantity = allocation?.quantityLots ?? 0;
  const allocatedCapital = allocation?.allocatedCapital ?? 0;
  return <div className="dt-strategy-allocation">
    <b className={quantity > 0 ? (direction === "long" ? "text-up" : "text-down") : ""}>
      {quantity > 0 ? displayLotsAndShares(quantity) : "未新增部位"}
    </b>
    <span>{allocation?.status ?? "此策略當時尚未啟用"}</span>
    {allocatedCapital > 0 && <small>模擬占用 {allocatedCapital.toLocaleString("zh-TW")} 元</small>}
  </div>;
}

export function DayTradingDashboard() {
  const {
    regime, signals, positions, alerts, trades, performance, connection, emergency,
    setInitial, setConnection, handleEvent, dismissEmergency,
  } = useDayTradingStore();
  const [userId, setUserId] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [lineToasts, setLineToasts] = useState<LineTradeDelivery[]>([]);
  const [monitored, setMonitored] = useState<string[]>([]);
  const [performancePositions, setPerformancePositions] = useState<DayTradingPosition[]>([]);
  const [todaySignals, setTodaySignals] = useState<DayTradingSignal[]>([]);
  const [selectedSignalId, setSelectedSignalId] = useState<string | null>(null);
  const [selectedPositionId, setSelectedPositionId] = useState<number | null>(null);
  const [performanceMonth] = useState(currentTaipeiMonth);
  const reconnectAttempt = useRef(0);
  const lineToastTimers = useRef<number[]>([]);

  const refresh = useCallback(async (id: string, prefetchedRegime?: unknown) => {
    const [regimeData, signalData, todaySignalData, positionData, alertData, performancePositionData, automationAlertData, tradeData, performanceData] = await Promise.all([
      prefetchedRegime ?? dayTradingClient.regime(id), dayTradingClient.signals(id), dayTradingClient.todaySignals(id),
      dayTradingClient.positions(id),
      dayTradingClient.alerts(id), dayTradingClient.positions(AUTOMATION_PERFORMANCE_USER_ID),
      dayTradingClient.alerts(AUTOMATION_PERFORMANCE_USER_ID),
      dayTradingClient.trades(AUTOMATION_PERFORMANCE_USER_ID, performanceMonth),
      dayTradingClient.performance(AUTOMATION_PERFORMANCE_USER_ID, performanceMonth),
    ]);
    const automaticPositions = (performancePositionData.items as DayTradingPosition[])
      .map((item) => ({ ...item, automaticTracking: true, automationStrategy: "fixed_2_lots", automationStrategyLabel: "原版固定 2 張" }));
    const manualPositions = (positionData.items as DayTradingPosition[])
      .map((item) => ({ ...item, automaticTracking: false }));
    const combinedPositions = [...automaticPositions, ...manualPositions]
      .filter((item, index, rows) => rows.findIndex((candidate) => candidate.id === item.id) === index);
    const automaticAlerts = (automationAlertData.items as DayTradingAlert[])
      .map((item) => ({ ...item, automaticTracking: true, automationStrategy: "fixed_2_lots", automationStrategyLabel: "原版固定 2 張" }));
    const manualAlerts = (alertData.items as DayTradingAlert[])
      .map((item) => ({ ...item, automaticTracking: false }));
    setTodaySignals(todaySignalData.items as DayTradingSignal[]);
    setPerformancePositions(automaticPositions);
    setInitial({
      regime: regimeData as MarketRegime,
      signals: signalData.items as DayTradingSignal[],
      candidates: [],
      positions: combinedPositions,
      alerts: [...automaticAlerts, ...manualAlerts]
        .filter((item, index, rows) => rows.findIndex((candidate) => candidate.id === item.id) === index)
        .sort((left, right) => right.id - left.id),
      trades: tradeData.items as DayTradingTrade[],
      performance: performanceData as DayTradingPerformance,
    });
  }, [performanceMonth, setInitial]);

  const initialize = useCallback(async (id: string) => {
    setError("");
    setLoading(true);
    try {
      const regimeData = await dayTradingClient.regime(id);
      setInitial({ regime: regimeData as MarketRegime });
      setLoading(false);
      await refresh(id, regimeData);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "當沖資料暫時無法連線");
    } finally {
      setLoading(false);
    }
  }, [refresh, setInitial]);

  useEffect(() => {
    const id = getUserId();
    setUserId(id);
    setMonitored(JSON.parse(localStorage.getItem("day-trading-monitored") ?? "[]") as string[]);
    void initialize(id);
  }, [initialize]);

  useEffect(() => {
    if (!userId) return;
    let disposed = false;
    let source: EventSource | null = null;
    let reconnectTimer: number | null = null;
    const connect = () => {
      if (disposed) return;
      setConnection(reconnectAttempt.current ? "reconnecting" : "connecting");
      source = new EventSource(`/api/day-trading/stream?user_id=${encodeURIComponent(userId)}`);
      source.onopen = () => {
        reconnectAttempt.current = 0;
        setConnection("connected");
      };
      EVENT_TYPES.forEach((type) => source?.addEventListener(type, (event) => {
        const message = event as MessageEvent;
        try {
          handleEvent(type, message.lastEventId, JSON.parse(message.data));
        } catch {
          setError("收到格式異常的即時事件，已忽略。");
        }
      }));
      source.onerror = () => {
        source?.close();
        setConnection("reconnecting");
        const delay = streamRetryDelay(reconnectAttempt.current++);
        reconnectTimer = window.setTimeout(connect, delay);
      };
    };
    connect();
    return () => {
      disposed = true;
      source?.close();
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
    };
  }, [userId, handleEvent, setConnection]);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(""), 3500);
    return () => window.clearTimeout(timer);
  }, [toast]);

  useEffect(() => () => {
    lineToastTimers.current.forEach((timer) => window.clearTimeout(timer));
  }, []);

  const handleLineTradeDelivery = useCallback((delivery: LineTradeDelivery) => {
    setLineToasts((current) => [delivery, ...current.filter((item) => item.id !== delivery.id)].slice(0, 4));
    const timer = window.setTimeout(() => {
      setLineToasts((current) => current.filter((item) => item.id !== delivery.id));
    }, 6_000);
    lineToastTimers.current.push(timer);
  }, []);

  const loadPortfolio = useCallback(async () => {
    if (!userId) return;
    const [positionData, alertData, todaySignalData, performancePositionData, automationAlertData, tradeData, performanceData] = await Promise.all([
      dayTradingClient.positions(userId), dayTradingClient.alerts(userId),
      dayTradingClient.todaySignals(userId),
      dayTradingClient.positions(AUTOMATION_PERFORMANCE_USER_ID),
      dayTradingClient.alerts(AUTOMATION_PERFORMANCE_USER_ID),
      dayTradingClient.trades(AUTOMATION_PERFORMANCE_USER_ID, performanceMonth),
      dayTradingClient.performance(AUTOMATION_PERFORMANCE_USER_ID, performanceMonth),
    ]);
    const automaticPositions = (performancePositionData.items as DayTradingPosition[])
      .map((item) => ({ ...item, automaticTracking: true, automationStrategy: "fixed_2_lots", automationStrategyLabel: "原版固定 2 張" }));
    const manualPositions = (positionData.items as DayTradingPosition[])
      .map((item) => ({ ...item, automaticTracking: false }));
    const automaticAlerts = (automationAlertData.items as DayTradingAlert[])
      .map((item) => ({ ...item, automaticTracking: true, automationStrategy: "fixed_2_lots", automationStrategyLabel: "原版固定 2 張" }));
    const manualAlerts = (alertData.items as DayTradingAlert[])
      .map((item) => ({ ...item, automaticTracking: false }));
    setTodaySignals(todaySignalData.items as DayTradingSignal[]);
    setPerformancePositions(automaticPositions);
    setInitial({
      positions: [...automaticPositions, ...manualPositions]
        .filter((item, index, rows) => rows.findIndex((candidate) => candidate.id === item.id) === index),
      alerts: [...automaticAlerts, ...manualAlerts]
        .filter((item, index, rows) => rows.findIndex((candidate) => candidate.id === item.id) === index)
        .sort((left, right) => right.id - left.id),
      trades: tradeData.items as DayTradingTrade[],
      performance: performanceData as DayTradingPerformance,
    });
  }, [performanceMonth, setInitial, userId]);

  useEffect(() => {
    if (!userId) return;
    const timer = window.setInterval(() => {
      void loadPortfolio().catch((reason) => {
        setError(reason instanceof Error ? reason.message : "績效更新失敗");
      });
    }, 30_000);
    return () => window.clearInterval(timer);
  }, [loadPortfolio, userId]);

  const monitor = (signal: DayTradingSignal) => {
    if (monitored.includes(signal.id)) {
      setToast(`${signal.symbol} 已在監控清單`);
      return;
    }
    const next = [...monitored, signal.id];
    setMonitored(next);
    localStorage.setItem("day-trading-monitored", JSON.stringify(next));
    setToast(`${signal.symbol} ${signal.stockName} 已加入當沖監控`);
  };

  const simulate = async (signal: DayTradingSignal) => {
    try {
      await dayTradingClient.createPosition(userId, signal.id, signal.direction, signal.price, 1);
      setToast(`已建立 ${signal.symbol} 的模擬${signal.direction === "long" ? "多單" : "空單"}，沒有送出真實委託`);
      await loadPortfolio();
    } catch (reason) {
      setToast(reason instanceof Error ? reason.message : "無法建立模擬持倉");
    }
  };

  const closePosition = async (position: DayTradingPosition, percentage: number) => {
    const action = position.direction === "long" ? "賣出" : "回補";
    if (!window.confirm(`確認模擬${action} ${position.symbol} 的 ${percentage}% 部位？\n這不會送出真實委託。`)) return;
    await dayTradingClient.closePosition(userId, position.id, percentage, `使用者手動確認${action}`);
    setToast(`已完成模擬${action} ${percentage}%`);
    await loadPortfolio();
  };

  const updatePosition = async (position: DayTradingPosition, body: Record<string, unknown>) => {
    await dayTradingClient.updatePosition(userId, position.id, body);
    setToast("模擬持倉設定已更新");
    await loadPortfolio();
  };

  if (loading) return <div className="dt-loading"><span className="spinner" /><h2>正在啟動 AI 當沖多空機器人</h2><p>連接市場行情、正式訊號與持倉資料…</p></div>;
  if (!regime) return <div className="dt-loading dt-load-error"><ShieldAlert /><h2>當沖資料暫時無法連線</h2><p>{error || "伺服器沒有回傳市場狀態，請稍後重試。"}</p><button type="button" onClick={() => void initialize(userId)} disabled={!userId}><RefreshCw size={16} />重新連線</button></div>;

  const regimeTone = regime.score >= 65 ? "breakout"
    : regime.score <= 35 ? "crash"
      : regime.environmentScore >= 60 ? "recovery" : "range";
  const selectedSignal = signals.find((item) => item.id === selectedSignalId) ?? null;
  const selectedPosition = positions.find((item) => item.id === selectedPositionId) ?? null;

  return <div className="adaptive-page day-trading-page day-trading-adaptive">
    <DayTradingDisclaimer mode={regime.mode} notice={regime.dataNotice} />
    {error && <div className="dt-error"><AlertTriangleIcon />{error}<button onClick={() => { setError(""); void refresh(userId); }}><RefreshCw />重試</button></div>}
    {toast && <div className="dt-toast" role="status"><Activity />{toast}</div>}
    {lineToasts.length > 0 && <div className="dt-line-toast-stack" aria-live="polite">
      {lineToasts.map((delivery) => <div className={`dt-line-toast side-${delivery.side}`} role="status" key={delivery.id}>
        <span className="dt-line-toast-icon"><BellRing /></span>
        <div><strong>LINE {delivery.sideLabel}通知已送出</strong><span>{delivery.symbol || "當沖訊號"}・{delivery.action}</span><small>{displayTime(delivery.sentAt)}</small></div>
        <button type="button" aria-label="關閉通知" onClick={() => setLineToasts((current) => current.filter((item) => item.id !== delivery.id))}><X /></button>
      </div>)}
    </div>}
    <EmergencyExitModal event={emergency} onDismiss={dismissEmergency} />

    <section className="adaptive-heading dt-hero">
      <div><span className="eyebrow"><Radio size={13} /> {regime.mode === "official" ? "MARKET DATA · SIGNALS ONLY" : regime.mode === "warming_up" ? "MARKET DATA WARMING UP" : "MOCK STREAMING · SIGNALS ONLY"}</span><h1>AI 當沖多空機器人</h1><p>共用大單動能雷達 {regime.supervisor?.candidateUniverseCount ?? 300} 檔・行情覆蓋 {regime.supervisor?.quoteCoverageCount ?? 0}/{regime.supervisor?.candidateUniverseCount ?? 300}・三關價 {regime.supervisor?.threeGateCoverageCount ?? 0}・5 分 K 完成 {regime.supervisor?.warmedSymbolCount ?? 0}・高頻追蹤 {regime.supervisor?.highFrequencyTrackingCount ?? 0}</p></div>
      <div className="day-trading-heading-actions"><div className="dt-hero-status"><StreamConnectionStatus status={connection} /><MarketDataDelayBadge seconds={regime.dataDelaySeconds} status={regime.dataStatus} /></div><button onClick={() => void refresh(userId)} disabled={!userId}><RefreshCw size={16} />更新</button></div>
    </section>

    {regime.marketOpen && regime.dataStatus !== "normal" && <div className="data-anomaly-banner"><ShieldAlert /><div><strong>行情已延遲 {regime.dataDelaySeconds} 秒</strong><span>目前停止產生新進場訊號，請勿依賴舊報價進行交易；既有持倉仍持續檢查出場風險。</span></div></div>}
    {(regime.automation.phase === "warmup" || (regime.automation.phase === "scanning" && (regime.supervisor?.warmedSymbolCount ?? 0) === 0)) && <div className="automation-banner phase-warmup"><Activity /><div><strong>多空動能掃描中</strong><span>09:00 開盤即開始多空、量能與大單掃描；09:05 起取得首根完整 5 分 K，通過風控才通知正式買進或放空。</span></div></div>}
    <section className={`regime-hero day-trading-regime ${regimeTone}`}>
      <div className="regime-light" />
      <div><small>盤中市場與機器人狀態</small><strong>{regime.directionLabel} · {regime.automation.robotStatus}</strong><p>{regime.recommendationSummary || regime.automation.statusMessage}</p><small>資料來源：{regime.dataSource} · 更新：{new Date(regime.updatedAt).toLocaleTimeString("zh-TW", { hour12: false })}</small></div>
      <div className="regime-stat"><span>多空分數</span><b>{regime.score} / 100</b></div>
      <div className="regime-stat"><span>當沖環境</span><b>{regime.environmentScore} · {regime.environmentLabel}</b></div>
      <div className="regime-stat"><span>推薦方向</span><b>{regime.preferredDirection}</b></div>
      <div className="regime-stat"><span>本小時精選</span><b>{signals.length} / {regime.maximumRecommendations} 檔</b></div>
      <div className="regime-stat"><span>機器人自動持倉</span><b>{performancePositions.length} 筆</b></div>
    </section>

    <div className="adaptive-grid dt-dashboard-grid day-trading-overview-grid single">
      <MarketRegimeCard regime={regime} />
    </div>

    <DayTradingPerformancePanel performance={performance} positions={performancePositions} trades={trades} />

    <section className="adaptive-table-card positions-section">
      <div className="table-title"><div><h2>當沖持倉監控</h2><p>顯示原版固定 2 張機器人持倉與手動模擬持倉。</p></div><span>{positions.length} 筆</span></div>
      {positions.length ? <div className="adaptive-table-wrap"><table><thead><tr><th>策略帳本</th><th>股票</th><th>方向</th><th>進場資訊</th><th>目前價</th><th>未實現盈虧</th><th>停損</th><th>目標價</th><th>健康／狀態</th><th>操作</th></tr></thead><tbody>{positions.map((position) => <tr key={position.id}><td><b>{position.automationStrategyLabel ?? "手動模擬"}</b><span>{position.automaticTracking ? "原版機器人帳本" : "個人模擬帳本"}</span></td><td><b>{position.symbol}</b><span>{position.stockName}</span></td><td className={position.direction === "long" ? "text-up" : "text-down"}>{position.direction === "long" ? "做多" : "放空"}</td><td><b>{displayNumber(position.entryPrice)}</b><span>{displayTime(position.openedAt)}</span></td><td>{displayNumber(position.currentPrice)}</td><td className={position.unrealizedProfit >= 0 ? "text-up" : "text-down"}><b>{displayNumber(position.unrealizedProfit, 0)} 元</b><span>{displayNumber(position.returnPercentage)}%</span></td><td>{displayNumber(position.stopLoss)}<span>{position.trailingStop == null ? "未設移動停損" : `移動 ${displayNumber(position.trailingStop)}`}</span></td><td>{displayNumber(position.target1)}<span>{displayNumber(position.target2)}</span></td><td><b>{position.healthScore}</b><span>{position.latestAction}</span></td><td><div className="row-actions">{position.automaticTracking ? <span className="candidate-status confirmed">機器人自動監控</span> : <button onClick={() => setSelectedPositionId(position.id)}><Eye size={14} />管理</button>}</div></td></tr>)}</tbody></table></div> : <div className="adaptive-empty">{(performance?.today?.tradeCount ?? 0) > 0 ? `今日機器人已完成 ${performance?.today?.tradeCount ?? 0} 筆出場，目前無未平倉部位。` : "目前尚無模擬持倉，系統持續掃描中。"}</div>}
    </section>

    <section className="adaptive-table-card live-signal-section">
      <div className="table-title"><div><h2>本小時 AI 當沖精選</h2><p>每小時最多 {regime.maximumRecommendations} 檔；不會為湊滿名額降低風控門檻。</p></div><span>{signals.length}／{regime.maximumRecommendations} 檔</span></div>
      {signals.length ? <div className="adaptive-table-wrap"><table><thead><tr><th>排名</th><th>股票</th><th>方向／三關價</th><th>評分</th><th>目前價</th><th>進場區間</th><th>停損／目標</th><th>風報比／大單</th><th>狀態</th><th>操作</th></tr></thead><tbody>{signals.map((signal) => <tr key={signal.id}><td>#{signal.rank}</td><td><b>{signal.symbol}</b><span>{signal.stockName} · {signal.market}</span></td><td><b className={signal.direction === "long" ? "text-up" : "text-down"}>{signal.direction === "long" ? "做多" : "放空"}</b><span>{signal.threeGateEntryStatus ?? signal.threeGateStatus ?? signal.action}</span></td><td><b>{signal.confidenceScore}</b><span>健康 {signal.healthScore}</span></td><td>{displayNumber(signal.price)}<span>{signal.changePercent >= 0 ? "+" : ""}{displayNumber(signal.changePercent)}%</span></td><td>{displayNumber(signal.entryMin)}～{displayNumber(signal.entryMax)}<span>{signal.vwapStatus}</span></td><td>{displayNumber(signal.stopLoss)}<span>{displayNumber(signal.target1)}／{displayNumber(signal.target2)}</span></td><td>{displayNumber(signal.riskRewardRatio)}<span>{signal.largeOrderStatus ?? `${displayNumber(signal.largeOrderForce, 0)} 分`}</span></td><td><span className={`candidate-status ${signal.status}`}>{signal.recommendationLabel || signal.status}</span></td><td><div className="row-actions"><button onClick={() => setSelectedSignalId(signal.id)}><Eye size={14} />詳情</button><button onClick={() => monitor(signal)}>監控</button><button onClick={() => void simulate(signal)}>模擬</button></div></td></tr>)}</tbody></table></div> : <div className="adaptive-empty">{regime.automation.phase === "scanning" ? "目前沒有符合風控標準的交易機會，建議觀望。" : regime.automation.statusMessage}</div>}
    </section>

    <section className="adaptive-table-card live-signal-section">
      <div className="table-title">
        <div><h2>今日全部正式訊號</h2><p>原版策略每次正式訊號固定模擬 2 張；0 張代表重複確認而未新增部位。</p></div>
        <span>{todaySignals.length} 檔</span>
      </div>
      {todaySignals.length ? <div className="adaptive-table-wrap"><table><thead><tr><th>推薦時間</th><th>股票／方向</th><th>原版固定 2 張</th><th>評分</th><th>進場點位</th><th>出場點位（停損／停利）</th></tr></thead><tbody>{todaySignals.map((signal) => {
        const fixedAllocation = signal.strategyAllocations?.fixed_2_lots;
        return <tr key={signal.id}><td>{displayTime(signal.recommendedAt ?? signal.generatedAt)}</td><td><b>{signal.symbol} {signal.stockName}</b><span className={signal.direction === "long" ? "text-up" : "text-down"}>{signal.direction === "long" ? "做多（買進後賣出）" : "放空（先賣後回補）"} · {signal.market}</span></td><td><StrategyAllocationCell allocation={fixedAllocation} direction={signal.direction} /></td><td><b>{signal.confidenceScore}</b><span>健康 {signal.healthScore}</span></td><td><b>{signal.direction === "long" ? "買進區" : "放空區"} {displayNumber(signal.entryMin)}～{displayNumber(signal.entryMax)}</b><span>訊號價 {displayNumber(signal.price)}</span></td><td><b>{signal.direction === "long" ? "停損賣出" : "停損回補"} {displayNumber(signal.stopLoss)}</b><span>{signal.direction === "long" ? "停利賣出" : "停利回補"} {displayNumber(signal.target1)}／{displayNumber(signal.target2)}</span></td></tr>;
      })}</tbody></table></div> : <div className="adaptive-empty">今天尚無正式當沖訊號。</div>}
    </section>

    <div className="dt-bottom-grid single">
      <AlertCenter alerts={alerts} onRead={(id) => {
        const alert = alerts.find((item) => item.id === id);
        const alertUserId = alert?.automaticTracking ? AUTOMATION_PERFORMANCE_USER_ID : userId;
        void dayTradingClient.readAlert(alertUserId, id).then(loadPortfolio);
      }} />
    </div>

    <LineNotificationPanel onTradeDelivery={handleLineTradeDelivery} />
    <TradeTimeline signals={signals} trades={trades} />
    {selectedSignal && <div className="adaptive-modal-backdrop" onClick={() => setSelectedSignalId(null)}><article className="adaptive-modal day-trading-detail-modal" onClick={(event) => event.stopPropagation()}><button className="modal-close" onClick={() => setSelectedSignalId(null)}><X /></button><span className="eyebrow">{selectedSignal.symbol} {selectedSignal.stockName}</span><h2>當沖訊號詳情</h2><LiveSignalCard signal={selectedSignal} onMonitor={monitor} onSimulate={(item) => void simulate(item)} onAnalyze={(symbol) => { window.location.href = `/?symbol=${symbol}&view=analysis`; }} /></article></div>}
    {selectedPosition && <div className="adaptive-modal-backdrop" onClick={() => setSelectedPositionId(null)}><article className="adaptive-modal day-trading-detail-modal" onClick={(event) => event.stopPropagation()}><button className="modal-close" onClick={() => setSelectedPositionId(null)}><X /></button><span className="eyebrow">{selectedPosition.symbol} {selectedPosition.stockName}</span><h2>持倉風控管理</h2><PositionMonitorCard position={selectedPosition} onClose={(item, percentage) => void closePosition(item, percentage).then(() => setSelectedPositionId(null))} onUpdate={(item, body) => void updatePosition(item, body)} /></article></div>}
    <DayTradingDisclaimer mode={regime.mode} notice={regime.dataNotice} />
  </div>;
}

function AlertTriangleIcon() {
  return <ShieldAlert size={18} />;
}
