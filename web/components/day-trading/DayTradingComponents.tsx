"use client";

import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle, Bell, BellRing, CheckCircle2, CircleDollarSign, Clock3, Download,
  Eye, Gauge, Play, ShieldAlert, Siren, TrendingDown,
  TrendingUp, Volume2, VolumeX, Wifi, WifiOff, X,
} from "lucide-react";
import { filterSignals, isExpired, signalRemainingMs } from "@/lib/day-trading-engine";
import type {
  DayTradingAlert, DayTradingPerformance, DayTradingPosition, DayTradingSignal,
  DayTradingTrade, EmergencyEvent, MarketRegime, StreamConnection,
} from "@/lib/day-trading-types";

const number = (value: number, digits = 2) => Number.isFinite(value)
  ? value.toLocaleString("zh-TW", { minimumFractionDigits: digits, maximumFractionDigits: digits })
  : "—";
const compact = (value: number) => value >= 100_000_000
  ? `${number(value / 100_000_000, 2)} 億`
  : value >= 10_000 ? `${number(value / 10_000, 1)} 萬` : number(value, 0);
const lotsAndShares = (value: number) => {
  if (!Number.isFinite(value)) return "—";
  const lots = value.toLocaleString("zh-TW", { maximumFractionDigits: 3 });
  const shares = Math.round(value * 1_000).toLocaleString("zh-TW");
  return `${lots} 張（${shares} 股）`;
};
const time = (value?: string) => value
  ? new Date(value).toLocaleTimeString("zh-TW", { hour12: false })
  : "—";
const taipeiDate = (value: string | Date) => new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Taipei", year: "numeric", month: "2-digit", day: "2-digit",
}).format(typeof value === "string" ? new Date(value) : value);

function downloadCsv(filename: string, lines: unknown[][]) {
  const csvCell = (value: unknown) => `"${String(value ?? "").replaceAll('"', '""')}"`;
  const blob = new Blob([
    `\uFEFF${lines.map((line) => line.map(csvCell).join(",")).join("\r\n")}`,
  ], { type: "text/csv;charset=utf-8" });
  const anchor = document.createElement("a");
  anchor.href = URL.createObjectURL(blob);
  anchor.download = filename;
  anchor.style.display = "none";
  document.body.appendChild(anchor);
  anchor.click();
  const objectUrl = anchor.href;
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1_000);
}

export function StreamConnectionStatus({ status }: { status: StreamConnection }) {
  const label = {
    connecting: "SSE 連線中", connected: "SSE 已連線",
    reconnecting: "SSE 自動重連中", disconnected: "SSE 已中斷",
  }[status];
  return <span className={`dt-connection ${status}`}>
    {status === "connected" ? <Wifi size={14} /> : <WifiOff size={14} />}{label}
  </span>;
}

export function MarketDataDelayBadge({ seconds, status }: { seconds: number; status: string }) {
  if (status === "closed") {
    return <span className="dt-delay normal"><Clock3 size={13} />今日已收盤</span>;
  }
  const level = status === "normal" && seconds <= 3 ? "normal" : seconds <= 8 ? "warning" : "danger";
  return <span className={`dt-delay ${level}`}><Clock3 size={13} />延遲 {number(seconds, 1)} 秒</span>;
}

export function SignalCountdown({ expiresAt, serverNow }: { expiresAt: string; serverNow?: string }) {
  const [remaining, setRemaining] = useState(() => signalRemainingMs(expiresAt, serverNow));
  useEffect(() => {
    const receivedAt = Date.now();
    const update = () => setRemaining(signalRemainingMs(expiresAt, serverNow, Date.now() - receivedAt));
    update();
    const timer = window.setInterval(update, 1000);
    return () => window.clearInterval(timer);
  }, [expiresAt, serverNow]);
  const seconds = Math.ceil(remaining / 1000);
  return <span className={`signal-countdown ${seconds > 0 && seconds <= 60 ? "expiring" : ""}`}>
    {remaining ? `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}` : "已失效"}
  </span>;
}

export function SignalReasonList({ reasons }: { reasons: string[] }) {
  return <ul className="dt-reason-list">{reasons.slice(0, 5).map((reason) => (
    <li key={reason}><CheckCircle2 size={13} />{reason}</li>
  ))}</ul>;
}

export function SignalWarningList({ warnings }: { warnings: string[] }) {
  return <ul className="dt-warning-list">{warnings.map((warning) => (
    <li key={warning}><AlertTriangle size={13} />{warning}</li>
  ))}</ul>;
}

