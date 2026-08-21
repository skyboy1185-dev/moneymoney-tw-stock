import { calculateIndicators } from "@/lib/indicators";
import type { DailyPrice, StockMeta, StockPayload, StockQuote } from "@/lib/types";

type JsonRecord = Record<string, unknown>;
type MonthlyPayload = { data?: unknown; tables?: unknown };
type FinMindRow = {
  date?: unknown;
  Trading_Volume?: unknown;
  open?: unknown;
  max?: unknown;
  min?: unknown;
  close?: unknown;
};
type FinMindPayload = { status?: number; msg?: string; data?: FinMindRow[] };
type YahooDailyPayload = {
  chart?: {
    result?: Array<{
      timestamp?: number[];
      indicators?: {
        quote?: Array<{
          open?: Array<number | null>;
          high?: Array<number | null>;
          low?: Array<number | null>;
          close?: Array<number | null>;
          volume?: Array<number | null>;
        }>;
      };
    }>;
  };
};

const HISTORY_MONTHS = 62;
const HISTORY_CALENDAR_DAYS = 1_900;
const HISTORY_CACHE_MS = 6 * 60 * 60 * 1_000;
const HISTORY_CONCURRENCY = 2;
// Broad-market scans can cover hundreds of symbols. A conservative limit keeps
// enough sockets and CPU available for interactive page/API requests.
const SCAN_HISTORY_CONCURRENCY = 2;
const SCAN_HISTORY_MONTHS = 8;
const SCAN_HISTORY_CALENDAR_DAYS = 400;
const historyCache = new Map<string, { value: DailyPrice[]; expiresAt: number }>();
const scanHistoryCache = new Map<string, { value: DailyPrice[]; expiresAt: number }>();
const deductionHistoryCache = new Map<string, { value: DailyPrice[]; expiresAt: number }>();
const historySourceCache = new Map<string, string>();
const inFlight = new Map<string, Promise<DailyPrice[]>>();
const scanInFlight = new Map<string, Promise<DailyPrice[]>>();
const deductionInFlight = new Map<string, Promise<DailyPrice[]>>();
const historyQueue: (() => void)[] = [];
const scanHistoryQueue: (() => void)[] = [];
let activeHistoryLoads = 0;
let activeScanHistoryLoads = 0;

function numberValue(value: unknown): number | null {
  const text = String(value ?? "").replaceAll(",", "").replaceAll("+", "").trim();
  if (!text || text === "--" || text === "---" || text === "除權息") return null;
  const valueAsNumber = Number(text);
  return Number.isFinite(valueAsNumber) ? valueAsNumber : null;
}

function rocDateToIso(value: unknown): string | null {
  const text = String(value ?? "").trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return text;
  const matched = text.match(/^(\d{2,3})\/(\d{2})\/(\d{2})$/);
  if (!matched) return null;
  return `${Number(matched[1]) + 1911}-${matched[2]}-${matched[3]}`;
}

function validCandle(
  meta: StockMeta,
  row: unknown[],
  volumeMultiplier: number,
): DailyPrice | null {
  const date = rocDateToIso(row[0]);
  const volume = numberValue(row[1]);
  const open = numberValue(row[3]);
  const high = numberValue(row[4]);
  const low = numberValue(row[5]);
  const close = numberValue(row[6]);
  if (
    !date || volume == null || open == null || high == null || low == null || close == null
    || volume < 0 || open <= 0 || high <= 0 || low <= 0 || close <= 0
    || high < Math.max(open, close) || low > Math.min(open, close)
  ) return null;
  return {
    symbol: meta.symbol,
    name: meta.name,
    date,
    open,
    high,
    low,
    close,
    volume: Math.round(volume * volumeMultiplier),
  };
}

export function parseTwseMonthlyHistory(payload: MonthlyPayload, meta: StockMeta): DailyPrice[] {
  if (!Array.isArray(payload.data)) return [];
  return payload.data
    .map((row) => Array.isArray(row) ? validCandle(meta, row, 1) : null)
    .filter((row): row is DailyPrice => row !== null);
}

export function parseTpexMonthlyHistory(payload: MonthlyPayload, meta: StockMeta): DailyPrice[] {
  if (!Array.isArray(payload.tables)) return [];
  const firstTable = payload.tables[0] as JsonRecord | undefined;
  if (!firstTable || !Array.isArray(firstTable.data)) return [];
  return firstTable.data
    .map((row) => Array.isArray(row) ? validCandle(meta, row, 1_000) : null)
    .filter((row): row is DailyPrice => row !== null);
}

