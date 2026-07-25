"use client";

import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle, Bell, BellRing, Bot, CheckCircle2, Clock3, Download,
  Eye, Gauge, Play, Settings2, ShieldAlert, Siren, TrendingDown,
  TrendingUp, Volume2, VolumeX, Wifi, WifiOff, X,
} from "lucide-react";
import { filterSignals, isExpired } from "@/lib/day-trading-engine";
import type {
  DayTradingAlert, DayTradingPerformance, DayTradingPosition, DayTradingSettings,
  DayTradingSignal, DayTradingTrade, EmergencyEvent, MarketRegime, StreamConnection,
} from "@/lib/day-trading-types";

const number = (value: number, digits = 2) => Number.isFinite(value)
  ? value.toLocaleString("zh-TW", { minimumFractionDigits: digits, maximumFractionDigits: digits })
  : "—";
const compact = (value: number) => value >= 100_000_000
  ? `${number(value / 100_000_000, 2)} 億`
  : value >= 10_000 ? `${number(value / 10_000, 1)} 萬` : number(value, 0);
const time = (value?: string) => value
  ? new Date(value).toLocaleTimeString("zh-TW", { hour12: false })
  : "—";

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
  const level = status === "normal" && seconds <= 3 ? "normal" : seconds <= 8 ? "warning" : "danger";
  return <span className={`dt-delay ${level}`}><Clock3 size={13} />延遲 {number(seconds, 1)} 秒</span>;
}