export function MarketRegimeCard({ regime }: { regime: MarketRegime }) {
  const metrics = regime.metrics;
  return <section className="dt-card dt-regime-card">
    <div className="dt-section-heading">
      <div><span className="eyebrow">TODAY&apos;S REGIME</span><h2>今日大盤多空方向</h2></div>
      <span className={`regime-pill ${regime.direction}`}>{regime.directionLabel}</span>
    </div>
    <div className="dt-regime-main">
      <div className="force-dial"><strong>{regime.score}</strong><span>多空分數 / 100</span></div>
      <div className="dt-permissions">
        <div><span>做多允許程度</span><strong className="text-up">{regime.longPermission}%</strong><i><b style={{ width: `${regime.longPermission}%` }} /></i></div>
        <div><span>放空允許程度</span><strong className="text-down">{regime.shortPermission}%</strong><i className="short"><b style={{ width: `${regime.shortPermission}%` }} /></i></div>
      </div>
      <div className="dt-strategy-summary">
        <span>優先方向<strong>{regime.preferredDirection}</strong></span>
        <span>市場風險<strong>{regime.risk}</strong></span>
        <span>當沖環境<strong>{regime.environmentLabel}</strong></span>
      </div>
    </div>
    <div className="market-metric-grid">
      {[
        ["加權指數", metrics.weightedIndex], ["櫃買指數", metrics.otcIndex], ["台指期", metrics.indexFutures],
        ["大盤 VWAP", metrics.vwap], ["1 分 K", metrics.oneMinuteTrend], ["5 分 K", metrics.fiveMinuteTrend],
        ["15 分 K", metrics.fifteenMinuteTrend], ["上漲／下跌", `${metrics.advancers}／${metrics.decliners}`],
        ["漲停／跌停", `${metrics.limitUp}／${metrics.limitDown}`], ["大單力道", metrics.largeOrderForce],
        ["小單力道", metrics.smallOrderForce], ["相對量", `${metrics.relativeVolume}x`],
        ["市場廣度", `${metrics.breadth}%`], ["波動程度", metrics.volatility],
      ].map(([label, value]) => <div key={String(label)}><span>{label}</span><strong>{String(value)}</strong></div>)}
    </div>
    <div className="dt-regime-footer">
      <div><span>適合策略</span>{regime.suitableStrategies.map((item) => <em key={item}>{item}</em>)}</div>
      <div><span>禁止策略</span>{regime.forbiddenStrategies.map((item) => <em className="forbidden" key={item}>{item}</em>)}</div>
      <SignalReasonList reasons={regime.reasons} />
    </div>
  </section>;
}

export function LiveSignalCard({
  signal, onMonitor, onSimulate, onAnalyze,
}: {
  signal: DayTradingSignal;
  onMonitor: (signal: DayTradingSignal) => void;
  onSimulate: (signal: DayTradingSignal) => void;
  onAnalyze: (symbol: string) => void;
}) {
  const long = signal.direction === "long";
  const expired = signal.serverNow
    ? signalRemainingMs(signal.expiresAt, signal.serverNow) <= 0
    : isExpired(signal.expiresAt);
  return <article className={`live-signal-card ${long ? "long" : "short"} ${expired ? "expired" : ""}`}>
    <div className="signal-card-top">
      <div className="signal-identity">
        <span className={`direction-icon ${long ? "long" : "short"}`}>{long ? <TrendingUp /> : <TrendingDown />}</span>
        <div><span>{signal.recommendationLabel} · {long ? "做多訊號" : "放空訊號"} · {signal.market}</span><h3>{signal.symbol} {signal.stockName}</h3></div>
      </div>
      <div className="signal-price"><strong>{number(signal.price)}</strong><span className={signal.changePercent >= 0 ? "text-up" : "text-down"}>{signal.changePercent >= 0 ? "+" : ""}{number(signal.changePercent)}%</span></div>
    </div>
    <div className="signal-command">
      <span>明確操作指令</span><strong>{expired ? "訊號已失效" : signal.action}</strong>
      <div><Clock3 size={13} />有效倒數 <SignalCountdown expiresAt={signal.expiresAt} serverNow={signal.serverNow} /></div>
    </div>
    <div className="signal-levels">
      <div><span>建議進場區</span><strong>{number(signal.entryMin)}～{number(signal.entryMax)}</strong></div>
      <div className="stop"><span>{long ? "停損價" : "停損回補"}</span><strong>{number(signal.stopLoss)}</strong></div>
      <div><span>{long ? "第一停利" : "第一回補"}</span><strong>{number(signal.target1)}</strong></div>
      <div><span>{long ? "第二停利" : "第二回補"}</span><strong>{number(signal.target2)}</strong></div>
    </div>
    {signal.threeGate && <div className="signal-three-gate">
      <div><span>三關價進場計畫 · {signal.threeGate.sourceDate}</span><strong className={long ? "text-up" : "text-down"}>{signal.threeGateEntryStatus ?? signal.threeGateStatus}</strong></div>
      <div><span>上關</span><strong>{number(signal.threeGate.upper)}</strong></div>
      <div><span>中關</span><strong>{number(signal.threeGate.middle)}</strong></div>
      <div><span>下關</span><strong>{number(signal.threeGate.lower)}</strong></div>
    </div>}
    <div className="signal-scores">
      <div><span>信心分數</span><strong>{signal.confidenceScore}</strong><i><b style={{ width: `${signal.confidenceScore}%` }} /></i></div>
      <div><span>健康度</span><strong>{signal.healthScore}</strong><i><b style={{ width: `${signal.healthScore}%` }} /></i></div>
      <span>風險報酬比 <strong>1：{number(signal.riskRewardRatio, 1)}</strong></span>
    </div>
    <div className="signal-evidence">
      <div><h4>命中理由</h4><SignalReasonList reasons={signal.reasons} /></div>
      <div><h4>風險警示</h4><SignalWarningList warnings={signal.warnings} /></div>
    </div>
    <div className="signal-meta">
      <span>訊號 {time(signal.generatedAt)}</span>
      <span>行情 {time(signal.quoteTimestamp)}</span>
      <span>{signal.dataSource} · {signal.quoteStatus ?? "展示行情"}</span>
      <span>{compact(signal.volume)} 股</span>
    </div>
    <div className="signal-actions">
      <button onClick={() => onAnalyze(signal.symbol)}><Eye size={14} />查看分析</button>
      <button onClick={() => onMonitor(signal)}><Bell size={14} />加入監控</button>
      <button className={long ? "long-action" : "short-action"} disabled={expired || signal.status === "blocked" || signal.threeGateInvalidated} onClick={() => onSimulate(signal)}>
        <Play size={14} />模擬{long ? "做多" : "放空"}
      </button>
    </div>
  </article>;
}