export function parseFinMindHistory(payload: FinMindPayload, meta: StockMeta): DailyPrice[] {
  if (payload.status !== 200 || !Array.isArray(payload.data)) return [];
  return payload.data.map((row) => {
    const date = rocDateToIso(row.date);
    const volume = numberValue(row.Trading_Volume);
    const open = numberValue(row.open);
    const high = numberValue(row.max);
    const low = numberValue(row.min);
    const close = numberValue(row.close);
    if (
      !date || volume == null || open == null || high == null || low == null || close == null
      || volume < 0 || open <= 0 || high <= 0 || low <= 0 || close <= 0
      || high < Math.max(open, close) || low > Math.min(open, close)
    ) return null;
    return {
      symbol: meta.symbol,
      name: meta.name,
      date,
      open,
      high,
      low,
      close,
      volume: Math.round(volume),
    } satisfies DailyPrice;
  }).filter((row): row is DailyPrice => row !== null);
}

function taipeiIsoDate(timestampSeconds: number): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Taipei",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(timestampSeconds * 1_000));
}

export function parseYahooDailyHistory(payload: YahooDailyPayload, meta: StockMeta): DailyPrice[] {
  const result = payload.chart?.result?.[0];
  const timestamps = result?.timestamp;
  const quote = result?.indicators?.quote?.[0];
  if (!Array.isArray(timestamps) || !quote) return [];
  return timestamps.map((timestamp, index) => {
    const open = numberValue(quote.open?.[index]);
    const high = numberValue(quote.high?.[index]);
    const low = numberValue(quote.low?.[index]);
    const close = numberValue(quote.close?.[index]);
    const volume = numberValue(quote.volume?.[index]);
    if (
      !Number.isFinite(timestamp) || timestamp <= 0
      || open == null || high == null || low == null || close == null || volume == null
      || open <= 0 || high <= 0 || low <= 0 || close <= 0 || volume < 0
      || high < Math.max(open, close) || low > Math.min(open, close)
    ) return null;
    return {
      symbol: meta.symbol,
      name: meta.name,
      date: taipeiIsoDate(timestamp),
      open,
      high,
      low,
      close,
      volume: Math.round(volume),
    } satisfies DailyPrice;
  }).filter((row): row is DailyPrice => row !== null);
}

function isoDaysAgo(days: number) {
  const date = new Date();
  date.setUTCDate(date.getUTCDate() - days);
  return date.toISOString().slice(0, 10);
}

async function fetchFinMindHistory(
  meta: StockMeta,
  calendarDays = HISTORY_CALENDAR_DAYS,
  minimumTradingDays = 240,
): Promise<DailyPrice[]> {
  const params = new URLSearchParams({
    dataset: "TaiwanStockPrice",
    data_id: meta.symbol,
    start_date: isoDaysAgo(calendarDays),
    end_date: new Date().toISOString().slice(0, 10),
  });
  const token = process.env.FINMIND_API_TOKEN?.trim();
  if (token) params.set("token", token);
  const response = await fetch(`https://api.finmindtrade.com/api/v4/data?${params}`, {
    headers: { Accept: "application/json", "User-Agent": "Mozilla/5.0 Moneymoney-TWSE-Dashboard" },
    signal: AbortSignal.timeout(15_000),
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`FinMind 歷史行情回應 ${response.status}`);
  const prices = parseFinMindHistory(await response.json() as FinMindPayload, meta);
  validateOfficialHistoryContinuity(prices, minimumTradingDays);
  return prices;
}

async function fetchYahooRecentHistory(meta: StockMeta): Promise<DailyPrice[]> {
  const suffix = meta.market === "上市" ? "TW" : "TWO";
  const response = await fetch(
    `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(meta.symbol)}.${suffix}?interval=1d&range=1y&events=history`,
    {
      headers: {
        Accept: "application/json",
        "User-Agent": "Mozilla/5.0 Moneymoney-TWSE-Dashboard",
      },
      signal: AbortSignal.timeout(10_000),
      cache: "no-store",
    },
  );
  if (!response.ok) throw new Error(`Yahoo Finance daily history ${response.status}`);
  const prices = parseYahooDailyHistory(await response.json() as YahooDailyPayload, meta);
  validateOfficialHistoryContinuity(prices, 60);
  return prices;
}

async function fetchYahooDeductionHistory(meta: StockMeta): Promise<DailyPrice[]> {
  const suffix = meta.market === "上市" ? "TW" : "TWO";
  const response = await fetch(
    `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(meta.symbol)}.${suffix}?interval=1d&range=2y&events=history`,
    {
      headers: {
        Accept: "application/json",
        "User-Agent": "Mozilla/5.0 Moneymoney-TWSE-Dashboard",
      },
      signal: AbortSignal.timeout(10_000),
      cache: "no-store",
    },
  );
  if (!response.ok) throw new Error(`Yahoo Finance deduction history ${response.status}`);
  const prices = parseYahooDailyHistory(await response.json() as YahooDailyPayload, meta);
  validateOfficialHistoryContinuity(prices, 300);
  return prices;
}

function recentMonths(count = HISTORY_MONTHS): { compact: string; slash: string }[] {
  const now = new Date();
  const result: { compact: string; slash: string }[] = [];
  for (let offset = 0; offset < count; offset += 1) {
    const date = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - offset, 1));
    const year = String(date.getUTCFullYear());
    const month = String(date.getUTCMonth() + 1).padStart(2, "0");
    result.push({ compact: `${year}${month}01`, slash: `${year}/${month}/01` });
  }
  return result;
}

