"use client";

import { useEffect, useRef, useState } from "react";
import {
  Activity, AlertTriangle, BellRing, ChevronDown, ChevronUp, Crosshair, Radio,
  Pin, TrendingDown, TrendingUp, X, Zap,
} from "lucide-react";
import type {
  ElectronicChipFlowAlert,
  ElectronicChipFlowAlertsResponse,
  ElectronicChipFlowPriceHistory,
  ElectronicChipFlowPricePoint,
  ElectronicChipFlowQuote,
} from "@/lib/electronic-chip-flow-alerts";
import type { StockDeductionSignals } from "@/lib/deduction-signals";
import type { MarketSnapshot } from "@/lib/market-types";
import { evaluateThreeGateLevels } from "@/lib/three-gate-price";
import { detectGroupResonances } from "@/lib/group-resonance";
import { evaluateLargeOrderGuidance } from "@/lib/large-order-action";
import { evaluateLargeOrderOutcomes } from "@/lib/large-order-outcome";

interface ElectronicChipFlowTickerProps {
  onSelectStock?: (symbol: string) => void;
  marketSnapshot?: MarketSnapshot | null;
}

interface MomentumToast {
  id: string;
  alert: ElectronicChipFlowAlert;
  kind: "warning" | "reinforced" | "joint" | "surge";
}

interface MomentumPanelNotice {
  alert: ElectronicChipFlowAlert;
  kind: "retreat" | "surge";
}

interface ThreeGateToast {
  id: string;
  symbol: string;
  name: string;
  levelLabel: string;
  levelPrice: number;
  currentPrice: number;
  position: "crossed-above" | "crossed-below";
  sourceDate: string;
}

const PINNED_MOMENTUM_SYMBOLS_KEY = "twse:pinned-momentum-symbols";
const PINNED_MOMENTUM_ALERTS_KEY = "twse:pinned-momentum-alerts";
const MOMENTUM_CLIENT_ID_KEY = "twse:momentum-client-id";
const THREE_GATE_NOTIFICATION_SIGNATURES_KEY = "twse:pinned-three-gate-notifications";
const EXPANDED_TECHNICAL_REFRESH_MS = 5_000;
const PINNED_TECHNICAL_REFRESH_MS = 5_000;
const ACTIVE_MONITOR_QUOTE_REFRESH_MS = 2_000;

function formatLots(value: number): string {
  return new Intl.NumberFormat("zh-TW", { maximumFractionDigits: 2 }).format(value);
}

function formatSigned(value: number): string {
  return `${value > 0 ? "+" : ""}${formatLots(value)}`;
}

function formatPrice(value: number): string {
  return new Intl.NumberFormat("zh-TW", {
    minimumFractionDigits: value < 100 ? 2 : 1,
    maximumFractionDigits: 2,
  }).format(value);
}

function formatAmount(value: number): string {
  return new Intl.NumberFormat("zh-TW", { maximumFractionDigits: 0 }).format(value);
}

function orderFlow(alert: ElectronicChipFlowAlert) {
  // Saved pinned alerts created before these gross-flow fields existed can
  // remain in localStorage. Use their net value as a safe display fallback.
  return {
    dayLargeLong: alert.dayLargeBuyLots ?? alert.recentBuyLots ?? Math.max(alert.recentNetBuyLots, 0),
    dayLargeShort: alert.dayLargeSellLots ?? alert.recentSellLots ?? Math.max(-alert.recentNetBuyLots, 0),
    dayRetailLong: alert.daySmallBuyLots ?? alert.recentSmallBuyLots ?? Math.max(alert.recentSmallNetBuyLots, 0),
    dayRetailShort: alert.daySmallSellLots ?? alert.recentSmallSellLots ?? Math.max(-alert.recentSmallNetBuyLots, 0),
    largeLong: alert.recentBuyLots ?? Math.max(alert.recentNetBuyLots, 0),
    largeShort: alert.recentSellLots ?? Math.max(-alert.recentNetBuyLots, 0),
    retailLong: alert.recentSmallBuyLots ?? Math.max(alert.recentSmallNetBuyLots, 0),
    retailShort: alert.recentSmallSellLots ?? Math.max(-alert.recentSmallNetBuyLots, 0),
  };
}

function localTime(value: string): string {
  return new Intl.DateTimeFormat("zh-TW", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "Asia/Taipei",
  }).format(new Date(value));
}

function localTimeWithSeconds(value: string): string {
  return new Intl.DateTimeFormat("zh-TW", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    timeZone: "Asia/Taipei",
  }).format(new Date(value));
}

function statusMessage(data: ElectronicChipFlowAlertsResponse | null): string {
  if (!data) return "熱門股大單監測載入中…";
  if (data.status === "disconnected") return "盤中大單監測服務暫時無法連線";
  if (data.status === "unavailable") return "大單監測等待逐筆成交行情";
  if (!data.marketOpen || data.status === "closed") return "目前非盤中時段，大單動能監控暫停";
  if (data.providerRateLimited) {
    return `Fugle 額度暫時限流，約 ${data.providerRetrySeconds ?? 0} 秒後自動續掃`;
  }
  if (data.status === "warming" || data.status === "scanning") {
    return `熱門股＋電子股輪巡中 ${data.scannedCount}/${data.candidateCount} 檔`;
  }
  return `目前尚未偵測到符合條件的熱門股（近 ${data.windowMinutes} 分鐘）`;
}

function TrendIcon({ alert }: { alert: ElectronicChipFlowAlert }) {
  if (alert.isWarning) return <TrendingDown size={12} />;
  if (alert.direction === "short") return <TrendingDown size={12} />;
  if (alert.reinforced || alert.simultaneousIncrease) return <TrendingUp size={12} />;
  return <Activity size={11} />;
}

const TAIWAN_INDEX_DAILY_LEVELS = {
  tradeDateLabel: "08/21",
  bullishPivot: 45_000,
  supportMin: 44_780,
  supportMax: 44_800,
  firstDownside: 44_500,
  nightLow: 44_261,
} as const;

function taiwanIndexLevelState(price: number | null | undefined): {
  label: string;
  tone: "bullish" | "neutral" | "warning" | "bearish";
} {
  if (!price || price <= 0) return { label: "等待即時價", tone: "neutral" };
  if (price >= TAIWAN_INDEX_DAILY_LEVELS.bullishPivot) return { label: "45K 上方・確認站穩", tone: "bullish" };
  if (price > TAIWAN_INDEX_DAILY_LEVELS.supportMax) return { label: "壓力下方・震盪偏空", tone: "neutral" };
  if (price >= TAIWAN_INDEX_DAILY_LEVELS.supportMin) return { label: "正在測試支撐帶", tone: "warning" };
  if (price >= TAIWAN_INDEX_DAILY_LEVELS.firstDownside) return { label: "跌破支撐・短線轉弱", tone: "warning" };
  if (price >= TAIWAN_INDEX_DAILY_LEVELS.nightLow) return { label: "偏空・留意夜盤低點", tone: "bearish" };
  return { label: "跌破夜低・空方加速", tone: "bearish" };
}

