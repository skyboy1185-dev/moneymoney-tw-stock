import { autoScreeningEngine } from "@/engines/AutoScreeningEngine";
import { recentEvents, saveEvent } from "@/database/event-store";
import type { MarketContext, MarketForceInput, MarketSnapshot, MetricCard, SystemEvent, TimelinePoint } from "@/lib/market-types";
import { calculateMarketForce } from "@/market/MarketForceCalculator";
import { marketRegimeDetector } from "@/market/MarketRegimeDetector";
import { strategySelector } from "@/market/StrategySelector";
import { marketDataProvider } from "@/services/market-data/mock-provider";
import { officialMarketDirectionProvider as marketDirectionProvider } from "@/services/market-direction/official-provider";

let cachedSnapshot: MarketSnapshot | null = null;
let cachedAt = 0;
let lockedManualStrategies = ["sideways-breakout"];

const directionLabel: Record<string, string> = {
  strong_bull: "強多", bull: "偏多", sideways: "盤整",
  bear: "偏空", strong_bear: "強空", transition: "多空轉折",
};

type MetricChanges = Pick<MetricCard, "change1m" | "change3m" | "change10m">;

function metric(
  id: string,
  label: string,
  value: number,
  unit: string,
  updatedAt: string,
  factor = 1,
  source?: string,
  detail?: string,
  isOfficial?: boolean,
  changes?: MetricChanges | null,
): MetricCard {
  const simulatedChanges = {
    change1m: value * .006 * factor,
    change3m: value * .014 * factor,
    change10m: value * .028 * factor,
  };
  return {
    id, label, value, unit,
    ...(changes === undefined ? simulatedChanges : changes ?? { change1m: 0, change3m: 0, change10m: 0 }),
    updatedAt, source, detail, isOfficial, hasIntradayChanges: changes !== null,
  };
}

function buildTimeline(context: MarketContext, count: number): TimelinePoint[] {
  const now = Date.now();
  return Array.from({ length: count }, (_, index) => {
    const offset = count - index - 1;
    const force = Math.max(-100, Math.min(100, context.score + Math.sin((index + 2) / 4) * 13 - offset * .12));
    return {
      time: new Date(now - offset * 60_000).toISOString(),
      largeOrder: context.largeOrderNet / 1e8 + Math.sin(index / 3) * 4,
      smallOrder: context.smallOrderNet / 1e8 + Math.cos(index / 4) * 2,
      force: Math.round(force),
      futuresPercent: context.futuresChangePercent + Math.sin(index / 5) * .25,
      indexPercent: context.indexChangePercent + Math.cos(index / 6) * .2,
      stockCount: Math.max(1, Math.round(8 + force / 13 + Math.sin(index / 2) * 2)),
      direction: force >= 60 ? "strong_bull" : force >= 20 ? "bull" : force <= -60 ? "strong_bear" : force <= -20 ? "bear" : "sideways",
    };
  });
}

