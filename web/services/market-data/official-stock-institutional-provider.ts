import type {
  StockInstitutionalFlowPoint,
  StockInstitutionalFlowResponse,
  StockMeta,
} from "@/lib/types";

const TWSE_COMPANY_INVESTORS_URL = "https://wwwc.twse.com.tw/rwd/zh/IIH/company/foreign";
const TPEX_DAILY_INVESTORS_URL =
  "https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php";

interface TwseCompanyInvestorsResponse {
  info?: { status?: string };
  chart?: {
    foreign?: {
      categories?: string[];
      series?: Array<{ name?: string; data?: Array<number | null> }>;
    };
  };
}

interface TpexDailyInvestorsResponse {
  tables?: Array<{ date?: string; data?: string[][] }>;
}

function ymdInTaipei(date = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Taipei",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date);
  const value = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${value.year}-${value.month}-${value.day}`;
}

function shiftDays(date: string, days: number) {
  const shifted = new Date(`${date}T00:00:00Z`);
  shifted.setUTCDate(shifted.getUTCDate() + days);
  return shifted.toISOString().slice(0, 10);
}

function compactDate(date: string) {
  return date.replaceAll("-", "");
}

function rocDate(date: string) {
  const [year, month, day] = date.split("-").map(Number);
  return `${year - 1911}/${String(month).padStart(2, "0")}/${String(day).padStart(2, "0")}`;
}

function number(value: unknown) {
  const parsed = Number(String(value ?? "").replaceAll(",", "").trim());
  return Number.isFinite(parsed) ? parsed : 0;
}

function tradingWeekdays(startDate: string, endDate: string) {
  const dates: string[] = [];
  const cursor = new Date(`${startDate}T00:00:00Z`);
  const end = new Date(`${endDate}T00:00:00Z`);
  while (cursor <= end) {
    const day = cursor.getUTCDay();
    if (day !== 0 && day !== 6) dates.push(cursor.toISOString().slice(0, 10));
    cursor.setUTCDate(cursor.getUTCDate() + 1);
  }
  return dates;
}

function totals(items: StockInstitutionalFlowPoint[]) {
  const result = items.reduce(
    (sum, item) => ({
      foreign: sum.foreign + item.foreign,
      trust: sum.trust + item.trust,
      dealer: sum.dealer + item.dealer,
      total: sum.total + item.total,
    }),
    { foreign: 0, trust: 0, dealer: 0, total: 0 },
  );
  return Object.fromEntries(
    Object.entries(result).map(([key, value]) => [key, Math.round(value * 1_000) / 1_000]),
  ) as typeof result;
}

export function parseTwseCompanyInvestors(
  payload: TwseCompanyInvestorsResponse,
): StockInstitutionalFlowPoint[] {
  const chart = payload.chart?.foreign;
  if (payload.info?.status !== "success" || !chart?.categories?.length || !chart.series?.length) return [];
  const series = chart.series;
  const foreign = series.find((item) => item.name === "外資")?.data ?? series[0]?.data ?? [];
  const trust = series.find((item) => item.name === "投信")?.data ?? series[1]?.data ?? [];
  const dealer = series.find((item) => item.name === "自營商")?.data ?? series[2]?.data ?? [];
  const total = series.find((item) => item.name === "總買賣超")?.data ?? series[3]?.data ?? [];
  return chart.categories.map((date, index) => ({
    date: date.replaceAll("/", "-"),
    foreign: number(foreign[index]),
    trust: number(trust[index]),
    dealer: number(dealer[index]),
    total: number(total[index]),
  }));
}

export function parseTpexDailyInvestors(
  payload: TpexDailyInvestorsResponse,
  symbol: string,
  fallbackDate: string,
): StockInstitutionalFlowPoint | null {
  const table = payload.tables?.[0];
  const row = table?.data?.find((item) => item[0]?.trim() === symbol);
  if (!row || row.length < 24) return null;
  return {
    date: table?.date
      ? table.date.split("/").map((part, index) =>
        index === 0 ? String(Number(part) + 1911) : part.padStart(2, "0")).join("-")
      : fallbackDate,
    foreign: number(row[4]) / 1_000,
    trust: number(row[13]) / 1_000,
    dealer: number(row[22]) / 1_000,
    total: number(row[23]) / 1_000,
  };
}

async function fetchJson<T>(url: string) {
  const response = await fetch(url, {
    headers: {
      Accept: "application/json",
      "User-Agent": "Moneymoney stock institutional flow",
    },
    cache: "no-store",
    signal: AbortSignal.timeout(15_000),
  });
  if (!response.ok) throw new Error(`官方法人資料回應 ${response.status}`);
  return response.json() as Promise<T>;
}

async function listedItems(symbol: string, startDate: string, endDate: string) {
  const query = new URLSearchParams({
    code: symbol,
    start: compactDate(startDate),
    end: compactDate(endDate),
  });
  const payload = await fetchJson<TwseCompanyInvestorsResponse>(
    `${TWSE_COMPANY_INVESTORS_URL}?${query}`,
  );
  return parseTwseCompanyInvestors(payload);
}

async function otcItems(symbol: string, startDate: string, endDate: string) {
  const results = await Promise.allSettled(
    tradingWeekdays(startDate, endDate).map(async (date) => {
      const query = new URLSearchParams({
        l: "zh-tw",
        o: "json",
        se: "EW",
        t: "D",
        d: rocDate(date),
        s: "0,asc",
      });
      const payload = await fetchJson<TpexDailyInvestorsResponse>(
        `${TPEX_DAILY_INVESTORS_URL}?${query}`,
      );
      return parseTpexDailyInvestors(payload, symbol, date);
    }),
  );
  return results
    .flatMap((result) => result.status === "fulfilled" && result.value ? [result.value] : [])
    .sort((a, b) => a.date.localeCompare(b.date));
}

export async function getOfficialStockInstitutionalFlow(
  meta: StockMeta,
): Promise<StockInstitutionalFlowResponse> {
  const endDate = ymdInTaipei();
  const startDate = shiftDays(endDate, -31);
  const items = meta.market === "上市"
    ? await listedItems(meta.symbol, startDate, endDate)
    : await otcItems(meta.symbol, startDate, endDate);
  if (!items.length) throw new Error(`${meta.symbol} 最近一個月尚無三大法人個股資料。`);
  return {
    symbol: meta.symbol,
    name: meta.name,
    market: meta.market,
    startDate,
    endDate,
    updatedAt: new Date().toISOString(),
    unit: "張",
    items,
    totals: totals(items),
    source: meta.market === "上市" ? "臺灣證券交易所" : "證券櫃檯買賣中心",
    sourceUrl: meta.market === "上市"
      ? `https://wwwc.twse.com.tw/IIH2/zh/company/investors.html?code=${meta.symbol}`
      : "https://www.tpex.org.tw/zh-tw/mainboard/trading/major-institutional/detail/day.html",
    notice: "正數代表買超、負數代表賣超；1 張＝1,000 股。資料為官方盤後統計，盤中不會即時變動。",
  };
}
