import { calculateIndicators } from "@/lib/indicators";
import type { DailyPrice, StockMeta, StockPayload, StockQuote } from "@/lib/types";
import { backendJson } from "@/services/backend-client";

type QuoteStockMeta = Pick<StockMeta, "symbol" | "name" | "market">;

const quoteCache = new Map<string, { value: StockQuote; expiresAt: number }>();
const lastTradeCache = new Map<string, StockQuote>();
const LIVE_QUOTE_CACHE_MS = 2_000;
const MIS_POLL_INTERVAL_MS = 850;
const MIS_POLL_ATTEMPTS = 8;
const MIS_BATCH_SIZE = 40;
const BACKEND_QUOTE_BATCH_SIZE = 50;

interface BackendOfficialQuote {
  symbol: string;
  name: string;
  price: number;
  previousClose: number;
  open: number;
  high: number;
  low: number;
  volume: number;
  change: number;
  changePercent: number;
  quoteTimestamp: string;
  source: StockQuote["source"];
  isRealtime: boolean;
  bestBid?: number | null;
  bestAsk?: number | null;
}

function number(value: unknown): number | null {
  const parsed = Number(String(value ?? "").replaceAll(",", "").trim());
  return Number.isFinite(parsed) ? parsed : null;
}

function isoDate(value: string): string {
  if (/^\d{8}$/.test(value)) return `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)}`;
  if (/^\d{7}$/.test(value)) {
    const year = Number(value.slice(0, 3)) + 1911;
    return `${year}-${value.slice(3, 5)}-${value.slice(5, 7)}`;
  }
  return value;
}

function taipeiDateTime(timestampSeconds: number) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Taipei",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).formatToParts(new Date(timestampSeconds * 1_000));
  const value = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((part) => part.type === type)?.value ?? "";
  return {
    date: `${value("year")}-${value("month")}-${value("day")}`,
    time: `${value("hour")}:${value("minute")}:${value("second")}`,
  };
}

export function isQuoteRealtime(date: string, time: string, now = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Taipei", weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false,
  }).formatToParts(now);
  const weekday = parts.find((part) => part.type === "weekday")?.value;
  const hour = Number(parts.find((part) => part.type === "hour")?.value ?? 0);
  const minute = Number(parts.find((part) => part.type === "minute")?.value ?? 0);
  const minutes = hour * 60 + minute;
  const today = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Taipei", year: "numeric", month: "2-digit", day: "2-digit",
  }).format(now);
  const quoteTime = new Date(`${date}T${time}+08:00`);
  const delaySeconds = Math.abs(now.getTime() - quoteTime.getTime()) / 1_000;
  return (
    !["Sat", "Sun"].includes(weekday ?? "")
    && minutes >= 540 && minutes <= 810
    && date === today
    && Number.isFinite(quoteTime.getTime())
    && delaySeconds <= 120
  );
}

export function parseYahooChartQuote(
  payload: unknown,
  meta: QuoteStockMeta,
  now = new Date(),
): StockQuote | null {
  const result = (
    payload as {
      chart?: {
        result?: Array<{
          meta?: Record<string, unknown>;
          indicators?: { quote?: Array<Record<string, unknown[]>> };
        }>;
      };
    }
  )?.chart?.result?.[0];
  const quoteMeta = result?.meta;
  const series = result?.indicators?.quote?.[0];
  if (!quoteMeta || !series) return null;

  const price = number(quoteMeta.regularMarketPrice);
  const previousClose = number(quoteMeta.previousClose ?? quoteMeta.chartPreviousClose);
  const timestamp = number(quoteMeta.regularMarketTime);
  const opens = Array.isArray(series.open) ? series.open.map(number).filter((value): value is number => value != null) : [];
  const highs = Array.isArray(series.high) ? series.high.map(number).filter((value): value is number => value != null) : [];
  const lows = Array.isArray(series.low) ? series.low.map(number).filter((value): value is number => value != null) : [];
  const volumes = Array.isArray(series.volume) ? series.volume.map(number).filter((value): value is number => value != null) : [];
  const open = opens[0] ?? price;
  const high = number(quoteMeta.regularMarketDayHigh) ?? (highs.length ? Math.max(...highs) : price);
  const low = number(quoteMeta.regularMarketDayLow) ?? (lows.length ? Math.min(...lows) : price);
  const volume = number(quoteMeta.regularMarketVolume)
    ?? volumes.reduce((sum, value) => sum + value, 0);
  if (
    price == null || price <= 0
    || previousClose == null || previousClose <= 0
    || timestamp == null || timestamp <= 0
    || open == null || high == null || low == null || volume == null
  ) return null;

  const quoteAt = taipeiDateTime(timestamp);
  const change = price - previousClose;
  return {
    symbol: meta.symbol,
    name: String(quoteMeta.longName || quoteMeta.shortName || meta.name),
    date: quoteAt.date,
    time: quoteAt.time,
    open,
    high,
    low,
    price,
    previousClose,
    change,
    changePercent: change / previousClose * 100,
    volume: Math.round(volume),
    source: "Yahoo Finance 準即時",
    isRealtime: isQuoteRealtime(quoteAt.date, quoteAt.time, now),
  };
}