export function DayTradingRankingTable({
  signals, monitored, onAnalyze, onMonitor, onSimulate,
}: {
  signals: DayTradingSignal[];
  monitored: string[];
  onAnalyze: (symbol: string) => void;
  onMonitor: (signal: DayTradingSignal) => void;
  onSimulate: (signal: DayTradingSignal) => void;
}) {
  const [filter, setFilter] = useState("all");
  const [sort, setSort] = useState("confidence");
  const [ignored, setIgnored] = useState<string[]>([]);
  const rows = useMemo(() => (filter === "monitored" ? signals.filter((item) => monitored.includes(item.id)) : filterSignals(signals, filter))
    .filter((item) => !ignored.includes(item.id))
    .sort((left, right) => {
      if (sort === "health") return right.healthScore - left.healthScore;
      if (sort === "volume") return right.volume - left.volume;
      if (sort === "change") return right.changePercent - left.changePercent;
      if (sort === "time") return +new Date(right.generatedAt) - +new Date(left.generatedAt);
      if (sort === "rr") return right.riskRewardRatio - left.riskRewardRatio;
      return right.confidenceScore - left.confidenceScore;
    }), [signals, monitored, filter, sort, ignored]);
  return <section className="dt-card ranking-card">
    <div className="dt-section-heading"><div><span className="eyebrow">MARKET SCAN CANDIDATES</span><h2>市場掃描候選清單</h2><p>未列入前三名者僅為市場候選，不代表建議買進或建議放空</p></div><strong>{rows.length} 檔</strong></div>
    <div className="ranking-toolbar">
      <div>{[
        ["all", "全部"], ["long", "只看做多"], ["short", "只看放空"], ["waiting", "等待進場"],
        ["confirmed", "已確認"], ["high", "高信心"], ["expiring", "即將失效"], ["monitored", "我的監控"], ["listed", "上市"], ["otc", "上櫃"],
      ].map(([value, label]) => <button className={filter === value ? "active" : ""} key={value} onClick={() => setFilter(value)}>{label}</button>)}</div>
      <select value={sort} onChange={(event) => setSort(event.target.value)}>
        <option value="confidence">信心分數排序</option><option value="health">健康度排序</option>
        <option value="volume">成交量排序</option><option value="change">漲跌幅排序</option>
        <option value="time">訊號時間排序</option><option value="rr">風險報酬比排序</option>
      </select>
    </div>
    <div className="table-scroll"><table className="dt-ranking-table"><thead><tr>
      <th>排名</th><th>股票</th><th>現價／漲跌</th><th>量／金額</th><th>方向</th><th>候選狀態</th>
      <th>信心／健康</th><th>VWAP／量價</th><th>大單／產業</th><th>有效倒數</th><th>操作</th>
    </tr></thead><tbody>{rows.map((item, index) => <tr key={item.id}>
      <td><b className="rank-number">{index + 1}</b></td>
      <td><button className="symbol-link" onClick={() => onAnalyze(item.symbol)}>{item.symbol}<small>{item.stockName} · {item.market}</small></button></td>
      <td><strong>{number(item.price)}</strong><small className={item.changePercent >= 0 ? "text-up" : "text-down"}>{number(item.changePercent)}%</small></td>
      <td>{compact(item.volume)}<small>{compact(item.turnover)}</small></td>
      <td><span className={`direction-tag ${item.direction}`}>{item.directionLabel}</span></td>
      <td><span className={`recommendation-tag ${item.isOfficialRecommendation ? "official" : "candidate"}`}>{item.recommendationLabel}</span><strong>{item.isOfficialRecommendation ? item.action : item.action === "放空資格待確認" ? item.action : `候選觀察：${item.action}`}</strong><small>{item.qualificationFailures?.slice(0, 2).join(" · ") || time(item.generatedAt)}</small></td>
      <td><span>{item.confidenceScore}／{item.healthScore}</span><small>R:R 1:{number(item.riskRewardRatio, 1)}</small></td>
      <td>{item.vwapStatus}<small>{item.volumeStatus}</small></td>
      <td className={item.largeOrderContinuousBuy ? "text-up" : item.largeOrderContinuousSell ? "text-down" : ""}>
        {item.largeOrderStatus ?? item.largeOrderForce}
        <small>{item.largeOrderContinuousBuy
          ? `近 5 分鐘 +${number(item.largeOrderRecentNetLots ?? 0)} 張・連續加多 ${item.largeOrderDirectionalSteps ?? item.largeOrderPositiveSteps ?? 0} 次`
          : item.largeOrderContinuousSell
            ? `近 5 分鐘 ${number(item.largeOrderRecentNetLots ?? 0)} 張・連續加空 ${item.largeOrderDirectionalSteps ?? item.largeOrderNegativeSteps ?? 0} 次`
            : item.industryStrength}</small>
      </td>
      <td><SignalCountdown expiresAt={item.expiresAt} serverNow={item.serverNow} /></td>
      <td><div className="table-actions"><button title="查看分析" onClick={() => onAnalyze(item.symbol)}><Eye /></button><button title="加入監控" onClick={() => onMonitor(item)}><Bell /></button><button title={`模擬${item.directionLabel}`} onClick={() => onSimulate(item)}><Play /></button><button title="忽略訊號" onClick={() => setIgnored((current) => [...current, item.id])}><X /></button></div></td>
    </tr>)}</tbody></table></div>
    {!rows.length && <div className="dt-empty">目前沒有符合篩選條件的即時訊號</div>}
  </section>;
}