export function SignalCountdown({ expiresAt }: { expiresAt: string }) {
  const [remaining, setRemaining] = useState(() => Math.max(0, new Date(expiresAt).getTime() - Date.now()));
  useEffect(() => {
    const update = () => setRemaining(Math.max(0, new Date(expiresAt).getTime() - Date.now()));
    update();
    const timer = window.setInterval(update, 1000);
    return () => window.clearInterval(timer);
  }, [expiresAt]);
  const seconds = Math.ceil(remaining / 1000);
  return <span className={`signal-countdown ${seconds <= 60 ? "expiring" : ""}`}>
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
  const expired = isExpired(signal.expiresAt);
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
      <div><Clock3 size={13} />有效倒數 <SignalCountdown expiresAt={signal.expiresAt} /></div>
    </div>
    <div className="signal-levels">
      <div><span>建議進場區</span><strong>{number(signal.entryMin)}～{number(signal.entryMax)}</strong></div>
      <div className="stop"><span>{long ? "停損價" : "停損回補"}</span><strong>{number(signal.stopLoss)}</strong></div>
      <div><span>{long ? "第一停利" : "第一回補"}</span><strong>{number(signal.target1)}</strong></div>
      <div><span>{long ? "第二停利" : "第二回補"}</span><strong>{number(signal.target2)}</strong></div>
    </div>
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
      <button className={long ? "long-action" : "short-action"} disabled={expired || signal.status === "blocked"} onClick={() => onSimulate(signal)}>
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
      <td className={item.largeOrderForce >= 0 ? "text-up" : "text-down"}>{item.largeOrderForce}<small>{item.industryStrength}</small></td>
      <td><SignalCountdown expiresAt={item.expiresAt} /></td>
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
      <span>張數<strong>{number(position.quantity, 1)}</strong></span><span>持有時間<strong>{Math.floor(position.holdingSeconds / 60)} 分 {position.holdingSeconds % 60} 秒</strong></span>
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

export function RiskControlPanel({
  settings, onSave,
}: { settings: DayTradingSettings; onSave: (settings: DayTradingSettings) => void }) {
  const [draft, setDraft] = useState(settings);
  const [permission, setPermission] = useState<NotificationPermission | "unsupported">(
    typeof Notification === "undefined" ? "unsupported" : Notification.permission,
  );
  useEffect(() => setDraft(settings), [settings]);
  const setNumber = (key: keyof DayTradingSettings, value: string) => setDraft((current) => ({ ...current, [key]: Number(value) }));
  const setBoolean = (key: keyof DayTradingSettings, value: boolean) => setDraft((current) => ({ ...current, [key]: value }));
  return <section className="dt-card risk-panel">
    <div className="dt-section-heading"><div><span className="eyebrow">RISK CONTROL</span><h2>交易風控與通知設定</h2></div><ShieldAlert /></div>
    <div className="risk-form-grid">
      {[
        ["capital", "總交易資金", "元"], ["maxRiskPerTrade", "單筆最大風險", "%"],
        ["maxDailyLoss", "單日最大虧損", "%"], ["maxDailyTrades", "每日最多交易", "筆"],
        ["maxPositionPercentage", "單檔最大部位", "%"], ["maxConsecutiveLosses", "連續虧損停止", "筆"],
        ["minimumRiskReward", "最低風險報酬比", ""], ["maximumSpread", "最大允許價差", "%"],
        ["minimumVolume", "最低成交量", "股"], ["minimumTurnover", "最低成交金額", "元"],
      ].map(([key, label, unit]) => <label key={key}><span>{label}</span><div><input type="number" value={String(draft[key as keyof DayTradingSettings])} onChange={(event) => setNumber(key as keyof DayTradingSettings, event.target.value)} /><em>{unit}</em></div></label>)}
      <label><span>最晚進場時間</span><input type="time" value={draft.latestEntryTime} onChange={(event) => setDraft({ ...draft, latestEntryTime: event.target.value })} /></label>
      <label><span>收盤前提醒</span><input type="time" value={draft.closeReminderTime} onChange={(event) => setDraft({ ...draft, closeReminderTime: event.target.value })} /></label>
    </div>
    <div className="schedule-settings">
      <div className="schedule-settings-title"><Clock3 /><div><strong>開盤自動啟動排程</strong><span>Asia/Taipei；後端會依設定自動切換階段，不需每天手動啟動</span></div></div>
      <div className="risk-form-grid">
        {[
          ["preheatTime", "系統預熱"], ["stockPoolTime", "載入股票池"],
          ["healthCheckTime", "健康檢查"], ["marketOpenTime", "台股開盤"],
          ["marketCloseTime", "停止與摘要"],
        ].map(([key, label]) => <label key={key}><span>{label}</span><input type="time" value={String(draft[key as keyof DayTradingSettings])} onChange={(event) => setDraft({ ...draft, [key]: event.target.value })} /></label>)}
        <label><span>開盤暖機</span><select value={draft.warmupMinutes} onChange={(event) => setDraft({ ...draft, warmupMinutes: Number(event.target.value) as DayTradingSettings["warmupMinutes"] })}><option value={0}>0 分鐘</option><option value={1}>1 分鐘</option><option value={3}>3 分鐘</option><option value={5}>5 分鐘</option><option value={10}>10 分鐘</option></select></label>
        <label><span>推薦重算頻率</span><select value={draft.recommendationRefreshSeconds} onChange={(event) => setDraft({ ...draft, recommendationRefreshSeconds: Number(event.target.value) as DayTradingSettings["recommendationRefreshSeconds"] })}><option value={5}>5 秒</option><option value={10}>10 秒</option><option value={15}>15 秒</option><option value={30}>30 秒</option></select></label>
        <label><span>替換分數門檻</span><div><input type="number" min={0} max={30} value={draft.replacementScoreGap} onChange={(event) => setNumber("replacementScoreGap", event.target.value)} /><em>分</em></div></label>
        <label><span>最短保留時間</span><div><input type="number" min={0} max={30} value={draft.minimumRetentionMinutes} onChange={(event) => setNumber("minimumRetentionMinutes", event.target.value)} /><em>分</em></div></label>
        <label><span>最低即時樣本</span><div><input type="number" min={2} value={draft.minimumLiveSamples} onChange={(event) => setNumber("minimumLiveSamples", event.target.value)} /><em>筆</em></div></label>
        <label><span>最大停損距離</span><div><input type="number" min={0.1} step={0.1} value={draft.maximumStopDistance} onChange={(event) => setNumber("maximumStopDistance", event.target.value)} /><em>%</em></div></label>
      </div>
    </div>
    <div className="notification-settings">
      {[
        ["notificationEnabled", "瀏覽器通知"], ["soundEnabled", "聲音提醒"],
        ["entryNotification", "進場通知"], ["exitNotification", "出場通知"],
        ["stopNotification", "停損通知"], ["targetNotification", "停利通知"],
        ["dataAlertNotification", "資料異常通知"], ["highConfidenceOnly", "只通知高信心"],
      ].map(([key, label]) => <label key={key}><input type="checkbox" checked={Boolean(draft[key as keyof DayTradingSettings])} onChange={(event) => setBoolean(key as keyof DayTradingSettings, event.target.checked)} /><span>{label}</span></label>)}
      <label>最低信心<input type="number" value={draft.minimumConfidence} onChange={(event) => setNumber("minimumConfidence", event.target.value)} /></label>
      <label>冷卻秒數<input type="number" value={draft.notificationCooldown} onChange={(event) => setNumber("notificationCooldown", event.target.value)} /></label>
    </div>
    <div className="risk-actions"><button onClick={async () => {
      if (draft.notificationEnabled && typeof Notification !== "undefined" && Notification.permission === "default") {
        setPermission(await Notification.requestPermission());
      }
      onSave(draft);
    }}><Settings2 size={15} />儲存風控與通知設定</button><p>{permission === "denied" ? "瀏覽器通知已被拒絕；請點網址列左側的網站設定，將「通知」改為允許。" : "達到限制後只停止新進場，既有持倉仍持續產生出場與停損提醒。"}</p></div>
  </section>;
}

export function TradeTimeline({
  signals, trades, performance,
}: { signals: DayTradingSignal[]; trades: DayTradingTrade[]; performance: DayTradingPerformance | null }) {
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
  const exportCsv = () => {
    const lines = [["訊號時間", "代號", "名稱", "方向", "指令", "價格", "進場區", "停損", "目標", "信心", "健康度"],
      ...rows.map((item) => [item.generatedAt, item.symbol, item.stockName, item.directionLabel, item.action, item.price, `${item.entryMin}-${item.entryMax}`, item.stopLoss, item.target2, item.confidenceScore, item.healthScore])];
    const blob = new Blob([`\uFEFF${lines.map((line) => line.join(",")).join("\n")}`], { type: "text/csv;charset=utf-8" });
    const anchor = document.createElement("a"); anchor.href = URL.createObjectURL(blob); anchor.download = "day-trading-signals.csv"; anchor.click(); URL.revokeObjectURL(anchor.href);
  };
  return <section className="dt-card trade-timeline">
    <div className="dt-section-heading"><div><span className="eyebrow">HISTORY & PERFORMANCE</span><h2>訊號與模擬交易紀錄</h2></div><button onClick={exportCsv}><Download size={14} />匯出 CSV</button></div>
    <div className="performance-grid">
      {[
        ["今日交易", performance?.tradeCount ?? 0], ["勝率", `${number(performance?.winRate ?? 0)}%`],
        ["總損益", number(performance?.totalProfit ?? 0, 0)], ["平均單筆", number(performance?.averageProfit ?? 0, 0)],
        ["最大虧損", number(performance?.maxLoss ?? 0, 0)], ["最大連續虧損", performance?.maxConsecutiveLosses ?? 0],
        ["做多績效", number(performance?.longProfit ?? 0, 0)],
        ["放空績效", number(performance?.shortProfit ?? 0, 0)], ["Profit Factor", number(performance?.profitFactor ?? 0)],
      ].map(([label, value]) => <div key={String(label)}><span>{label}</span><strong>{value}</strong></div>)}
    </div>
    <div className="timeline-filters"><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜尋股票代號或名稱" /><input type="date" value={dateFilter} onChange={(event) => setDateFilter(event.target.value)} /><select value={direction} onChange={(event) => setDirection(event.target.value)}><option value="all">多空全部</option><option value="long">做多</option><option value="short">放空</option></select><select value={actionFilter} onChange={(event) => setActionFilter(event.target.value)}><option value="all">全部指令</option><option value="等待">等待進場</option><option value="買進">買進</option><option value="放空">放空</option><option value="出">出場</option><option value="回補">回補</option></select></div>
    <div className="timeline-list">{rows.map((item) => <div key={item.id} className={`timeline-item ${item.direction}`}><i /><time>{time(item.generatedAt)}</time><div><strong>{item.symbol} {item.stockName}</strong><span>{item.action}</span><small>{item.reasons.slice(0, 3).join(" · ")}</small></div><em>{number(item.price)}</em></div>)}</div>
    {!!trades.length && <div className="simulation-trades"><h3>已完成模擬交易</h3>{trades.map((trade) => <div key={trade.id}><span>{trade.symbol} {trade.stockName}</span><strong className={trade.profit >= 0 ? "text-up" : "text-down"}>{number(trade.profit, 0)}</strong><small>{trade.exitReason}</small></div>)}</div>}
  </section>;
}

export function SimulationControls({ onTrigger }: { onTrigger: (scenario: string) => void }) {
  const buttons = [
    ["market_open", "模擬週五開盤"],
    ["long_signal", "產生做多訊號"], ["short_signal", "產生放空訊號"],
    ["long_stop", "觸發多單停損"], ["short_stop", "觸發空單停損"],
    ["target_1", "觸發第一停利"], ["emergency_exit", "觸發緊急出場"],
    ["data_delay", "模擬資料延遲"], ["disconnect", "模擬行情斷線"],
  ];
  return <section className="dt-card simulation-controls">
    <div><Bot size={20} /><span><strong>Mock Streaming 測試控制台</strong><small>僅影響展示模式，不會執行任何真實交易</small></span></div>
    <div>{buttons.map(([value, label]) => <button key={value} onClick={() => onTrigger(value)}>{label}</button>)}</div>
  </section>;
}

export function DayTradingDisclaimer() {
  return <div className="dt-disclaimer"><ShieldAlert size={18} /><div><strong>展示模式，非即時行情</strong><p>本頁所有行情、訊號與模擬交易僅供研究參考，不構成投資建議。系統不會直接下單，所有買進、放空、賣出與回補均須由使用者自行確認。</p></div></div>;
}