function isMarketSession(now = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Taipei", weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false,
  }).formatToParts(now);
  const weekday = parts.find((part) => part.type === "weekday")?.value;
  const hour = Number(parts.find((part) => part.type === "hour")?.value ?? 0);
  const minute = Number(parts.find((part) => part.type === "minute")?.value ?? 0);
  const minutes = hour * 60 + minute;
  return !["Sat", "Sun"].includes(weekday ?? "") && minutes >= 540 && minutes <= 810;
}

export function parseMisStockQuote(
  row: Record<string, unknown>,
  meta: QuoteStockMeta,
  previousTrade?: StockQuote,
  now = new Date(),
): StockQuote | null {
  const quoteDate = isoDate(String(row.d));
  const snapshotTime = String(row.t || row.ot || "");
  const previousClose = number(row.y);
  const matchedPrice = number(row.z);
  const hasMatchedPrice = matchedPrice != null && matchedPrice > 0;
  const firstPositiveLevel = (value: unknown) => String(value ?? "").split("_")
    .map(number).find((level): level is number => level != null && level > 0) ?? null;
  const bestAsk = firstPositiveLevel(row.a);
  const bestBid = firstPositiveLevel(row.b);
  const orderBookPrice = bestAsk && bestAsk > 0
    ? bestAsk
    : bestBid && bestBid > 0
      ? bestBid
      : null;
  const cachedTrade = previousTrade?.source === "TWSE MIS"
    && previousTrade.date === quoteDate
    && isQuoteRealtime(previousTrade.date, previousTrade.time, now)
    ? previousTrade
    : undefined;
  if (previousClose == null || previousClose <= 0 || (!hasMatchedPrice && !cachedTrade && !orderBookPrice)) {
    return null;
  }
  const price = hasMatchedPrice ? matchedPrice : cachedTrade?.price ?? orderBookPrice!;
  const tradeTime = hasMatchedPrice ? snapshotTime : cachedTrade?.time ?? snapshotTime;
  const source = hasMatchedPrice || cachedTrade ? "TWSE MIS" : "TWSE MIS 五檔參考價";
  const open = number(row.o) ?? cachedTrade?.open ?? price;
  const high = number(row.h) ?? cachedTrade?.high ?? price;
  const low = number(row.l) ?? cachedTrade?.low ?? price;
  if (!tradeTime || open == null || high == null || low == null) return null;
  const change = price - previousClose;
  const volumeLots = number(row.v);
  return {
    symbol: meta.symbol,
    name: String(row.n || meta.name),
    date: quoteDate,
    time: tradeTime,
    open,
    high,
    low,
    price,
    previousClose,
    change,
    changePercent: previousClose ? (change / previousClose) * 100 : 0,
    volume: volumeLots != null ? Math.round(volumeLots * 1000) : cachedTrade?.volume ?? 0,
    bestBid: bestBid && bestBid > 0 ? bestBid : undefined,
    bestAsk: bestAsk && bestAsk > 0 ? bestAsk : undefined,
    source,
    isRealtime: isQuoteRealtime(quoteDate, tradeTime, now),
  };
}

