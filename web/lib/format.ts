export function safeNumber(value: number | null | undefined, digits = 2): string {
  if (value == null || !Number.isFinite(value)) return "暫無資料";
  return value.toLocaleString("zh-TW", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function formatPercent(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "暫無資料";
  return `${value >= 0 ? "+" : ""}${safeNumber(value)}%`;
}

export function formatVolume(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "暫無資料";
  return `${Math.round(value / 1000).toLocaleString("zh-TW")} 張`;
}

export function formatMarketCap(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "暫無資料";
  if (value >= 1e12) return `${safeNumber(value / 1e12)} 兆`;
  return `${safeNumber(value / 1e8)} 億`;
}

export function valueClass(value: number): string {
  return value > 0 ? "text-up" : value < 0 ? "text-down" : "text-muted";
}
