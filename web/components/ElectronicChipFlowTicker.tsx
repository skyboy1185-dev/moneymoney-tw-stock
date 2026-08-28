"use client";

import { useEffect, useRef, useState } from "react";
import {
  Activity, AlertTriangle, ChevronDown, ChevronUp, Crosshair, Radio,
  Pin, TrendingDown, TrendingUp, X, Zap,
} from "lucide-react";
import type {
  ElectronicChipFlowAlert,
  ElectronicChipFlowAlertsResponse,
  ElectronicChipFlowPricePoint,
  ElectronicChipFlowQuote,
} from "@/lib/electronic-chip-flow-alerts";
import type { StockDeductionSignals } from "@/lib/deduction-signals";
import type { MarketSnapshot } from "@/lib/market-types";
import type { MarketIndexDefenseResponse } from "@/lib/market-index-defense";
import { evaluateThreeGateLevels } from "@/lib/three-gate-price";
import { detectGroupResonances } from "@/lib/group-resonance";
import { evaluateLargeOrderOutcomes } from "@/lib/large-order-outcome";
import { selectLargeOrderMomentumToastCandidates, selectLargeOrderRankings } from "@/lib/electronic-chip-flow-rankings";
import { buildDingSelectionRows, type DingSelectionRow } from "@/lib/ding-selection";
import { buildTaiwanIndexKeyLevels, formatIndexLevel } from "@/lib/taiwan-index-key-levels";

interface ElectronicChipFlowTickerProps {
  onSelectStock?: (symbol: string) => void;
  marketSnapshot?: MarketSnapshot | null;
}

interface MomentumToast {
  id: string;
  alert: ElectronicChipFlowAlert;
  kind: "reinforced" | "joint" | "surge";
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
const MOMENTUM_BAR_LAYOUT_KEY = "twse:momentum-bar-layout";
const PINNED_TECHNICAL_REFRESH_MS = 5_000;
const ACTIVE_MONITOR_QUOTE_REFRESH_MS = 2_000;

type MomentumBarLayout = "compact" | "classic";
type ExpandedMomentumPanel = "long" | "short" | "ding";

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
  if (!data.marketOpen || data.status === "closed") return "非交易時段不更新 Top10；若目前為 0，通常是盤後尚無累計或後端重新部署後等待下個交易日重新累積";
  if (data.providerRateLimited) {
    return `Fugle 額度暫時限流，約 ${data.providerRetrySeconds ?? 0} 秒後自動續掃`;
  }
  if (data.status === "warming" || data.status === "scanning") {
    return `熱門股＋電子股輪巡中 ${data.scannedCount}/${data.candidateCount} 檔`;
  }
  return "目前尚無開盤累計Top10資料";
}

function TrendIcon({ alert }: { alert: ElectronicChipFlowAlert }) {
  if (alert.isWarning) return <TrendingDown size={12} />;
  if (alert.direction === "short") return <TrendingDown size={12} />;
  if (alert.reinforced || alert.simultaneousIncrease) return <TrendingUp size={12} />;
  return <Activity size={11} />;
}

function TaiwanIndexPulseBar({
  data,
  marketSnapshot,
  marketDefense,
}: {
  data: ElectronicChipFlowAlertsResponse | null;
  marketSnapshot?: MarketSnapshot | null;
  marketDefense?: MarketIndexDefenseResponse | null;
}) {
  const pulse = data?.marketPulse;
  const futures = marketSnapshot?.context;
  const pulseLive = data?.marketOpen && pulse?.status === "realtime";
  const direction = pulseLive ? pulse.direction : "neutral";
  const trendLabel = pulseLive ? pulse.trendLabel : data?.marketOpen ? "大／小單暖機中" : "現貨收盤・停止更新";
  const directionLabel = pulseLive ? pulse.directionLabel : data?.marketOpen ? "等待判斷" : "盤後";
  const referencePrice = (futures?.futuresPrice && futures.futuresPrice > 0)
    ? futures.futuresPrice
    : futures?.indexPrice;
  const keyLevels = buildTaiwanIndexKeyLevels(futures, marketDefense);
  const pivotText = keyLevels.pivot
    ? `多空 ${formatIndexLevel(keyLevels.pivot.value)}（${keyLevels.pivot.source}）`
    : "多空 等待資料";
  const supportText = keyLevels.support
    ? `支撐 ${formatIndexLevel(keyLevels.support.low)}～${formatIndexLevel(keyLevels.support.high)}（${keyLevels.support.source}）`
    : "支撐 等待資料";
  const downsideText = keyLevels.downsideTargets.length
    ? `下看 ${keyLevels.downsideTargets.map((item) => `${formatIndexLevel(item.value)}（${item.source}）`).join("／")}`
    : "下看 資料不足";
  return <div
    className={`taiwan-index-pulse direction-${direction}`}
    title={`${pulse?.source ?? "監控池逐筆成交方向聚合推估"}；台指期價格採官方行情，大／小單不是期貨投資人身分資料。`}
  >
    <div className="taiwan-index-pulse-title">
      <Activity size={14} /><strong>目前台指盤勢</strong><em>市場推估</em>
    </div>
    <div className="taiwan-index-pulse-futures">
      <small>台指期 {futures?.futuresContract ?? ""}</small>
      <strong>{referencePrice ? formatLots(referencePrice) : "—"}</strong>
      <span className={(futures?.futuresChangePercent ?? 0) > 0 ? "up" : (futures?.futuresChangePercent ?? 0) < 0 ? "down" : ""}>
        {futures ? `${formatSigned(futures.futuresChange)}（${formatSigned(futures.futuresChangePercent)}%）` : "行情待補"}
      </span>
    </div>
    <div
      className={`taiwan-index-key-levels state-${keyLevels.tone}`}
      title={keyLevels.title}
    >
      <small><Crosshair size={11} />{keyLevels.tradeDateLabel} 關鍵</small>
      <strong>{keyLevels.stateLabel}</strong>
      <span className="pivot">{pivotText}</span>
      <span className="support">{supportText}</span>
      <span className="downside">{downsideText}</span>
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
      const sessionBased = alert.rankingBasis === "session";
      const momentumCaption = sessionBased ? "開盤累計" : `近 ${windowMinutes} 分`;
      return <button
        className={`chip-alert-item level-${alert.alertLevel}`}
        key={`${alert.symbol}-${index}`}
        type="button"
        onClick={() => onSelectStock(alert.symbol)}
        title={`${alert.name} ${alert.symbol}｜${alert.message}｜大單資料為推估值`}
      >
        {alert.rank && <span className="chip-alert-rank">#{alert.rank}</span>}
        <strong>{alert.name}</strong>
        <b>{alert.symbol}</b>
        <em><TrendIcon alert={alert} />{alert.simultaneousIncrease ? (direction === "short" ? "大小單同步偏空" : "大小單同步增加") : alert.trendLabel}</em>
        <span>今日累積｜大單 多 {formatLots(flow.dayLargeLong)}／空 {formatLots(flow.dayLargeShort)}・散戶 多 {formatLots(flow.dayRetailLong)}／空 {formatLots(flow.dayRetailShort)} 張</span>
        <small>{alert.simultaneousIncrease ? `合力 ${formatSigned(alert.combinedNetBuyLots)}` : `${direction === "short" ? "賣壓" : "動能"} ${formatSigned(alert.momentumChangeLots)}`}・{momentumCaption}・{alert.time}</small>
      </button>
    })}
  </>;
}