async function fetchYahooQuote(meta: QuoteStockMeta): Promise<StockQuote | null> {
  const suffix = meta.market === "上市" ? "TW" : "TWO";
  const endpoint = `https://query1.finance.yahoo.com/v8/finance/chart/${meta.symbol}.${suffix}?interval=1m&range=1d`;
  const response = await fetch(endpoint, {
    headers: {
      Accept: "application/json",
      "User-Agent": "Mozilla/5.0 Moneymoney-TWSE-Dashboard",
    },
    signal: AbortSignal.timeout(8_000),
    cache: "no-store",
  });
  if (!response.ok) return null;
  return parseYahooChartQuote(await response.json(), meta);
}

async function fetchMisRowBatch(metas: QuoteStockMeta[]): Promise<Record<string, unknown>[]> {
  const channels = metas.map((meta) =>
    `${meta.market === "上市" ? "tse" : "otc"}_${meta.symbol}.tw`,
  ).join("|");
  const params = new URLSearchParams({
    ex_ch: channels,
    json: "1",
    delay: "0",
    _: String(Date.now()),
  });
  const endpoint = `https://mis.twse.com.tw/stock/api/getStockInfo.jsp?${params}`;
  const response = await fetch(endpoint, {
    headers: {
      Accept: "application/json",
      Referer: "https://mis.twse.com.tw/stock/fibest.jsp",
      "User-Agent": "Mozilla/5.0 Moneymoney-TWSE-Dashboard",
    },
    signal: AbortSignal.timeout(6_000),
    cache: "no-store",
  });
  if (!response.ok) return [];
  const payload = await response.json();
  return Array.isArray(payload.msgArray) ? payload.msgArray : [];
}

async function fetchMisRows(metas: QuoteStockMeta[]): Promise<Record<string, unknown>[]> {
  const batches: QuoteStockMeta[][] = [];
  for (let index = 0; index < metas.length; index += MIS_BATCH_SIZE) {
    batches.push(metas.slice(index, index + MIS_BATCH_SIZE));
  }
  const rows: Record<string, unknown>[] = [];
  const concurrency = 8;
  for (let index = 0; index < batches.length; index += concurrency) {
    const wave = await Promise.allSettled(
      batches.slice(index, index + concurrency).map((batch) => fetchMisRowBatch(batch)),
    );
    rows.push(...wave.flatMap((result) => result.status === "fulfilled" ? result.value : []));
  }
  return rows;
}

/** One-pass market snapshot for broad market views such as industry breadth.
 * Unlike getOfficialQuotes this does not retry inactive symbols, so a full-market
 * request cannot multiply into hundreds of MIS calls. Missing symbols are left
 * to the caller to count as unchanged or mark as unavailable.
 */
export async function getOfficialSnapshotQuotes(metas: QuoteStockMeta[]): Promise<Map<string, StockQuote>> {
  const results = new Map<string, StockQuote>();
  const metaBySymbol = new Map(metas.map((meta) => [meta.symbol, meta]));
  const rows = await fetchMisRows(metas);
  for (const row of rows) {
    const symbol = String(row.c ?? "");
    const meta = metaBySymbol.get(symbol);
    if (!meta) continue;
    const quote = parseMisStockQuote(row, meta, lastTradeCache.get(symbol));
    if (!quote) continue;
    results.set(symbol, quote);
    const matchedPrice = number(row.z);
    if (matchedPrice != null && matchedPrice > 0) lastTradeCache.set(symbol, quote);
  }
  return results;
}

async function fetchMisQuotes(metas: QuoteStockMeta[]): Promise<Map<string, StockQuote>> {
  const results = new Map<string, StockQuote>();
  const unresolved = new Map(metas.map((meta) => [meta.symbol, meta]));
  const attempts = isMarketSession() ? MIS_POLL_ATTEMPTS : 1;
  for (let attempt = 0; attempt < attempts && unresolved.size; attempt += 1) {
    const rows = await fetchMisRows([...unresolved.values()]);
    const rowsBySymbol = new Map(rows.map((row) => [String(row.c ?? ""), row]));
    for (const [symbol, meta] of unresolved) {
      const row = rowsBySymbol.get(symbol);
      if (!row) continue;
      const previousTrade = lastTradeCache.get(symbol);
      const quote = parseMisStockQuote(row, meta, previousTrade);
      if (quote) results.set(symbol, quote);
      const matchedPrice = number(row.z);
      if (quote && matchedPrice != null && matchedPrice > 0) {
        lastTradeCache.set(symbol, quote);
        unresolved.delete(symbol);
      }
    }
    if (unresolved.size && attempt < attempts - 1) {
      await new Promise((resolve) => setTimeout(resolve, MIS_POLL_INTERVAL_MS));
    }
  }
  return results;
}