function TaiwanIndexPulseBar({
  data,
  marketSnapshot,
}: {
  data: ElectronicChipFlowAlertsResponse | null;
  marketSnapshot?: MarketSnapshot | null;
}) {
  const pulse = data?.marketPulse;
  const futures = marketSnapshot?.context;
  const pulseLive = data?.marketOpen && pulse?.status === "realtime";
  const direction = pulseLive ? pulse.direction : "neutral";
  const trendLabel = pulseLive ? pulse.trendLabel : data?.marketOpen ? "大／小單暖機中" : "現貨收盤・停止更新";
  const directionLabel = pulseLive ? pulse.directionLabel : data?.marketOpen ? "等待判斷" : "盤後";
  const keyLevelState = taiwanIndexLevelState(futures?.futuresPrice);
  return <div
    className={`taiwan-index-pulse direction-${direction}`}
    title={`${pulse?.source ?? "監控池逐筆成交方向聚合推估"}；台指期價格採官方行情，大／小單不是期貨投資人身分資料。`}
  >
    <div className="taiwan-index-pulse-title">
      <Activity size={14} /><strong>目前台指盤勢</strong><em>市場推估</em>
    </div>
    <div className="taiwan-index-pulse-futures">
      <small>台指期 {futures?.futuresContract ?? ""}</small>
      <strong>{futures?.futuresPrice ? formatLots(futures.futuresPrice) : "—"}</strong>
      <span className={(futures?.futuresChangePercent ?? 0) > 0 ? "up" : (futures?.futuresChangePercent ?? 0) < 0 ? "down" : ""}>
        {futures ? `${formatSigned(futures.futuresChange)}（${formatSigned(futures.futuresChangePercent)}%）` : "行情待補"}
      </span>
    </div>
    <div
      className={`taiwan-index-key-levels state-${keyLevelState.tone}`}
      title="45,000 站穩才轉多；44,780～44,800 為支撐帶；跌破後依序留意 44,500 與夜盤低點 44,261。"
    >
      <small><Crosshair size={11} />{TAIWAN_INDEX_DAILY_LEVELS.tradeDateLabel} 關鍵</small>
      <strong>{keyLevelState.label}</strong>
      <span className="pivot">多空 45,000</span>
      <span className="support">支撐 44,780～44,800</span>
      <span className="downside">下看 44,500／44,261</span>
    </div>
    <div className="taiwan-index-pulse-flow large">
      <small>監控池大單淨額</small>
      <strong>{pulseLive ? `${formatSigned(pulse.largeNetLots)} 張` : "—"}</strong>
      <span>{pulseLive ? `本次 ${formatSigned(pulse.largeChangeLots)}` : "等待盤中資料"}</span>
    </div>
    <div className="taiwan-index-pulse-flow small">
      <small>監控池小單淨額</small>
      <strong>{pulseLive ? `${formatSigned(pulse.smallNetLots)} 張` : "—"}</strong>
      <span>{pulseLive ? `本次 ${formatSigned(pulse.smallChangeLots)}` : "等待盤中資料"}</span>
    </div>
    <div className="taiwan-index-pulse-verdict">
      <small>{directionLabel}</small><strong>{trendLabel}</strong>
      <time>{pulseLive && pulse.updatedAt ? `${pulse.coverageCount} 檔・${localTimeWithSeconds(pulse.updatedAt)}` : marketSnapshot?.marketStatus ?? "非交易時間"}</time>
    </div>
  </div>;
}

function momentumPanelNotices(alerts: ElectronicChipFlowAlert[]): MomentumPanelNotice[] {
  return alerts.flatMap((alert): MomentumPanelNotice[] => {
    if (alert.isWarning || alert.trend === "fading") {
      return [{ alert, kind: "retreat" }];
    }
    if (
      alert.reinforced
      || alert.trend === "strengthening"
      || (alert.currentQualifies && (alert.trend === "starting" || alert.momentumChangeLots > 0))
    ) {
      return [{ alert, kind: "surge" }];
    }
    return [];
  }).sort((left, right) => {
    if (left.kind !== right.kind) return left.kind === "retreat" ? -1 : 1;
    return Math.abs(right.alert.momentumChangeLots) - Math.abs(left.alert.momentumChangeLots);
  });
}

function AlertItems({
  alerts,
  windowMinutes,
  onSelectStock,
  direction = "long",
}: {
  alerts: ElectronicChipFlowAlert[];
  windowMinutes: number;
  onSelectStock: (symbol: string) => void;
  direction?: "long" | "short";
}) {
  return <>
    {alerts.map((alert, index) => {
      const flow = orderFlow(alert);
      return <button
        className={`chip-alert-item level-${alert.alertLevel}`}
        key={`${alert.symbol}-${index}`}
        type="button"
        onClick={() => onSelectStock(alert.symbol)}
        title={`${alert.name} ${alert.symbol}｜${alert.message}｜大單資料為推估值`}
      >
        <strong>{alert.name}</strong>
        <b>{alert.symbol}</b>
        <em><TrendIcon alert={alert} />{alert.simultaneousIncrease ? (direction === "short" ? "大小單同步偏空" : "大小單同步增加") : alert.trendLabel}</em>
        <span>今日累積｜大單 多 {formatLots(flow.dayLargeLong)}／空 {formatLots(flow.dayLargeShort)}・散戶 多 {formatLots(flow.dayRetailLong)}／空 {formatLots(flow.dayRetailShort)} 張</span>
        <small>{alert.simultaneousIncrease ? `合力 ${formatSigned(alert.combinedNetBuyLots)}` : `${direction === "short" ? "賣壓" : "動能"} ${formatSigned(alert.momentumChangeLots)}`}・近 {windowMinutes} 分・{alert.time}</small>
      </button>
    })}
  </>;
}

function MomentumHistory({ alert, direction = "long" }: { alert: ElectronicChipFlowAlert; direction?: "long" | "short" }) {
  const points = alert.history.slice(-12);
  const series = [
    { key: "large", label: "大單", field: "recentNetBuyLots" },
    { key: "small", label: "小單", field: "recentSmallNetBuyLots" },
  ] as const;

  return <div className={`chip-momentum-histories direction-${direction}`} aria-label={`${alert.name}${direction === "short" ? "空方" : "多方"}大小單動能紀錄`}>
    {series.map(({ key, label, field }) => {
      const maxValue = Math.max(1, ...points.map((point) => Math.abs(point[field])));
      return <div className="chip-momentum-history-row" key={key}>
        <span className={key}>{label}</span>
        <div className="chip-momentum-history">
          {points.map((point, index) => {
            const value = point[field];
            const previousValue = index > 0 ? points[index - 1][field] : value;
            const change = value - previousValue;
            return <i
              className={`${key} ${change < 0 ? "down" : "up"} ${key === "large" && point.qualified ? "qualified" : ""} ${key === "small" && point.simultaneousIncrease ? "joint" : ""}`}
              key={`${key}-${point.time}-${index}`}
              style={{ height: `${Math.max(14, Math.abs(value) / maxValue * 100)}%` }}
              title={`${point.time}｜${label} ${formatSigned(value)} 張｜較前次 ${formatSigned(change)}`}
            />;
          })}
        </div>
      </div>;
    })}
  </div>;
}

function SignalOutcomeValidation({
  alert,
  points,
}: {
  alert: ElectronicChipFlowAlert;
  points: ElectronicChipFlowPricePoint[];
}) {
  const outcomes = evaluateLargeOrderOutcomes(alert, points);
  return <div className="chip-signal-outcomes" aria-label="大單訊號事後驗證">
    <small>訊號後績效</small>
    {outcomes.map((outcome) => <span key={outcome.minutes}>
      {outcome.minutes} 分
      <b className={(outcome.returnPercent ?? 0) > 0 ? "up" : (outcome.returnPercent ?? 0) < 0 ? "down" : ""}>
        {outcome.status === "pending" ? "等待" : outcome.status === "unavailable" ? "—" : `${formatSigned(outcome.returnPercent ?? 0)}%`}
      </b>
    </span>)}
  </div>;
}

function mergePricePoints(
  current: ElectronicChipFlowPricePoint[],
  incoming: ElectronicChipFlowPricePoint[],
): ElectronicChipFlowPricePoint[] {
  const byTimestamp = new Map(current.map((point) => [point.timestamp, point]));
  for (const point of incoming) {
    if (Number.isFinite(point.price) && point.price > 0 && !Number.isNaN(Date.parse(point.timestamp))) {
      byTimestamp.set(point.timestamp, point);
    }
  }
  const sorted = [...byTimestamp.values()]
    .sort((left, right) => Date.parse(left.timestamp) - Date.parse(right.timestamp));
  const latestTradeDate = sorted.at(-1)?.timestamp.slice(0, 10);
  return sorted
    .filter((point) => point.timestamp.slice(0, 10) === latestTradeDate)
    .slice(-420);
}

