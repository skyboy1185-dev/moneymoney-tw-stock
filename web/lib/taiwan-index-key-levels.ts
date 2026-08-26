import type { MarketIndexDefenseResponse } from "./market-index-defense";
import type { MarketContext } from "./market-types";

export type TaiwanIndexKeyLevelTone = "bullish" | "neutral" | "warning" | "bearish";

export interface TaiwanIndexLevel {
  label: string;
  value: number;
  source: string;
}

export interface TaiwanIndexSupportZone {
  low: number;
  high: number;
  source: string;
}

export interface TaiwanIndexKeyLevels {
  available: boolean;
  tradeDateLabel: string;
  referencePrice: number | null;
  referenceSource: string;
  stateLabel: string;
  tone: TaiwanIndexKeyLevelTone;
  pivot: TaiwanIndexLevel | null;
  support: TaiwanIndexSupportZone | null;
  downsideTargets: TaiwanIndexLevel[];
  title: string;
}

function finite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value > 0;
}

export function formatIndexLevel(value: number): string {
  return new Intl.NumberFormat("zh-TW", {
    maximumFractionDigits: value >= 10_000 ? 0 : 2,
  }).format(value);
}

function formatMonthDay(value: string | null | undefined): string {
  const text = String(value ?? "").trim();
  const match = text.match(/(\d{4})[-/](\d{1,2})[-/](\d{1,2})/);
  if (match) return `${match[2].padStart(2, "0")}/${match[3].padStart(2, "0")}`;
  const parsed = value ? new Date(value) : new Date();
  const safe = Number.isNaN(parsed.getTime()) ? new Date() : parsed;
  return new Intl.DateTimeFormat("zh-TW", {
    month: "2-digit",
    day: "2-digit",
    timeZone: "Asia/Taipei",
  }).format(safe);
}

function stepFor(price: number): number {
  if (price >= 20_000) return 100;
  if (price >= 5_000) return 50;
  if (price >= 1_000) return 10;
  return 1;
}

function roundUp(value: number, step: number): number {
  return Math.ceil(value / step) * step;
}

function roundDown(value: number, step: number): number {
  return Math.floor(value / step) * step;
}

function roundLevel(value: number): number {
  return Math.round(value * 100) / 100;
}

function levelDate(context: MarketContext | null | undefined, defense: MarketIndexDefenseResponse | null | undefined): string {
  return formatMonthDay(
    context?.futuresQuoteAt
    ?? context?.indexQuoteAt
    ?? defense?.quoteAt,
  );
}

function reference(context: MarketContext | null | undefined, defense: MarketIndexDefenseResponse | null | undefined) {
  if (finite(context?.futuresPrice)) {
    return {
      price: context.futuresPrice,
      source: `台指期${context.futuresContract ? ` ${context.futuresContract}` : ""}`,
      quoteAt: context.futuresQuoteAt,
    };
  }
  if (finite(context?.indexPrice)) {
    return { price: context.indexPrice, source: "加權指數即時價", quoteAt: context.indexQuoteAt };
  }
  if (finite(defense?.currentPrice)) {
    return { price: defense.currentPrice, source: defense.indexName || "TAIEX 防守點資料", quoteAt: defense.quoteAt };
  }
  return { price: null, source: "資料不足", quoteAt: undefined };
}

function defenseLevels(defense: MarketIndexDefenseResponse | null | undefined) {
  return [
    { level: defense?.defense.week, source: "近5日大量區" },
    { level: defense?.defense.month, source: "近20日大量區" },
  ].flatMap(({ level, source }) => {
    if (!level) return [];
    return [
      { value: level.zoneLow, source: `${source}下緣` },
      { value: level.defensePrice, source: `${source}POC` },
      { value: level.zoneHigh, source: `${source}上緣` },
    ].filter((item) => finite(item.value));
  });
}

function choosePivot(referencePrice: number, defense: MarketIndexDefenseResponse | null | undefined): TaiwanIndexLevel {
  const step = stepFor(referencePrice);
  const rounded = roundUp(referencePrice + step * 0.05, step);
  const candidates = defenseLevels(defense)
    .filter((item) => item.value > referencePrice * 1.001)
    .concat({ value: rounded, source: "上方整數關卡" })
    .sort((left, right) => left.value - right.value);
  const chosen = candidates[0];
  return { label: "多空", value: roundLevel(chosen.value), source: chosen.source };
}

