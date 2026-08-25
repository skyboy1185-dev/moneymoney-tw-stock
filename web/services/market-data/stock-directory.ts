import type { Market, StockMeta } from "@/lib/types";
import { stockCatalog } from "@/services/stock-service";

type DirectoryRow = Record<string, unknown>;

const TWSE_DIRECTORY_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL";
// Use the stock-only endpoint. The daily-close-all endpoint also contains
// thousands of warrants and is large enough to time out in production,
// which previously caused the whole OTC directory to disappear.
const TPEX_DIRECTORY_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes";
const DIRECTORY_CACHE_MS = 6 * 60 * 60 * 1_000;

let directoryCache: { stocks: StockMeta[]; expiresAt: number } | null = null;

const EXTRA_FALLBACK_STOCKS: StockMeta[] = [
  {
    symbol: "8096",
    name: "擎亞",
    industry: "電子通路",
    market: "上櫃" as Market,
    peRatio: null,
    dividendYield: null,
    priceToBook: null,
    eps: null,
    marketCap: null,
  },
];

function text(row: DirectoryRow, keys: string[]): string {
  for (const key of keys) {
    const value = row[key];
    if (value != null && String(value).trim()) return String(value).trim();
  }
  return "";
}

function normalize(value: string): string {
  return value.trim().replace(/\s+/g, "").toLocaleLowerCase("zh-TW");
}

function toMeta(row: DirectoryRow, market: Market): StockMeta | null {
  const symbol = text(row, market === "上市"
    ? ["Code", "公司代號", "證券代號"]
    : ["SecuritiesCompanyCode", "Code", "公司代號", "證券代號"]);
  const name = text(row, market === "上市"
    ? ["Name", "公司簡稱", "證券名稱"]
    : ["CompanyName", "Name", "公司簡稱", "證券名稱"]);
  if (!symbol || !name || !/^[0-9A-Z]{4,7}$/i.test(symbol)) return null;
  return {
    symbol,
    name,
    industry: "暫無資料",
    market,
    peRatio: null,
    dividendYield: null,
    priceToBook: null,
    eps: null,
    marketCap: null,
  };
}

export function parseOfficialStockDirectory(listedRows: DirectoryRow[], otcRows: DirectoryRow[]): StockMeta[] {
  const merged = [
    ...listedRows.map((row) => toMeta(row, "上市")),
    ...otcRows.map((row) => toMeta(row, "上櫃")),
  ].filter((stock): stock is StockMeta => stock !== null);
  return [...new Map(merged.map((stock) => [stock.symbol, stock])).values()];
}

function fallbackDirectory(): StockMeta[] {
  return [
    ...stockCatalog.map((stock) => ({
      ...stock,
      peRatio: null,
      dividendYield: null,
      priceToBook: null,
      eps: null,
      marketCap: null,
    })),
    ...EXTRA_FALLBACK_STOCKS,
  ];
}

async function fetchRows(url: string): Promise<DirectoryRow[]> {
  const response = await fetch(url, {
    headers: { Accept: "application/json" },
    signal: AbortSignal.timeout(8_000),
    next: { revalidate: 21_600 },
  });
  if (!response.ok) throw new Error(`股票目錄回應 ${response.status}`);
  const payload: unknown = await response.json();
  return Array.isArray(payload) ? payload as DirectoryRow[] : [];
}

export async function getOfficialStockDirectory(): Promise<StockMeta[]> {
  if (directoryCache && directoryCache.expiresAt > Date.now()) return directoryCache.stocks;
  const [listed, otc] = await Promise.allSettled([
    fetchRows(TWSE_DIRECTORY_URL),
    fetchRows(TPEX_DIRECTORY_URL),
  ]);
  const official = parseOfficialStockDirectory(
    listed.status === "fulfilled" ? listed.value : [],
    otc.status === "fulfilled" ? otc.value : [],
  );
  const stocks = [...new Map(
    [...official, ...fallbackDirectory()].map((stock) => [stock.symbol, stock]),
  ).values()];
  directoryCache = { stocks, expiresAt: Date.now() + DIRECTORY_CACHE_MS };
  return stocks;
}

export function findStockInDirectory(stocks: StockMeta[], query: string): StockMeta | null {
  const keyword = normalize(query);
  if (!keyword) return null;
  return stocks.find((stock) => normalize(stock.symbol) === keyword)
    ?? stocks.find((stock) => normalize(stock.name) === keyword)
    ?? stocks.find((stock) => normalize(stock.symbol).startsWith(keyword))
    ?? stocks.find((stock) => normalize(stock.name).startsWith(keyword))
    ?? stocks.find((stock) => normalize(stock.name).includes(keyword))
    ?? null;
}

export async function resolveOfficialStock(query: string): Promise<StockMeta | null> {
  return findStockInDirectory(await getOfficialStockDirectory(), query);
}

export function resetStockDirectoryCacheForTests() {
  directoryCache = null;
}