function IntradayPriceTrend({
  quote,
  points,
  loading,
  title = "盤中即時股價走勢",
}: {
  quote?: ElectronicChipFlowQuote;
  points: ElectronicChipFlowPricePoint[];
  loading: boolean;
  title?: string;
}) {
  const chartPoints = mergePricePoints(
    points,
    quote ? [{ timestamp: quote.quoteTimestamp, price: quote.price, isRealtime: quote.isRealtime }] : [],
  );
  const width = 520;
  const height = 92;
  const paddingX = 9;
  const paddingY = 9;
  const reference = quote?.previousClose ?? chartPoints[0]?.price ?? 0;
  const values = [...chartPoints.map((point) => point.price), ...(reference > 0 ? [reference] : [])];
  const rawMin = values.length ? Math.min(...values) : 0;
  const rawMax = values.length ? Math.max(...values) : 0;
  const pricePadding = Math.max((rawMax - rawMin) * 0.12, rawMax * 0.001, 0.01);
  const minPrice = rawMin - pricePadding;
  const maxPrice = rawMax + pricePadding;
  const chartWidth = width - paddingX * 2;
  const chartHeight = height - paddingY * 2;
  const xFor = (index: number) => paddingX + (chartPoints.length <= 1 ? chartWidth : index / (chartPoints.length - 1) * chartWidth);
  const yFor = (price: number) => paddingY + (maxPrice - price) / Math.max(maxPrice - minPrice, 0.01) * chartHeight;
  const coordinates = chartPoints.map((point, index) => `${xFor(index).toFixed(1)},${yFor(point.price).toFixed(1)}`);
  const lastPoint = chartPoints.at(-1);
  const rising = (quote?.change ?? ((lastPoint?.price ?? 0) - reference)) >= 0;
  const firstTimestamp = chartPoints[0]?.timestamp;
  const lastTimestamp = lastPoint?.timestamp;
  const referenceY = reference > 0 ? yFor(reference) : null;

  return <div className={`chip-price-trend ${rising ? "up" : "down"}`} aria-label={`${title}圖`}>
    <div className="chip-price-trend-heading">
      <strong><Activity size={12} />{title}</strong>
      <span>{chartPoints.length} 筆・每次報價自動更新</span>
      {lastPoint && <b>{formatPrice(lastPoint.price)}</b>}
    </div>
    {chartPoints.length ? <div className="chip-price-trend-canvas">
      <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" role="img" aria-label={`目前 ${formatPrice(lastPoint!.price)}，共 ${chartPoints.length} 筆盤中價格`}>
        <line className="grid" x1={paddingX} x2={width - paddingX} y1={height / 2} y2={height / 2} />
        {referenceY !== null && <line className="reference" x1={paddingX} x2={width - paddingX} y1={referenceY} y2={referenceY} />}
        {coordinates.length > 1 && <polyline className="price-line" points={coordinates.join(" ")} />}
        <circle className="last-price" cx={xFor(chartPoints.length - 1)} cy={yFor(lastPoint!.price)} r="3.5" />
      </svg>
      <span className="high">{formatPrice(rawMax)}</span>
      <span className="low">{formatPrice(rawMin)}</span>
      <time className="start" dateTime={firstTimestamp}>{firstTimestamp ? localTimeWithSeconds(firstTimestamp) : ""}</time>
      <time className="end" dateTime={lastTimestamp}>{lastTimestamp ? localTimeWithSeconds(lastTimestamp) : ""}</time>
    </div> : <div className="chip-price-trend-empty">{loading ? "正在載入今日盤中走勢…" : "等待第一筆盤中即時價格"}</div>}
    <small>虛線為昨收 {reference > 0 ? formatPrice(reference) : "—"}；紅線上漲、綠線下跌</small>
  </div>;
}

function DeductionSignalReminder({
  signals,
  loading,
  direction,
}: {
  signals?: StockDeductionSignals;
  loading: boolean;
  direction: "long" | "short";
}) {
  if (!signals && loading) {
    return <div className="chip-deduction-reminder loading"><span className="spinner small" />正在比對日／週／月扣三低與扣三高…</div>;
  }
  if (!signals?.matches.length) return null;
  const timeframeLabels = { day: "日 K", week: "週 K", month: "月 K" } as const;
  return <div className="chip-deduction-reminder" role="note" aria-label="均線扣抵提醒">
    <div className="chip-deduction-heading">
      <strong><AlertTriangle size={13} />均線扣抵提醒</strong>
      <span>符合 {signals.matches.length} 種 20 期均線模型</span>
    </div>
    <div className="chip-deduction-matches">{signals.matches.map((match) => {
      const aligned = (direction === "long" && match.direction === "low")
        || (direction === "short" && match.direction === "high");
      const values = match.deductionValues.map((value) => formatPrice(value)).join("／");
      return <div
        className={`${match.direction} ${aligned ? "aligned" : "counter"}`}
        key={`${match.timeframe}-${match.direction}`}
        title={`未來三期扣抵值：${values}；推估 MA：${match.projectedMaValues.map((value) => formatPrice(value)).join(" → ")}`}
      >
        <strong>{timeframeLabels[match.timeframe]}・扣三{match.direction === "low" ? "低" : "高"}</strong>
        <span>扣抵值 {values}</span>
        <small>現價較扣抵均值 {formatSigned(match.deductionGapPercent)}%・{aligned ? `${direction === "long" ? "多方" : "空方"}條件同向` : "與目前動能方向相反，留意轉折"}</small>
      </div>;
    })}</div>
    <small>扣抵為均線結構提醒，不代表價格一定上漲或下跌。</small>
  </div>;
}

function ThreeGateReminder({ signals, quote }: { signals?: StockDeductionSignals; quote?: ElectronicChipFlowQuote }) {
  if (!signals?.threeGate || signals.currentPrice == null) return null;
  const currentPrice = quote?.price ?? signals.currentPrice;
  const previousClose = quote?.previousClose ?? signals.previousClose;
  const statuses = evaluateThreeGateLevels(currentPrice, previousClose, signals.threeGate);
  const middle = statuses.find((status) => status.key === "middle")!;
  const headline = middle.position === "crossed-above"
    ? "今日站上中關價"
    : middle.position === "crossed-below"
      ? "今日跌破中關價"
      : middle.position === "above"
        ? "目前站在中關價之上"
        : "目前位於中關價之下";
  const positionLabel = {
    "crossed-above": "今日站上",
    "crossed-below": "今日跌破",
    above: "目前在其上",
    below: "目前在其下",
  } as const;
  return <div className={`chip-three-gate-reminder ${middle.position}`} role="note" aria-label="三關價提醒">
    <div className="chip-three-gate-heading">
      <strong><Crosshair size={13} />三關價提醒</strong>
      <b>{headline}</b>
      <small>現價 {formatPrice(currentPrice)}・依 {signals.threeGate.sourceDate} 高低價計算</small>
    </div>
    <div className="chip-three-gate-levels">{statuses.map((status) => <div className={`${status.key} ${status.position}`} key={status.key}>
      <span>{status.label}</span>
      <strong>{formatPrice(status.price)}</strong>
      <small>{positionLabel[status.position]}</small>
    </div>)}</div>
  </div>;
}