async function fetchMonth(meta: StockMeta, month: { compact: string; slash: string }): Promise<DailyPrice[]> {
  const listed = meta.market === "上市";
  const url = listed
    ? `https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date=${month.compact}&stockNo=${encodeURIComponent(meta.symbol)}`
    : `https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock?code=${encodeURIComponent(meta.symbol)}&date=${encodeURIComponent(month.slash)}&id=&response=json`;
  const response = await fetch(url, {
    headers: { Accept: "application/json", "User-Agent": "Mozilla/5.0 Moneymoney-TWSE-Dashboard" },
    signal: AbortSignal.timeout(12_000),
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`${listed ? "TWSE" : "TPEx"} 歷史行情回應 ${response.status}`);
  const payload = await response.json() as MonthlyPayload;
  return listed ? parseTwseMonthlyHistory(payload, meta) : parseTpexMonthlyHistory(payload, meta);
}

async function fetchMonthWithRetry(
  meta: StockMeta,
  month: { compact: string; slash: string },
): Promise<DailyPrice[]> {
  let lastError: unknown;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      return await fetchMonth(meta, month);
    } catch (error) {
      lastError = error;
      if (attempt < 2) {
        await new Promise((resolve) => setTimeout(resolve, 250 * (2 ** attempt)));
      }
    }
  }
  throw lastError instanceof Error ? lastError : new Error("官方月行情下載失敗");
}

export function validateOfficialHistoryContinuity(prices: DailyPrice[], minimumTradingDays = 240): void {
  if (prices.length < minimumTradingDays) {
    throw new Error(`官方歷史行情不足，目前只有 ${prices.length} 個交易日`);
  }
  for (let index = 1; index < prices.length; index += 1) {
    const previous = new Date(`${prices[index - 1].date}T00:00:00Z`);
    const current = new Date(`${prices[index].date}T00:00:00Z`);
    const gapDays = (current.getTime() - previous.getTime()) / 86_400_000;
    if (gapDays > 18) {
      throw new Error(`官方歷史行情存在日期缺口：${prices[index - 1].date}～${prices[index].date}`);
    }
  }
}

async function loadRecentOfficialHistory(meta: StockMeta): Promise<DailyPrice[]> {
  if (process.env.FINMIND_API_TOKEN?.trim()) {
    try {
      const prices = await fetchFinMindHistory(meta, SCAN_HISTORY_CALENDAR_DAYS, 60);
      historySourceCache.set(meta.symbol, "FinMind TaiwanStockPrice（彙整市場日成交資料）");
      return prices;
    } catch {
      // Continue with the single-request market-history adapter below.
    }
  }
  try {
    const prices = await fetchYahooRecentHistory(meta);
    historySourceCache.set(meta.symbol, "Yahoo Finance 台股日線（掃描備援）");
    return prices;
  } catch {
    // Exchange monthly reports remain the final source when the range adapter fails.
  }
  const months = recentMonths(SCAN_HISTORY_MONTHS);
  const rows: DailyPrice[] = [];
  for (let start = 0; start < months.length; start += 3) {
    const results = await Promise.allSettled(
      months.slice(start, start + 3).map((month) => fetchMonthWithRetry(meta, month)),
    );
    rows.push(...results.flatMap((result) => result.status === "fulfilled" ? result.value : []));
  }
  const deduplicated = [...new Map(rows.map((row) => [row.date, row])).values()]
    .sort((left, right) => left.date.localeCompare(right.date));
  validateOfficialHistoryContinuity(deduplicated, 60);
  historySourceCache.set(
    meta.symbol,
    meta.market === "上市" ? "TWSE 個股日成交資訊" : "TPEx 個股日成交資訊",
  );
  return deduplicated;
}