export function PositionMonitorCard({
  position, onClose, onUpdate,
}: {
  position: DayTradingPosition;
  onClose: (position: DayTradingPosition, percentage: number) => void;
  onUpdate: (position: DayTradingPosition, body: Record<string, unknown>) => void;
}) {
  const long = position.direction === "long";
  return <article className={`position-card ${long ? "long" : "short"}`}>
    <div className="position-head"><div><span className={`direction-tag ${position.direction}`}>{position.directionLabel}</span><h3>{position.symbol} {position.stockName}</h3></div><strong className={position.unrealizedProfit >= 0 ? "text-up" : "text-down"}>{position.unrealizedProfit >= 0 ? "+" : ""}{number(position.unrealizedProfit, 0)}<small>{number(position.returnPercentage)}%</small></strong></div>
    <div className="position-grid">
      <span>進場價格<strong>{number(position.entryPrice)}</strong></span><span>現價<strong>{number(position.currentPrice)}</strong></span>
      <span>模擬成交數量<strong>{lotsAndShares(position.quantity)}</strong></span><span>持有時間<strong>{Math.floor(position.holdingSeconds / 60)} 分 {position.holdingSeconds % 60} 秒</strong></span>
      <span>停損價<strong>{number(position.stopLoss)}</strong></span><span>移動停利<strong>{position.trailingStop ? number(position.trailingStop) : "未啟用"}</strong></span>
      <span>第一目標<strong>{number(position.target1)}</strong></span><span>第二目標<strong>{number(position.target2)}</strong></span>
    </div>
    <div className="position-health"><span>健康度 {position.healthScore}</span><i><b style={{ width: `${position.healthScore}%` }} /></i><strong>{position.latestAction}</strong></div>
    <div className="position-actions">
      <button onClick={() => onClose(position, 30)}>{long ? "手動減碼 30%" : "部分回補 30%"}</button>
      <button onClick={() => onClose(position, 50)}>{long ? "手動減碼 50%" : "部分回補 50%"}</button>
      <button onClick={() => onClose(position, 100)}>{long ? "手動全部賣出" : "手動全部回補"}</button>
      <button onClick={() => {
        const value = window.prompt("輸入新的停損價", String(position.stopLoss));
        if (value && Number(value) > 0) onUpdate(position, { stop_loss: Number(value) });
      }}>修改停損</button>
      <button onClick={() => onUpdate(position, { trailing_stop: position.currentPrice })}>啟用移動停利</button>
      <button onClick={() => onUpdate(position, { sound_enabled: !position.soundEnabled })}>{position.soundEnabled ? <Volume2 /> : <VolumeX />}</button>
    </div>
  </article>;
}

