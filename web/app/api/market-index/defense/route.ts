import { NextResponse } from "next/server";
import { calculateChipDefense } from "@/lib/chip-defense";
import type {
  MarketIndexDefenseResponse,
  MarketIndexDefenseSnapshot,
} from "@/lib/market-index-defense";
import type { DailyPrice } from "@/lib/types";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

interface TwsePayload {
  stat?: string;
  data?: unknown[][];
}

interface TpexPayload {
  stat?: string;
  tables?: Array<{ data?: unknown[][] }>;
}

interface FinMindStockPrice {
  date?: string;
  stock_id?: string;
  Trading_Volume?: number;
  Trading_money?: number;
  open?: number;
  max?: number;
  min?: number;
  close?: number;
}

interface FinMindPayload {
  status?: number;
  msg?: string;
  data?: FinMindStockPrice[];
}

function numberValue(value: unknown): number {
  const parsed = Number(String(value ?? "").replaceAll(",", "").trim());
  return Number.isFinite(parsed) ? parsed : 0;
}

function rocDate(value: unknown): string | null {
  const match = String(value ?? "").trim().match(/^(\d{2,3})\/(\d{2})\/(\d{2})$/);
  return match ? `${Number(match[1]) + 1911}-${match[2]}-${match[3]}` : null;
}

function marketDate(value: unknown): string | null {
  const text = String(value ?? "").trim();
  const iso = text.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (iso) return text;
  const gregorian = text.match(/^(\d{4})\/(\d{2})\/(\d{2})$/);
  if (gregorian) return `${gregorian[1]}-${gregorian[2]}-${gregorian[3]}`;
  return rocDate(text);
}

function taipeiYearMonth(): { year: number; month: number } {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Taipei",
    year: "numeric",
    month: "2-digit",
  }).formatToParts(new Date());
  return {
    year: Number(parts.find((part) => part.type === "year")?.value),
    month: Number(parts.find((part) => part.type === "month")?.value),
  };
}

function monthKeys(count = 3): string[] {
  const { year, month } = taipeiYearMonth();
  return Array.from({ length: count }, (_, offset) => {
    const date = new Date(Date.UTC(year, month - 1 - offset, 1));
    return `${date.getUTCFullYear()}${String(date.getUTCMonth() + 1).padStart(2, "0")}01`;
  });
}

async function officialJson(url: string): Promise<TwsePayload> {
  const response = await fetch(url, {
    headers: {
      Accept: "application/json",
      "User-Agent": "Mozilla/5.0 Moneymoney-TWSE-Dashboard",
    },
    signal: AbortSignal.timeout(10_000),
    next: { revalidate: 300 },
  });
  if (!response.ok) throw new Error(`TWSE HTTP ${response.status}`);
  const payload = await response.json() as TwsePayload;
  if (payload.stat !== "OK") throw new Error("TWSE 指數歷史資料尚未發布");
  return payload;
}

async function officialTpexJson(url: string): Promise<TpexPayload> {
  let lastError: unknown;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      const response = await fetch(url, {
        headers: {
          Accept: "application/json",
          Referer: "https://www.tpex.org.tw/zh-tw/indices/stock-index/industrial/inxh.html",
          "User-Agent": "Mozilla/5.0 Moneymoney-TWSE-Dashboard",
        },
        signal: AbortSignal.timeout(10_000),
        next: { revalidate: 300 },
      });
      if (!response.ok) throw new Error(`TPEx HTTP ${response.status}`);
      const payload = await response.json() as TpexPayload;
      if (payload.stat?.toLowerCase() !== "ok") throw new Error("TPEx 指數歷史資料尚未發布");
      return payload;
    } catch (error) {
      lastError = error;
      if (attempt === 0) await new Promise((resolve) => setTimeout(resolve, 350));
    }
  }
  throw lastError instanceof Error ? lastError : new Error("TPEx 指數資料讀取失敗");
}

async function proxiedTpexJson(url: string): Promise<TpexPayload> {
  const response = await fetch(`https://r.jina.ai/http://${url}`, {
    headers: {
      Accept: "text/plain",
      "User-Agent": "Mozilla/5.0 Moneymoney-TWSE-Dashboard",
    },
    signal: AbortSignal.timeout(12_000),
    next: { revalidate: 300 },
  });
  if (!response.ok) throw new Error(`TPEx reader HTTP ${response.status}`);
  const text = await response.text();
  const marker = "Markdown Content:";
  const jsonText = text.includes(marker) ? text.slice(text.indexOf(marker) + marker.length).trim() : text.trim();
  const payload = JSON.parse(jsonText) as TpexPayload;
  if (payload.stat?.toLowerCase() !== "ok") throw new Error("TPEx reader data unavailable");
  return payload;
}