function CompactSignalPills({
  alerts,
  direction,
  windowMinutes,
  onSelectStock,
}: {
  alerts: ElectronicChipFlowAlert[];
  direction: "long" | "short";
  windowMinutes: number;
  onSelectStock: (symbol: string) => void;
}) {
  return <>
    {alerts.map((alert) => {
      const flow = orderFlow(alert);
      const momentumLots = direction === "short"
        ? alert.sessionNetSellLots ?? alert.recentNetSellLots ?? Math.max(0, -alert.recentNetBuyLots)
        : alert.sessionNetBuyLots ?? Math.max(0, alert.recentNetBuyLots);
      const momentumLabel = direction === "short" ? "賣壓" : "買盤";
      const momentumCaption = alert.rankingBasis === "session" ? "開盤累計" : `${windowMinutes}分`;
      return <button
        className={`chip-signal-pill ${direction} level-${alert.alertLevel}`}
        key={`${direction}-${alert.symbol}`}
        type="button"
        onClick={() => onSelectStock(alert.symbol)}
        title={`${alert.name} ${alert.symbol}｜${alert.message}｜點擊查看個股`}
      >
        <span>{alert.rank ? <b className="chip-signal-rank">#{alert.rank}</b> : (direction === "short" ? <TrendingDown size={11} /> : <TrendingUp size={11} />)}{direction === "short" ? "空" : "多"}</span>
        <strong>{alert.symbol} {alert.name}</strong>
        <b>{momentumLabel} {formatLots(momentumLots)} 張</b>
        <small>{alert.simultaneousIncrease ? "大小單同步" : alert.trendLabel}・{momentumCaption}・{alert.time}</small>
        <em>大 {formatLots(flow.largeLong)}／空 {formatLots(flow.largeShort)}</em>
      </button>;
    })}
  </>;
}

function MomentumBarLayoutToggle({
  layout,
  onChange,
}: {
  layout: MomentumBarLayout;
  onChange: (layout: MomentumBarLayout) => void;
}) {
  return <div className="chip-layout-toggle" aria-label="動能 BAR 版型切換">
    <button
      type="button"
      className={layout === "compact" ? "active" : ""}
      onClick={() => onChange("compact")}
    >精簡</button>
    <button
      type="button"
      className={layout === "classic" ? "active" : ""}
      onClick={() => onChange("classic")}
    >原版</button>
  </div>;
}

function CompactMomentumSummary({
  data,
  alerts,
  shortAlerts,
  dingRows,
  dingLoading,
  expanded,
  onToggleExpanded,
  onSelectStock,
}: {
  data: ElectronicChipFlowAlertsResponse | null;
  alerts: ElectronicChipFlowAlert[];
  shortAlerts: ElectronicChipFlowAlert[];
  dingRows: DingSelectionRow[];
  dingLoading: boolean;
  expanded: ExpandedMomentumPanel | null;
  onToggleExpanded: (direction: ExpandedMomentumPanel) => void;
  onSelectStock: (symbol: string) => void;
}) {
  const longTop = alerts.slice(0, 3);
  const shortTop = shortAlerts.slice(0, 3);
  const rankingLimit = data?.rankingLimit ?? 10;
  const hasSignals = longTop.length > 0 || shortTop.length > 0;
  return <div className="chip-compact-summary-row">
    <div className="chip-compact-actions">
      <button
        className={`chip-compact-side long ${expanded === "long" ? "active" : ""}`}
        type="button"
        onClick={() => onToggleExpanded("long")}
        aria-expanded={expanded === "long"}
      >
        <Zap size={13} /><strong>多方開盤累計Top{rankingLimit}</strong><b>{data?.longRankingCount ?? alerts.length}</b>
        <small>{data ? `顯示 ${alerts.length}/${rankingLimit}・正式 ${data.longCount ?? 0}・掃描 ${data.scannedCount}/${data.candidateCount}` : "載入中"}</small>
      </button>
      <button
        className={`chip-compact-side short ${expanded === "short" ? "active" : ""}`}
        type="button"
        onClick={() => onToggleExpanded("short")}
        aria-expanded={expanded === "short"}
      >
        <TrendingDown size={13} /><strong>空方開盤累計Top{rankingLimit}</strong><b>{data?.shortRankingCount ?? shortAlerts.length}</b>
        <small>顯示 {shortAlerts.length}/{rankingLimit}・正式 {data?.shortCount ?? 0}・加空 {data?.shortStrengtheningCount ?? 0}</small>
      </button>
      <button
        className={`chip-compact-side ding ${expanded === "ding" ? "active" : ""}`}
        type="button"
        onClick={() => onToggleExpanded("ding")}
        aria-expanded={expanded === "ding"}
      >
        <Crosshair size={13} /><strong>丁選股</strong><b>{dingRows.length}</b>
        <small>{dingLoading ? "扣抵比對中" : `Top${rankingLimit} 扣抵訊號`}</small>
      </button>
    </div>
    <div className="chip-compact-signals" aria-live="polite">
      {hasSignals ? <>
        <CompactSignalPills
          alerts={longTop}
          direction="long"
          windowMinutes={data?.windowMinutes ?? 5}
          onSelectStock={onSelectStock}
        />
        <CompactSignalPills
          alerts={shortTop}
          direction="short"
          windowMinutes={data?.windowMinutes ?? 5}
          onSelectStock={onSelectStock}
        />
      </> : <span className="chip-alert-message">{statusMessage(data)}</span>}
    </div>
    {data && <small className="chip-compact-status">
      Top收合持續偵測 {data.autoTopTrackingCount ?? 0}・釘選加碼 {data.extraPinnedTrackingCount ?? 0}/{data.extraPinnedTrackingLimit ?? 10}・高頻 {data.highFrequencyTrackingCount}・{data.marketOpen ? "盤中監控" : "盤後停止更新"}
    </small>}
  </div>;
}

// Kept for rollback/reference, but the live ticker uses only the summary entry
// row now. Showing both made Top10 look duplicated when the same rankings were
// rendered twice.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
function Top10QuickRows({
  data,
  alerts,
  shortAlerts,
  dingRows,
  dingLoading,
  expanded,
  onToggleExpanded,
  onSelectStock,
}: {
  data: ElectronicChipFlowAlertsResponse | null;
  alerts: ElectronicChipFlowAlert[];
  shortAlerts: ElectronicChipFlowAlert[];
  dingRows: DingSelectionRow[];
  dingLoading: boolean;
  expanded: ExpandedMomentumPanel | null;
  onToggleExpanded: (direction: ExpandedMomentumPanel) => void;
  onSelectStock: (symbol: string) => void;
}) {
  const rankingLimit = data?.rankingLimit ?? 10;
  const statusLabel = data ? (data.marketOpen ? "盤中累計" : "非交易保留") : "載入中";
  const rows = [
    { direction: "long" as const, title: `多方開盤累計大單買入 Top${rankingLimit}`, items: alerts.slice(0, rankingLimit), total: data?.longRankingCount ?? alerts.length },
    { direction: "short" as const, title: `空方開盤累計大單賣出 Top${rankingLimit}`, items: shortAlerts.slice(0, rankingLimit), total: data?.shortRankingCount ?? shortAlerts.length },
  ];
  const forceLots = (alert: ElectronicChipFlowAlert, direction: "long" | "short") => direction === "short"
    ? alert.sessionNetSellLots ?? alert.recentNetSellLots ?? Math.max(0, -alert.recentNetBuyLots)
    : alert.sessionNetBuyLots ?? Math.max(0, alert.recentNetBuyLots);
  return <div className="chip-top10-quick" aria-label="多空開盤累計大單Top10快速列">
    {rows.map((row) => <div className={`chip-top10-row ${row.direction}`} key={row.direction}>
      <button
        type="button"
        className={`chip-top10-title ${expanded === row.direction ? "active" : ""}`}
        onClick={() => onToggleExpanded(row.direction)}
        aria-expanded={expanded === row.direction}
      >
        {row.direction === "long" ? <Zap size={12} /> : <TrendingDown size={12} />}
        <strong>{row.title}</strong>
        <small>{row.items.length ? `${row.items.length}/${rankingLimit}` : `${statusLabel}・0/${rankingLimit}`}</small>
      </button>
      <div className="chip-top10-list">
        {row.items.length ? row.items.map((alert, index) => <button
          type="button"
          className="chip-top10-chip"
          key={`${row.direction}-${alert.symbol}`}
          title={`${row.title}｜#${alert.rank ?? index + 1} ${alert.symbol} ${alert.name}｜${formatLots(forceLots(alert, row.direction))}張`}
          onClick={() => onSelectStock(alert.symbol)}
        >
          <i>#{alert.rank ?? index + 1}</i>
          <b>{alert.symbol}</b>
          <span>{alert.name}</span>
          <em>{formatLots(forceLots(alert, row.direction))}張</em>
        </button>) : <span className="chip-top10-empty">{row.direction === "long" ? "多方Top10尚無資料" : "空方Top10尚無資料"}・{statusLabel}・API {row.total}</span>}
      </div>
    </div>)}
    <div className="chip-top10-row ding">
      <button
        type="button"
        className={`chip-top10-title ${expanded === "ding" ? "active" : ""}`}
        onClick={() => onToggleExpanded("ding")}
        aria-expanded={expanded === "ding"}
      >
        <Crosshair size={12} />
        <strong>丁選股｜扣抵訊號</strong>
        <small>{dingLoading ? "比對中" : `${dingRows.length}/${rankingLimit}`}</small>
      </button>
      <div className="chip-top10-list">
        {dingRows.length ? dingRows.map((row) => <button
          type="button"
          className="chip-top10-chip ding"
          key={`ding-${row.symbol}`}
          title={`丁選股｜#${row.sourceRank} ${row.symbol} ${row.name}｜${row.matches.length} 個扣抵訊號`}
          onClick={() => onSelectStock(row.symbol)}
        >
          <i>#{row.sourceRank}</i>
          <b>{row.symbol}</b>
          <span>{row.name}</span>
          <em>{row.matches.length}訊號</em>
        </button>) : <span className="chip-top10-empty">{dingLoading ? "丁選股扣抵比對中" : "目前Top10沒有丁選股扣抵訊號"}・{statusLabel}</span>}
      </div>
    </div>
  </div>;
}

// eslint-disable-next-line @typescript-eslint/no-unused-vars
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

// eslint-disable-next-line @typescript-eslint/no-unused-vars
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

// eslint-disable-next-line @typescript-eslint/no-unused-vars
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

// eslint-disable-next-line @typescript-eslint/no-unused-vars
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

// eslint-disable-next-line @typescript-eslint/no-unused-vars
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
  extraPinnedSymbols,
  trackedSymbols,
  alertSymbols,
  deductionSignals,
  quotes,
  onTogglePin,
  onClose,
  onSelectStock,
}: {
  data: ElectronicChipFlowAlertsResponse;
  alerts: ElectronicChipFlowAlert[];
  direction: "long" | "short";
  pinnedSymbols: Set<string>;
  extraPinnedSymbols: Set<string>;
  trackedSymbols: Set<string>;
  alertSymbols: Set<string>;
  deductionSignals: Record<string, StockDeductionSignals>;
  quotes: Record<string, ElectronicChipFlowQuote>;
  onTogglePin: (alert: ElectronicChipFlowAlert) => void;
  onClose: () => void;
  onSelectStock: (symbol: string) => void;
}) {
  const isShort = direction === "short";
  const rankingLimit = data.rankingLimit ?? 10;
  const rankedAlerts = alerts
    .slice(0, rankingLimit)
    .map((alert, index) => ({ ...alert, rank: alert.rank ?? index + 1 }))
    .sort((left, right) => (left.rank ?? 99) - (right.rank ?? 99));
  const rankedSymbols = new Set(rankedAlerts.map((alert) => alert.symbol));
  const pinnedTrackingAlerts = alerts.filter((alert) => {
    const ranked = rankedSymbols.has(alert.symbol);
    return !ranked && (
      pinnedSymbols.has(alert.symbol)
      || extraPinnedSymbols.has(alert.symbol)
      || trackedSymbols.has(alert.symbol)
      || !alertSymbols.has(alert.symbol)
    );
  });
  const groupResonances = data.marketOpen ? detectGroupResonances(rankedAlerts, quotes) : [];
  const maxRankScore = Math.max(1, ...rankedAlerts.map((alert) => alert.rankScore ?? 0));
  const sideLabel = isShort ? "賣壓" : "買盤";
  const ratioLabel = isShort ? "賣買比" : "買賣比";
  const stepLabel = isShort ? "連續偏空" : "連續偏多";

  const strengthMeta = (alert: ElectronicChipFlowAlert) => {
    if (alert.largeOrderOffsetting) return { label: "多空抵銷", tone: "caution" };
    if (alert.isWarning || alert.trend === "weakening" || alert.trend === "fading") return { label: "轉弱警示", tone: "warning" };
    const rank = alert.rank ?? rankingLimit + 1;
    if (rank <= 3) return { label: "極強", tone: "extreme" };
    if (rank <= 6) return { label: "強", tone: "strong" };
    return { label: "觀察", tone: "watch" };
  };

  const rowFacts = (alert: ElectronicChipFlowAlert) => {
    const sessionBased = alert.rankingBasis === "session";
    const forceLots = isShort
      ? sessionBased ? alert.sessionNetSellLots ?? alert.recentNetSellLots ?? Math.max(0, -alert.recentNetBuyLots) : alert.recentNetSellLots ?? Math.max(0, -alert.recentNetBuyLots)
      : sessionBased ? alert.sessionNetBuyLots ?? Math.max(0, alert.recentNetBuyLots) : Math.max(0, alert.recentNetBuyLots);
    const oppositeLots = isShort ? Math.max(0, alert.recentNetBuyLots) : (alert.recentNetSellLots ?? Math.max(0, -alert.recentNetBuyLots));
    const dayForceLots = isShort ? alert.sessionLargeSellLots ?? Math.max(0, -alert.largeNetLots) : alert.sessionLargeBuyLots ?? Math.max(0, alert.largeNetLots);
    const changeLots = isShort ? -alert.momentumChangeLots : alert.momentumChangeLots;
    const ratio = isShort ? alert.sessionSellBuyRatio ?? alert.sellBuyRatio ?? 0 : alert.sessionBuySellRatio ?? alert.buySellRatio;
    const steps = isShort ? alert.negativeSteps ?? 0 : alert.positiveSteps;
    const groupResonance = groupResonances.find((resonance) => resonance.symbols.includes(alert.symbol));
    const deductionMatchCount = deductionSignals[alert.symbol]?.matches.filter((match) =>
      match.signalDate <= data.tradeDate
    ).length ?? 0;
    const tags = [
      alert.currentQualifies ? "仍符合" : "追蹤中",
      deductionMatchCount ? `丁選股 ${deductionMatchCount}` : "",
      alert.reinforced ? (isShort ? "持續加空" : "持續轉強") : "",
      alert.simultaneousIncrease ? (isShort ? "大小單同步偏空" : "大小單同步") : "",
      alert.trendStreak >= 2 ? `${alert.trendStreak}段連續` : "",
      sessionBased ? "開盤累計" : "",
      alert.rankingFillReason === "gross" ? (isShort ? "賣買抵銷" : "買賣抵銷") : "",
      groupResonance ? `族群共振 ${groupResonance.group}` : "",
      alert.largeOrderOffsetting ? "多空抵銷" : "",
      alert.isWarning ? "轉弱" : "",
    ].filter(Boolean);
    return { forceLots, oppositeLots, dayForceLots, changeLots, ratio, steps, groupResonance, tags, sessionBased };
  };

  return <aside className={`chip-momentum-panel ${isShort ? "short-side" : "long-side"}`} aria-label={isShort ? "空方大單動能雷達" : "多方大單動能雷達"}>
    <header>
      <div>
        <strong>{isShort ? <TrendingDown size={14} /> : <Zap size={14} />}{isShort ? `空方開盤累計大單賣出 Top${rankingLimit}` : `多方開盤累計大單買入 Top${rankingLimit}`}</strong>
        <span>這裡就是{isShort ? "空方累計" : "多方累計"} bar 的展開排名；第 1～{rankingLimit} 名依開盤累計大單、強度、累計{sideLabel}、{ratioLabel}與是否續強排序。</span>
      </div>
      <div className="chip-momentum-summary">
        <span className={isShort ? "short" : "positive"}>{isShort ? <TrendingDown size={12} /> : <TrendingUp size={12} />}{isShort ? `空方Top${rankingLimit} ${rankedAlerts.length}/${rankingLimit}` : `多方Top${rankingLimit} ${rankedAlerts.length}/${rankingLimit}`}</span>
        <span className={isShort ? "short" : "positive"}>{isShort ? <TrendingDown size={12} /> : <TrendingUp size={12} />}{isShort ? `持續加空 ${data.shortStrengtheningCount ?? 0}` : `大小單同步 ${data.jointIncreaseCount}`}</span>
        {groupResonances.length > 0 && <span className="group-warning"><AlertTriangle size={12} />族群共振 {groupResonances.length} 組・強烈注意</span>}
        {pinnedSymbols.size > 0 && <span className="pinned"><Pin size={12} />已釘選 {pinnedSymbols.size}</span>}
        {(data.extraPinnedTrackingCount ?? 0) > 0 && <span className="pinned"><Pin size={12} />釘選加碼 {data.extraPinnedTrackingCount}/{data.extraPinnedTrackingLimit ?? 10}</span>}
        <small>收合仍持續偵測Top{rankingLimit}・釘選加碼 {data.extraPinnedTrackingCount ?? 0}/{data.extraPinnedTrackingLimit ?? 10}・監控池 {data.candidateCount}/{data.candidateTarget ?? data.candidateCount}</small>
      </div>
      <button type="button" onClick={onClose} aria-label="關閉大單動能雷達"><X size={15} /></button>
    </header>
    {((data.universeStatus && data.universeStatus !== "healthy") || data.candidateCount < (data.candidateTarget ?? data.candidateCount)) && <div className="chip-universe-notice" role="status">
      <AlertTriangle size={13} />
      <span>{data.universeNotice ?? `目前覆蓋 ${data.candidateCount}/${data.candidateTarget ?? data.candidateCount} 檔；官方來源或處置名單保護尚未完整，系統會持續重試。`}</span>
    </div>}
    <div className="chip-momentum-list">
      {rankedAlerts.length ? rankedAlerts.map((alert) => {
        const pinned = pinnedSymbols.has(alert.symbol);
        const strength = strengthMeta(alert);
        const facts = rowFacts(alert);
        const strengthPercent = Math.max(42, Math.min(100, ((alert.rankScore ?? 0) / maxRankScore) * 100));
        const dataState = data.marketOpen ? (alert.dataState ?? "warming") : "closed";
        const dataStateLabel = data.marketOpen
          ? (alert.dataStateLabel ?? "資料暖機中")
          : "盤後停止更新";
        return <article
          className={`chip-strength-row tone-${strength.tone} level-${alert.alertLevel} ${pinned ? "is-pinned" : ""} ${facts.groupResonance ? `has-group-resonance resonance-${facts.groupResonance.direction}` : ""}`}
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
          <div className="chip-strength-rank">
            <span>#{alert.rank}</span>
            <strong>{strength.label}</strong>
          </div>
          <div className="chip-strength-main">
            <div className="chip-strength-title">
              <strong>{alert.symbol} {alert.name}</strong>
              <span>{alert.industry}</span>
              <em><TrendIcon alert={alert} />{alert.trendLabel}</em>
              <span className={`chip-data-state state-${dataState}`}>{dataStateLabel}</span>
            </div>
            <div className="chip-strength-bar" aria-label={`強度 ${Math.round(strengthPercent)}%`}>
              <i style={{ width: `${strengthPercent}%` }} />
            </div>
            <div className="chip-strength-tags">
              {facts.tags.map((tag) => <span key={tag}>{tag}</span>)}
            </div>
          </div>
          <div className="chip-strength-score">
            <small>強度分</small>
            <strong>{Math.round(alert.rankScore ?? 0)}</strong>
          </div>
          <div className="chip-strength-metrics">
            <span><small>{facts.sessionBased ? "開盤累計" : `近 ${data.windowMinutes} 分`}{sideLabel}</small><strong>{formatLots(facts.forceLots)} 張</strong></span>
            <span><small>{ratioLabel}</small><strong>{facts.ratio ? `${facts.ratio.toFixed(2)}x` : "—"}</strong></span>
            <span><small>{stepLabel}</small><strong>{facts.steps} 段</strong></span>
            <span><small>較前次</small><strong className={facts.changeLots >= 0 ? "flow-long" : "flow-short"}>{formatSigned(facts.changeLots)} 張</strong></span>
            <span><small>今日累積</small><strong>{formatLots(facts.dayForceLots)} 張</strong></span>
            <span><small>反向大單</small><strong>{formatLots(facts.oppositeLots)} 張</strong></span>
          </div>
          <div className="chip-strength-actions">
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
          </div>
          <p>{isShort ? "空方開盤累計大單賣出" : "多方開盤累計大單買入"}第 {alert.rank ?? "?"} 名｜{alert.message}</p>
        </article>;
      }) : <div className="chip-momentum-empty">{isShort ? `目前尚無符合空方Top${rankingLimit}條件的標的；系統持續輪巡中。` : `目前尚無符合多方Top${rankingLimit}條件的標的；系統持續輪巡中。`}</div>}
      {pinnedTrackingAlerts.length > 0 && <section className="chip-pinned-followup" aria-label="釘選加碼追蹤">
        <header>
          <strong><Pin size={13} />釘選加碼追蹤</strong>
          <small>不列入Top{rankingLimit}排名，保留快速追蹤</small>
        </header>
        {pinnedTrackingAlerts.map((alert) => {
          const facts = rowFacts(alert);
          const extraPinned = extraPinnedSymbols.has(alert.symbol);
          const retained = pinnedSymbols.has(alert.symbol) && !extraPinned && !alertSymbols.has(alert.symbol);
          return <button
            type="button"
            className="chip-pinned-followup-row"
            key={`pinned-${alert.symbol}`}
            onClick={() => onSelectStock(alert.symbol)}
          >
            <strong>{alert.symbol} {alert.name}</strong>
            <span>{extraPinned ? "額外釘選追蹤" : retained ? "釘選保留" : "持續追蹤"}</span>
            <b>{sideLabel} {formatLots(facts.forceLots)} 張</b>
            <small>{facts.tags.slice(0, 3).join("・") || alert.trendLabel}</small>
          </button>;
        })}
      </section>}
    </div>
    <footer>強度分只代表大單動能條件一致度，非勝率；Top排名用於快速篩選，仍需搭配個股K線與風險控管。</footer>
  </aside>;
}

