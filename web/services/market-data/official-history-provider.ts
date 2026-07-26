import { calculateIndicators } from "@/lib/indicators";
import type { DailyPrice, StockMeta, StockPayload, StockQuote } from "@/lib/types";

type JsonRecord = Record<string, unknown>;
type MonthlyPayload = { data?: unknown; tables?: unknown };

const HISTORY_MONTHS = 16;
const HISTORY_CACHE_MS = 6 * 60 * 60 * 1_000;
const historyCache = new Map<string, { value: DailyPrice[]; expiresAt: number }>();
const inFlight = new Map<string, Promise<DailyPrice[]>>();

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
    next: { revalidate: 21_600 },
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

export function validateOfficialHistoryContinuity(prices: DailyPrice[]): void {
  if (prices.length < 240) {
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

async function loadOfficialHistory(meta: StockMeta): Promise<DailyPrice[]> {
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
  return deduplicated;
}

export async function getOfficialHistory(meta: StockMeta): Promise<DailyPrice[]> {
  const cached = historyCache.get(meta.symbol);
  if (cached && cached.expiresAt > Date.now()) return cached.value;
  const pending = inFlight.get(meta.symbol);
  if (pending) return pending;
  const request = loadOfficialHistory(meta)
    .then((prices) => {
      historyCache.set(meta.symbol, { value: prices, expiresAt: Date.now() + HISTORY_CACHE_MS });
      return prices;
    })
    .finally(() => inFlight.delete(meta.symbol));
  inFlight.set(meta.symbol, request);
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
  if (!quote?.isRealtime) return history;
  const candle = quoteCandle(meta, quote);
  if (!candle) return history;
  return [...history.filter((row) => row.date !== candle.date), candle]
    .sort((a, b) => a.date.localeCompare(b.date));
}

export async function buildOfficialStockPayload(
  meta: StockMeta,
  quote: StockQuote | null,
): Promise<StockPayload> {
  const history = await getOfficialHistory(meta);
  // MIS intraday volume and the official end-of-day volume can differ because
  // the daily report includes the completed market sessions. Only use MIS to
  // build the unfinished current-day candle while the market is open.
  const merged = mergeOfficialHistoryWithQuote(history, meta, quote);
  const last = merged.at(-1);
  if (!last) throw new Error("官方歷史行情為空");
  const liveQuote = quote?.isRealtime ? quote : undefined;
  const quoteTimestamp = liveQuote ? `${liveQuote.date}T${liveQuote.time}+08:00` : `${last.date}T13:30:00+08:00`;
  const historySource = meta.market === "上市" ? "TWSE 個股日成交資訊" : "TPEx 個股日成交資訊";
  return {
    meta,
    prices: merged,
    indicators: calculateIndicators(merged),
    updatedAt: quoteTimestamp,
    quote: liveQuote,
    dataMode: "official_history",
    dataQuality: {
      status: liveQuote ? "official_realtime" : "official_close",
      historySource,
      quoteSource: liveQuote?.source ?? historySource,
      quoteTimestamp,
      lastTradingDate: last.date,
      signalEligible: Boolean(liveQuote),
    },
    dataNotice: `日 K、成交量、均線與 MACD 均由${meta.market === "上市" ? "證交所" : "櫃買中心"}官方歷史成交資料計算；${liveQuote ? "盤中當日 K 棒暫以 MIS 行情更新，收盤後改用正式日成交資料" : "目前顯示最近有效的正式收盤資料"}。`,
  };
}

export function resetOfficialHistoryCacheForTests() {
  historyCache.clear();
  inFlight.clear();
}
