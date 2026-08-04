import type { DailyPrice } from "./types";

export interface VolumePriceBin {
  low: number;
  high: number;
  midpoint: number;
  volume: number;
  volumePct: number;
  isPoc: boolean;
  hasCurrentPrice: boolean;
}

export interface HighVolumeZone {
  low: number;
  high: number;
  volume: number;
  volumePct: number;
  includesPoc: boolean;
}

export interface VolumeProfile {
  days: number;
  startDate: string;
  endDate: string;
  totalVolume: number;
  currentPrice: number;
  poc: VolumePriceBin;
  bins: VolumePriceBin[];
  zones: HighVolumeZone[];
  position: "above" | "inside" | "below";
  positionLabel: string;
}

export interface VolumePriceTrendPoint {
  date: string;
  close: number;
  xPct: number;
  yPct: number;
}

function round(value: number, digits = 2) {
  const scale = 10 ** digits;
  return Math.round(value * scale) / scale;
}

function percentile(values: number[], ratio: number) {
  const ordered = [...values].sort((a, b) => a - b);
  return ordered[Math.floor((ordered.length - 1) * ratio)] ?? 0;
}

export function buildVolumePriceTrend(
  prices: DailyPrice[],
  days: number,
  priceMin: number,
  priceMax: number,
): VolumePriceTrendPoint[] {
  if (!(priceMax > priceMin)) return [];
  const source = prices
    .filter((item) =>
      Number.isFinite(item.close)
      && item.close > 0
      && Boolean(item.date))
    .slice(-days);
  return source.map((item, index) => ({
    date: item.date,
    close: item.close,
    xPct: source.length === 1 ? 100 : index / (source.length - 1) * 100,
    yPct: Math.min(100, Math.max(0, (priceMax - item.close) / (priceMax - priceMin) * 100)),
  }));
}

export function buildVolumeProfile(
  prices: DailyPrice[],
  days = 60,
  binCount = 24,
): VolumeProfile | null {
  const source = prices
    .filter((price) =>
      Number.isFinite(price.low)
      && Number.isFinite(price.high)
      && price.high >= price.low
      && price.volume > 0)
    .slice(-days);
  if (!source.length || binCount < 4) return null;
  const priceMin = Math.min(...source.map((price) => price.low));
  const priceMax = Math.max(...source.map((price) => price.high));
  if (!(priceMax > priceMin)) return null;
  const step = (priceMax - priceMin) / binCount;
  const volumes = Array.from({ length: binCount }, () => 0);

  for (const price of source) {
    const dayLow = Math.max(priceMin, price.low);
    const dayHigh = Math.min(priceMax, price.high);
    if (dayHigh === dayLow) {
      const index = Math.min(binCount - 1, Math.max(0, Math.floor((price.close - priceMin) / step)));
      volumes[index] += price.volume;
      continue;
    }
    const overlaps = volumes.map((_, index) => {
      const low = priceMin + index * step;
      const high = index === binCount - 1 ? priceMax : low + step;
      return Math.max(0, Math.min(dayHigh, high) - Math.max(dayLow, low));
    });
    const overlapTotal = overlaps.reduce((sum, value) => sum + value, 0);
    if (!overlapTotal) continue;
    overlaps.forEach((overlap, index) => {
      volumes[index] += price.volume * overlap / overlapTotal;
    });
  }

  const totalVolume = volumes.reduce((sum, volume) => sum + volume, 0);
  if (!totalVolume) return null;
  const currentPrice = source.at(-1)!.close;
  const pocIndex = volumes.indexOf(Math.max(...volumes));
  const bins: VolumePriceBin[] = volumes.map((volume, index) => {
    const low = priceMin + index * step;
    const high = index === binCount - 1 ? priceMax : low + step;
    return {
      low: round(low),
      high: round(high),
      midpoint: round((low + high) / 2),
      volume: Math.round(volume),
      volumePct: round(volume / totalVolume * 100, 2),
      isPoc: index === pocIndex,
      hasCurrentPrice: currentPrice >= low && (index === binCount - 1 ? currentPrice <= high : currentPrice < high),
    };
  });

  const threshold = percentile(volumes, 0.75);
  const mergedZones: HighVolumeZone[] = [];
  let cursor = 0;
  while (cursor < bins.length) {
    if (volumes[cursor] < threshold) {
      cursor += 1;
      continue;
    }
    const start = cursor;
    let zoneVolume = 0;
    while (cursor < bins.length && volumes[cursor] >= threshold) {
      zoneVolume += volumes[cursor];
      cursor += 1;
    }
    const end = cursor - 1;
    mergedZones.push({
      low: bins[start].low,
      high: bins[end].high,
      volume: Math.round(zoneVolume),
      volumePct: round(zoneVolume / totalVolume * 100, 2),
      includesPoc: pocIndex >= start && pocIndex <= end,
    });
  }
  const zones = mergedZones.sort((a, b) => b.volume - a.volume).slice(0, 3);
  const poc = bins[pocIndex];
  const position = currentPrice > poc.high ? "above" : currentPrice < poc.low ? "below" : "inside";
  const positionLabel = position === "above"
    ? "目前股價位於最大量區上方，回測此區時可觀察承接力道。"
    : position === "below"
      ? "目前股價位於最大量區下方，反彈至此區時可觀察賣壓。"
      : "目前股價位於最大量區內，多空成本接近，容易出現拉鋸。";
  return {
    days: source.length,
    startDate: source[0].date,
    endDate: source.at(-1)!.date,
    totalVolume: Math.round(totalVolume),
    currentPrice,
    poc,
    bins,
    zones,
    position,
    positionLabel,
  };
}