export function EmergencyExitModal({ event, onDismiss }: { event: EmergencyEvent | null; onDismiss: () => void }) {
  useEffect(() => {
    if (!event) return;
    const AudioContextCtor = window.AudioContext ?? (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (AudioContextCtor) {
      const context = new AudioContextCtor();
      const oscillator = context.createOscillator();
      oscillator.frequency.value = 880;
      oscillator.connect(context.destination);
      oscillator.start();
      oscillator.stop(context.currentTime + 0.22);
      window.setTimeout(() => void context.close(), 400);
    }
    if (Notification.permission === "granted") {
      new Notification(event.title, { body: `${event.message}｜${event.reason}`, tag: event.id, requireInteraction: true });
    }
  }, [event]);
  if (!event) return null;
  return <div className="emergency-overlay" role="alertdialog" aria-modal="true">
    <div className="emergency-modal">
      <Siren size={42} /><span>EMERGENCY EXIT</span><h2>{event.title}</h2>
      <div className="emergency-stock"><strong>{event.position?.symbol} {event.position?.stockName}</strong><em>{event.position?.direction === "short" ? "空單" : "多單"}</em></div>
      <div className="emergency-action">{event.action}</div>
      <dl><div><dt>目前價格</dt><dd>{number(event.price)}</dd></div><div><dt>停損價</dt><dd>{number(Number(event.position?.stopLoss ?? 0))}</dd></div><div><dt>觸發原因</dt><dd>{event.reason}</dd></div><div><dt>通知時間</dt><dd>{new Date().toLocaleTimeString("zh-TW", { hour12: false })}</dd></div></dl>
      <p>本系統不會自動下單，請自行確認交易。</p>
      <button onClick={onDismiss}>我知道了</button>
    </div>
  </div>;
}

export function AlertCenter({ alerts, onRead }: { alerts: DayTradingAlert[]; onRead: (id: number) => void }) {
  const unread = alerts.filter((item) => !item.readAt).length;
  return <section className="dt-card alert-center">
    <div className="dt-section-heading"><div><span className="eyebrow">ALERT CENTER</span><h2>通知中心</h2></div><span className="unread-badge"><BellRing size={14} />{unread} 未讀</span></div>
    <div className="alert-list">{alerts.length ? alerts.slice(0, 12).map((alert) => <button className={`alert-item ${alert.level} ${alert.readAt ? "read" : ""}`} key={alert.id} onClick={() => onRead(alert.id)}>
      <span>{alert.level === "emergency" ? <Siren /> : <AlertTriangle />}</span><div><strong>{alert.title}</strong><p>{alert.message}</p><small>{alert.reason} · {time(alert.createdAt)}</small></div><em>{alert.action}</em>
    </button>) : <div className="dt-empty">目前沒有出場或風險通知</div>}</div>
  </section>;
}

export function TradeTimeline({
  signals, trades,
}: { signals: DayTradingSignal[]; trades: DayTradingTrade[] }) {
  const [query, setQuery] = useState("");
  const [direction, setDirection] = useState("all");
  const [dateFilter, setDateFilter] = useState("");
  const [actionFilter, setActionFilter] = useState("all");
  const rows = signals.filter((item) =>
    (!query || `${item.symbol}${item.stockName}`.includes(query))
    && (direction === "all" || item.direction === direction)
    && (!dateFilter || item.generatedAt.slice(0, 10) === dateFilter)
    && (actionFilter === "all" || item.action.includes(actionFilter)),
  );
  return <section className="dt-card trade-timeline">
    <div className="dt-section-heading"><div><span className="eyebrow">SIGNAL & TRADE HISTORY</span><h2>訊號與完成交易紀錄</h2><p>可依日期、方向與指令篩選；上方績效卡提供今日與本月匯出。</p></div></div>
    <div className="timeline-filters"><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜尋股票代號或名稱" /><input type="date" value={dateFilter} onChange={(event) => setDateFilter(event.target.value)} /><select value={direction} onChange={(event) => setDirection(event.target.value)}><option value="all">多空全部</option><option value="long">做多</option><option value="short">放空</option></select><select value={actionFilter} onChange={(event) => setActionFilter(event.target.value)}><option value="all">全部指令</option><option value="等待">等待進場</option><option value="買進">買進</option><option value="放空">放空</option><option value="出">出場</option><option value="回補">回補</option></select></div>
    <div className="timeline-list">{rows.map((item) => <div key={item.id} className={`timeline-item ${item.direction}`}><i /><time>{time(item.generatedAt)}</time><div><strong>{item.symbol} {item.stockName}</strong><span>{item.action}</span><small>{item.reasons.slice(0, 3).join(" · ")}</small></div><em>{number(item.price)}</em></div>)}</div>
    {!!trades.length && <div className="simulation-trades"><h3>原版策略已完成交易</h3>{trades.map((trade) => <div key={trade.id}><span>{trade.symbol} {trade.stockName}<small>{trade.strategyName}</small></span><strong className={trade.profit >= 0 ? "text-up" : "text-down"}>{number(trade.profit, 0)}</strong><small>{trade.exitReason}</small></div>)}</div>}
  </section>;
}

export function DayTradingPerformancePanel({
  performance, positions, trades,
}: {
  performance: DayTradingPerformance | null;
  positions: DayTradingPosition[];
  trades: DayTradingTrade[];
}) {
  const tradeDate = performance?.today?.tradeDate ?? taipeiDate(new Date());
  const month = performance?.period ?? tradeDate.slice(0, 7);
  // The API defines daily realized performance by exit date, so the detail export
  // must use the same boundary even when a position was opened on an earlier day.
  const todayTrades = trades.filter((trade) => taipeiDate(trade.exitTime) === tradeDate);
  const todayPositions = positions.filter((position) => taipeiDate(position.openedAt) === tradeDate);
  const monthPositions = positions.filter((position) => taipeiDate(position.openedAt).startsWith(month));
  const liveTodayUnrealized = todayPositions.reduce((sum, item) => sum + item.unrealizedProfit, 0);
  const liveMonthUnrealized = monthPositions.reduce((sum, item) => sum + item.unrealizedProfit, 0);
  const todayRealized = performance?.today?.realizedProfit ?? 0;
  const monthRealized = performance?.realizedProfit ?? 0;
  const todayTotal = todayRealized + liveTodayUnrealized;
  const monthTotal = monthRealized + liveMonthUnrealized;
  const todayLongRealized = performance?.today?.longRealizedProfit ?? performance?.today?.longProfit ?? 0;
  const todayLongUnrealized = performance?.today?.longUnrealizedProfit
    ?? todayPositions.filter((item) => item.direction === "long").reduce((sum, item) => sum + item.unrealizedProfit, 0);
  const todayLongTotal = todayLongRealized + todayLongUnrealized;
  const todayShortRealized = performance?.today?.shortRealizedProfit ?? performance?.today?.shortProfit ?? 0;
  const todayShortUnrealized = performance?.today?.shortUnrealizedProfit
    ?? todayPositions.filter((item) => item.direction === "short").reduce((sum, item) => sum + item.unrealizedProfit, 0);
  const todayShortTotal = todayShortRealized + todayShortUnrealized;
  const monthLongRealized = performance?.longRealizedProfit ?? performance?.longProfit ?? 0;
  const monthLongUnrealized = performance?.longUnrealizedProfit
    ?? monthPositions.filter((item) => item.direction === "long").reduce((sum, item) => sum + item.unrealizedProfit, 0);
  const monthLongTotal = monthLongRealized + monthLongUnrealized;
  const monthShortRealized = performance?.shortRealizedProfit ?? performance?.shortProfit ?? 0;
  const monthShortUnrealized = performance?.shortUnrealizedProfit
    ?? monthPositions.filter((item) => item.direction === "short").reduce((sum, item) => sum + item.unrealizedProfit, 0);
  const monthShortTotal = monthShortRealized + monthShortUnrealized;
  const reportTime = new Date().toLocaleString("zh-TW", { hour12: false, timeZone: "Asia/Taipei" });
  const pnlClass = (value: number) => value >= 0 ? "text-up" : "text-down";
  const strategyKey = performance?.strategy?.key ?? "paper";
  const strategyLabel = performance?.strategy?.label ?? "模擬策略";

  const exportToday = () => downloadCsv(
    `AI當沖多空機器人_${strategyKey}_今日明細_${tradeDate}.csv`,
    [
      [`AI 當沖多空機器人－${strategyLabel}－今日模擬績效`],
      ["交易日", tradeDate, "報表產生時間", reportTime],
      ["部位規則", "每次正式訊號固定 2 張", "計算方式", "原版策略獨立模擬"],
      ["已完成交易", performance?.today?.tradeCount ?? 0, "未平倉", todayPositions.length, "勝率", `${performance?.today?.winRate ?? 0}%`],
      ["已實現損益", todayRealized, "未實現損益", liveTodayUnrealized, "今日總盈虧", todayTotal],
      ["今日多單已實現", todayLongRealized, "多單未實現", todayLongUnrealized, "多單合計", todayLongTotal],
      ["今日空單已實現", todayShortRealized, "空單未實現", todayShortUnrealized, "空單合計", todayShortTotal],
      ["手續費", performance?.today?.fee ?? 0, "交易稅", performance?.today?.tax ?? 0, "滑價", performance?.today?.slippage ?? 0, "交易成本", performance?.today?.tradingCost ?? 0],
      [],
      ["代號", "名稱", "方向", "狀態", "進場時間", "進場點位", "出場／現價", "數量（張）", "盈虧", "報酬率", "進場原因", "出場原因"],
      ...todayPositions.map((item) => [
        item.symbol, item.stockName, item.direction === "long" ? "做多" : "放空", "持倉中",
        new Date(item.openedAt).toLocaleString("zh-TW", { hour12: false, timeZone: "Asia/Taipei" }),
        item.entryPrice, item.currentPrice, item.quantity, item.unrealizedProfit,
        `${item.returnPercentage}%`, item.latestAction, "",
      ]),
      ...todayTrades.map((item) => [
        item.symbol, item.stockName, item.direction === "long" ? "做多" : "放空", "已出場",
        new Date(item.entryTime).toLocaleString("zh-TW", { hour12: false, timeZone: "Asia/Taipei" }),
        item.entryPrice, item.exitPrice, item.quantity, item.profit,
        `${item.returnPercentage}%`, item.entryReason, item.exitReason,
      ]),
    ],
  );

  const exportMonth = () => downloadCsv(
    `AI當沖多空機器人_${strategyKey}_本月績效_${month}.csv`,
    [
      [`AI 當沖多空機器人－${strategyLabel}－本月模擬績效`],
      ["月份", month, "報表產生時間", reportTime],
      ["已完成交易", performance?.tradeCount ?? 0, "獲利", performance?.wins ?? 0, "虧損", performance?.losses ?? 0, "勝率", `${performance?.winRate ?? 0}%`],
      ["已實現損益", monthRealized, "未實現損益", liveMonthUnrealized, "本月總盈虧", monthTotal],
      ["本月多單已實現", monthLongRealized, "多單未實現", monthLongUnrealized, "多單合計", monthLongTotal],
      ["本月空單已實現", monthShortRealized, "空單未實現", monthShortUnrealized, "空單合計", monthShortTotal],
      ["手續費", performance?.fee ?? 0, "交易稅", performance?.tax ?? 0, "滑價", performance?.slippage ?? 0, "交易成本", performance?.tradingCost ?? 0],
      [],
      ["代號", "名稱", "方向", "狀態", "進場時間", "進場點位", "出場時間", "出場／現價", "數量（張）", "手續費", "交易稅", "滑價", "盈虧", "報酬率", "進場原因", "出場原因", "策略"],
      ...monthPositions.map((item) => [
        item.symbol, item.stockName, item.direction === "long" ? "做多" : "放空", "持倉中",
        new Date(item.openedAt).toLocaleString("zh-TW", { hour12: false, timeZone: "Asia/Taipei" }), item.entryPrice,
        "", item.currentPrice, item.quantity, "", "", "", item.unrealizedProfit,
        `${item.returnPercentage}%`, item.latestAction, "", "",
      ]),
      ...trades.map((item) => [
        item.symbol, item.stockName, item.direction === "long" ? "做多" : "放空", "已出場",
        new Date(item.entryTime).toLocaleString("zh-TW", { hour12: false, timeZone: "Asia/Taipei" }), item.entryPrice,
        new Date(item.exitTime).toLocaleString("zh-TW", { hour12: false, timeZone: "Asia/Taipei" }), item.exitPrice,
        item.quantity, item.fee, item.tax, item.slippage, item.profit,
        `${item.returnPercentage}%`, item.entryReason, item.exitReason, item.strategyName,
      ]),
    ],
  );

  return <section className="adaptive-performance-card dt-card dt-performance-card">
    <div className="adaptive-performance-title"><div><span className="eyebrow">ORIGINAL FIXED-LOT PAPER STRATEGY</span><h2><CircleDollarSign size={19} />{performance?.strategy?.label ?? "原版固定 2 張"}</h2><p>{performance?.strategy?.description ?? "每次正式訊號固定模擬 2 張"}；已完成交易已扣除手續費、交易稅與滑價。</p></div><div className="adaptive-performance-actions"><strong>本月勝率 {number(performance?.winRate ?? 0)}%</strong><button onClick={exportToday}><Download size={14} />匯出今日</button><button onClick={exportMonth}><Download size={14} />匯出本月績效表</button></div></div>
    <div className="adaptive-performance-summary day-trading-performance-summary">
      <article><span>原版部位規則</span><b>每筆固定 2 張</b><small>每次正式訊號獨立模擬</small></article>
      <article><span>今日完成交易</span><b>{performance?.today?.tradeCount ?? 0} 筆</b><small>{tradeDate} · 持倉 {todayPositions.length} 筆</small></article>
      <article className={todayTotal >= 0 ? "profit" : "loss"}><span>今日總盈虧</span><b>{number(todayTotal, 0)} 元</b><small>已實現 {number(todayRealized, 0)} · 未實現 {number(liveTodayUnrealized, 0)}</small></article>
      <article><span>本月完成交易</span><b>{performance?.tradeCount ?? 0} 筆</b><small>獲利 {performance?.wins ?? 0} · 虧損 {performance?.losses ?? 0}</small></article>
      <article className={monthTotal >= 0 ? "profit" : "loss"}><span>本月總盈虧</span><b>{number(monthTotal, 0)} 元</b><small>已實現 {number(monthRealized, 0)} · 未實現 {number(liveMonthUnrealized, 0)}</small></article>
      <article><span>本月交易成本</span><b>{number(performance?.tradingCost ?? 0, 0)} 元</b><small>手續費、交易稅與滑價</small></article>
    </div>
    <div className="day-trading-directional-pnl">
      <article className={monthLongTotal >= 0 ? "profit" : "loss"}>
        <div><span>多單賺賠</span><b>本月 {number(monthLongTotal, 0)} 元</b></div>
        <small>今日 {number(todayLongTotal, 0)}（已實現 {number(todayLongRealized, 0)}／未實現 {number(todayLongUnrealized, 0)}）</small>
        <small>本月已實現 {number(monthLongRealized, 0)}／未實現 {number(monthLongUnrealized, 0)}</small>
      </article>
      <article className={monthShortTotal >= 0 ? "profit" : "loss"}>
        <div><span>空單賺賠</span><b>本月 {number(monthShortTotal, 0)} 元</b></div>
        <small>今日 {number(todayShortTotal, 0)}（已實現 {number(todayShortRealized, 0)}／未實現 {number(todayShortUnrealized, 0)}）</small>
        <small>本月已實現 {number(monthShortRealized, 0)}／未實現 {number(monthShortUnrealized, 0)}</small>
      </article>
    </div>
    <div className="adaptive-trade-columns">
      <div><h3>今日模擬持倉</h3>{todayPositions.length ? <div className="adaptive-trade-table"><table><thead><tr><th>股票</th><th>方向</th><th>進場價格／時間</th><th>成交數量（張／股）</th><th>目前價格</th><th>未實現盈虧</th></tr></thead><tbody>{todayPositions.map((item) => <tr key={item.id}><td><b>{item.symbol}</b><span>{item.stockName}</span></td><td>{item.direction === "long" ? "做多（買進）" : "放空（先賣）"}</td><td><b>{item.direction === "long" ? "買進價" : "放空價"} {number(item.entryPrice)}</b><span>{new Date(item.openedAt).toLocaleString("zh-TW", { hour12: false, timeZone: "Asia/Taipei" })}</span></td><td><b>{lotsAndShares(item.quantity)}</b><span>1 張＝1,000 股</span></td><td><b>現價 {number(item.currentPrice)}</b></td><td className={item.unrealizedProfit >= 0 ? "profit" : "loss"}>{number(item.unrealizedProfit, 0)} 元<span>{number(item.returnPercentage)}%</span></td></tr>)}</tbody></table></div> : <p className="adaptive-trade-empty">{todayTrades.length ? `今日機器人已進場，並完成 ${todayTrades.length} 筆出場交易。` : "今天尚無模擬進場紀錄。"}</p>}</div>
      <div><h3>今日已完成交易</h3>{todayTrades.length ? <div className="adaptive-trade-table"><table><thead><tr><th>股票</th><th>方向</th><th>買賣點（進場／出場）</th><th>成交數量（張／股）</th><th>淨盈虧</th><th>出場原因</th></tr></thead><tbody>{todayTrades.map((item) => <tr key={item.id}><td><b>{item.symbol}</b><span>{item.stockName}</span></td><td>{item.direction === "long" ? "做多（買進後賣出）" : "放空（先賣後回補）"}</td><td><b>進場 {number(item.entryPrice)} → 出場 {number(item.exitPrice)}</b><span>{new Date(item.exitTime).toLocaleString("zh-TW", { hour12: false, timeZone: "Asia/Taipei" })}</span></td><td><b>{lotsAndShares(item.quantity)}</b><span>1 張＝1,000 股</span></td><td className={item.profit >= 0 ? "profit" : "loss"}>{number(item.profit, 0)} 元<span>{number(item.returnPercentage)}%</span></td><td>{item.exitReason}</td></tr>)}</tbody></table></div> : <p className="adaptive-trade-empty">今天尚無已完成的模擬交易。</p>}</div>
    </div>
    <footer><AlertTriangle size={14} />原版策略每次正式訊號固定模擬 2 張，歷史績效完整保留；不代表真實成交，也不構成投資建議。</footer>
  </section>;
}

export function DayTradingDisclaimer({ mode = "demo", notice }: { mode?: string; notice?: string }) {
  const title = mode === "official"
    ? "實際市場行情模式"
    : mode === "warming_up"
      ? "實際行情暖機中"
      : "展示模式，非即時行情";
  return <div className="dt-disclaimer"><ShieldAlert size={18} /><div><strong>{title}</strong><p>{notice || "本頁所有行情、訊號與模擬交易僅供研究參考，不構成投資建議。"} 系統不會直接下單，所有買進、放空、賣出與回補均須由使用者自行確認。</p></div></div>;
}