function MomentumPanel({
  data,
  alerts,
  direction,
  pinnedSymbols,
  trackedSymbols,
  alertSymbols,
  quotes,
  priceHistory,
  quoteLoading,
  deductionSignals,
  deductionLoading,
  onTogglePin,
  onClose,
  onSelectStock,
}: {
  data: ElectronicChipFlowAlertsResponse;
  alerts: ElectronicChipFlowAlert[];
  direction: "long" | "short";
  pinnedSymbols: Set<string>;
  trackedSymbols: Set<string>;
  alertSymbols: Set<string>;
  quotes: Record<string, ElectronicChipFlowQuote>;
  priceHistory: Record<string, ElectronicChipFlowPricePoint[]>;
  quoteLoading: boolean;
  deductionSignals: Record<string, StockDeductionSignals>;
  deductionLoading: boolean;
  onTogglePin: (alert: ElectronicChipFlowAlert) => void;
  onClose: () => void;
  onSelectStock: (symbol: string) => void;
}) {
  const isShort = direction === "short";
  const groupResonances = data.marketOpen ? detectGroupResonances(alerts, quotes) : [];
  const topGroupResonances = groupResonances.slice(0, 3);
  const topNotices = momentumPanelNotices(alerts);
  return <aside className={`chip-momentum-panel ${isShort ? "short-side" : "long-side"}`} aria-label={isShort ? "空方大單動能雷達" : "多方大單動能雷達"}>
    <header>
      <div>
        <strong>{isShort ? <TrendingDown size={14} /> : <Zap size={14} />}{isShort ? "空方大單動能雷達" : "多方大單動能雷達"}</strong>
        <span>官方成交量熱門股＋核心電子股；{isShort ? "專找大戶持續賣超與同步偏空" : "專找大戶持續買超與同步轉強"}</span>
      </div>
      <div className="chip-momentum-summary">
        <span className={isShort ? "short" : "positive"}>{isShort ? <TrendingDown size={12} /> : <TrendingUp size={12} />}{isShort ? `空方啟動 ${data.shortCount ?? 0}` : `持續轉強 ${data.strengtheningCount}`}</span>
        <span className={isShort ? "short" : "positive"}>{isShort ? <TrendingDown size={12} /> : <TrendingUp size={12} />}{isShort ? `持續加空 ${data.shortStrengtheningCount ?? 0}` : `大小單同步 ${data.jointIncreaseCount}`}</span>
        {!isShort && <span className="warning"><AlertTriangle size={12} />轉弱警示 {data.warningCount}</span>}
        {groupResonances.length > 0 && <span className="group-warning"><AlertTriangle size={12} />族群共振 {groupResonances.length} 組・強烈注意</span>}
        {pinnedSymbols.size > 0 && <span className="pinned"><Pin size={12} />已釘選 {pinnedSymbols.size}</span>}
        <small>{data.candidateCount}/{data.candidateTarget ?? data.candidateCount} 檔監控池・本輪普查 {data.baselineCycleScannedCount}/{data.candidateCount}・約 {data.baselineCycleTargetSeconds} 秒一輪・熱門快掃 {data.fastCandidateCount}・高頻追蹤 {data.highFrequencyTrackingCount}・展開／釘選約 2 秒即時監控</small>
      </div>
      <button type="button" onClick={onClose} aria-label="關閉大單動能雷達"><X size={15} /></button>
    </header>
    {((data.universeStatus && data.universeStatus !== "healthy") || data.candidateCount < (data.candidateTarget ?? data.candidateCount)) && <div className="chip-universe-notice" role="status">
      <AlertTriangle size={13} />
      <span>{data.universeNotice ?? `目前覆蓋 ${data.candidateCount}/${data.candidateTarget ?? data.candidateCount} 檔；官方來源或處置名單保護尚未完整，系統會持續重試。`}</span>
    </div>}
    {topNotices.length > 0 && <div className="chip-momentum-alert-banner" role="alert" aria-live="assertive">
      <strong><BellRing size={15} />大單即時異動 <b>{topNotices.length}</b></strong>
      <div>{topNotices.map(({ alert, kind }) => <button
        type="button"
        className={kind}
        key={`${kind}-${alert.symbol}`}
        onClick={() => onSelectStock(alert.symbol)}
      >
        <span>{alert.symbol} {alert.name}</span>
        <b>{kind === "retreat"
          ? isShort ? "大單賣壓急退" : "大單買盤急退"
          : isShort ? "大單賣壓急增" : "大單買盤急增"}</b>
        <small>近段 {formatLots(isShort
          ? alert.recentNetSellLots ?? Math.max(0, -alert.recentNetBuyLots)
          : Math.max(0, alert.recentNetBuyLots))} 張・本次 {formatSigned(alert.momentumChangeLots)} 張</small>
      </button>)}</div>
    </div>}
    {topGroupResonances.length > 0 && <div className="chip-group-resonance-banner" role="alert" aria-live="assertive">
      <strong><AlertTriangle size={15} />族群共振，強烈注意</strong>
      <div>{topGroupResonances.map((resonance) => <span className={resonance.direction} key={`${resonance.group}-${resonance.direction}`}>
        {resonance.group} {resonance.count} 檔同步{resonance.direction === "up" ? "上漲" : "下跌"}・平均 {formatSigned(resonance.averageChangePercent)}%・{resonance.names.join("、")}
      </span>)}</div>
    </div>}
    <div className="chip-momentum-list">
      {alerts.length ? alerts.map((alert) => {
        const pinned = pinnedSymbols.has(alert.symbol);
        const tracking = pinned && trackedSymbols.has(alert.symbol) && !alertSymbols.has(alert.symbol);
        const retained = pinned && !trackedSymbols.has(alert.symbol) && !alertSymbols.has(alert.symbol);
        const flow = orderFlow(alert);
        const quote = quotes[alert.symbol];
        const quoteDirection = quote ? (quote.change > 0 ? "up" : quote.change < 0 ? "down" : "flat") : "flat";
        const groupResonance = groupResonances.find((resonance) => resonance.symbols.includes(alert.symbol));
        const guidance = evaluateLargeOrderGuidance({
          alert,
          quote,
          marketOpen: data.marketOpen,
          resonance: groupResonance,
        });
        const dataState = data.marketOpen ? (alert.dataState ?? "warming") : "closed";
        const dataStateLabel = data.marketOpen
          ? (alert.dataStateLabel ?? "資料暖機中")
          : "盤後停止更新";
        return <article
          className={`chip-momentum-card level-${alert.alertLevel} ${pinned ? "is-pinned" : ""} ${retained ? "is-retained" : ""} ${groupResonance ? `has-group-resonance resonance-${groupResonance.direction}` : ""}`}
          key={alert.symbol}
          onClick={() => onSelectStock(alert.symbol)}
          onKeyDown={(event) => {
            if (event.target !== event.currentTarget) return;
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              onSelectStock(alert.symbol);
            }
          }}
          role="button"
          tabIndex={0}
        >
          <div className="chip-momentum-stock">
            <strong>{alert.name}</strong><b>{alert.symbol}</b>
            {tracking && <span className="chip-momentum-retained">持續追蹤</span>}
            {retained && <span className="chip-momentum-retained">釘選保留</span>}
            <span className={`chip-data-state state-${dataState}`}>{dataStateLabel}</span>
            <em><TrendIcon alert={alert} />{alert.simultaneousIncrease ? (isShort ? "大小單同步偏空" : "大小單同步增加") : alert.trendLabel}</em>
            <button
              type="button"
              className={`chip-momentum-pin ${pinned ? "active" : ""}`}
              aria-label={`${pinned ? "取消持續監控" : "釘選並持續監控"} ${alert.name} ${alert.symbol}`}
              aria-pressed={pinned}
              title={pinned ? "取消釘選與持續監控" : "釘選到最上方並持續快速監控"}
              onClick={(event) => {
                event.stopPropagation();
                onTogglePin(alert);
              }}
            ><Pin size={12} /></button>
            <div className={`chip-momentum-quote ${quoteDirection}`}>
              {quote ? <>
                <small>{quote.isRealtime ? "即時價" : "收盤／延遲價"}</small>
                <strong>{formatPrice(quote.price)}</strong>
                <span>{formatSigned(quote.change)}（{formatSigned(quote.changePercent)}%）</span>
                <time dateTime={quote.quoteTimestamp}>{localTimeWithSeconds(quote.quoteTimestamp)}</time>
              </> : <small>{quoteLoading ? "即時股價讀取中…" : "行情暫時無法取得"}</small>}
            </div>
            {groupResonance && <div className={`chip-group-resonance-tag ${groupResonance.direction}`}>
              <AlertTriangle size={11} />強烈注意｜{groupResonance.group} {groupResonance.count} 檔同步{groupResonance.direction === "up" ? "上漲" : "下跌"}
            </div>}
          </div>
          {alert.largeOrderOffsetting && <div className="chip-order-offsetting">
            <AlertTriangle size={12} />多空大單同步增加、目前互相抵銷；不當成單邊主力訊號。
          </div>}
          <div className="chip-momentum-numbers">
            <span className="day-total"><small>今日累積・大單多方</small><strong className="flow-long">{formatLots(flow.dayLargeLong)} 張</strong></span>
            <span className="day-total"><small>今日累積・大單空方</small><strong className="flow-short">{formatLots(flow.dayLargeShort)} 張</strong></span>
            <span className="day-total"><small>今日累積・散戶多方*</small><strong className="flow-long">{formatLots(flow.dayRetailLong)} 張</strong></span>
            <span className="day-total"><small>今日累積・散戶空方*</small><strong className="flow-short">{formatLots(flow.dayRetailShort)} 張</strong></span>
            <span><small>近 {data.windowMinutes} 分・大單多方</small><strong className="flow-long">{formatLots(flow.largeLong)} 張</strong></span>
            <span><small>近 {data.windowMinutes} 分・大單空方</small><strong className="flow-short">{formatLots(flow.largeShort)} 張</strong></span>
            <span><small>散戶多方（小單推估）</small><strong className="flow-long">{formatLots(flow.retailLong)} 張</strong></span>
            <span><small>散戶空方（小單推估）</small><strong className="flow-short">{formatLots(flow.retailShort)} 張</strong></span>
            <span><small>大單淨額</small><strong className={alert.recentNetBuyLots < 0 ? "flow-short" : "flow-long"}>{formatSigned(alert.recentNetBuyLots)} 張</strong></span>
            <span><small>散戶淨額</small><strong className={alert.recentSmallNetBuyLots < 0 ? "flow-short" : "flow-long"}>{formatSigned(alert.recentSmallNetBuyLots)} 張</strong></span>
            <span><small>大小單合計淨額</small><strong className={alert.combinedNetBuyLots < 0 ? "flow-short" : "flow-long"}>{formatSigned(alert.combinedNetBuyLots)} 張</strong></span>
            <span><small>較前次</small><strong className={alert.momentumChangeLots < 0 ? "flow-short" : "flow-long"}>{formatSigned(alert.momentumChangeLots)} 張</strong></span>
          </div>
          <MomentumHistory alert={alert} direction={direction} />
          <IntradayPriceTrend
            quote={quote}
            points={priceHistory[alert.symbol] ?? []}
            loading={quoteLoading}
            title="目前股價與盤中即時走勢"
          />
          <SignalOutcomeValidation alert={alert} points={priceHistory[alert.symbol] ?? []} />
          <ThreeGateReminder signals={deductionSignals[alert.symbol]} quote={quote} />
          <DeductionSignalReminder
            signals={deductionSignals[alert.symbol]}
            loading={deductionLoading}
            direction={direction}
          />
          <div className={`chip-large-order-guidance action-${guidance.action}`} aria-label={`大單條件研判：${guidance.label}`}>
            <div>
              <small>大單條件研判</small>
              <strong>{guidance.label}</strong>
              <b>{guidance.score == null ? "—" : guidance.score}<i>{guidance.score == null ? "資料待補" : "/100"}</i></b>
              <em>條件一致度・非勝率</em>
            </div>
            <span>{guidance.reasons.length ? guidance.reasons.join("・") : "等待更多確認條件"}</span>
            {guidance.cautions.length > 0 && <span className="caution"><AlertTriangle size={11} />{guidance.cautions.join("・")}</span>}
          </div>
          <p>{alert.message}</p>
          <footer>
            出現 {alert.occurrenceCount} 次・高峰 {formatLots(Math.abs(alert.peakRecentNetBuyLots))} 張・啟動 {localTime(alert.firstDetectedAt)}・成交更新 {localTime(alert.updatedAt)}
            {alert.lastScannedAt ? `・掃描 ${localTimeWithSeconds(alert.lastScannedAt)}` : "・尚未完成掃描"}
            {alert.lastLargeOrderAt ? `・最後大單 ${localTimeWithSeconds(alert.lastLargeOrderAt)}` : "・尚無新大單"}
            {alert.effectiveNetThresholdLots != null ? `・有效淨額門檻 ${formatLots(alert.effectiveNetThresholdLots)} 張` : ""}
            {alert.largeOrderThresholdAmount != null ? `・單筆大單門檻 $${formatAmount(alert.largeOrderThresholdAmount)}` : ""}
            {retained ? "・釘選保留，等待伺服器恢復追蹤" : pinned ? "・釘選後持續快速監控" : ""}
          </footer>
        </article>;
      }) : <div className="chip-momentum-empty">{isShort ? "尚未出現空方大單啟動標的；系統持續輪巡中。" : "尚未出現多方大單啟動標的；系統持續輪巡中。"}</div>}
    </div>
    <footer>{isShort ? "空方柱狀圖以藍色顯示賣壓，柱越高代表淨賣超越強；" : "柱狀圖已分為大單（紫紅）與小單（青綠），淡色代表較前次轉弱；"}「今日累積」自開盤起計算，「近 {data.windowMinutes} 分」用於判斷當下動能。操作建議是保守條件研判，分數代表條件一致度而非勝率；大單與散戶身分皆為成交方向推估。</footer>
  </aside>;
}