export function parseBackendOfficialQuote(payload: BackendOfficialQuote): StockQuote | null {
  const timestamp = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})/.exec(payload.quoteTimestamp);
  if (
    !timestamp
    || !Number.isFinite(payload.price) || payload.price <= 0
    || !Number.isFinite(payload.previousClose) || payload.previousClose <= 0
    || !Number.isFinite(payload.open)
    || !Number.isFinite(payload.high)
    || !Number.isFinite(payload.low)
    || !Number.isFinite(payload.volume)
  ) return null;
  return {
    symbol: payload.symbol,
    name: payload.name,
    date: timestamp[1],
    time: timestamp[2],
    open: payload.open,
    high: payload.high,
    low: payload.low,
    price: payload.price,
    previousClose: payload.previousClose,
    change: payload.change,
    changePercent: payload.changePercent,
    volume: payload.volume,
    bestBid: payload.bestBid ?? undefined,
    bestAsk: payload.bestAsk ?? undefined,
    source: payload.source,
    isRealtime: payload.isRealtime,
  };
}

async function fetchBackendMisQuotes(metas: QuoteStockMeta[]): Promise<Map<string, StockQuote>> {
  if (!metas.length) return new Map();
  const batches: QuoteStockMeta[][] = [];
  for (let index = 0; index < metas.length; index += BACKEND_QUOTE_BATCH_SIZE) {
    batches.push(metas.slice(index, index + BACKEND_QUOTE_BATCH_SIZE));
  }
  const results = new Map<string, StockQuote>();
  const concurrency = 4;
  for (let index = 0; index < batches.length; index += concurrency) {
    const payloads = await Promise.allSettled(batches.slice(index, index + concurrency).map((items) =>
      backendJson<{ items: BackendOfficialQuote[] }>(
        "/market-data/quotes",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ items }),
        },
        15_000,
      ),
    ));
    for (const payload of payloads) {
      if (payload.status !== "fulfilled") continue;
      for (const item of payload.value.items) {
        const quote = parseBackendOfficialQuote(item);
        if (quote) results.set(quote.symbol, quote);
      }
    }
  }
  return results;
}

async function fetchClosingQuote(meta: QuoteStockMeta): Promise<StockQuote | null> {
  const listed = meta.market === "上市";
  const endpoint = listed
    ? "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
    : "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes";
  const response = await fetch(endpoint, { signal: AbortSignal.timeout(8_000), next: { revalidate: 60 } });
  if (!response.ok) return null;
  const rows = await response.json();
  const row = rows.find((item: Record<string, string>) =>
    listed ? item.Code === meta.symbol : item.SecuritiesCompanyCode === meta.symbol,
  );
  if (!row) return null;
  const price = number(listed ? row.ClosingPrice : row.Close);
  const change = number(listed ? row.Change : row.Change);
  const open = number(listed ? row.OpeningPrice : row.Open);
  const high = number(listed ? row.HighestPrice : row.High);
  const low = number(listed ? row.LowestPrice : row.Low);
  const volume = number(listed ? row.TradeVolume : row.TradingShares);
  if (price == null || change == null || open == null || high == null || low == null || volume == null) return null;
  const previousClose = price - change;
  return {
    symbol: meta.symbol, name: meta.name, date: isoDate(String(row.Date)), time: "收盤",
    open, high, low, price, previousClose, change,
    changePercent: previousClose ? (change / previousClose) * 100 : 0,
    volume, source: listed ? "TWSE OpenAPI" : "TPEx OpenAPI", isRealtime: false,
  };
}

