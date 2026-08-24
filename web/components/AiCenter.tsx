"use client";

import { useEffect, useMemo, useState } from "react";
import { Activity, AlertTriangle, ArrowDownRight, ArrowUpRight, Bot, BrainCircuit, BriefcaseBusiness, Clock3, Database, Eye, Radio, ShieldCheck, Sparkles, Star, TrendingUp, X, Zap } from "lucide-react";
import type { MarketDirection, MarketSnapshot, RankingRow, TimelinePoint } from "@/lib/market-types";
import { formatPercent, formatVolume, safeNumber, valueClass } from "@/lib/format";
import { AIStockWorkflow } from "@/components/AIStockWorkflow";

const LABELS: Record<MarketDirection, string> = {
  strong_bull: "強多", bull: "偏多", sideways: "盤整", bear: "偏空", strong_bear: "強空", transition: "多空轉折",
};
const directionClass = (direction: MarketDirection) => `direction-${direction.replace("_", "-")}`;

function MiniChart({ points, field, min, max, color = "#8b7cff" }: { points: TimelinePoint[]; field: keyof TimelinePoint; min?: number; max?: number; color?: string }) {
  const values = points.map((point) => Number(point[field]));
  const low = min ?? Math.min(...values);
  const high = max ?? Math.max(...values);
  const range = high - low || 1;
  const path = values.map((value, index) => `${index ? "L" : "M"} ${(index / Math.max(1, values.length - 1)) * 100} ${92 - ((value - low) / range) * 80}`).join(" ");
  return <svg className="ai-line-chart" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true"><defs><linearGradient id={`fill-${String(field)}`} x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor={color} stopOpacity=".28" /><stop offset="1" stopColor={color} stopOpacity="0" /></linearGradient></defs><path d={`${path} L 100 100 L 0 100 Z`} fill={`url(#fill-${String(field)})`} /><path d={path} fill="none" stroke={color} strokeWidth="1.7" vectorEffect="non-scaling-stroke" /></svg>;
}

function metricValue(value: number, unit: string) {
  if (unit === "%") return `${value >= 0 ? "+" : ""}${safeNumber(value)}%`;
  if (unit === "億元") return `${value >= 0 ? "+" : ""}${safeNumber(value)}`;
  return safeNumber(value);
}

