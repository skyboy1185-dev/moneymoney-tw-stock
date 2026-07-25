"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Activity, Bot, Clock3, Gauge, Radio, RefreshCw, ShieldAlert } from "lucide-react";
import { streamRetryDelay } from "@/lib/day-trading-engine";
import type {
  DayTradingAlert, DayTradingPerformance, DayTradingPosition, DayTradingSettings,
  DayTradingSignal, DayTradingTrade, MarketRegime,
} from "@/lib/day-trading-types";
import { dayTradingClient } from "@/services/day-trading-client";
import { useDayTradingStore } from "@/stores/day-trading-store";
import {
  AlertCenter, DayTradingDisclaimer, DayTradingRankingTable, EmergencyExitModal,
  LiveSignalCard, MarketDataDelayBadge, MarketRegimeCard, PositionMonitorCard,
  RiskControlPanel, SimulationControls, StreamConnectionStatus, TradeTimeline,
} from "./DayTradingComponents";

const EVENT_TYPES = [
  "market_update", "quote_update", "new_signal", "signal_update", "signal_expired",
  "position_update", "exit_warning", "emergency_exit", "data_delay", "data_disconnected",
];

function getUserId() {
  let id = localStorage.getItem("moneymoney-user-id");
  if (!id) {
    id = globalThis.crypto?.randomUUID?.() ?? `local-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    localStorage.setItem("moneymoney-user-id", id);
  }
  return id;
}

export function DayTradingDashboard() {
  const {
    regime, signals, candidates, positions, alerts, trades, performance, settings, connection, emergency,
    setInitial, setConnection, handleEvent, dismissEmergency,
  } = useDayTradingStore();
  const [userId, setUserId] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [monitored, setMonitored] = useState<string[]>([]);
  const reconnectAttempt = useRef(0);

  const refresh = useCallback(async (id: string) => {
    const [regimeData, signalData, positionData, alertData, tradeData, performanceData, settingsData] = await Promise.all([
      dayTradingClient.regime(id), dayTradingClient.signals(id), dayTradingClient.positions(id),
      dayTradingClient.alerts(id), dayTradingClient.trades(id), dayTradingClient.performance(id),
      dayTradingClient.settings(id),
    ]);
    setInitial({
      regime: regimeData as MarketRegime,
      signals: signalData.items as DayTradingSignal[],
      candidates: signalData.candidates as DayTradingSignal[],
      positions: positionData.items as DayTradingPosition[],
      alerts: alertData.items as DayTradingAlert[],
      trades: tradeData.items as DayTradingTrade[],
      performance: performanceData as DayTradingPerformance,
      settings: settingsData,
    });
  }, [setInitial]);

  useEffect(() => {
    const id = getUserId();
    setUserId(id);
    setMonitored(JSON.parse(localStorage.getItem("day-trading-monitored") ?? "[]") as string[]);
    void refresh(id).catch((reason) => setError(reason instanceof Error ? reason.message : "資料載入失敗")).finally(() => setLoading(false));
  }, [refresh]);

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

  const loadPortfolio = async () => {
    if (!userId) return;
    const [positionData, alertData, tradeData, performanceData] = await Promise.all([
      dayTradingClient.positions(userId), dayTradingClient.alerts(userId),
      dayTradingClient.trades(userId), dayTradingClient.performance(userId),
    ]);
    setInitial({
      positions: positionData.items as DayTradingPosition[],
      alerts: alertData.items as DayTradingAlert[],
      trades: tradeData.items as DayTradingTrade[],
      performance: performanceData as DayTradingPerformance,
    });
  };

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

  const trigger = async (scenario: string) => {
    await dayTradingClient.scenario(userId, scenario);
    setToast("測試情境已送出，將由 SSE 推送結果");
  };

  if (loading || !regime) return <div className="dt-loading"><span className="spinner" /><h2>正在啟動 AI 當沖多空機器人</h2><p>載入 Mock Streaming Data、風控設定與模擬持倉…</p></div>;

  return <div className="day-trading-page">
    <DayTradingDisclaimer />
    {error && <div className="dt-error"><AlertTriangleIcon />{error}<button onClick={() => { setError(""); void refresh(userId); }}><RefreshCw />重試</button></div>}
    {toast && <div className="dt-toast" role="status"><Activity />{toast}</div>}
    <EmergencyExitModal event={emergency} onDismiss={dismissEmergency} />

    <section className="dt-hero">
      <div><span className="eyebrow"><Radio size={13} /> MOCK STREAMING · SIGNALS ONLY</span><h1>AI 當沖多空機器人</h1><p>即時掃描台股，判斷做多、放空與出場時機</p></div>
      <div className="dt-hero-status"><StreamConnectionStatus status={connection} /><MarketDataDelayBadge seconds={regime.dataDelaySeconds} status={regime.dataStatus} /></div>
    </section>

    {regime.dataStatus !== "normal" && <div className="data-anomaly-banner"><ShieldAlert /><div><strong>行情已延遲 {regime.dataDelaySeconds} 秒</strong><span>目前停止產生新進場訊號，請勿依賴舊報價進行交易；既有持倉仍持續檢查出場風險。</span></div></div>}
    <section className={`automation-banner phase-${regime.automation.phase}`}>
      <Clock3 />
      <div>
        <strong>{regime.automation.robotStatus}</strong>
        <span>{regime.recommendationSummary || regime.automation.statusMessage}</span>
      </div>
      <dl>
        <div><dt>台北時間</dt><dd>{new Date(regime.automation.localTime).toLocaleTimeString("zh-TW", { hour12: false, timeZone: "Asia/Taipei" })}</dd></div>
        <div><dt>行情樣本</dt><dd>{regime.automation.quoteSamples}／{regime.automation.minimumLiveSamples}</dd></div>
        <div><dt>正式訊號</dt><dd>{regime.automation.formalSignalsAllowed ? "允許" : "暫停"}</dd></div>
      </dl>
    </section>

    <section className="dt-system-strip">
      <div><span>市場狀態</span><strong>{regime.directionLabel}</strong></div>
      <div><span>多空分數</span><strong>{regime.score} / 100</strong></div>
      <div><span>當沖環境</span><strong>{regime.environmentScore} · {regime.environmentLabel}</strong></div>
      <div><span>推薦方向</span><strong>{regime.preferredDirection}</strong></div>
      <div><span>行情來源</span><strong>{regime.dataSource}</strong></div>
      <div><span>機器人</span><strong className="scanning"><Bot size={14} />{regime.automation.robotStatus}</strong></div>
      <div><span>今日精選</span><strong>{signals.length} / {regime.maximumRecommendations} 檔</strong></div>
      <div><span>交易時段</span><strong>{regime.session}</strong></div>
      <div><span>最後更新</span><strong>{new Date(regime.updatedAt).toLocaleTimeString("zh-TW", { hour12: false })}</strong></div>
    </section>

    <div className="dt-dashboard-grid">
      <MarketRegimeCard regime={regime} />
      <SimulationControls onTrigger={(scenario) => void trigger(scenario)} />
    </div>

    <section className="live-signal-section">
      <div className="dt-section-heading"><div><span className="eyebrow">AI OFFICIAL PICKS</span><h2>今日 AI 當沖精選：{signals.length}／{regime.maximumRecommendations} 檔</h2><p>出場與停損通知永遠優先；只有通過全部硬性風控的候選才列為正式推薦</p></div><span className="signals-only"><Gauge size={15} />只提供訊號，不自動下單</span></div>
      {signals.length
        ? <div className="live-signal-grid">{signals.map((signal) => <LiveSignalCard key={signal.id} signal={signal} onMonitor={monitor} onSimulate={(item) => void simulate(item)} onAnalyze={(symbol) => { window.location.href = `/?symbol=${symbol}&view=analysis`; }} />)}</div>
        : <div className="dt-empty official-empty"><Bot /><h3>今日 AI 當沖精選：0／{regime.maximumRecommendations} 檔</h3><p>{regime.automation.phase === "scanning" ? "目前沒有符合風控標準的交易機會，建議觀望。" : regime.automation.statusMessage}</p></div>}
    </section>

    <DayTradingRankingTable signals={candidates} monitored={monitored} onMonitor={monitor} onSimulate={(item) => void simulate(item)} onAnalyze={(symbol) => { window.location.href = `/?symbol=${symbol}&view=analysis`; }} />

    <section className="positions-section">
      <div className="dt-section-heading"><div><span className="eyebrow">POSITION FIRST</span><h2>我的當沖監控</h2><p>每次行情更新都先檢查停損與出場，再掃描新進場機會</p></div><strong>{positions.length} 筆未平倉模擬部位</strong></div>
      <div className="position-grid-list">{positions.length ? positions.map((position) => <PositionMonitorCard key={position.id} position={position} onClose={(item, percentage) => void closePosition(item, percentage)} onUpdate={(item, body) => void updatePosition(item, body)} />) : <div className="dt-empty large"><Clock3 /><h3>尚無模擬持倉</h3><p>可從即時訊號卡或排行榜建立模擬多單／空單。</p></div>}</div>
    </section>

    <div className="dt-bottom-grid">
      <AlertCenter alerts={alerts} onRead={(id) => void dayTradingClient.readAlert(userId, id).then(loadPortfolio)} />
      {settings && <RiskControlPanel settings={settings} onSave={(value) => void dayTradingClient.saveSettings(userId, value).then((saved) => { setInitial({ settings: saved }); setToast("風控與通知設定已儲存"); })} />}
    </div>

    <TradeTimeline signals={signals} trades={trades} performance={performance} />
    <DayTradingDisclaimer />
  </div>;
}

function AlertTriangleIcon() {
  return <ShieldAlert size={18} />;
}