export async function buildMarketSnapshot(autoMode = true, forceRefresh = false): Promise<MarketSnapshot> {
  const now = Date.now();
  const ttl = cachedSnapshot?.marketOpen
    ? Number(process.env.MARKET_FORCE_REFRESH_SECONDS ?? 10) * 1000 - 1_000
    : cachedSnapshot?.futuresMarketOpen
      ? Math.max(30, Number(process.env.FUTURES_REFRESH_SECONDS ?? 30)) * 1000 - 1_000
      : Number(process.env.MARKET_SCAN_SECONDS ?? 60) * 1000 - 5_000;
  if (!forceRefresh && cachedSnapshot && now - cachedAt < ttl && autoMode) {
    return { ...cachedSnapshot, nextUpdateSeconds: Math.max(0, Math.ceil((ttl - (now - cachedAt)) / 1000)) };
  }
  const [status, index, futures, orders, breadth] = await Promise.all([
    marketDataProvider.getMarketStatus(),
    marketDirectionProvider.getMarketIndex(),
    marketDirectionProvider.getIndexFutures(),
    marketDirectionProvider.getOrderStatistics(),
    marketDirectionProvider.getMarketBreadth(),
  ]);
  const breadthScore = ((breadth.up - breadth.down) / (breadth.up + breadth.down + breadth.flat)) * 100;
  const forceInput: MarketForceInput = {
    largeOrderNet: orders.largeOrderNet,
    futuresDirection: futures.changePercent * 70,
    indexTrend: index.changePercent * 65,
    marketBreadth: breadthScore,
    indexVsVwap: 42,
    volumeMomentum: 31,
    aboveMa20Ratio: 48,
  };
  const force = calculateMarketForce(forceInput);
  const futuresMarketOpen = Boolean(futures.sessionOpen);
  const baseContext = {
    marketOpen: status.open,
    futuresMarketOpen,
    indexPrice: index.price, indexChange: index.change, indexChangePercent: index.changePercent,
    futuresPrice: futures.price, futuresChange: futures.change, futuresChangePercent: futures.changePercent,
    adx: 27.4, indexAboveMa20: true, indexAboveMa60: true, indexAboveMa120: true,
    ma20Slope: .42, ma60Slope: .18, macdAboveZero: true,
    largeOrderNet: orders.largeOrderNet, smallOrderNet: orders.smallOrderNet,
    breadthUp: breadth.up, breadthDown: breadth.down,
    futuresContract: futures.contract, futuresSource: futures.source, futuresQuoteAt: futures.quoteAt,
    indexSource: index.source, indexQuoteAt: index.quoteAt,
  };
  const regime = marketRegimeDetector.detectMarketRegime(force, baseContext);
  const context: MarketContext = { ...force, ...baseContext, regime };
  const recommendations = strategySelector.select(force.direction);
  const recommendedIds = recommendations.filter((item) => item.enabled).slice(0, 3).map((item) => item.id);
  if (autoMode) lockedManualStrategies = recommendedIds;
  const activeStrategyIds = autoMode ? recommendedIds : lockedManualStrategies;
  const previousSnapshot = cachedSnapshot;
  const previousRanks = new Map((previousSnapshot?.rankings ?? []).map((row) => [row.symbol, row]));
  const rankings = (await autoScreeningEngine.scan(context, activeStrategyIds)).map((row) => {
    const previous = previousRanks.get(row.symbol);
    return {
      ...row,
      movement: !previous ? "new" as const
        : row.score >= previous.score + 3 ? "up" as const
        : row.score <= previous.score - 3 ? "down" as const
        : row.score < 60 ? "leaving" as const : "steady" as const,
    };
  });
  const updatedAt = new Date().toISOString();
  const futuresChanges = [futures.change1m, futures.change3m, futures.change10m].every((value) => value != null)
    ? { change1m: futures.change1m!, change3m: futures.change3m!, change10m: futures.change10m! }
    : null;
  const futuresReference = futures.price - futures.change;
  const futuresPercentChanges = futuresChanges && futuresReference
    ? {
        change1m: futuresChanges.change1m / futuresReference * 100,
        change3m: futuresChanges.change3m / futuresReference * 100,
        change10m: futuresChanges.change10m / futuresReference * 100,
      }
    : null;
  const metrics = [
    metric("large-order", "大單淨額", orders.largeOrderNet / 1e8, "億元", updatedAt, 1, "系統模擬推估", "展示資料", false),
    metric("small-order", "小單淨額", orders.smallOrderNet / 1e8, "億元", updatedAt, -1, "系統模擬推估", "展示資料", false),
    metric("market-force", "多空力道", force.score, "分", updatedAt, 1, "多指標計算", "含模擬大／小單", false),
    metric("futures", `台指期 ${futures.contract ?? ""}`.trim(), futures.price, "點", updatedAt, 1, futures.source, futures.session, futures.isOfficial, futuresChanges),
    metric("futures-change", "台指期漲跌", futures.change, "點", updatedAt, 1, futures.source, futures.quoteAt, futures.isOfficial, futuresChanges),
    metric("futures-percent", "台指期漲跌幅", futures.changePercent, "%", updatedAt, 1, futures.source, futures.quoteAt, futures.isOfficial, futuresPercentChanges),
    metric("index", "加權指數", index.price, "點", updatedAt, 1, index.source, index.quoteAt, index.isOfficial, null),
    metric("index-change", "加權指數漲跌", index.change, "點", updatedAt, 1, index.source, index.quoteAt, index.isOfficial, null),
    metric("index-percent", "加權指數漲跌幅", index.changePercent, "%", updatedAt, 1, index.source, index.quoteAt, index.isOfficial, null),
  ];
  const events: SystemEvent[] = [];
  if (!previousSnapshot || previousSnapshot.force.direction !== force.direction) {
    events.push({
      type: "market_regime_changed",
      oldValue: previousSnapshot ? directionLabel[previousSnapshot.force.direction] : "尚未建立",
      newValue: directionLabel[force.direction], triggeredAt: updatedAt, reasons: force.reasons.slice(0, 3),
    });
  }
  if (!previousSnapshot || previousSnapshot.activeStrategyIds.join(",") !== activeStrategyIds.join(",")) {
    events.push({
      type: "strategy_auto_changed", strategyId: activeStrategyIds[0],
      oldValue: previousSnapshot?.activeStrategyIds.join(",") ?? "未啟用",
      newValue: recommendations.find((item) => item.id === activeStrategyIds[0])?.name,
      triggeredAt: updatedAt, reasons: ["盤勢連續確認後更新推薦策略"],
    });
  }
  rankings.filter((row) => !previousRanks.has(row.symbol)).forEach((row) => events.push({
    type: "stock_entered_ranking", symbol: row.symbol, strategyId: row.strategyId,
    price: row.price, score: row.score, triggeredAt: updatedAt, reasons: row.reasons.slice(0, 3),
  }));
  const currentSymbols = new Set(rankings.map((row) => row.symbol));
  previousSnapshot?.rankings.filter((row) => !currentSymbols.has(row.symbol)).forEach((row) => events.push({
    type: "stock_left_ranking", symbol: row.symbol, strategyId: row.strategyId,
    oldValue: row.score, triggeredAt: updatedAt, reasons: ["目前條件完整度低於排行榜門檻"],
  }));
  rankings.filter((row) => {
    const previous = previousRanks.get(row.symbol);
    return previous && Math.abs(row.score - previous.score) >= 10;
  }).forEach((row) => events.push({
    type: "stock_score_changed", symbol: row.symbol, strategyId: row.strategyId,
    oldValue: previousRanks.get(row.symbol)!.score, newValue: row.score,
    price: row.price, score: row.score, triggeredAt: updatedAt, reasons: row.reasons.slice(0, 3),
  }));
  events.forEach(saveEvent);
  const snapshot: MarketSnapshot = {
    mode: "demo",
    marketOpen: status.open,
    futuresMarketOpen,
    marketStatus: status.open
      ? "台股盤中／台指期日盤"
      : futuresMarketOpen
        ? "台股收盤／台指期夜盤交易中"
        : status.label,
    updatedAt,
    delaySeconds: status.open ? 2 : futuresMarketOpen ? 30 : 0,
    nextUpdateSeconds: status.open ? 10 : futuresMarketOpen ? Math.max(30, Number(process.env.FUTURES_REFRESH_SECONDS ?? 30)) : 60,
    force, context, recommendations, activeStrategyIds, rankings, metrics,
    timeline: buildTimeline(context, 36), events: recentEvents(12),
  };
  if (autoMode) { cachedSnapshot = snapshot; cachedAt = now; }
  return snapshot;
}