async function monthPrices(month: string): Promise<DailyPrice[]> {
  const [indexPayload, turnoverPayload] = await Promise.all([
    officialJson(`https://www.twse.com.tw/rwd/zh/TAIEX/MI_5MINS_HIST?date=${month}&response=json`),
    officialJson(`https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?date=${month}&response=json`),
  ]);
  const turnoverByDate = new Map<string, number>();
  for (const row of turnoverPayload.data ?? []) {
    const date = rocDate(row[0]);
    const tradedValue = numberValue(row[2]);
    if (date && tradedValue > 0) turnoverByDate.set(date, tradedValue);
  }
  return (indexPayload.data ?? []).flatMap((row): DailyPrice[] => {
    const date = rocDate(row[0]);
    const open = numberValue(row[1]);
    const high = numberValue(row[2]);
    const low = numberValue(row[3]);
    const close = numberValue(row[4]);
    const volume = date ? turnoverByDate.get(date) ?? 0 : 0;
    if (!date || !open || !high || !low || !close || !volume) return [];
    return [{ symbol: "TAIEX", name: "發行量加權股價指數", date, open, high, low, close, volume }];
  });
}

async function otcMonthPrices(
  month: string,
  fetchPayload: (url: string) => Promise<TpexPayload> = officialTpexJson,
): Promise<DailyPrice[]> {
  const queryDate = `${month.slice(0, 4)}/${month.slice(4, 6)}/01`;
  const search = new URLSearchParams({ date: queryDate, response: "json" });
  // TPEx applies stricter burst limits than TWSE. Keep these calls sequential
  // so a cold cache cannot turn four simultaneous requests into 429/520 errors.
  const indexPayload = await fetchPayload(
    `https://www.tpex.org.tw/www/zh-tw/indexInfo/inx?${search}`,
  );
  const turnoverPayload = await fetchPayload(
    `https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingIndexRpk?${search}`,
  );
  const turnoverByDate = new Map<string, number>();
  for (const row of turnoverPayload.tables?.[0]?.data ?? []) {
    const date = marketDate(row[0]);
    const tradedValue = numberValue(row[2]);
    if (date && tradedValue > 0) turnoverByDate.set(date, tradedValue);
  }
  return (indexPayload.tables?.[0]?.data ?? []).flatMap((row): DailyPrice[] => {
    const date = marketDate(row[0]);
    const open = numberValue(row[1]);
    const high = numberValue(row[2]);
    const low = numberValue(row[3]);
    const close = numberValue(row[4]);
    const volume = date ? turnoverByDate.get(date) ?? 0 : 0;
    if (!date || !open || !high || !low || !close || !volume) return [];
    return [{ symbol: "TPEx", name: "櫃買指數", date, open, high, low, close, volume }];
  });
}

async function liveIndex(
  fallback: DailyPrice,
  channel = "tse_t00.tw",
): Promise<{ price: number; quoteAt: string }> {
  try {
    const response = await fetch(
      `https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=${channel}&json=1&delay=0`,
      {
        headers: {
          Accept: "application/json",
          Referer: "https://mis.twse.com.tw/stock/fibest.jsp?stock=t00",
          "User-Agent": "Mozilla/5.0 Moneymoney-TWSE-Dashboard",
        },
        signal: AbortSignal.timeout(8_000),
        cache: "no-store",
      },
    );
    if (!response.ok) throw new Error("MIS unavailable");
    const row = (await response.json() as { msgArray?: Record<string, string>[] }).msgArray?.[0];
    const price = numberValue(row?.z) || numberValue(row?.y) || fallback.close;
    const date = String(row?.d ?? "").replace(/^(\d{4})(\d{2})(\d{2})$/, "$1-$2-$3") || fallback.date;
    return { price, quoteAt: `${date} ${row?.t || "13:30:00"}` };
  } catch {
    return { price: fallback.close, quoteAt: `${fallback.date} 收盤` };
  }
}

async function loadPrices(
  loader: (month: string) => Promise<DailyPrice[]>,
): Promise<DailyPrice[]> {
  const results = await Promise.all(monthKeys().map((month) => loader(month).catch(() => [])));
  const byDate = new Map(results.flat().map((price) => [price.date, price]));
  return [...byDate.values()].sort((left, right) => left.date.localeCompare(right.date));
}

