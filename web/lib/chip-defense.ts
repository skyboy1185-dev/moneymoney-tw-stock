import type { DailyPrice } from "./types";
import { buildVolumeProfile } from "./volume-profile";

export type ChipDefenseStatus = "held" | "testing" | "broken";

export interface ChipDefenseLevel {
  timeframe: "week" | "month";
  label: "週防守" | "月防守";
  tradingDays: number;
  startDate: string;
  endDate: string;
  defensePrice: number;
  zoneLow: number;
  zoneHigh: number;
  zoneVolumePct: number;
  currentPrice: number;
  distancePct: number;
  status: ChipDefenseStatus;
  statusLabel: string;
}

export interface ChipDefenseResult {
  week: ChipDefenseLevel | null;
  month: ChipDefenseLevel | null;
}

const SETTINGS = {
  week: { label: "週防守" as const, days: 5, bins: 10 },
  month: { label: "月防守" as const, days: 20, bins: 20 },
};

function round(value: number, digits = 2) {
  const scale = 10 ** digits;
  return Math.round(value * scale) / scale;
}

export function calculateChipDefenseLevel(
  prices: DailyPrice[],
  currentPrice: number,
  timeframe: "week" | "month",
): ChipDefenseLevel | null {
  if (!Number.isFinite(currentPrice) || currentPrice <= 0) return null;
  const setting = SETTINGS[timeframe];
  const profile = buildVolumeProfile(prices, setting.days, setting.bins);
  if (!profile) return null;

  const pocZone = profile.zones.find((zone) => zone.includesPoc);
  const zoneLow = pocZone?.low ?? profile.poc.low;
  const zoneHigh = pocZone?.high ?? profile.poc.high;
  const zoneVolumePct = pocZone?.volumePct ?? profile.poc.volumePct;
  const status: ChipDefenseStatus = currentPrice > zoneHigh
    ? "held"
    : currentPrice >= zoneLow
      ? "testing"
      : "broken";

  return {
    timeframe,
    label: setting.label,
    tradingDays: profile.days,
    startDate: profile.startDate,
    endDate: profile.endDate,
    defensePrice: profile.poc.midpoint,
    zoneLow,
    zoneHigh,
    zoneVolumePct,
    currentPrice: round(currentPrice),
    distancePct: round((currentPrice / profile.poc.midpoint - 1) * 100),
    status,
    statusLabel: status === "held"
      ? "守在防守區上方"
      : status === "testing"
        ? "正在測試防守區"
        : "已跌破防守區",
  };
}

export function calculateChipDefense(
  prices: DailyPrice[],
  currentPrice: number,
): ChipDefenseResult {
  return {
    week: calculateChipDefenseLevel(prices, currentPrice, "week"),
    month: calculateChipDefenseLevel(prices, currentPrice, "month"),
  };
}
