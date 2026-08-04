import type { DailyPrice } from "./types";

export interface SupportResistanceLevels {
  support1: number | null;
  support2: number | null;
  resistance1: number | null;
  resistance2: number | null;
}

const LEVEL_ZONE_PERCENT = 0.015;
const PIVOT_RADIUS = 2;
const LOOKBACK = 120;

function isDistinct(level: number, selected: number[]) {
  return selected.every((value) =>
    Math.abs(level - value) / Math.min(level, value) >= LEVEL_ZONE_PERCENT,
  );
}

function nearestDistinct(
  values: number[],
  currentPrice: number,
  side: "support" | "resistance",
  count: number,
) {
  const sorted = [...values]
    .filter((value) =>
      Number.isFinite(value)
      && value > 0
      && (side === "support" ? value < currentPrice : value > currentPrice),
    )
    .sort((left, right) =>
      side === "support" ? right - left : left - right,
    );
  const result: number[] = [];
  for (const level of sorted) {
    if (isDistinct(level, result)) result.push(level);
    if (result.length === count) break;
  }
  return result;
}

function pivotLevels(prices: DailyPrice[], kind: "high" | "low") {
  const levels: number[] = [];
  for (let index = PIVOT_RADIUS; index < prices.length - PIVOT_RADIUS; index += 1) {
    const value = prices[index][kind];
    const neighbors = prices
      .slice(index - PIVOT_RADIUS, index + PIVOT_RADIUS + 1)
      .map((price) => price[kind]);
    const pivot = kind === "high"
      ? value === Math.max(...neighbors)
      : value === Math.min(...neighbors);
    if (pivot) levels.push(value);
  }
  return levels;
}

function fallbackLevels(prices: DailyPrice[], kind: "high" | "low") {
  const values: number[] = [];
  for (const length of [20, 60, LOOKBACK]) {
    const window = prices.slice(-length);
    if (!window.length) continue;
    values.push(
      kind === "high"
        ? Math.max(...window.map((price) => price.high))
        : Math.min(...window.map((price) => price.low)),
    );
  }
  values.push(...prices.map((price) => price[kind]));
  return values;
}

function fillLevels(
  primary: number[],
  fallback: number[],
  currentPrice: number,
  side: "support" | "resistance",
) {
  const result = nearestDistinct(primary, currentPrice, side, 2);
  if (result.length < 2) {
    for (const level of nearestDistinct(fallback, currentPrice, side, fallback.length)) {
      if (isDistinct(level, result)) result.push(level);
      if (result.length === 2) break;
    }
  }
  return result.sort((left, right) =>
    side === "support" ? right - left : left - right,
  );
}

export function calculateSupportResistance(
  prices: DailyPrice[],
): SupportResistanceLevels {
  const latest = prices.at(-1);
  if (!latest) {
    return {
      support1: null,
      support2: null,
      resistance1: null,
      resistance2: null,
    };
  }
  const recent = prices.slice(-LOOKBACK);
  const supports = fillLevels(
    pivotLevels(recent, "low"),
    fallbackLevels(recent, "low"),
    latest.close,
    "support",
  );
  const resistances = fillLevels(
    pivotLevels(recent, "high"),
    fallbackLevels(recent, "high"),
    latest.close,
    "resistance",
  );
  return {
    support1: supports[0] ?? null,
    support2: supports[1] ?? null,
    resistance1: resistances[0] ?? null,
    resistance2: resistances[1] ?? null,
  };
}