async function loadOtcPrices(): Promise<DailyPrice[]> {
  const end = new Date();
  const start = new Date(end);
  start.setUTCDate(start.getUTCDate() - 90);
  const dateText = (date: Date) => date.toISOString().slice(0, 10);
  const params = new URLSearchParams({
    dataset: "TaiwanStockPrice",
    data_id: "TPEx",
    start_date: dateText(start),
    end_date: dateText(end),
  });
  const token = process.env.FINMIND_API_TOKEN?.trim();
  if (token) params.set("token", token);

  try {
    const response = await fetch(`https://api.finmindtrade.com/api/v4/data?${params}`, {
      headers: {
        Accept: "application/json",
        "User-Agent": "Mozilla/5.0 Moneymoney-TWSE-Dashboard",
      },
      signal: AbortSignal.timeout(8_000),
      next: { revalidate: 300 },
    });
    if (!response.ok) throw new Error(`FinMind HTTP ${response.status}`);
    const payload = await response.json() as FinMindPayload;
    if (payload.status !== 200) throw new Error(payload.msg || "FinMind data unavailable");
    const prices = (payload.data ?? []).flatMap((row): DailyPrice[] => {
      const date = marketDate(row.date);
      const open = numberValue(row.open);
      const high = numberValue(row.max);
      const low = numberValue(row.min);
      const close = numberValue(row.close);
      const volume = numberValue(row.Trading_money) || numberValue(row.Trading_Volume);
      if (!date || !open || !high || !low || !close || !volume) return [];
      return [{ symbol: "TPEx", name: "櫃買指數", date, open, high, low, close, volume }];
    });
    if (prices.length >= 20) return prices;
  } catch {
    // Fall through to the TPEx reader route. Some deployment regions cannot
    // connect to FinMind or TPEx directly.
  }

  const proxiedResults = await Promise.all(
    monthKeys(2).map((month) => otcMonthPrices(month, proxiedTpexJson).catch(() => [])),
  );
  const proxiedByDate = new Map(proxiedResults.flat().map((price) => [price.date, price]));
  const proxiedPrices = [...proxiedByDate.values()]
    .sort((left, right) => left.date.localeCompare(right.date));
  if (proxiedPrices.length >= 20) return proxiedPrices;

  const results: DailyPrice[][] = [];
  for (const month of monthKeys(2)) {
    try {
      results.push(await otcMonthPrices(month));
    } catch {
      results.push([]);
    }
  }
  const byDate = new Map(results.flat().map((price) => [price.date, price]));
  return [...byDate.values()].sort((left, right) => left.date.localeCompare(right.date));
}

async function taiexDefense(): Promise<MarketIndexDefenseSnapshot> {
  const prices = await loadPrices(monthPrices);
  const latest = prices.at(-1);
  if (!latest || prices.length < 5) throw new Error("加權指數歷史資料不足");
  const live = await liveIndex(latest);
  return {
    indexName: "加權指數",
    currentPrice: live.price,
    source: "臺灣證券交易所 TAIEX 日線＋全市場每日成交金額",
    quoteAt: live.quoteAt,
    calculationNote: "近5／20個交易日以全市場每日成交金額加權，計算指數主要成交區與防守點。",
    defense: calculateChipDefense(prices, live.price),
  };
}

async function otcDefense(): Promise<MarketIndexDefenseSnapshot> {
  const prices = await loadOtcPrices();
  const latest = prices.at(-1);
  if (!latest || prices.length < 5) throw new Error("櫃買指數歷史資料不足");
  const live = await liveIndex(latest, "otc_o00.tw");
  return {
    indexName: "櫃買指數",
    currentPrice: live.price,
    source: "TPEx／FinMind 指數日線與成交金額＋TWSE MIS 即時櫃買指數",
    quoteAt: live.quoteAt,
    calculationNote: "近5／20個交易日以上櫃市場每日成交金額加權，計算櫃買指數主要成交區與防守點。",
    defense: calculateChipDefense(prices, live.price),
  };
}

export async function GET() {
  try {
    const [taiex, otcResult] = await Promise.all([
      taiexDefense(),
      otcDefense().then((value) => ({ value })).catch((error: unknown) => ({
        value: null,
        error: error instanceof Error ? error.message : "櫃買指數防守點計算失敗",
      })),
    ]);
    const payload: MarketIndexDefenseResponse = {
      ...taiex,
      otc: otcResult.value,
      ...(otcResult.value ? {} : { otcError: otcResult.error }),
    };
    return NextResponse.json(payload, { headers: { "Cache-Control": "no-store, max-age=0" } });
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "大盤防守點計算失敗" },
      { status: 503 },
    );
  }
}