async function loadOfficialHistory(meta: StockMeta): Promise<DailyPrice[]> {
  try {
    const prices = await fetchFinMindHistory(meta);
    historySourceCache.set(meta.symbol, "FinMind TaiwanStockPrice（彙整市場日成交資料）");
    return prices;
  } catch {
    // Use the exchange monthly endpoints when the range adapter is unavailable
    // or returns an incomplete series.
  }
  const months = recentMonths();
  const rows: DailyPrice[] = [];
  const failures: unknown[] = [];
  // Smaller batches avoid triggering exchange throttling and make a missing
  // month less likely than firing every historical request at once.
  for (let start = 0; start < months.length; start += 3) {
    const results = await Promise.allSettled(
      months.slice(start, start + 3).map((month) => fetchMonthWithRetry(meta, month)),
    );
    rows.push(...results.flatMap((result) => result.status === "fulfilled" ? result.value : []));
    failures.push(...results.filter((result) => result.status === "rejected"));
  }
  const deduplicated = [...new Map(rows.map((row) => [row.date, row])).values()]
    .sort((left, right) => left.date.localeCompare(right.date));
  validateOfficialHistoryContinuity(deduplicated);
  if (failures.length && !deduplicated.length) throw new Error("所有官方歷史行情請求均失敗");
  historySourceCache.set(
    meta.symbol,
    meta.market === "上市" ? "TWSE 個股日成交資訊" : "TPEx 個股日成交資訊",
  );
  return deduplicated;
}

async function withHistoryConcurrency<T>(task: () => Promise<T>): Promise<T> {
  if (activeHistoryLoads >= HISTORY_CONCURRENCY) {
    await new Promise<void>((resolve) => historyQueue.push(resolve));
  }
  activeHistoryLoads += 1;
  try {
    return await task();
  } finally {
    activeHistoryLoads -= 1;
    historyQueue.shift()?.();
  }
}

async function withScanHistoryConcurrency<T>(task: () => Promise<T>): Promise<T> {
  if (activeScanHistoryLoads >= SCAN_HISTORY_CONCURRENCY) {
    await new Promise<void>((resolve) => scanHistoryQueue.push(resolve));
  }
  activeScanHistoryLoads += 1;
  try {
    return await task();
  } finally {
    activeScanHistoryLoads -= 1;
    scanHistoryQueue.shift()?.();
  }
}

export async function getOfficialHistory(meta: StockMeta): Promise<DailyPrice[]> {
  const cached = historyCache.get(meta.symbol);
  if (cached && cached.expiresAt > Date.now()) return cached.value;
  const pending = inFlight.get(meta.symbol);
  if (pending) return pending;
  const request = withHistoryConcurrency(() => loadOfficialHistory(meta))
    .then((prices) => {
      historyCache.set(meta.symbol, { value: prices, expiresAt: Date.now() + HISTORY_CACHE_MS });
      return prices;
    })
    .finally(() => inFlight.delete(meta.symbol));
  inFlight.set(meta.symbol, request);
  return request;
}

export async function getOfficialRecentHistory(meta: StockMeta): Promise<DailyPrice[]> {
  const full = historyCache.get(meta.symbol);
  if (full && full.expiresAt > Date.now()) return full.value;
  const cached = scanHistoryCache.get(meta.symbol);
  if (cached && cached.expiresAt > Date.now()) return cached.value;
  const pending = scanInFlight.get(meta.symbol);
  if (pending) return pending;
  const request = withScanHistoryConcurrency(() => loadRecentOfficialHistory(meta))
    .then((prices) => {
      scanHistoryCache.set(meta.symbol, { value: prices, expiresAt: Date.now() + HISTORY_CACHE_MS });
      return prices;
    })
    .finally(() => scanInFlight.delete(meta.symbol));
  scanInFlight.set(meta.symbol, request);
  return request;
}

/**
 * Load enough daily candles for the 20-month deduction model with one compact
 * range request. Fall back to the full official history adapter when the range
 * source is unavailable. Results are cached separately from broad scans.
 */