export function ElectronicChipFlowTicker({ onSelectStock, marketSnapshot }: ElectronicChipFlowTickerProps) {
  const [data, setData] = useState<ElectronicChipFlowAlertsResponse | null>(null);
  const [tickerAlerts, setTickerAlerts] = useState<ElectronicChipFlowAlert[]>([]);
  const [momentumToasts, setMomentumToasts] = useState<MomentumToast[]>([]);
  const [threeGateToasts, setThreeGateToasts] = useState<ThreeGateToast[]>([]);
  const tickerSignature = useRef("");
  const urgentSignatures = useRef(new Map<string, string>());
  const toastTimers = useRef<number[]>([]);
  const threeGateNotificationSignatures = useRef(new Set<string>());
  const threeGateLastPrices = useRef(new Map<string, number>());
  const threeGateToastTimers = useRef<number[]>([]);
  const [expanded, setExpanded] = useState<"long" | "short" | null>(null);
  const [pinnedSymbols, setPinnedSymbols] = useState<string[]>([]);
  const pinnedSymbolsRef = useRef<string[]>([]);
  const clientIdRef = useRef("");
  const expandedTrackingSymbolsRef = useRef<string[]>([]);
  const [pinnedAlertSnapshots, setPinnedAlertSnapshots] = useState<ElectronicChipFlowAlert[]>([]);
  const [momentumQuotes, setMomentumQuotes] = useState<Record<string, ElectronicChipFlowQuote>>({});
  const [momentumPriceHistory, setMomentumPriceHistory] = useState<Record<string, ElectronicChipFlowPricePoint[]>>({});
  const [quoteLoading, setQuoteLoading] = useState(false);
  const [deductionSignals, setDeductionSignals] = useState<Record<string, StockDeductionSignals>>({});
  const [deductionLoading, setDeductionLoading] = useState(false);

  useEffect(() => {
    try {
      let clientId = window.localStorage.getItem(MOMENTUM_CLIENT_ID_KEY) ?? "";
      if (!/^[A-Za-z0-9_-]{8,64}$/.test(clientId)) {
        clientId = window.crypto.randomUUID();
        window.localStorage.setItem(MOMENTUM_CLIENT_ID_KEY, clientId);
      }
      clientIdRef.current = clientId;
      const stored = JSON.parse(window.localStorage.getItem(PINNED_MOMENTUM_SYMBOLS_KEY) ?? "[]");
      if (Array.isArray(stored)) {
        const symbols = stored.filter((symbol): symbol is string => typeof symbol === "string");
        pinnedSymbolsRef.current = symbols;
        setPinnedSymbols(symbols);
      }
      const storedAlerts = JSON.parse(window.localStorage.getItem(PINNED_MOMENTUM_ALERTS_KEY) ?? "[]");
      if (Array.isArray(storedAlerts)) {
        setPinnedAlertSnapshots(storedAlerts.filter(
          (alert): alert is ElectronicChipFlowAlert => Boolean(alert && typeof alert === "object" && typeof alert.symbol === "string"),
        ));
      }
      const storedGateSignatures = JSON.parse(window.sessionStorage.getItem(THREE_GATE_NOTIFICATION_SIGNATURES_KEY) ?? "[]");
      if (Array.isArray(storedGateSignatures)) {
        threeGateNotificationSignatures.current = new Set(
          storedGateSignatures.filter((signature): signature is string => typeof signature === "string"),
        );
      }
    } catch {
      clientIdRef.current = window.crypto.randomUUID();
      window.localStorage.removeItem(PINNED_MOMENTUM_SYMBOLS_KEY);
      window.localStorage.removeItem(PINNED_MOMENTUM_ALERTS_KEY);
    }
  }, []);

  const togglePin = (alert: ElectronicChipFlowAlert) => {
    const isPinned = pinnedSymbols.includes(alert.symbol);
    const nextSymbols = isPinned
      ? pinnedSymbols.filter((item) => item !== alert.symbol)
      : [alert.symbol, ...pinnedSymbols];
    const nextSnapshots = isPinned
      ? pinnedAlertSnapshots.filter((item) => item.symbol !== alert.symbol)
      : [alert, ...pinnedAlertSnapshots.filter((item) => item.symbol !== alert.symbol)];
    setPinnedSymbols(nextSymbols);
    pinnedSymbolsRef.current = nextSymbols;
    setPinnedAlertSnapshots(nextSnapshots);
    window.localStorage.setItem(PINNED_MOMENTUM_SYMBOLS_KEY, JSON.stringify(nextSymbols));
    window.localStorage.setItem(PINNED_MOMENTUM_ALERTS_KEY, JSON.stringify(nextSnapshots));
    if (isPinned) {
      setThreeGateToasts((current) => current.filter((item) => item.symbol !== alert.symbol));
      threeGateLastPrices.current.delete(alert.symbol);
    }
  };

  useEffect(() => {
    const currentAlerts = [
      ...(data?.trackedAlerts ?? []), ...(data?.alerts ?? []),
      ...(data?.trackedShortAlerts ?? []), ...(data?.shortAlerts ?? []),
    ];
    if (!currentAlerts.length || !pinnedSymbols.length) return;
    setPinnedAlertSnapshots((current) => {
      const snapshots = new Map(current.map((alert) => [alert.symbol, alert]));
      currentAlerts.forEach((alert) => {
        if (pinnedSymbols.includes(alert.symbol)) snapshots.set(alert.symbol, alert);
      });
      const next = pinnedSymbols.flatMap((symbol) => {
        const alert = snapshots.get(symbol);
        return alert ? [alert] : [];
      });
      window.localStorage.setItem(PINNED_MOMENTUM_ALERTS_KEY, JSON.stringify(next));
      return next;
    });
  }, [data?.alerts, data?.shortAlerts, data?.trackedAlerts, data?.trackedShortAlerts, pinnedSymbols]);

  const disposedSymbolsSignature = (data?.disposedExcludedSymbols ?? []).join(",");
  useEffect(() => {
    if (!disposedSymbolsSignature) return;
    const disposed = new Set(disposedSymbolsSignature.split(","));
    setPinnedSymbols((current) => {
      const next = current.filter((symbol) => !disposed.has(symbol));
      pinnedSymbolsRef.current = next;
      window.localStorage.setItem(PINNED_MOMENTUM_SYMBOLS_KEY, JSON.stringify(next));
      return next;
    });
    setPinnedAlertSnapshots((current) => {
      const next = current.filter((alert) => !disposed.has(alert.symbol));
      window.localStorage.setItem(PINNED_MOMENTUM_ALERTS_KEY, JSON.stringify(next));
      return next;
    });
    setThreeGateToasts((current) => current.filter((item) => !disposed.has(item.symbol)));
  }, [disposedSymbolsSignature]);

  const closeMomentumToast = (id: string) => {
    setMomentumToasts((current) => current.filter((item) => item.id !== id));
  };

  const closeThreeGateToast = (id: string) => {
    setThreeGateToasts((current) => current.filter((item) => item.id !== id));
  };

  useEffect(() => {
    let controller: AbortController | null = null;
    let timer: number | null = null;
    let stopped = false;
    const load = async () => {
      let nextRefreshMs = 2_000;
      if (document.visibilityState === "hidden") return;
      controller?.abort();
      controller = new AbortController();
      try {
        const pinned = pinnedSymbolsRef.current.slice(0, 20).join(",");
        const tracking = expandedTrackingSymbolsRef.current.slice(0, 20).join(",");
        const clientId = clientIdRef.current || "legacy-client";
        const response = await fetch(`/api/chip-flow/electronic-alerts?pinned=${encodeURIComponent(pinned)}&tracking=${encodeURIComponent(tracking)}&clientId=${encodeURIComponent(clientId)}`, {
          cache: "no-store",
          signal: controller.signal,
        });
        const rawPayload = await response.json() as ElectronicChipFlowAlertsResponse;
        // A small per-browser jitter prevents many dashboards opened around the
        // bell from hitting the server on the exact same millisecond.
        nextRefreshMs = rawPayload.marketOpen
          ? 2_000 + Math.round(Math.random() * 500)
          : 30_000;
        // The BAR is strictly intraday. Closing records remain in the database,
        // but must not be replayed as live marquee items or urgent popups.
        const payload = rawPayload.marketOpen ? rawPayload : {
          ...rawPayload,
          status: "closed" as const,
          alerts: [],
          trackedAlerts: [],
          warningCount: 0,
          strengtheningCount: 0,
          jointIncreaseCount: 0,
          shortCount: 0,
          shortStrengtheningCount: 0,
          shortAlerts: [],
          trackedShortAlerts: [],
        };
        if (!rawPayload.marketOpen) {
          toastTimers.current.forEach((toastTimer) => window.clearTimeout(toastTimer));
          toastTimers.current = [];
          setMomentumToasts([]);
          setThreeGateToasts([]);
          urgentSignatures.current.clear();
        }
        const nextTickerAlerts = Array.from(
          new Map(payload.alerts.map((alert) => [alert.symbol, alert])).values(),
        );
        // Counts and raw BAR values remain live in the expanded panel. The
        // marquee changes only when a stock enters/leaves or its trend state
        // actually changes, so a repeated occurrence does not restart it.
        const nextSignature = nextTickerAlerts.map((alert) => [
          alert.symbol,
          alert.trend,
          alert.alertLevel,
          alert.currentQualifies ? 1 : 0,
          alert.isWarning ? 1 : 0,
          alert.reinforced ? 1 : 0,
          alert.simultaneousIncrease ? 1 : 0,
        ].join(":"))
          .sort()
          .join("|");
        if (nextSignature !== tickerSignature.current) {
          tickerSignature.current = nextSignature;
          setTickerAlerts(nextTickerAlerts);
        }
        const nextUrgentSignatures = new Map<string, string>();
        const freshToasts: MomentumToast[] = [];
        nextTickerAlerts.forEach((alert) => {
          if (!alert.isWarning && !alert.reinforced && !alert.simultaneousIncrease && !alert.currentQualifies && alert.alertLevel !== "critical") return;
          const kind = alert.isWarning || alert.alertLevel === "critical"
            ? "warning"
            : alert.simultaneousIncrease
              ? "joint"
              : alert.reinforced
                ? "reinforced"
                : "surge";
          const signature = [
            kind,
            kind === "warning" ? alert.trend : 1,
          ].join(":");
          nextUrgentSignatures.set(alert.symbol, signature);
          if (urgentSignatures.current.get(alert.symbol) === signature) return;
          freshToasts.push({
            id: `${alert.symbol}-${alert.updatedAt}-${signature}`,
            alert,
            kind,
          });
        });
        urgentSignatures.current = nextUrgentSignatures;
        if (freshToasts.length) {
          const prioritized = freshToasts
            .sort((left, right) => {
              const priority = { warning: 4, joint: 3, reinforced: 2, surge: 1 } as const;
              return priority[right.kind] - priority[left.kind];
            })
            .slice(0, 3);
          setMomentumToasts((current) => [
            ...prioritized,
            ...current.filter((item) => !prioritized.some((fresh) => fresh.alert.symbol === item.alert.symbol)),
          ].slice(0, 3));
          prioritized.forEach((item) => {
            const timer = window.setTimeout(
              () => closeMomentumToast(item.id),
              item.kind === "warning" ? 15_000 : item.kind === "surge" ? 12_000 : 10_000,
            );
            toastTimers.current.push(timer);
          });
        }
        setData(payload);
      } catch (error) {
        if ((error as Error).name !== "AbortError") {
          setData((current) => current ? { ...current, status: "disconnected" } : null);
          nextRefreshMs = 5_000 + Math.round(Math.random() * 1_000);
        }
      } finally {
        if (!stopped) timer = window.setTimeout(load, nextRefreshMs);
      }
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") void load();
    };
    void load();
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      stopped = true;
      if (timer !== null) window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibilityChange);
      controller?.abort();
      toastTimers.current.forEach((toastTimer) => window.clearTimeout(toastTimer));
      toastTimers.current = [];
      threeGateToastTimers.current.forEach((toastTimer) => window.clearTimeout(toastTimer));
      threeGateToastTimers.current = [];
    };
  }, []);

  const alerts = tickerAlerts;
  const shortAlerts = data?.shortAlerts ?? [];
  const disposedSymbols = new Set(data?.disposedExcludedSymbols ?? []);
  const pinnedSet = new Set(pinnedSymbols);
  const trackedPanelAlerts = data?.trackedAlerts ?? [];
  const livePanelAlerts = Array.from(
    new Map([...trackedPanelAlerts, ...(data?.alerts ?? [])].map((alert) => [alert.symbol, alert])).values(),
  );
  const trackedPanelSymbols = new Set(trackedPanelAlerts.map((alert) => alert.symbol));
  const alertPanelSymbols = new Set((data?.alerts ?? []).map((alert) => alert.symbol));
  const panelAlerts = Array.from(new Map([
    ...pinnedAlertSnapshots.filter((alert) => !disposedSymbols.has(alert.symbol)).map((alert) => [alert.symbol, alert] as const),
    ...livePanelAlerts.map((alert) => [alert.symbol, alert] as const),
  ]).values()).sort((left, right) => {
    const leftPinnedIndex = pinnedSymbols.indexOf(left.symbol);
    const rightPinnedIndex = pinnedSymbols.indexOf(right.symbol);
    if (leftPinnedIndex >= 0 && rightPinnedIndex >= 0) return leftPinnedIndex - rightPinnedIndex;
    return Number(rightPinnedIndex >= 0) - Number(leftPinnedIndex >= 0);
  });
  const trackedShortPanelAlerts = data?.trackedShortAlerts ?? [];
  const liveShortPanelAlerts = Array.from(
    new Map([...trackedShortPanelAlerts, ...shortAlerts].map((alert) => [alert.symbol, alert])).values(),
  );
  const trackedShortPanelSymbols = new Set(trackedShortPanelAlerts.map((alert) => alert.symbol));
  const shortAlertPanelSymbols = new Set(shortAlerts.map((alert) => alert.symbol));
  const shortPanelAlerts = Array.from(new Map([
    ...pinnedAlertSnapshots.filter((alert) => alert.direction === "short" && !disposedSymbols.has(alert.symbol)).map((alert) => [alert.symbol, alert] as const),
    ...liveShortPanelAlerts.map((alert) => [alert.symbol, alert] as const),
  ]).values()).sort((left, right) => {
    const leftPinnedIndex = pinnedSymbols.indexOf(left.symbol);
    const rightPinnedIndex = pinnedSymbols.indexOf(right.symbol);
    if (leftPinnedIndex >= 0 && rightPinnedIndex >= 0) return leftPinnedIndex - rightPinnedIndex;
    return Number(rightPinnedIndex >= 0) - Number(leftPinnedIndex >= 0);
  });
  const expandedAlerts = expanded === "short" ? shortPanelAlerts : panelAlerts;
  const monitoredQuoteAlerts = Array.from(new Map([
    ...pinnedAlertSnapshots
      .filter((alert) => pinnedSet.has(alert.symbol) && !disposedSymbols.has(alert.symbol))
      .map((alert) => [alert.symbol, alert] as const),
    ...(expanded ? expandedAlerts.map((alert) => [alert.symbol, alert] as const) : []),
  ]).values());
  const quoteRequestPayload = monitoredQuoteAlerts.length
    ? JSON.stringify(monitoredQuoteAlerts.map((alert) => ({
        symbol: alert.symbol,
        name: alert.name,
        market: alert.market,
      })).sort((left, right) => left.symbol.localeCompare(right.symbol)))
    : "";
  const technicalSignalAlerts = Array.from(new Map([
    ...pinnedAlertSnapshots
      .filter((alert) => pinnedSet.has(alert.symbol) && !disposedSymbols.has(alert.symbol))
      .map((alert) => [alert.symbol, alert] as const),
    ...(expanded ? expandedAlerts.map((alert) => [alert.symbol, alert] as const) : []),
  ]).values());
  const technicalSignalRequestPayload = technicalSignalAlerts.length
    ? JSON.stringify(technicalSignalAlerts.map((alert) => ({
        symbol: alert.symbol,
        name: alert.name,
        market: alert.market,
      })).sort((left, right) => left.symbol.localeCompare(right.symbol)))
    : "";
  const pinnedSymbolsSignature = [...pinnedSymbols].sort().join(",");
  const expandedTrackingSymbolsSignature = expanded
    ? expandedAlerts.map((alert) => alert.symbol).slice(0, 20).join(",")
    : "";

  useEffect(() => {
    expandedTrackingSymbolsRef.current = expandedTrackingSymbolsSignature
      ? expandedTrackingSymbolsSignature.split(",")
      : [];
  }, [expandedTrackingSymbolsSignature]);

  useEffect(() => {
    if (!expanded || !quoteRequestPayload) return;
    const controller = new AbortController();
    const loadHistory = async () => {
      try {
        const response = await fetch("/api/market-data/quote-history", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: `{"items":${quoteRequestPayload}}`,
          cache: "no-store",
          signal: controller.signal,
        });
        if (!response.ok) throw new Error(`走勢回應 ${response.status}`);
        const payload = await response.json() as { items: ElectronicChipFlowPriceHistory[] };
        setMomentumPriceHistory((current) => {
          const next = { ...current };
          for (const item of payload.items) {
            next[item.symbol] = mergePricePoints(next[item.symbol] ?? [], item.points);
          }
          return next;
        });
      } catch (error) {
        if ((error as Error).name !== "AbortError") {
          // The live quote loop below still builds a chart when warmup history
          // is temporarily unavailable.
        }
      }
    };
    void loadHistory();
    return () => controller.abort();
  }, [expanded, quoteRequestPayload]);

  useEffect(() => {
    if (!technicalSignalRequestPayload || (!expanded && !data?.marketOpen)) {
      setDeductionLoading(false);
      return;
    }
    let stopped = false;
    let controller: AbortController | null = null;
    let timer: number | null = null;
    const pinnedSetForRequest = new Set(pinnedSymbolsSignature.split(",").filter(Boolean));
    const metadataItems = JSON.parse(technicalSignalRequestPayload) as Array<{ symbol: string; name: string }>;
    const metadata = new Map(metadataItems.map((item) => [item.symbol, item]));
    const loadTechnicalSignals = async () => {
      if (document.visibilityState === "hidden") return;
      if (timer !== null) {
        window.clearTimeout(timer);
        timer = null;
      }
      controller?.abort();
      controller = new AbortController();
      if (expanded) setDeductionLoading(true);
      try {
        const response = await fetch("/api/market-data/deduction-signals", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: `{"items":${technicalSignalRequestPayload}}`,
          cache: "no-store",
          signal: controller.signal,
        });
        if (!response.ok) throw new Error(`扣抵與三關價訊號回應 ${response.status}`);
        const payload = await response.json() as { items: StockDeductionSignals[] };
        if (stopped) return;
        setDeductionSignals((current) => ({
          ...current,
          ...Object.fromEntries(payload.items.map((item) => [item.symbol, item])),
        }));

        if (data?.marketOpen && pinnedSetForRequest.size) {
          const freshToasts: ThreeGateToast[] = [];
          for (const item of payload.items) {
            if (!pinnedSetForRequest.has(item.symbol) || !item.threeGate || item.currentPrice == null) continue;
            const priorPrice = threeGateLastPrices.current.get(item.symbol) ?? item.previousClose;
            const statuses = evaluateThreeGateLevels(item.currentPrice, priorPrice, item.threeGate);
            threeGateLastPrices.current.set(item.symbol, item.currentPrice);
            const alert = metadata.get(item.symbol);
            for (const crossing of statuses) {
              if (crossing.position !== "crossed-above" && crossing.position !== "crossed-below") continue;
              const signature = `${data.tradeDate}:${item.symbol}:${crossing.key}:${crossing.position}`;
              if (threeGateNotificationSignatures.current.has(signature)) continue;
              threeGateNotificationSignatures.current.add(signature);
              freshToasts.push({
                id: signature,
                symbol: item.symbol,
                name: alert?.name ?? item.symbol,
                levelLabel: crossing.label,
                levelPrice: crossing.price,
                currentPrice: item.currentPrice,
                position: crossing.position,
                sourceDate: item.threeGate.sourceDate,
              });
            }
          }
          window.sessionStorage.setItem(
            THREE_GATE_NOTIFICATION_SIGNATURES_KEY,
            JSON.stringify([...threeGateNotificationSignatures.current].slice(-120)),
          );
          if (freshToasts.length) {
            const visibleToasts = freshToasts.slice(0, 3);
            setThreeGateToasts((current) => [
              ...visibleToasts,
              ...current.filter((item) => !visibleToasts.some((fresh) => fresh.id === item.id)),
            ].slice(0, 3));
            visibleToasts.forEach((item) => {
              const toastTimer = window.setTimeout(() => closeThreeGateToast(item.id), 15_000);
              threeGateToastTimers.current.push(toastTimer);
            });
          }
        }
      } catch (error) {
        if ((error as Error).name !== "AbortError") {
          // Other momentum details remain available when technical history is unavailable.
        }
      } finally {
        if (!stopped) {
          setDeductionLoading(false);
          if ((expanded || pinnedSetForRequest.size > 0) && data?.marketOpen) {
            const refreshMs = expanded
              ? EXPANDED_TECHNICAL_REFRESH_MS
              : PINNED_TECHNICAL_REFRESH_MS;
            timer = window.setTimeout(loadTechnicalSignals, refreshMs + Math.round(Math.random() * 500));
          }
        }
      }
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") void loadTechnicalSignals();
    };
    void loadTechnicalSignals();
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      stopped = true;
      if (timer !== null) window.clearTimeout(timer);
      controller?.abort();
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [data?.marketOpen, data?.tradeDate, expanded, pinnedSymbolsSignature, technicalSignalRequestPayload]);

  useEffect(() => {
    if (!quoteRequestPayload) {
      setQuoteLoading(false);
      return;
    }
    let stopped = false;
    let controller: AbortController | null = null;
    let timer: number | null = null;
    const refreshMilliseconds = data?.marketOpen
      ? ACTIVE_MONITOR_QUOTE_REFRESH_MS + Math.round(Math.random() * 250)
      : 60_000;
    const loadQuotes = async () => {
      if (document.visibilityState === "hidden") return;
      controller?.abort();
      controller = new AbortController();
      setQuoteLoading(true);
      try {
        const response = await fetch("/api/market-data/quotes", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: `{"items":${quoteRequestPayload}}`,
          cache: "no-store",
          signal: controller.signal,
        });
        if (!response.ok) throw new Error(`行情回應 ${response.status}`);
        const payload = await response.json() as { items: ElectronicChipFlowQuote[] };
        if (!stopped) {
          setMomentumQuotes(Object.fromEntries(payload.items.map((quote) => [quote.symbol, quote])));
          setMomentumPriceHistory((current) => {
            const next = { ...current };
            for (const quote of payload.items) {
              next[quote.symbol] = mergePricePoints(next[quote.symbol] ?? [], [{
                timestamp: quote.quoteTimestamp,
                price: quote.price,
                isRealtime: quote.isRealtime,
              }]);
            }
            return next;
          });
        }
      } catch (error) {
        if (!stopped && (error as Error).name !== "AbortError") {
          // Keep the last valid quote visible while the next automatic retry is
          // pending. Its timestamp still makes stale data explicit to the user.
        }
      } finally {
        if (!stopped) {
          setQuoteLoading(false);
          timer = window.setTimeout(loadQuotes, refreshMilliseconds);
        }
      }
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") void loadQuotes();
    };
    void loadQuotes();
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      stopped = true;
      if (timer !== null) window.clearTimeout(timer);
      controller?.abort();
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [data?.marketOpen, quoteRequestPayload]);

  const hasAlerts = alerts.length > 0;
  const hasShortAlerts = shortAlerts.length > 0;
  // Keep one item per symbol. A previous fill-to-four implementation made a
  // single alert (for example 新普) appear back-to-back many times.
  const scrollingAlerts = alerts;
  const label = data?.marketOpen ? "盤中大單動能" : "大單動能暫停";
  const selectStock = (symbol: string) => {
    setExpanded(null);
    if (onSelectStock) {
      onSelectStock(symbol);
      return;
    }
    window.location.assign(`/?symbol=${encodeURIComponent(symbol)}&view=analysis`);
  };

  return <>
    {(threeGateToasts.length > 0 || momentumToasts.length > 0) && <div className="chip-emergency-stack" aria-live="assertive">
      {threeGateToasts.map((item) => <article
        className={`chip-emergency-toast ${item.position === "crossed-above" ? "gate-up" : "gate-down"}`}
        key={item.id}
        role="alert"
      >
        <button type="button" className="chip-emergency-body" onClick={() => selectStock(item.symbol)}>
          <span className="chip-emergency-icon"><Crosshair /></span>
          <div>
            <strong>已釘選・三關價特別提醒</strong>
            <h4>{item.symbol} {item.name}</h4>
            <p>{item.position === "crossed-above" ? "今日站上" : "今日跌破"}{item.levelLabel} {formatPrice(item.levelPrice)}・目前股價 {formatPrice(item.currentPrice)}</p>
            <small>依 {item.sourceDate} 高低價計算・點擊查看個股分析</small>
          </div>
        </button>
        <button type="button" className="chip-emergency-close" aria-label="關閉三關價通知" onClick={() => closeThreeGateToast(item.id)}><X /></button>
      </article>)}
      {momentumToasts.map((item) => {
        const flow = orderFlow(item.alert);
        return <article
        className={`chip-emergency-toast ${item.kind}`}
        key={item.id}
        role="alert"
      >
        <button type="button" className="chip-emergency-body" onClick={() => selectStock(item.alert.symbol)}>
          <span className="chip-emergency-icon">{item.kind === "warning" ? <AlertTriangle /> : item.kind === "surge" ? <Zap /> : <TrendingUp />}</span>
          <div>
            <strong>{item.kind === "warning" ? "大單急退警示" : item.kind === "joint" ? "大小單同步增加" : item.kind === "reinforced" ? "大單急增・持續轉強" : "大單急增"}</strong>
            <h4>{item.alert.symbol} {item.alert.name}</h4>
            <p>{item.alert.message}</p>
            <small>近 {data?.windowMinutes ?? 5} 分｜大單 多 {formatLots(flow.largeLong)}／空 {formatLots(flow.largeShort)}｜散戶 多 {formatLots(flow.retailLong)}／空 {formatLots(flow.retailShort)} 張・{item.alert.time}</small>
          </div>
        </button>
        <button type="button" className="chip-emergency-close" aria-label="關閉緊急通知" onClick={() => closeMomentumToast(item.id)}><X /></button>
      </article>})}
    </div>}
    <section
    className={`chip-alert-ticker ${hasAlerts ? "has-alerts" : ""} ${hasShortAlerts ? "has-short-alerts" : ""} ${expanded ? "is-expanded" : ""}`}
    aria-label="熱門股與電子股大單動能提醒"
    title={data?.notice}
  >
    <TaiwanIndexPulseBar data={data} marketSnapshot={marketSnapshot} />
    <div className="chip-alert-row long-row">
      <button className="chip-alert-label" type="button" onClick={() => setExpanded((current) => current === "long" ? null : "long")} aria-expanded={expanded === "long"}>
        {data?.warningCount ? <AlertTriangle size={14} /> : hasAlerts ? <Zap size={14} /> : <Radio size={13} />}
        <strong>{label}</strong><em>{data?.warningCount ? `${data.warningCount} 警示` : "多方"}</em>
        {expanded === "long" ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
      </button>
      <div className="chip-alert-viewport" aria-live="polite">
        {alerts.length === 1 ? <div className="chip-alert-group chip-alert-single"><AlertItems alerts={alerts} windowMinutes={data?.windowMinutes ?? 5} onSelectStock={selectStock} /></div> : hasAlerts ? <div className="chip-alert-track">
          <div className="chip-alert-group"><AlertItems alerts={scrollingAlerts} windowMinutes={data?.windowMinutes ?? 5} onSelectStock={selectStock} /></div>
          <div className="chip-alert-group" aria-hidden="true"><AlertItems alerts={scrollingAlerts} windowMinutes={data?.windowMinutes ?? 5} onSelectStock={selectStock} /></div>
        </div> : <span className="chip-alert-message">{statusMessage(data)}</span>}
      </div>
      {data && <small className="chip-alert-coverage">多方 {alerts.length}・掃描 {data.scannedCount}/{data.candidateCount}</small>}
    </div>
    <div className="chip-alert-row short-row">
      <button className="chip-alert-label" type="button" onClick={() => setExpanded((current) => current === "short" ? null : "short")} aria-expanded={expanded === "short"}>
        {hasShortAlerts ? <TrendingDown size={14} /> : <Radio size={13} />}
        <strong>{data?.marketOpen ? "盤中空方大單動能" : "空方動能暫停"}</strong><em>{hasShortAlerts ? `${shortAlerts.length} 檔` : "空方"}</em>
        {expanded === "short" ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
      </button>
      <div className="chip-alert-viewport" aria-live="polite">
        {shortAlerts.length === 1 ? <div className="chip-alert-group chip-alert-single short-group"><AlertItems alerts={shortAlerts} direction="short" windowMinutes={data?.windowMinutes ?? 5} onSelectStock={selectStock} /></div> : hasShortAlerts ? <div className="chip-alert-track">
          <div className="chip-alert-group short-group"><AlertItems alerts={shortAlerts} direction="short" windowMinutes={data?.windowMinutes ?? 5} onSelectStock={selectStock} /></div>
          <div className="chip-alert-group short-group" aria-hidden="true"><AlertItems alerts={shortAlerts} direction="short" windowMinutes={data?.windowMinutes ?? 5} onSelectStock={selectStock} /></div>
        </div> : <span className="chip-alert-message">{data?.marketOpen ? "目前尚未偵測到大戶持續加空" : "目前非盤中時段，空方動能監控暫停"}</span>}
      </div>
      {data && <small className="chip-alert-coverage">空方 {data.shortCount ?? 0}・持續加空 {data.shortStrengtheningCount ?? 0}</small>}
    </div>
    {expanded && data && <MomentumPanel
      data={data}
      alerts={expandedAlerts}
      direction={expanded}
      pinnedSymbols={pinnedSet}
      trackedSymbols={expanded === "short" ? trackedShortPanelSymbols : trackedPanelSymbols}
      alertSymbols={expanded === "short" ? shortAlertPanelSymbols : alertPanelSymbols}
      quotes={momentumQuotes}
      priceHistory={momentumPriceHistory}
      quoteLoading={quoteLoading}
      deductionSignals={deductionSignals}
      deductionLoading={deductionLoading}
      onTogglePin={togglePin}
      onClose={() => setExpanded(null)}
      onSelectStock={selectStock}
    />}
  </section>
  </>;
}
