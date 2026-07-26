import { calculateIndicators } from "@/lib/indicators";
import type { DailyPrice, StockMeta, StockPayload, StockQuote } from "@/lib/types";

const quoteCache = new Map<string, { value: StockQuote; expiresAt: number }>();

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

async function fetchMisQuote(meta: StockMeta): Promise<StockQuote | null> {
  const exchange = meta.market === "上市" ? "tse" : "otc";
  const endpoint = `https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=${exchange}_${meta.symbol}.tw&json=1&delay=0`;
  const response = await fetch(endpoint, {
    headers: {
      Accept: "application/json",
      Referer: `https://mis.twse.com.tw/stock/fibest.jsp?stock=${meta.symbol}`,
      "User-Agent": "Mozilla/5.0 Moneymoney-TWSE-Dashboard",
    },
    signal: AbortSignal.timeout(6_000),
    cache: "no-store",
  });
  if (!response.ok) return null;
  const payload = await response.json();
  const row = payload.msgArray?.[0];
  if (!row) return null;
  const quoteDate = isoDate(String(row.d));
  const quoteTime = String(row.t || row.ot || "13:30:00");
  const previousClose = number(row.y);
  const lastTrade = number(row.z);
  const price = lastTrade && lastTrade > 0 ? lastTrade : previousClose;
  const open = number(row.o) ?? price;
  const high = number(row.h) ?? price;
  const low = number(row.l) ?? price;
  if (price == null || previousClose == null || open == null || high == null || low == null) return null;
  const change = price - previousClose;
  const bestAsk = number(String(row.a ?? "").split("_")[0]);
  const bestBid = number(String(row.b ?? "").split("_")[0]);
  return {
    symbol: meta.symbol, name: String(row.n || meta.name), date: quoteDate, time: quoteTime,
    open, high, low, price, previousClose, change,
    changePercent: previousClose ? (change / previousClose) * 100 : 0,
    volume: Math.round((number(row.v) ?? 0) * 1000),
    bestBid: bestBid && bestBid > 0 ? bestBid : undefined,
    bestAsk: bestAsk && bestAsk > 0 ? bestAsk : undefined,
    source: "TWSE MIS", isRealtime: Boolean(lastTrade && lastTrade > 0 && isQuoteRealtime(quoteDate, quoteTime)),
  };
}

async function fetchClosingQuote(meta: StockMeta): Promise<StockQuote | null> {
  const listed = meta.market === "上市";
  const endpoint = listed
    ? "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
    : "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes";
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

export async function getOfficialQuote(meta: StockMeta): Promise<StockQuote | null> {
  const cached = quoteCache.get(meta.symbol);
  if (cached && cached.expiresAt > Date.now()) return cached.value;
  try {
    const quote = await fetchMisQuote(meta) ?? await fetchClosingQuote(meta);
    if (!quote) return null;
    quoteCache.set(meta.symbol, { value: quote, expiresAt: Date.now() + (quote.isRealtime ? 8_000 : 60_000) });
    return quote;
  } catch {
    try {
      const fallback = await fetchClosingQuote(meta);
      if (fallback) quoteCache.set(meta.symbol, { value: fallback, expiresAt: Date.now() + 60_000 });
      return fallback;
    } catch {
      return null;
    }
  }
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