function DingSelectionPanel({
  rows,
  loading,
  error,
  tradeDate,
  onClose,
  onSelectStock,
}: {
  rows: DingSelectionRow[];
  loading: boolean;
  error: string;
  tradeDate?: string;
  onClose: () => void;
  onSelectStock: (symbol: string) => void;
}) {
  const timeframeLabel = { day: "日", week: "週", month: "月" } as const;
  const directionLabel = { low: "扣三低", high: "扣三高" } as const;
  return <aside className="chip-momentum-panel ding-side" aria-label="丁選股扣抵訊號">
    <header>
      <div>
        <strong><Crosshair size={14} />丁選股｜Top10 扣抵訊號</strong>
        <span>只從目前多空開盤累計 Top10 內比對；API 會先裁切至 {tradeDate ?? "當日"}，前端再排除較晚 signalDate。</span>
      </div>
      <div className="chip-momentum-summary">
        <span className="positive"><TrendingUp size={12} />丁選股 {rows.length}</span>
        {loading && <span className="pinned">比對中</span>}
        {error && <span className="warning"><AlertTriangle size={12} />{error}</span>}
      </div>
      <button type="button" onClick={onClose} aria-label="關閉丁選股面板"><X size={15} /></button>
    </header>
    <div className="chip-momentum-list ding-list">
      {rows.length ? rows.map((row) => {
        const headline = row.matches.map((match) =>
          `${timeframeLabel[match.timeframe]}${directionLabel[match.direction]}`
        ).join(" / ");
        const strongest = row.matches
          .slice()
          .sort((left, right) => Math.abs(right.deductionGapPercent) - Math.abs(left.deductionGapPercent))[0];
        return <article
          className="chip-strength-row tone-watch level-info ding-row"
          key={row.symbol}
          onClick={() => onSelectStock(row.symbol)}
          role="button"
          tabIndex={0}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              onSelectStock(row.symbol);
            }
          }}
        >
          <div className="chip-strength-rank"><span>#{row.sourceRank}</span><strong>丁</strong></div>
          <div className="chip-strength-main">
            <div className="chip-strength-title">
              <strong>{row.symbol} {row.name}</strong>
              <span>{headline}</span>
              <em>扣抵結構</em>
            </div>
            <div className="chip-strength-tags">
              {row.matches.map((match) => <span key={`${row.symbol}-${match.timeframe}-${match.direction}`}>
                {timeframeLabel[match.timeframe]}{directionLabel[match.direction]} {formatSigned(match.deductionGapPercent)}%
              </span>)}
            </div>
          </div>
          <div className="chip-strength-score">
            <small>訊號數</small>
            <strong>{row.matches.length}</strong>
          </div>
          <div className="chip-strength-metrics">
            <span><small>現價</small><strong>{row.currentPrice == null ? "無" : formatPrice(row.currentPrice)}</strong></span>
            <span><small>扣抵均值</small><strong>{strongest ? formatPrice(strongest.deductionAverage) : "無"}</strong></span>
            <span><small>訊號日期</small><strong>{strongest?.signalDate ?? "無"}</strong></span>
            <span><small>最新資料日</small><strong>{row.latestPriceDate ?? "無"}</strong></span>
            <span><small>計算時間</small><strong>{localTimeWithSeconds(row.calculatedAt)}</strong></span>
          </div>
          <p>丁選股只展示均線扣抵將移出的歷史 K 值；這不是未來價格預測，也不會直接升級為正式進場訊號。</p>
        </article>;
      }) : <div className="chip-momentum-empty">{loading ? "正在比對 Top10 的丁選股扣抵訊號…" : "目前多空 Top10 沒有符合丁選股扣抵條件的股票。"}</div>}
    </div>
    <footer>丁選股採 as-of 檢查：訊號日期晚於當前交易日會被排除；正式進場仍由各機器人自己的風控與資料新鮮度決定。</footer>
  </aside>;
}