export async function getOfficialDeductionHistory(meta: StockMeta): Promise<DailyPrice[]> {
  const full = historyCache.get(meta.symbol);
  if (full && full.expiresAt > Date.now()) return full.value;
  const cached = deductionHistoryCache.get(meta.symbol);
  if (cached && cached.expiresAt > Date.now()) return cached.value;
  const pending = deductionInFlight.get(meta.symbol);
  if (pending) return pending;
  const request = withScanHistoryConcurrency(async () => {
    try {
      const prices = await fetchYahooDeductionHistory(meta);
      historySourceCache.set(meta.symbol, "Yahoo Finance 台股日線（扣抵計算）");
      return prices;
    } catch {
      return getOfficialHistory(meta);
    }
  }).then((prices) => {
    deductionHistoryCache.set(meta.symbol, { value: prices, expiresAt: Date.now() + HISTORY_CACHE_MS });
    return prices;
  }).finally(() => deductionInFlight.delete(meta.symbol));
  deductionInFlight.set(meta.symbol, request);
  return request;
}

function quoteCandle(meta: StockMeta, quote: StockQuote): DailyPrice | null {
  return validCandle(
    meta,
    [quote.date, quote.volume, 0, quote.open, quote.high, quote.low, quote.price],
    1,
  );
}

export function mergeOfficialHistoryWithQuote(
  history: DailyPrice[],
  meta: StockMeta,
  quote: StockQuote | null,
): DailyPrice[] {
  if (!quote) return history;
  const candle = quoteCandle(meta, quote);
  if (!candle) return history;
  const latestHistoryDate = history.at(-1)?.date;
  if (!quote.isRealtime && latestHistoryDate && candle.date <= latestHistoryDate) return history;
  if (latestHistoryDate && candle.date < latestHistoryDate) return history;
  return [...history.filter((row) => row.date !== candle.date), candle]
    .sort((a, b) => a.date.localeCompare(b.date));
}

function stockPayloadFromHistory(
  meta: StockMeta,
  quote: StockQuote | null,
  history: DailyPrice[],
): StockPayload {
  // MIS intraday volume and the official end-of-day volume can differ because
  // the daily report includes the completed market sessions. Only use MIS to
  // build the unfinished current-day candle while the market is open.
  const merged = mergeOfficialHistoryWithQuote(history, meta, quote);
  const last = merged.at(-1);
  if (!last) throw new Error("官方歷史行情為空");
  const marketQuote = quote && quote.date === last.date ? quote : undefined;
  const quoteTimestamp = marketQuote ? `${marketQuote.date}T${marketQuote.time}+08:00` : `${last.date}T13:30:00+08:00`;
  const historySource = historySourceCache.get(meta.symbol)
    ?? (meta.market === "上市" ? "TWSE 個股日成交資訊" : "TPEx 個股日成交資訊");
  const quoteStatus = marketQuote?.isRealtime
    ? "official_realtime"
    : marketQuote?.source === "Yahoo Finance 準即時"
      ? "delayed"
      : "official_close";
  return {
    meta,
    prices: merged,
    indicators: calculateIndicators(merged),
    updatedAt: quoteTimestamp,
    quote: marketQuote,
    dataMode: "official_history",
    dataQuality: {
      status: quoteStatus,
      historySource,
      quoteSource: marketQuote?.source ?? historySource,
      quoteTimestamp,
      lastTradingDate: last.date,
      signalEligible: Boolean(marketQuote?.isRealtime),
    },
    dataNotice: `日 K、成交量、均線與 MACD 由 ${historySource} 計算；${
      marketQuote?.isRealtime
        ? `盤中當日 K 棒以 ${marketQuote.source} 更新，收盤後改用完整日成交資料`
        : marketQuote
          ? `當日 K 棒使用 ${marketQuote.source} 的實際市場資料，因報價可能延遲，不產生正式交易訊號`
          : "目前顯示最近有效的市場收盤資料"
    }。`,
  };
}

export async function buildOfficialStockPayload(
  meta: StockMeta,
  quote: StockQuote | null,
): Promise<StockPayload> {
  return stockPayloadFromHistory(meta, quote, await getOfficialHistory(meta));
}

export async function buildOfficialRecentStockPayload(
  meta: StockMeta,
  quote: StockQuote | null,
): Promise<StockPayload> {
  return stockPayloadFromHistory(meta, quote, await getOfficialRecentHistory(meta));
}

export function resetOfficialHistoryCacheForTests() {
  historyCache.clear();
  scanHistoryCache.clear();
  deductionHistoryCache.clear();
  historySourceCache.clear();
  inFlight.clear();
  scanInFlight.clear();
  deductionInFlight.clear();
}