export async function getOfficialQuotes(metas: QuoteStockMeta[]): Promise<Map<string, StockQuote>> {
  const result = new Map<string, StockQuote>();
  const now = Date.now();
  const missing: QuoteStockMeta[] = [];
  for (const meta of metas) {
    const cached = quoteCache.get(meta.symbol);
    if (cached && cached.expiresAt > now) result.set(meta.symbol, cached.value);
    else missing.push(meta);
  }
  if (!missing.length) return result;
  try {
    const liveQuotes = await fetchMisQuotes(missing);
    for (const [symbol, quote] of liveQuotes) {
      quoteCache.set(symbol, {
        value: quote,
        expiresAt: Date.now() + (quote.isRealtime ? LIVE_QUOTE_CACHE_MS : 15_000),
      });
      result.set(symbol, quote);
    }
  } catch {
    // A missing MIS response is handled below without disguising yesterday's close as a live price.
  }
  try {
    const backendQuotes = await fetchBackendMisQuotes(
      missing.filter((meta) => !result.has(meta.symbol)),
    );
    for (const [symbol, quote] of backendQuotes) {
      quoteCache.set(symbol, {
        value: quote,
        expiresAt: Date.now() + (quote.isRealtime ? LIVE_QUOTE_CACHE_MS : 15_000),
      });
      result.set(symbol, quote);
    }
  } catch {
    // The public official close remains the final fallback if the backend MIS relay is unavailable.
  }
  if (isMarketSession()) {
    await Promise.all(missing.filter((meta) => !result.has(meta.symbol)).map(async (meta) => {
      try {
        const fallback = await fetchYahooQuote(meta);
        if (!fallback) return;
        quoteCache.set(meta.symbol, { value: fallback, expiresAt: Date.now() + 15_000 });
        result.set(meta.symbol, fallback);
      } catch {
        // Keep the symbol unavailable rather than presenting an old close as today's quote.
      }
    }));
    return result;
  }
  await Promise.all(missing.filter((meta) => !result.has(meta.symbol)).map(async (meta) => {
    try {
      const fallback = await fetchClosingQuote(meta);
      if (!fallback) return;
      quoteCache.set(meta.symbol, { value: fallback, expiresAt: Date.now() + 60_000 });
      result.set(meta.symbol, fallback);
    } catch {
      // Keep the symbol unavailable when neither live nor official closing data can be verified.
    }
  }));
  return result;
}

export async function getOfficialQuote(meta: QuoteStockMeta): Promise<StockQuote | null> {
  const quotes = await getOfficialQuotes([meta]);
  return quotes.get(meta.symbol) ?? null;
}

export function resetOfficialQuoteCacheForTests() {
  quoteCache.clear();
  lastTradeCache.clear();
}

function scalePrice(price: DailyPrice, ratio: number): DailyPrice {
  const round = (value: number) => Math.round(value * ratio * 100) / 100;
  return { ...price, open: round(price.open), high: round(price.high), low: round(price.low), close: round(price.close) };
}

export function mergeOfficialQuote(payload: StockPayload, quote: StockQuote): StockPayload {
  const historyBeforeQuote = payload.prices.filter((price) => price.date < quote.date);
  if (!historyBeforeQuote.length) return { ...payload, quote, updatedAt: `${quote.date}T${quote.time}+08:00` };
  const simulatedPrevious = historyBeforeQuote.at(-1)!.close;
  const ratio = simulatedPrevious ? quote.previousClose / simulatedPrevious : 1;
  const scaledHistory = historyBeforeQuote.map((price) => scalePrice(price, ratio));
  const officialCandle: DailyPrice = {
    symbol: payload.meta.symbol, name: payload.meta.name, date: quote.date,
    open: quote.open, high: quote.high, low: quote.low, close: quote.price, volume: quote.volume,
  };
  const prices = [...scaledHistory, officialCandle].slice(-5280);
  return {
    ...payload,
    prices,
    indicators: calculateIndicators(prices),
    quote,
    updatedAt: `${quote.date}T${quote.time === "收盤" ? "13:30:00" : quote.time}+08:00`,
    dataMode: "official_quote_demo_history",
    dataNotice: "最新報價與昨收取自官方市場資訊；較早歷史 K 線仍為展示資料，不可用於交易決策。",
  };
}