export function ElectronicChipFlowTicker({ onSelectStock, marketSnapshot }: ElectronicChipFlowTickerProps) {
  const [data, setData] = useState<ElectronicChipFlowAlertsResponse | null>(null);
  const [, setTickerAlerts] = useState<ElectronicChipFlowAlert[]>([]);
  const [momentumToasts, setMomentumToasts] = useState<MomentumToast[]>([]);
  const [threeGateToasts, setThreeGateToasts] = useState<ThreeGateToast[]>([]);
  const tickerSignature = useRef("");
  const urgentSignatures = useRef(new Map<string, string>());
  const toastTimers = useRef<number[]>([]);
  const threeGateNotificationSignatures = useRef(new Set<string>());
  const threeGateLastPrices = useRef(new Map<string, number>());
  const threeGateToastTimers = useRef<number[]>([]);
  const [expanded, setExpanded] = useState<ExpandedMomentumPanel | null>(null);
  const [pinnedSymbols, setPinnedSymbols] = useState<string[]>([]);
  const pinnedSymbolsRef = useRef<string[]>([]);
  const clientIdRef = useRef("");
  const expandedTrackingSymbolsRef = useRef<string[]>([]);
  const [pinnedAlertSnapshots, setPinnedAlertSnapshots] = useState<ElectronicChipFlowAlert[]>([]);
  const [momentumQuotes, setMomentumQuotes] = useState<Record<string, ElectronicChipFlowQuote>>({});
  const [deductionSignals, setDeductionSignals] = useState<Record<string, StockDeductionSignals>>({});
  const [deductionLoading, setDeductionLoading] = useState(false);
  const [deductionError, setDeductionError] = useState("");
  const [marketDefense, setMarketDefense] = useState<MarketIndexDefenseResponse | null>(null);
  const [barLayout, setBarLayout] = useState<MomentumBarLayout>("compact");

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
      const storedLayout = window.localStorage.getItem(MOMENTUM_BAR_LAYOUT_KEY);
      if (storedLayout === "classic" || storedLayout === "compact") setBarLayout(storedLayout);
    } catch {
      clientIdRef.current = window.crypto.randomUUID();
      window.localStorage.removeItem(PINNED_MOMENTUM_SYMBOLS_KEY);
      window.localStorage.removeItem(PINNED_MOMENTUM_ALERTS_KEY);
    }
  }, []);

  const changeBarLayout = (nextLayout: MomentumBarLayout) => {
    setBarLayout(nextLayout);
    window.localStorage.setItem(MOMENTUM_BAR_LAYOUT_KEY, nextLayout);
  };

  useEffect(() => {
    let timer: number | null = null;
    let stopped = false;
    const load = async () => {
      try {
        const response = await fetch("/api/market-index/defense", { cache: "no-store" });
        const payload = await response.json() as MarketIndexDefenseResponse & { error?: string };
        if (!response.ok || payload.error) throw new Error(payload.error ?? "market defense unavailable");
        if (!stopped) setMarketDefense(payload);
      } catch {
        if (!stopped) setMarketDefense(null);
      } finally {
        if (!stopped) {
          const refreshMs = marketSnapshot?.marketOpen || marketSnapshot?.futuresMarketOpen ? 60_000 : 300_000;
          timer = window.setTimeout(load, refreshMs);
        }
      }
    };
    void load();
    return () => {
      stopped = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [marketSnapshot?.marketOpen, marketSnapshot?.futuresMarketOpen]);

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
          new Map(selectLargeOrderRankings(payload, "long").map((alert) => [alert.symbol, alert])).values(),
        );
        // Counts and raw BAR values remain live in the expanded panel. The
        // marquee changes only when a stock enters/leaves or its trend state
        // actually changes, so a repeated occurrence does not restart it.
        const nextSignature = nextTickerAlerts.map((alert) => [
          alert.rank ?? 0,
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
        selectLargeOrderMomentumToastCandidates(payload).forEach(({ alert, kind }) => {
          const signature = `${kind}:1`;
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
              const priority = { joint: 3, reinforced: 2, surge: 1 } as const;
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
              item.kind === "surge" ? 12_000 : 10_000,
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

  const longRankingAlerts = selectLargeOrderRankings(data, "long");
  const shortRankingAlerts = selectLargeOrderRankings(data, "short");
  const rankingLimit = data?.rankingLimit ?? 10;
  const runningLongTop10 = longRankingAlerts.slice(0, rankingLimit);
  const runningShortTop10 = shortRankingAlerts.slice(0, rankingLimit);
  const alerts = runningLongTop10;
  const shortAlerts = runningShortTop10;
  const disposedSymbols = new Set(data?.disposedExcludedSymbols ?? []);
  const pinnedSet = new Set(pinnedSymbols);
  const dingSourceAlerts = Array.from(new Map([
    ...runningLongTop10,
    ...runningShortTop10,
  ].map((alert) => [alert.symbol, alert])).values());
  const dingRequestPayload = dingSourceAlerts.length
    ? JSON.stringify(dingSourceAlerts.map((alert) => ({
        symbol: alert.symbol,
        name: alert.name,
        market: alert.market,
      })).sort((left, right) => left.symbol.localeCompare(right.symbol)))
    : "";
  const dingRows = buildDingSelectionRows(
    Object.values(deductionSignals),
    dingSourceAlerts,
    data?.tradeDate ?? "0000-00-00",
    rankingLimit,
  );
  const autoTopSymbols = new Set([
    ...longRankingAlerts.map((alert) => alert.symbol),
    ...shortRankingAlerts.map((alert) => alert.symbol),
  ]);
  const extraPinnedTrackingSymbols = new Set(
    pinnedSymbols
      .filter((symbol) => !autoTopSymbols.has(symbol) && !disposedSymbols.has(symbol))
      .slice(0, data?.extraPinnedTrackingLimit ?? 10),
  );
  const sortRankedThenPinned = (left: ElectronicChipFlowAlert, right: ElectronicChipFlowAlert) => {
    const leftRank = left.rank ?? Number.POSITIVE_INFINITY;
    const rightRank = right.rank ?? Number.POSITIVE_INFINITY;
    if (leftRank !== rightRank) return leftRank - rightRank;
    const leftPinnedIndex = pinnedSymbols.indexOf(left.symbol);
    const rightPinnedIndex = pinnedSymbols.indexOf(right.symbol);
    if (leftPinnedIndex >= 0 && rightPinnedIndex >= 0) return leftPinnedIndex - rightPinnedIndex;
    if (leftPinnedIndex >= 0) return 1;
    if (rightPinnedIndex >= 0) return -1;
    return left.symbol.localeCompare(right.symbol);
  };
  const trackedPanelAlerts = data?.trackedAlerts ?? [];
  const liveLongPanelAlerts = longRankingAlerts;
  const liveLongPanelSymbols = new Set([
    ...liveLongPanelAlerts.map((alert) => alert.symbol),
    ...trackedPanelAlerts.map((alert) => alert.symbol),
  ]);
  const livePanelAlerts = Array.from(
    new Map([...liveLongPanelAlerts, ...trackedPanelAlerts].map((alert) => [alert.symbol, alert])).values(),
  );
  const trackedPanelSymbols = new Set(trackedPanelAlerts.map((alert) => alert.symbol));
  const alertPanelSymbols = new Set(longRankingAlerts.map((alert) => alert.symbol));
  const panelAlerts = [
    ...livePanelAlerts.map((alert) => [alert.symbol, alert] as const),
    ...pinnedAlertSnapshots
      .filter((alert) => !disposedSymbols.has(alert.symbol) && !liveLongPanelSymbols.has(alert.symbol))
      .map((alert) => [alert.symbol, alert] as const),
  ].map(([, alert]) => alert).sort(sortRankedThenPinned);
  const trackedShortPanelAlerts = data?.trackedShortAlerts ?? [];
  const liveShortRankedAlerts = shortRankingAlerts;
  const liveShortPanelSymbols = new Set([
    ...liveShortRankedAlerts.map((alert) => alert.symbol),
    ...trackedShortPanelAlerts.map((alert) => alert.symbol),
  ]);
  const liveShortPanelAlerts = Array.from(
    new Map([...liveShortRankedAlerts, ...trackedShortPanelAlerts].map((alert) => [alert.symbol, alert])).values(),
  );
  const trackedShortPanelSymbols = new Set(trackedShortPanelAlerts.map((alert) => alert.symbol));
  const shortAlertPanelSymbols = new Set(shortAlerts.map((alert) => alert.symbol));
  const shortPanelAlerts = [
    ...liveShortPanelAlerts.map((alert) => [alert.symbol, alert] as const),
    ...pinnedAlertSnapshots
      .filter((alert) => alert.direction === "short" && !disposedSymbols.has(alert.symbol) && !liveShortPanelSymbols.has(alert.symbol))
      .map((alert) => [alert.symbol, alert] as const),
  ].map(([, alert]) => alert).sort(sortRankedThenPinned);
  const expandedTradeSide = expanded === "long" || expanded === "short" ? expanded : null;
  const expandedAlerts = expandedTradeSide === "short" ? shortPanelAlerts : panelAlerts;
  const monitoredQuoteAlerts = Array.from(new Map([
    ...pinnedAlertSnapshots
      .filter((alert) => pinnedSet.has(alert.symbol) && !disposedSymbols.has(alert.symbol))
      .map((alert) => [alert.symbol, alert] as const),
    ...(expandedTradeSide ? expandedAlerts.map((alert) => [alert.symbol, alert] as const) : []),
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
  ]).values());
  const technicalSignalRequestPayload = technicalSignalAlerts.length
    ? JSON.stringify(technicalSignalAlerts.map((alert) => ({
        symbol: alert.symbol,
        name: alert.name,
        market: alert.market,
      })).sort((left, right) => left.symbol.localeCompare(right.symbol)))
    : "";
  const pinnedSymbolsSignature = [...pinnedSymbols].sort().join(",");
  const expandedTrackingSymbolsSignature = expandedTradeSide
    ? (expandedTradeSide === "short" ? shortAlerts : alerts)
      .map((alert) => alert.symbol)
      .slice(0, rankingLimit)
      .join(",")
    : "";

  useEffect(() => {
    expandedTrackingSymbolsRef.current = expandedTrackingSymbolsSignature
      ? expandedTrackingSymbolsSignature.split(",")
      : [];
  }, [expandedTrackingSymbolsSignature]);

  useEffect(() => {
    if (!dingRequestPayload) {
      setDeductionSignals({});
      setDeductionError("");
      setDeductionLoading(false);
      return;
    }
    let stopped = false;
    const controller = new AbortController();
    setDeductionLoading(true);
    void fetch("/api/market-data/deduction-signals", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: `{"asOfDate":${JSON.stringify(data?.tradeDate ?? null)},"items":${dingRequestPayload}}`,
      cache: "no-store",
      signal: controller.signal,
    }).then(async (response) => {
      const payload = await response.json() as { items?: StockDeductionSignals[]; error?: string };
      if (!response.ok) throw new Error(payload.error ?? `deduction signals ${response.status}`);
      if (stopped) return;
      setDeductionSignals(Object.fromEntries((payload.items ?? []).map((item) => [item.symbol, item])));
      setDeductionError("");
    }).catch((error) => {
      if (!stopped && (error as Error).name !== "AbortError") {
        setDeductionError("扣抵訊號暫時無法取得");
      }
    }).finally(() => {
      if (!stopped) setDeductionLoading(false);
    });
    return () => {
      stopped = true;
      controller.abort();
    };
  }, [dingRequestPayload, data?.tradeDate]);

  useEffect(() => {
    if (!technicalSignalRequestPayload || !data?.marketOpen) {
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
          if (pinnedSetForRequest.size > 0 && data?.marketOpen) {
            const refreshMs = PINNED_TECHNICAL_REFRESH_MS;
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
  }, [data?.marketOpen, data?.tradeDate, pinnedSymbolsSignature, technicalSignalRequestPayload]);

  useEffect(() => {
    if (!quoteRequestPayload) {
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
        }
      } catch (error) {
        if (!stopped && (error as Error).name !== "AbortError") {
          // Keep the last valid quote visible while the next automatic retry is
          // pending. Its timestamp still makes stale data explicit to the user.
        }
      } finally {
        if (!stopped) {
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
  const longLabel = data?.marketOpen ? `多方開盤累計大單買入 Top${rankingLimit}` : `多方今日累計Top${rankingLimit}`;
  const toggleExpanded = (direction: ExpandedMomentumPanel) => setExpanded((current) => current === direction ? null : direction);
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
          <span className="chip-emergency-icon">{item.kind === "surge" ? <Zap /> : <TrendingUp />}</span>
          <div>
            <strong>{item.kind === "joint" ? "大小單同步增加" : item.kind === "reinforced" ? "大單急增・持續轉強" : "大單急增"}</strong>
            <h4>{item.alert.symbol} {item.alert.name}</h4>
            <p>{item.alert.message}</p>
            <small>近 {data?.windowMinutes ?? 5} 分｜大單 多 {formatLots(flow.largeLong)}／空 {formatLots(flow.largeShort)}｜散戶 多 {formatLots(flow.retailLong)}／空 {formatLots(flow.retailShort)} 張・{item.alert.time}</small>
          </div>
        </button>
        <button type="button" className="chip-emergency-close" aria-label="關閉緊急通知" onClick={() => closeMomentumToast(item.id)}><X /></button>
      </article>})}
    </div>}
    <section
    className={`chip-alert-ticker layout-${barLayout} ${hasShortAlerts ? "has-short-alerts" : ""} ${expanded ? "is-expanded" : ""}`}
    aria-label="熱門股與電子股大單動能提醒"
    title={data?.notice}
  >
    <TaiwanIndexPulseBar data={data} marketSnapshot={marketSnapshot} marketDefense={marketDefense} />
    <MomentumBarLayoutToggle layout={barLayout} onChange={changeBarLayout} />
    {barLayout === "compact" ? <CompactMomentumSummary
      data={data}
      alerts={alerts}
      shortAlerts={shortAlerts}
      dingRows={dingRows}
      dingLoading={deductionLoading}
      expanded={expanded}
      onToggleExpanded={toggleExpanded}
      onSelectStock={selectStock}
    /> : <>
      <div className="chip-alert-row long-row">
        <button className="chip-alert-label" type="button" onClick={() => toggleExpanded("long")} aria-expanded={expanded === "long"}>
          {hasAlerts ? <Zap size={14} /> : <Radio size={13} />}
          <strong>{longLabel}</strong><em>{alerts.length} 檔</em>
          {expanded === "long" ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
        </button>
        <div className="chip-alert-viewport" aria-live="polite">
          {alerts.length === 1 ? <div className="chip-alert-group chip-alert-single"><AlertItems alerts={alerts} windowMinutes={data?.windowMinutes ?? 5} onSelectStock={selectStock} /></div> : hasAlerts ? <div className="chip-alert-track">
            <div className="chip-alert-group"><AlertItems alerts={scrollingAlerts} windowMinutes={data?.windowMinutes ?? 5} onSelectStock={selectStock} /></div>
            <div className="chip-alert-group" aria-hidden="true"><AlertItems alerts={scrollingAlerts} windowMinutes={data?.windowMinutes ?? 5} onSelectStock={selectStock} /></div>
          </div> : <span className="chip-alert-message">{statusMessage(data)}</span>}
        </div>
        {data && <small className="chip-alert-coverage">多方Top{rankingLimit} 顯示 {alerts.length}/{rankingLimit}・正式 {data.longCount ?? 0}・Top收合偵測 {data.autoTopTrackingCount ?? 0}</small>}
      </div>
      <div className="chip-alert-row short-row">
        <button className="chip-alert-label" type="button" onClick={() => toggleExpanded("short")} aria-expanded={expanded === "short"}>
          {hasShortAlerts ? <TrendingDown size={14} /> : <Radio size={13} />}
          <strong>{data?.marketOpen ? `空方開盤累計大單賣出 Top${rankingLimit}` : `空方今日累計Top${rankingLimit}`}</strong><em>{hasShortAlerts ? `${shortAlerts.length} 檔` : "空方"}</em>
          {expanded === "short" ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
        </button>
        <div className="chip-alert-viewport" aria-live="polite">
          {shortAlerts.length === 1 ? <div className="chip-alert-group chip-alert-single short-group"><AlertItems alerts={shortAlerts} direction="short" windowMinutes={data?.windowMinutes ?? 5} onSelectStock={selectStock} /></div> : hasShortAlerts ? <div className="chip-alert-track">
            <div className="chip-alert-group short-group"><AlertItems alerts={shortAlerts} direction="short" windowMinutes={data?.windowMinutes ?? 5} onSelectStock={selectStock} /></div>
            <div className="chip-alert-group short-group" aria-hidden="true"><AlertItems alerts={shortAlerts} direction="short" windowMinutes={data?.windowMinutes ?? 5} onSelectStock={selectStock} /></div>
          </div> : <span className="chip-alert-message">{data?.marketOpen ? "目前尚未累計到空方Top資料" : "今日尚無空方累計資料"}</span>}
        </div>
        {data && <small className="chip-alert-coverage">空方Top{rankingLimit} 顯示 {shortAlerts.length}/{rankingLimit}・正式 {data.shortCount ?? 0}・持續加空 {data.shortStrengtheningCount ?? 0}</small>}
      </div>
    </>}
    {expandedTradeSide && data && <MomentumPanel
      data={data}
      alerts={expandedAlerts}
      direction={expandedTradeSide}
      pinnedSymbols={pinnedSet}
      extraPinnedSymbols={extraPinnedTrackingSymbols}
      trackedSymbols={expandedTradeSide === "short" ? trackedShortPanelSymbols : trackedPanelSymbols}
      alertSymbols={expandedTradeSide === "short" ? shortAlertPanelSymbols : alertPanelSymbols}
      deductionSignals={deductionSignals}
      quotes={momentumQuotes}
      onTogglePin={togglePin}
      onClose={() => setExpanded(null)}
      onSelectStock={selectStock}
    />}
    {expanded === "ding" && <DingSelectionPanel
      rows={dingRows}
      loading={deductionLoading}
      error={deductionError}
      tradeDate={data?.tradeDate}
      onClose={() => setExpanded(null)}
      onSelectStock={selectStock}
    />}
  </section>
  </>;
}

