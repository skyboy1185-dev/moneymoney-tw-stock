import type { Market, StockMeta } from "@/lib/types";

type JsonRow = Record<string, unknown>;

const LISTED_VALUATION_URL = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL";
const LISTED_COMPANY_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L";
const LISTED_CLOSE_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL";
const OTC_VALUATION_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis";
const OTC_COMPANY_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O";
const OTC_CLOSE_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes";
const FUNDAMENTALS_CACHE_MS = 6 * 60 * 60 * 1_000;

const INDUSTRIES: Record<string, string> = {
  "01": "水泥工業", "02": "食品工業", "03": "塑膠工業", "04": "紡織纖維",
  "05": "電機機械", "06": "電器電纜", "08": "玻璃陶瓷", "09": "造紙工業",
  "10": "鋼鐵工業", "11": "橡膠工業", "12": "汽車工業", "14": "建材營造",
  "15": "航運業", "16": "觀光餐旅", "17": "金融保險", "18": "貿易百貨",
  "20": "其他", "21": "化學工業", "22": "生技醫療業", "23": "油電燃氣業",
  "24": "半導體業", "25": "電腦及週邊設備業", "26": "光電業", "27": "通信網路業",
  "28": "電子零組件業", "29": "電子通路業", "30": "資訊服務業", "31": "其他電子業",
  "32": "文化創意業", "33": "居家生活", "34": "數位雲端", "35": "綠能環保",
  "36": "運動休閒",
};

export interface FundamentalRows {
  valuation: JsonRow[];
  companies: JsonRow[];
  closes: JsonRow[];
}

let cache = new Map<Market, { rows: FundamentalRows; expiresAt: number }>();

function text(row: JsonRow | undefined, keys: string[]) {
  if (!row) return "";
  for (const key of keys) {
    const value = row[key];
    if (value != null && String(value).trim()) return String(value).trim();
  }
  return "";
}

function numberValue(value: unknown): number | null {
  const normalized = String(value ?? "").replaceAll(",", "").trim();
  if (!normalized || ["N/A", "----", "---", "--"].includes(normalized)) return null;
  const parsed = Number(normalized);
  return Number.isFinite(parsed) ? parsed : null;
}

function rocDateToIso(value: unknown) {
  const date = String(value ?? "").trim();
  if (/^\d{7}$/.test(date)) {
    return `${Number(date.slice(0, 3)) + 1911}-${date.slice(3, 5)}-${date.slice(5, 7)}`;
  }
  if (/^\d{8}$/.test(date)) {
    return `${date.slice(0, 4)}-${date.slice(4, 6)}-${date.slice(6, 8)}`;
  }
  return date;
}

async function fetchRows(url: string) {
  const response = await fetch(url, {
    headers: { Accept: "application/json" },
    signal: AbortSignal.timeout(12_000),
    next: { revalidate: 21_600 },
  });
  if (!response.ok) throw new Error(`Official fundamentals request failed: ${response.status}`);
  const payload: unknown = await response.json();
  return Array.isArray(payload) ? payload as JsonRow[] : [];
}

async function loadRows(market: Market): Promise<FundamentalRows> {
  const cached = cache.get(market);
  if (cached && cached.expiresAt > Date.now()) return cached.rows;
  const listed = market === "上市";
  const [valuation, companies, closes] = await Promise.all([
    fetchRows(listed ? LISTED_VALUATION_URL : OTC_VALUATION_URL),
    fetchRows(listed ? LISTED_COMPANY_URL : OTC_COMPANY_URL),
    fetchRows(listed ? LISTED_CLOSE_URL : OTC_CLOSE_URL),
  ]);
  const rows = { valuation, companies, closes };
  cache.set(market, { rows, expiresAt: Date.now() + FUNDAMENTALS_CACHE_MS });
  return rows;
}

export function parseOfficialFundamentals(
  meta: StockMeta,
  rows: FundamentalRows,
  currentPrice?: number,
): StockMeta {
  const listed = meta.market === "上市";
  const valuation = rows.valuation.find((row) =>
    text(row, listed ? ["Code"] : ["SecuritiesCompanyCode"]) === meta.symbol,
  );
  const company = rows.companies.find((row) =>
    text(row, listed ? ["公司代號"] : ["SecuritiesCompanyCode"]) === meta.symbol,
  );
  const closeRow = rows.closes.find((row) =>
    text(row, listed ? ["Code"] : ["SecuritiesCompanyCode"]) === meta.symbol,
  );
  const peRatio = numberValue(text(valuation, listed ? ["PEratio"] : ["PriceEarningRatio"]));
  const dividendYield = numberValue(text(valuation, listed ? ["DividendYield"] : ["YieldRatio"]));
  const priceToBook = numberValue(text(valuation, listed ? ["PBratio"] : ["PriceBookRatio"]));
  const valuationClose = numberValue(text(closeRow, listed ? ["ClosingPrice"] : ["Close"]));
  const issuedShares = numberValue(text(
    company,
    listed ? ["已發行普通股數或TDR原股發行股數"] : ["IssueShares"],
  ));
  const industryCode = text(company, listed ? ["產業別"] : ["SecuritiesIndustryCode"]);
  const marketPrice = currentPrice && currentPrice > 0 ? currentPrice : valuationClose;
  return {
    ...meta,
    industry: INDUSTRIES[industryCode] ?? meta.industry,
    peRatio,
    dividendYield,
    priceToBook,
    eps: peRatio && valuationClose ? valuationClose / peRatio : null,
    marketCap: issuedShares && marketPrice ? issuedShares * marketPrice : null,
    fundamentalsDate: rocDateToIso(text(valuation, ["Date"])),
    fundamentalsSource: listed ? "TWSE 官方基本資料" : "TPEx 官方基本資料",
  };
}

export async function enrichOfficialStockMeta(
  meta: StockMeta,
  currentPrice?: number,
): Promise<StockMeta> {
  try {
    return parseOfficialFundamentals(meta, await loadRows(meta.market), currentPrice);
  } catch {
    return meta;
  }
}

export function resetOfficialFundamentalsCacheForTests() {
  cache = new Map();
}