export function AiCenter({ snapshot, loading, autoMode, onAutoModeChange, onSelectStock, userId = "" }: {
  snapshot: MarketSnapshot | null;
  loading: boolean;
  autoMode: boolean;
  onAutoModeChange: (value: boolean) => void;
  onSelectStock: (symbol: string) => void;
  userId?: string;
}) {
  const [chart, setChart] = useState<"orders" | "force" | "index" | "regime" | "count">("force");
  const [sort, setSort] = useState<"rank" | "score" | "changePercent">("rank");
  const [watchSymbols, setWatchSymbols] = useState<Set<string>>(new Set());
  const [holdingSymbols, setHoldingSymbols] = useState<Set<string>>(new Set());
  const [holdingTarget, setHoldingTarget] = useState<RankingRow | null>(null);
  const [holdingForm, setHoldingForm] = useState({ cost: "", lots: "1", buyDate: new Date().toISOString().slice(0, 10) });
  const [actionMessage, setActionMessage] = useState("");
  useEffect(() => {
    if (!userId) return;
    void Promise.all([
      fetch("/api/watchlist", { headers: { "x-user-id": userId } }).then((response) => response.json()),
      fetch("/api/holdings", { headers: { "x-user-id": userId } }).then((response) => response.json()),
    ]).then(([watch, holdings]) => {
      setWatchSymbols(new Set((watch.items ?? []).map((item: { symbol: string }) => item.symbol)));
      setHoldingSymbols(new Set((holdings.items ?? []).map((item: { symbol: string }) => item.symbol)));
    });
  }, [userId]);

  const addWatch = async (symbol: string) => {
    if (!userId || watchSymbols.has(symbol)) return;
    setWatchSymbols((current) => new Set(current).add(symbol));
    const response = await fetch("/api/watchlist", {
      method: "POST", headers: { "Content-Type": "application/json", "x-user-id": userId },
      body: JSON.stringify({ symbol }),
    });
    if (!response.ok && response.status !== 409) {
      setWatchSymbols((current) => { const next = new Set(current); next.delete(symbol); return next; });
      const payload = await response.json();
      setActionMessage(payload.error ?? "加入自選失敗");
      return;
    }
    setActionMessage(`${symbol} 已加入自選觀察`);
  };

  const addHolding = async () => {
    if (!holdingTarget || !userId) return;
    const cost = Number(holdingForm.cost);
    const lots = Number(holdingForm.lots);
    if (!(cost > 0) || !(lots > 0)) { setActionMessage("請輸入有效的成本與張數"); return; }
    const response = await fetch("/api/holdings", {
      method: "POST", headers: { "Content-Type": "application/json", "x-user-id": userId },
      body: JSON.stringify({ symbol: holdingTarget.symbol, cost, lots, buyDate: holdingForm.buyDate, fromWatchlist: watchSymbols.has(holdingTarget.symbol) }),
    });
    const payload = await response.json();
    if (!response.ok && response.status !== 409) { setActionMessage(payload.error ?? "加入持股失敗"); return; }
    setHoldingSymbols((current) => new Set(current).add(holdingTarget.symbol));
    setWatchSymbols((current) => { const next = new Set(current); next.delete(holdingTarget.symbol); return next; });
    setActionMessage(response.status === 409 ? `${holdingTarget.symbol} 已在我的持股` : `${holdingTarget.symbol} 已加入我的持股`);
    setHoldingTarget(null);
  };
  const rankings = useMemo(() => {
    const rows = [...(snapshot?.rankings ?? [])];
    if (sort === "score") return rows.sort((a, b) => b.score - a.score);
    if (sort === "changePercent") return rows.sort((a, b) => b.changePercent - a.changePercent);
    return rows;
  }, [snapshot, sort]);
  if (loading && !snapshot) return <div className="page-loading"><span className="spinner" /><p>AI 決策引擎正在建立市場快照…</p></div>;
  if (!snapshot) return <div className="empty-state"><BrainCircuit size={32} /><h2>目前沒有市場快照</h2><p>請確認行情串流連線後重試。</p></div>;

  const activeNames = snapshot.recommendations.filter((item) => snapshot.activeStrategyIds.includes(item.id)).map((item) => item.name.replace(" Bot", ""));
  const anyMarketOpen = snapshot.marketOpen || snapshot.futuresMarketOpen;
  return (
    <div className="ai-center">
      {actionMessage && <button className="portfolio-toast" onClick={() => setActionMessage("")}>{actionMessage}<X size={13} /></button>}
      <div className="ai-page-heading">
        <div><p className="section-kicker">SUPER AI DAYTRADE SYSTEM</p><h1><BrainCircuit size={25} />超強AI當沖系統</h1><p>掃描 AI 供應鏈、低軌衛星、玻纖布與廠務工程族群，加入多空雙向、風控優先與績效追蹤。</p></div>
        <div className="ai-heading-actions"><span className="official-data-badge"><Database size={14} />個股行情：市場實際資料</span><label className="auto-toggle"><span><Bot size={16} />AI 自動模式</span><input type="checkbox" checked={autoMode} onChange={(event) => onAutoModeChange(event.target.checked)} /><i /></label></div>
      </div>

      <section className="war-room">
        <div className="war-room-top">
          <div className="war-title"><span><Zap size={20} /></span><div><p>AI MARKET COMMAND</p><h2>AI 戰情室</h2></div></div>
          <div className="live-meta"><span className={anyMarketOpen ? "online-dot" : "closed-dot"} />{snapshot.marketStatus}<span><Clock3 size={12} />{new Date(snapshot.updatedAt).toLocaleTimeString("zh-TW", { hour12: false })}</span></div>
        </div>
        <div className="war-room-grid">
          <div className={`regime-orb ${directionClass(snapshot.force.direction)}`}><span>目前市場狀態</span><strong>{LABELS[snapshot.force.direction]}</strong><small>{snapshot.context.regime === "wave_up" ? "波段上漲" : snapshot.context.regime === "wave_down" ? "波段下跌" : snapshot.context.regime === "range" ? "區間盤整" : "多空轉折觀察"}</small></div>
          <div className="command-metrics">
            <div><span>多空力道</span><strong className={snapshot.force.score >= 0 ? "text-up" : "text-down"}>{snapshot.force.score >= 0 ? "+" : ""}{snapshot.force.score}</strong><small>-100 ～ +100</small></div>
            <div><span>盤勢信心度</span><strong>{snapshot.force.confidence}%</strong><small>指標一致程度</small></div>
            <div><span>市場風險</span><strong>{Math.abs(snapshot.force.score) >= 60 ? "中低" : Math.abs(snapshot.force.score) >= 20 ? "中" : "中高"}</strong><small>依波動與分歧估算</small></div>
            <div><span>符合股票</span><strong>{snapshot.rankings.length} 檔</strong><small>分數 ≥ 55</small></div>
          </div>
          <div className="recommended-now"><span>目前推薦策略</span><div>{activeNames.map((name) => <strong key={name}><Sparkles size={12} />{name}</strong>)}</div><p>{snapshot.force.reasons.slice(0, 2).join("；")}</p></div>
          <div className="stream-status"><div><Radio size={15} /><span>資料狀態</span><strong>{snapshot.futuresMarketOpen && !snapshot.marketOpen ? "台指期官方 30 秒取樣" : snapshot.marketOpen ? `${snapshot.delaySeconds} 秒` : "非交易時間"}</strong></div><div><Clock3 size={15} /><span>下次更新</span><strong>{anyMarketOpen ? `${snapshot.nextUpdateSeconds} 秒` : "60 秒檢查"}</strong></div><div><ShieldCheck size={15} /><span>個股訊號</span><strong>{snapshot.marketOpen ? "盤中暫時" : "收盤確認"}</strong></div></div>
        </div>
      </section>

      <section className="ai-section">
        <div className="ai-section-title"><div><Activity size={17} /><div><h2>大單、小單與多空力道</h2><p>大／小單為模擬推估；台指期採期交所官方日夜盤行情</p></div></div><span>{snapshot.futuresMarketOpen && !snapshot.marketOpen ? "夜盤每 30 秒更新" : `每 ${snapshot.marketOpen ? "10" : "60"} 秒更新`}</span></div>
        <div className="metric-grid">
          {snapshot.metrics.map((metric) => {
            const up = metric.change1m >= 0;
            return <article key={metric.id} className="force-metric">
              <div><span>{metric.label}</span>{up ? <ArrowUpRight className="text-up" size={16} /> : <ArrowDownRight className="text-down" size={16} />}</div>
              <strong className={metric.value > 0 && ["large-order","market-force","futures-change","futures-percent","index-change","index-percent"].includes(metric.id) ? "text-up" : metric.value < 0 ? "text-down" : ""}>{metricValue(metric.value, metric.unit)} <small>{metric.unit}</small></strong>
              {metric.hasIntradayChanges === false
                ? <div className="delta-row"><span>1m <b>—</b></span><span>3m <b>—</b></span><span>10m <b>—</b></span></div>
                : <div className="delta-row"><span>1m <b className={valueClass(metric.change1m)}>{metric.change1m >= 0 ? "+" : ""}{safeNumber(metric.change1m, 2)}</b></span><span>3m <b className={valueClass(metric.change3m)}>{safeNumber(metric.change3m, 2)}</b></span><span>10m <b className={valueClass(metric.change10m)}>{safeNumber(metric.change10m, 2)}</b></span></div>}
              <small className={metric.isOfficial ? "metric-source official" : "metric-source demo"}>{metric.source ?? "展示資料"} · {metric.detail ?? new Date(metric.updatedAt).toLocaleTimeString("zh-TW", { hour12: false })}</small>
            </article>;
          })}
        </div>
        <p className="data-disclaimer">大單、小單與多空力道為系統依成交金額、成交方向及市場指標推估，並非交易所公布的投資人身分資料。</p>
      </section>

      <div className="ai-two-column">
        <section className="ai-section strategy-recommendations">
          <div className="ai-section-title"><div><Bot size={17} /><div><h2>AI 策略推薦</h2><p>依目前盤勢自動評估七個 Robot</p></div></div></div>
          <div className="bot-list">{snapshot.recommendations.map((item) => <article key={item.id} className={item.enabled ? "enabled" : ""}>
            <div className="bot-icon"><Bot size={16} /></div><div><div><strong>{item.name}</strong>{item.enabled && <span>推薦啟用</span>}</div><p>{item.reason}</p><small>風險 {item.risk} · {"★".repeat(item.stars)}{"☆".repeat(5 - item.stars)}</small></div>
            <div className="fit-ring"><strong>{item.fit}</strong><span>適配度</span></div>
          </article>)}</div>
        </section>

        <section className="ai-section market-chart-panel">
          <div className="ai-section-title"><div><TrendingUp size={17} /><div><h2>市場動態圖</h2><p>相同尺度比較標準化數值</p></div></div></div>
          <div className="chart-switches">{[["orders","大／小單"],["force","多空力道"],["index","期貨／現貨"],["regime","狀態時間軸"],["count","選股數量"]].map(([id,label]) => <button key={id} className={chart === id ? "active" : ""} onClick={() => setChart(id as typeof chart)}>{label}</button>)}</div>
          <div className="ai-chart-wrap">
            {chart === "orders" && <><MiniChart points={snapshot.timeline} field="largeOrder" color="#ff6366" /><MiniChart points={snapshot.timeline} field="smallOrder" color="#29c97a" /></>}
            {chart === "force" && <><div className="force-bands"><span>+60</span><span>+20</span><span>0</span><span>-20</span><span>-60</span></div><MiniChart points={snapshot.timeline} field="force" min={-100} max={100} color="#8b7cff" /></>}
            {chart === "index" && <><MiniChart points={snapshot.timeline} field="futuresPercent" color="#ffb85c" /><MiniChart points={snapshot.timeline} field="indexPercent" color="#6ea8ff" /></>}
            {chart === "regime" && <div className="regime-timeline">{snapshot.timeline.map((point, index) => <i key={index} className={directionClass(point.direction)} title={`${new Date(point.time).toLocaleTimeString("zh-TW")} ${LABELS[point.direction]}`} />)}</div>}
            {chart === "count" && <MiniChart points={snapshot.timeline} field="stockCount" min={0} color="#38d9c5" />}
          </div>
          <div className="chart-legend">{chart === "orders" ? <><span className="red">大單淨額</span><span className="green">小單淨額</span></> : chart === "index" ? <><span className="orange">台指期漲跌幅</span><span className="blue">加權指數漲跌幅</span></> : <span>最近 36 分鐘模擬趨勢</span>}</div>
        </section>
      </div>

      <AIStockWorkflow
        snapshot={snapshot}
        userId={userId}
        watchSymbols={watchSymbols}
        onAddWatch={addWatch}
        onAnalyze={onSelectStock}
      />

      <div className="intraday-warning"><AlertTriangle size={16} /><span>盤中技術指標與策略訊號為暫時計算結果，可能在 K 棒完成前發生變化。</span></div>
      {holdingTarget && <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.currentTarget === event.target) setHoldingTarget(null); }}>
        <div className="holding-modal" role="dialog" aria-modal="true" aria-labelledby="holding-modal-title">
          <button className="modal-close" onClick={() => setHoldingTarget(null)}><X size={17} /></button>
          <div className="modal-icon"><BriefcaseBusiness size={20} /></div>
          <h2 id="holding-modal-title">加入我的持股</h2>
          <p>{holdingTarget.symbol} {holdingTarget.name} · 保留 AI {holdingTarget.score} 分與「{holdingTarget.strategyName}」紀錄</p>
          <div className="holding-form-grid">
            <label><span>買進成本（每股）</span><input type="number" min="0.01" step="0.01" value={holdingForm.cost} onChange={(event) => setHoldingForm({ ...holdingForm, cost: event.target.value })} /></label>
            <label><span>張數</span><input type="number" min="0.001" step="0.001" value={holdingForm.lots} onChange={(event) => setHoldingForm({ ...holdingForm, lots: event.target.value })} /></label>
            <label><span>買進日期</span><input type="date" value={holdingForm.buyDate} onChange={(event) => setHoldingForm({ ...holdingForm, buyDate: event.target.value })} /></label>
          </div>
          <div className="modal-actions"><button className="button ghost" onClick={() => setHoldingTarget(null)}>取消</button><button className="button primary" onClick={() => void addHolding()}>確認加入持股</button></div>
        </div>
      </div>}
    </div>
  );
}
