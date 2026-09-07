export interface PatternChartCandle {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
}

export interface PatternChartKeyPoint {
  name: string;
  date: string;
  price: number;
  confirmedDate: string;
}

export interface PatternOverlayLine {
  kind: "polyline" | "curve";
  label: string;
  points: PatternChartKeyPoint[];
}

export interface ChartPoint {
  x: number;
  y: number;
}

function validPoint(point: PatternChartKeyPoint): boolean {
  return Boolean(point.name && /^\d{4}-\d{2}-\d{2}$/.test(point.date))
    && Number.isFinite(point.price)
    && point.price > 0;
}

function chronological(points: PatternChartKeyPoint[]): PatternChartKeyPoint[] {
  return points.filter(validPoint).slice().sort((left, right) =>
    left.date.localeCompare(right.date) || left.name.localeCompare(right.name));
}

export function selectPatternChartRows<T extends PatternChartCandle>(
  rows: T[],
  keyPoints: PatternChartKeyPoint[],
  baseBars = 80,
  maxBars = 180,
  leadingBars = 5,
): T[] {
  if (!rows.length) return [];
  const ordered = rows.slice().sort((left, right) => left.date.localeCompare(right.date));
  const dates = new Map(ordered.map((row, index) => [row.date, index]));
  const matchedIndexes = keyPoints
    .map((point) => dates.get(point.date))
    .filter((index): index is number => index != null);
  if (!matchedIndexes.length) return ordered.slice(-baseBars);

  const earliestPatternIndex = Math.min(...matchedIndexes);
  const desiredStart = Math.max(0, earliestPatternIndex - leadingBars);
  const maxStart = Math.max(0, ordered.length - maxBars);
  return ordered.slice(Math.max(desiredStart, maxStart));
}

export function buildPatternOverlayLines(
  patternType: string,
  keyPoints: PatternChartKeyPoint[],
): PatternOverlayLine[] {
  const points = chronological(keyPoints);
  if (patternType === "ASCENDING_TRIANGLE") {
    const resistance = points.filter((point) => point.name.startsWith("水平壓力"));
    const support = points.filter((point) => point.name.startsWith("墊高低點"));
    const lines: PatternOverlayLine[] = [];
    if (resistance.length >= 2) lines.push({ kind: "polyline", label: "水平壓力", points: resistance });
    if (support.length >= 2) lines.push({ kind: "polyline", label: "上升支撐", points: support });
    return lines.length ? lines : points.length >= 2
      ? [{ kind: "polyline", label: "型態輪廓", points }]
      : [];
  }
  if (points.length < 2) return [];
  return [{
    kind: patternType === "ROUNDED_BOTTOM" ? "curve" : "polyline",
    label: "型態輪廓",
    points,
  }];
}

/** Convert anchors to a smooth SVG path that passes through every anchor. */
export function catmullRomPath(points: ChartPoint[]): string {
  if (!points.length) return "";
  if (points.length === 1) return `M ${points[0].x} ${points[0].y}`;
  let path = `M ${points[0].x} ${points[0].y}`;
  for (let index = 0; index < points.length - 1; index += 1) {
    const previous = points[Math.max(0, index - 1)];
    const current = points[index];
    const next = points[index + 1];
    const after = points[Math.min(points.length - 1, index + 2)];
    const control1 = {
      x: current.x + (next.x - previous.x) / 6,
      y: current.y + (next.y - previous.y) / 6,
    };
    const control2 = {
      x: next.x - (after.x - current.x) / 6,
      y: next.y - (after.y - current.y) / 6,
    };
    path += ` C ${control1.x} ${control1.y}, ${control2.x} ${control2.y}, ${next.x} ${next.y}`;
  }
  return path;
}