function chooseSupport(referencePrice: number, defense: MarketIndexDefenseResponse | null | undefined): TaiwanIndexSupportZone {
  const step = stepFor(referencePrice);
  const week = defense?.defense.week;
  const month = defense?.defense.month;
  const candidates = [
    week ? { low: week.zoneLow, high: week.zoneHigh, source: "近5日大量區" } : null,
    month ? { low: month.zoneLow, high: month.zoneHigh, source: "近20日大量區" } : null,
  ].filter((item): item is TaiwanIndexSupportZone => (
    Boolean(item) && finite(item?.low) && finite(item?.high) && item!.low <= referencePrice * 1.015
  ));
  if (candidates.length) {
    const chosen = candidates.sort((left, right) =>
      Math.abs(referencePrice - left.high) - Math.abs(referencePrice - right.high)
    )[0];
    return {
      low: roundLevel(Math.min(chosen.low, chosen.high)),
      high: roundLevel(Math.max(chosen.low, chosen.high)),
      source: chosen.source,
    };
  }
  const high = roundDown(referencePrice - step * 0.05, step);
  return {
    low: roundLevel(Math.max(step, high - step)),
    high: roundLevel(high),
    source: "下方整數關卡",
  };
}

function chooseDownsideTargets(
  referencePrice: number,
  support: TaiwanIndexSupportZone,
  defense: MarketIndexDefenseResponse | null | undefined,
): TaiwanIndexLevel[] {
  const step = stepFor(referencePrice);
  const roundedTarget = roundDown(support.low - step * 0.05, step);
  const candidates = defenseLevels(defense)
    .filter((item) => item.value < support.low * 0.999)
    .concat({ value: roundedTarget, source: "下一整數關卡" })
    .filter((item) => finite(item.value))
    .sort((left, right) => right.value - left.value);
  const seen = new Set<number>();
  return candidates.flatMap((item) => {
    const rounded = roundLevel(item.value);
    if (seen.has(rounded)) return [];
    seen.add(rounded);
    return [{ label: "下看", value: rounded, source: item.source }];
  }).slice(0, 2);
}

function stateLabel(referencePrice: number, pivot: TaiwanIndexLevel, support: TaiwanIndexSupportZone, downsideTargets: TaiwanIndexLevel[]) {
  if (referencePrice >= pivot.value) return { label: "站上多空分界", tone: "bullish" as const };
  if (referencePrice > support.high) return { label: "分界下方・支撐上方", tone: "neutral" as const };
  if (referencePrice >= support.low) return { label: "正在測試支撐", tone: "warning" as const };
  const firstTarget = downsideTargets[0]?.value;
  if (firstTarget && referencePrice >= firstTarget) return { label: "跌破支撐・短線轉弱", tone: "warning" as const };
  return { label: "支撐失守・偏空", tone: "bearish" as const };
}

export function buildTaiwanIndexKeyLevels(
  context: MarketContext | null | undefined,
  defense: MarketIndexDefenseResponse | null | undefined,
): TaiwanIndexKeyLevels {
  const ref = reference(context, defense);
  const tradeDateLabel = levelDate(context, defense);
  if (!finite(ref.price)) {
    return {
      available: false,
      tradeDateLabel,
      referencePrice: null,
      referenceSource: ref.source,
      stateLabel: "等待官方行情",
      tone: "neutral",
      pivot: null,
      support: null,
      downsideTargets: [],
      title: "關鍵價資料不足，等待官方台指期或加權指數行情。",
    };
  }
  const pivot = choosePivot(ref.price, defense);
  const support = chooseSupport(ref.price, defense);
  const downsideTargets = chooseDownsideTargets(ref.price, support, defense);
  const state = stateLabel(ref.price, pivot, support, downsideTargets);
  const downsideText = downsideTargets.length
    ? `下看 ${downsideTargets.map((item) => `${formatIndexLevel(item.value)}（${item.source}）`).join("／")}`
    : "下方目標資料不足";
  return {
    available: true,
    tradeDateLabel,
    referencePrice: ref.price,
    referenceSource: ref.source,
    stateLabel: state.label,
    tone: state.tone,
    pivot,
    support,
    downsideTargets,
    title: [
      `${ref.source} ${formatIndexLevel(ref.price)}`,
      `多空分界 ${formatIndexLevel(pivot.value)}（${pivot.source}）`,
      `支撐 ${formatIndexLevel(support.low)}～${formatIndexLevel(support.high)}（${support.source}）`,
      downsideText,
    ].join("；"),
  };
}
