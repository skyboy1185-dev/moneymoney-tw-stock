import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import type {
  FlowAmount,
  InstitutionFlowRow,
  InstitutionId,
  InstitutionalInvestorResponse,
  MarketFlowAmount,
  RetailFuturesHistoryPoint,
  RetailFuturesPosition,
} from "@/lib/institutional-investor-types";

type InstitutionKey = Exclude<InstitutionId, "total">;
type InstitutionMap = Record<InstitutionKey, FlowAmount>;

interface TwsePayload {
  stat?: string;
  date?: string;
  title?: string;
  data?: unknown[][];
}

interface TpexPayload {
  stat?: string;
  date?: string;
  tables?: Array<{
    title?: string;
    subtitle?: string;
    date?: string;
    data?: unknown[][];
  }>;
}

interface TpexOpenApiSummaryRow {
  Date: string;
  Investor: string;
  PurchaseAmount: string;
  SaleAmount: string;
  Net: string;
}

interface TaifexDailyRow {
  Date: string;
  Contract: string;
  "ContractMonth(Week)": string;
  OpenInterest: string;
  TradingSession: string;
}

interface TaifexInstitutionRow {
  Date: string;
  ContractCode: string;
  Item: string;
  "OpenInterest(Long)": string;
  "OpenInterest(Short)": string;
}

const ZERO: FlowAmount = { buy: 0, sell: 0, net: 0 };
const CACHE_MS = 5 * 60_000;
const REFRESH_RETRY_MS = 60_000;
const CACHE_DIRECTORY = "data";
const CACHE_FILE = "data/institutional-investors-cache.json";
let cached: { expiresAt: number; value: InstitutionalInvestorResponse } | null = null;
let pending: Promise<InstitutionalInvestorResponse> | null = null;
let diskCacheLoaded = false;
let nextRefreshAt = 0;

function isCachedResponse(value: unknown): value is InstitutionalInvestorResponse {
  if (!value || typeof value !== "object") return false;
  const item = value as Partial<InstitutionalInvestorResponse>;
  return (
    typeof item.asOfDate === "string"
    && typeof item.updatedAt === "string"
    && Array.isArray(item.items)
    && item.items.length > 0
    && Boolean(item.retailFutures)
    && Array.isArray(item.retailFutures?.items)
    && Array.isArray(item.retailFutures?.history)
  );
}

async function loadPersistentCache() {
  if (diskCacheLoaded) return;
  diskCacheLoaded = true;
  try {
    const stored = JSON.parse(await readFile(CACHE_FILE, "utf8")) as {
      expiresAt?: number;
      value?: unknown;
    };
    if (isCachedResponse(stored.value)) {
      cached = {
        expiresAt: Number.isFinite(stored.expiresAt)
          ? Number(stored.expiresAt)
          : Date.now() - 1,
        value: stored.value,
      };
    }
  } catch {
    // The first successful refresh will create the persistent cache file.
  }
}

async function savePersistentCache(entry: {
  expiresAt: number;
  value: InstitutionalInvestorResponse;
}) {
  const temporary = `${CACHE_FILE}.${process.pid}.tmp`;
  try {
    await mkdir(CACHE_DIRECTORY, { recursive: true });
    await writeFile(temporary, JSON.stringify(entry), "utf8");
    await rename(temporary, CACHE_FILE);
  } catch (reason) {
    console.warn("institutional-investors persistent cache unavailable", reason);
  }
}

function staleResponse(value: InstitutionalInvestorResponse): InstitutionalInvestorResponse {
  const staleNotice = "官方來源暫時無法更新，目前顯示最近一次成功取得的資料。";
  return {
    ...value,
    dataNotice: value.dataNotice.includes(staleNotice)
      ? value.dataNotice
      : `${value.dataNotice} ${staleNotice}`,
  };
}

function amount(value: unknown) {
  const parsed = Number(String(value ?? "").replaceAll(",", "").trim());
  return Number.isFinite(parsed) ? parsed : 0;
}

function rowAmount(row: unknown[]): FlowAmount {
  return { buy: amount(row[1]), sell: amount(row[2]), net: amount(row[3]) };
}

function add(...items: FlowAmount[]): FlowAmount {
  return items.reduce(
    (result, item) => ({
      buy: result.buy + item.buy,
      sell: result.sell + item.sell,
      net: result.net + item.net,
    }),
    { ...ZERO },
  );
}

export function parseInstitutionTable(rows: unknown[][]): InstitutionMap {
  const normalized = rows.map((row) => ({
    label: String(row[0] ?? "").trim().replaceAll("（", "(").replaceAll("）", ")"),
    value: rowAmount(row),
  }));
  const foreign = normalized.find(({ label }) => label.startsWith("外資及陸資") && label.includes("不含"));
  const trust = normalized.find(({ label }) => label === "投信");
  const dealerTotal = normalized.find(({ label }) => label === "自營商合計");
  const dealerParts = normalized.filter(({ label }) =>
    label === "自營商(自行買賣)" || label === "自營商(避險)");

  if (!foreign || !trust || (!dealerTotal && dealerParts.length !== 2)) {
    throw new Error("三大法人官方資料欄位不完整。");
  }
  return {
    foreign: foreign.value,
    trust: trust.value,
    dealer: dealerTotal?.value ?? add(...dealerParts.map(({ value }) => value)),
  };
}

export function periodEndFromTitle(title: string) {
  const matches = [...title.matchAll(/(\d{3})年(\d{2})月(\d{2})日/g)];
  const match = matches.at(-1);
  if (!match) return null;
  return `${Number(match[1]) + 1911}-${match[2]}-${match[3]}`;
}

function marketFlow(listed: FlowAmount, otc: FlowAmount): MarketFlowAmount {
  return { listed, otc, total: add(listed, otc) };
}

export function combineInstitutionPeriods(
  dayListed: InstitutionMap,
  dayOtc: InstitutionMap,
  monthListed: InstitutionMap,
  monthOtc: InstitutionMap,
  yearListed: InstitutionMap,
  yearOtc: InstitutionMap,
): InstitutionFlowRow[] {
  const labels: Record<InstitutionId, string> = {
    foreign: "外資及陸資",
    trust: "投信",
    dealer: "自營商",
    total: "三大法人合計",
  };
  const keys: InstitutionKey[] = ["foreign", "trust", "dealer"];
  const rows = keys.map((id): InstitutionFlowRow => ({
    id,
    label: labels[id],
    day: marketFlow(dayListed[id], dayOtc[id]),
    month: marketFlow(monthListed[id], monthOtc[id]),
    year: marketFlow(yearListed[id], yearOtc[id]),
  }));
  return [
    ...rows,
    {
      id: "total",
      label: labels.total,
      day: marketFlow(add(...keys.map((key) => dayListed[key])), add(...keys.map((key) => dayOtc[key]))),
      month: marketFlow(add(...keys.map((key) => monthListed[key])), add(...keys.map((key) => monthOtc[key]))),
      year: marketFlow(add(...keys.map((key) => yearListed[key])), add(...keys.map((key) => yearOtc[key]))),
    },
  ];
}

function ymd(date: Date) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Taipei",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date);
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === type)?.value ?? "";
  return `${part("year")}-${part("month")}-${part("day")}`;
}

function compactDate(date: string) {
  return date.replaceAll("-", "");
}

function rocDate(date: string, precision: "day" | "month" = "day") {
  const [year, month, day] = date.split("-");
  return precision === "month"
    ? `${Number(year) - 1911}/${month}`
    : `${Number(year) - 1911}/${month}/${day}`;
}

async function fetchJson<T>(url: string): Promise<T> {
  const response = await fetch(url, {
    cache: "no-store",
    headers: { Accept: "application/json", "User-Agent": "Moneymoney institutional dashboard" },
    signal: AbortSignal.timeout(12_000),
  });
  if (!response.ok) throw new Error(`官方資料服務回應 ${response.status}`);
  return response.json() as Promise<T>;
}

function reasonText(reason: unknown) {
  return reason instanceof Error ? reason.message : String(reason);
}

async function fetchJsonFromFirstAvailable<T>(urls: string[]) {
  const failures: string[] = [];
  for (const url of urls) {
    try {
      return { payload: await fetchJson<T>(url), url };
    } catch (reason) {
      failures.push(`${new URL(url).hostname}: ${reasonText(reason)}`);
    }
  }
  throw new Error(failures.join("; "));
}

async function fetchTaifexCsv(url: string, body: URLSearchParams) {
  const response = await fetch(url, {
    method: "POST",
    body,
    cache: "no-store",
    headers: {
      Accept: "text/csv,text/plain,*/*",
      "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
      "User-Agent": "Moneymoney futures positioning dashboard",
    },
    signal: AbortSignal.timeout(20_000),
  });
  if (!response.ok) throw new Error(`期交所資料服務回應 ${response.status}`);
  return new TextDecoder("big5").decode(await response.arrayBuffer()).replace(/^\uFEFF/, "");
}

async function twseDay(date: string) {
  const path = `/rwd/zh/fund/BFI82U?date=${compactDate(date)}&response=json`;
  const { payload, url } = await fetchJsonFromFirstAvailable<TwsePayload>([
    `https://www.twse.com.tw${path}`,
    `https://wwwc.twse.com.tw${path}`,
  ]);
  if (payload.stat !== "OK" || payload.date !== compactDate(date) || !payload.data?.length) {
    throw new Error("證交所當日資料尚未發布。");
  }
  return { values: parseInstitutionTable(payload.data), url };
}

function tpexRows(payload: TpexPayload) {
  const rows = payload.tables?.[0]?.data;
  if (payload.stat?.toLowerCase() !== "ok" || !rows?.length) {
    throw new Error("櫃買中心資料尚未發布。");
  }
  return rows;
}

async function tpexDay(date: string) {
  const url = `https://www.tpex.org.tw/web/stock/3insti/3insti_summary/3itrdsum_result.php?d=${encodeURIComponent(rocDate(date))}&l=zh-tw&o=json&p=1&t=D`;
  try {
    const payload = await fetchJson<TpexPayload>(url);
    if (payload.date !== compactDate(date)) throw new Error("櫃買中心當日資料尚未發布。");
    return { values: parseInstitutionTable(tpexRows(payload)), url };
  } catch (legacyReason) {
    const openApiUrl = "https://www.tpex.org.tw/openapi/v1/tpex_3insti_summary";
    try {
      const rows = await fetchJson<TpexOpenApiSummaryRow[]>(openApiUrl);
      const rocDateText = rows[0]?.Date ?? "";
      const openApiDate = /^\d{7}$/.test(rocDateText)
        ? `${Number(rocDateText.slice(0, 3)) + 1911}-${rocDateText.slice(3, 5)}-${rocDateText.slice(5, 7)}`
        : null;
      if (openApiDate !== date || !rows.length) throw new Error("櫃買 OpenAPI 尚未發布該日資料。");
      return {
        values: parseInstitutionTable(rows.map((row) => [
          row.Investor,
          row.PurchaseAmount,
          row.SaleAmount,
          row.Net,
        ])),
        url: openApiUrl,
      };
    } catch (openApiReason) {
      throw new Error(
        `櫃買舊版端點：${reasonText(legacyReason)}；櫃買 OpenAPI：${reasonText(openApiReason)}`,
      );
    }
  }
}

async function latestCommonDay() {
  const today = new Date();
  const failures: string[] = [];
  for (let offset = 0; offset < 14; offset += 1) {
    const candidateDate = new Date(today);
    candidateDate.setDate(today.getDate() - offset);
    const date = ymd(candidateDate);
    const [listed, otc] = await Promise.allSettled([twseDay(date), tpexDay(date)]);
    if (listed.status === "fulfilled" && otc.status === "fulfilled") {
      return { date, listed: listed.value, otc: otc.value };
    }
    failures.push(
      `${date} TWSE=${listed.status === "rejected" ? reasonText(listed.reason) : "OK"}`
      + ` TPEx=${otc.status === "rejected" ? reasonText(otc.reason) : "OK"}`,
    );
  }
  throw new Error(`最近交易日的三大法人資料尚無法取得。${failures.slice(0, 3).join(" | ")}`);
}

async function twseMonth(date: string) {
  const monthDate = `${date.slice(0, 7)}-01`;
  const path = `/rwd/zh/fund/BFI82U?type=month&monthDate=${compactDate(monthDate)}&response=json`;
  const { payload, url } = await fetchJsonFromFirstAvailable<TwsePayload>([
    `https://www.twse.com.tw${path}`,
    `https://wwwc.twse.com.tw${path}`,
  ]);
  if (payload.stat !== "OK" || !payload.data?.length) throw new Error("證交所月資料尚未發布。");
  return {
    values: parseInstitutionTable(payload.data),
    endDate: periodEndFromTitle(payload.title ?? ""),
    url,
  };
}

async function tpexPeriod(date: string, period: "month" | "year") {
  const year = Number(date.slice(0, 4));
  const query = period === "month"
    ? `d=${encodeURIComponent(rocDate(date, "month"))}&t=M`
    : `y=${year - 1911}&t=Y`;
  const url = `https://www.tpex.org.tw/web/stock/3insti/3insti_summary/3itrdsum_result.php?${query}&l=zh-tw&o=json&p=1`;
  const payload = await fetchJson<TpexPayload>(url);
  return { values: parseInstitutionTable(tpexRows(payload)), url };
}

function addMaps(...maps: InstitutionMap[]): InstitutionMap {
  return {
    foreign: add(...maps.map((item) => item.foreign)),
    trust: add(...maps.map((item) => item.trust)),
    dealer: add(...maps.map((item) => item.dealer)),
  };
}

function htmlText(value: string) {
  return value
    .replace(/<[^>]+>/g, " ")
    .replaceAll("&nbsp;", " ")
    .replaceAll("&amp;", "&")
    .replaceAll("&#8722;", "-")
    .replace(/\s+/g, " ")
    .trim();
}

function htmlRows(html: string) {
  return [...html.matchAll(/<tr\b[^>]*>([\s\S]*?)<\/tr>/gi)].map((row) =>
    [...row[1].matchAll(/<t[dh]\b[^>]*>([\s\S]*?)<\/t[dh]>/gi)]
      .map((cell) => htmlText(cell[1])));
}

function integer(value: string) {
  const parsed = Number(value.replaceAll(",", "").replace(/[^\d-]/g, ""));
  return Number.isFinite(parsed) ? parsed : 0;
}

export function parseTaifexMarketOpenInterest(html: string, contract: "MTX" | "TMF") {
  const values = htmlRows(html)
    .filter((cells) =>
      cells.length >= 13
      && cells[0] === contract
      && !cells[1].includes("/")
      && /^-?[\d,]+$/.test(cells[12]))
    .map((cells) => integer(cells[12]));
  if (!values.length) throw new Error(`${contract} 全市場未平倉量尚未發布。`);
  return values.reduce((sum, value) => sum + value, 0);
}

export function parseTaifexInstitutionalOpenInterest(html: string, product: string) {
  const rows = htmlRows(html);
  const first = rows.findIndex((cells) => cells.includes(product));
  if (first < 0) throw new Error(`${product} 三大法人未平倉量尚未發布。`);
  const productRows = rows.slice(first, first + 3);
  let long = 0;
  let short = 0;
  for (const [index, cells] of productRows.entries()) {
    const identityIndex = index === 0 ? 2 : 0;
    const longIndex = index === 0 ? 9 : 7;
    const shortIndex = index === 0 ? 11 : 9;
    if (!["自營商", "投信", "外資", "外資及陸資"].includes(cells[identityIndex])) {
      throw new Error(`${product} 法人資料列不完整。`);
    }
    long += integer(cells[longIndex] ?? "");
    short += integer(cells[shortIndex] ?? "");
  }
  if (long <= 0 && short <= 0) throw new Error(`${product} 法人未平倉量尚未揭露。`);
  return { long, short };
}

export function buildRetailFuturesPosition(
  id: "mini" | "micro",
  marketOpenInterest: number,
  institutional: { long: number; short: number },
): RetailFuturesPosition {
  const retailLong = Math.max(0, marketOpenInterest - institutional.long);
  const retailShort = Math.max(0, marketOpenInterest - institutional.short);
  const retailNet = retailLong - retailShort;
  const ratioPct = marketOpenInterest > 0 ? retailNet / marketOpenInterest * 100 : 0;
  return {
    id,
    label: id === "mini" ? "小台散戶多空比" : "微台散戶多空比",
    contract: id === "mini" ? "MTX" : "TMF",
    marketOpenInterest,
    institutionalLong: institutional.long,
    institutionalShort: institutional.short,
    retailLong,
    retailShort,
    retailNet,
    ratioPct,
    bias: ratioPct > 0.05 ? "偏多" : ratioPct < -0.05 ? "偏空" : "中性",
  };
}

function retailHistoryPoint(date: string, items: RetailFuturesPosition[]): RetailFuturesHistoryPoint {
  const mini = items.find(({ id }) => id === "mini")!;
  const micro = items.find(({ id }) => id === "micro")!;
  return {
    date,
    miniRatioPct: mini.ratioPct,
    microRatioPct: micro.ratioPct,
    miniNet: mini.retailNet,
    microNet: micro.retailNet,
  };
}

function csvRows(csv: string) {
  return csv
    .split(/\r?\n/)
    .slice(1)
    .filter((line) => line.trim())
    .map((line) => line.split(",").map((cell) => cell.trim()));
}

export function parseTaifexInstitutionalCsv(csv: string, product: string) {
  const result = new Map<string, { long: number; short: number }>();
  for (const cells of csvRows(csv)) {
    if (cells[1] !== product || !["自營商", "投信", "外資", "外資及陸資"].includes(cells[2])) continue;
    const date = cells[0].replaceAll("/", "-");
    const current = result.get(date) ?? { long: 0, short: 0 };
    current.long += integer(cells[9] ?? "");
    current.short += integer(cells[11] ?? "");
    result.set(date, current);
  }
  return result;
}

export function parseTaifexMarketOpenInterestCsv(csv: string, contract: "MTX" | "TMF") {
  const result = new Map<string, number>();
  for (const cells of csvRows(csv)) {
    if (
      cells[1] !== contract
      || cells[2].includes("/")
      || cells[17] !== "一般"
      || !/^\d+$/.test(cells[11] ?? "")
    ) continue;
    const date = cells[0].replaceAll("/", "-");
    result.set(date, (result.get(date) ?? 0) + integer(cells[11]));
  }
  return result;
}

export function parseTaifexForeignNetCsv(csv: string, product = "臺股期貨") {
  const result = new Map<string, { long: number; short: number; net: number }>();
  for (const cells of csvRows(csv)) {
    if (cells[1] !== product || !["外資", "外資及陸資"].includes(cells[2])) continue;
    const long = integer(cells[9] ?? "");
    const short = integer(cells[11] ?? "");
    result.set(cells[0].replaceAll("/", "-"), { long, short, net: long - short });
  }
  return result;
}

function fiveMonthRange(end: string) {
  const [year, month] = end.split("-").map(Number);
  const start = new Date(Date.UTC(year, month - 5, 1)).toISOString().slice(0, 10);
  return { start, end };
}

function monthlyRanges(start: string, end: string) {
  const ranges: Array<{ start: string; end: string }> = [];
  let cursor = new Date(`${start}T00:00:00Z`);
  const last = new Date(`${end}T00:00:00Z`);
  while (cursor <= last) {
    const monthEnd = new Date(Date.UTC(cursor.getUTCFullYear(), cursor.getUTCMonth() + 1, 0));
    ranges.push({
      start: cursor.toISOString().slice(0, 10),
      end: (monthEnd < last ? monthEnd : last).toISOString().slice(0, 10),
    });
    cursor = new Date(Date.UTC(cursor.getUTCFullYear(), cursor.getUTCMonth() + 1, 1));
  }
  return ranges;
}

async function institutionalCsv(start: string, end: string, commodityId: "TXF" | "MXF" | "TMF") {
  return fetchTaifexCsv(
    "https://www.taifex.com.tw/cht/3/futContractsDateDown",
    new URLSearchParams({
      queryStartDate: start.replaceAll("-", "/"),
      queryEndDate: end.replaceAll("-", "/"),
      commodityId,
    }),
  );
}

async function dailyMarketCsv(start: string, end: string, contract: "MTX" | "TMF") {
  return fetchTaifexCsv(
    "https://www.taifex.com.tw/cht/3/futDataDown",
    new URLSearchParams({
      down_type: "1",
      commodity_id: contract,
      commodity_id2: "",
      queryStartDate: start.replaceAll("-", "/"),
      queryEndDate: end.replaceAll("-", "/"),
    }),
  );
}

async function retailFuturesFromDownloads() {
  const range = fiveMonthRange(ymd(new Date()));
  const months = monthlyRanges(range.start, range.end);
  const [miniInstitutionCsv, microInstitutionCsv, foreignTxCsv, miniMarketCsvs, microMarketCsvs] = await Promise.all([
    institutionalCsv(range.start, range.end, "MXF"),
    institutionalCsv(range.start, range.end, "TMF"),
    institutionalCsv(range.start, range.end, "TXF"),
    Promise.all(months.map(({ start, end }) => dailyMarketCsv(start, end, "MTX"))),
    Promise.all(months.map(({ start, end }) => dailyMarketCsv(start, end, "TMF"))),
  ]);
  const miniInstitution = parseTaifexInstitutionalCsv(miniInstitutionCsv, "小型臺指期貨");
  const microInstitution = parseTaifexInstitutionalCsv(microInstitutionCsv, "微型臺指期貨");
  const miniMarket = parseTaifexMarketOpenInterestCsv(miniMarketCsvs.join("\n"), "MTX");
  const microMarket = parseTaifexMarketOpenInterestCsv(microMarketCsvs.join("\n"), "TMF");
  const foreignNets = parseTaifexForeignNetCsv(foreignTxCsv);
  const dates = [...miniMarket.keys()]
    .filter((date) =>
      microMarket.has(date)
      && miniInstitution.has(date)
      && microInstitution.has(date))
    .sort();
  const points = dates.map((date) => {
    const items = [
      buildRetailFuturesPosition("mini", miniMarket.get(date)!, miniInstitution.get(date)!),
      buildRetailFuturesPosition("micro", microMarket.get(date)!, microInstitution.get(date)!),
    ];
    return { date, items };
  });
  const current = points.at(-1);
  if (!current || points.length < 2) throw new Error("期交所近五個月逐日資料不完整。");
  const foreignDates = [...foreignNets.keys()].sort();
  const foreignAsOfDate = foreignDates.at(-1);
  const foreignPreviousDate = foreignDates.at(-2) ?? null;
  if (!foreignAsOfDate) throw new Error("期交所外資臺股期貨多空資料不完整。");
  const foreignNet = foreignNets.get(foreignAsOfDate)!;
  const previousNet = foreignPreviousDate ? foreignNets.get(foreignPreviousDate)?.net ?? null : null;
  return {
    asOfDate: current.date,
    items: current.items,
    history: points.map(({ date, items }) => retailHistoryPoint(date, items)),
    foreignNet: {
      contract: "TX" as const,
      asOfDate: foreignAsOfDate,
      ...foreignNet,
      previousDate: foreignPreviousDate,
      previousNet,
      change: previousNet == null ? null : foreignNet.net - previousNet,
    },
  };
}

async function retailFuturesFromOpenApi() {
  const [daily, institutional] = await Promise.all([
    fetchJson<TaifexDailyRow[]>("https://openapi.taifex.com.tw/v1/DailyMarketReportFut"),
    fetchJson<TaifexInstitutionRow[]>("https://openapi.taifex.com.tw/v1/MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate"),
  ]);
  const date = daily[0]?.Date;
  if (!date || date !== institutional[0]?.Date) throw new Error("期交所兩份資料日期不一致。");
  const make = (id: "mini" | "micro") => {
    const contract = id === "mini" ? "MTX" : "TMF";
    const product = id === "mini" ? "小型臺指期貨" : "微型臺指期貨";
    const marketOpenInterest = daily
      .filter((row) =>
        row.Date === date
        && row.Contract === contract
        && row.TradingSession === "一般"
        && !row["ContractMonth(Week)"].includes("/"))
      .reduce((sum, row) => sum + integer(row.OpenInterest), 0);
    const institutions = institutional.filter((row) => row.Date === date && row.ContractCode === product);
    return buildRetailFuturesPosition(id, marketOpenInterest, {
      long: institutions.reduce((sum, row) => sum + integer(row["OpenInterest(Long)"]), 0),
      short: institutions.reduce((sum, row) => sum + integer(row["OpenInterest(Short)"]), 0),
    });
  };
  const items = [make("mini"), make("micro")];
  const asOfDate = `${date.slice(0, 4)}-${date.slice(4, 6)}-${date.slice(6, 8)}`;
  const txForeign = institutional.find((row) =>
    row.Date === date && row.ContractCode === "臺股期貨" && ["外資", "外資及陸資"].includes(row.Item));
  return {
    asOfDate,
    items,
    history: [retailHistoryPoint(asOfDate, items)],
    foreignNet: {
      contract: "TX" as const,
      asOfDate,
      long: integer(txForeign?.["OpenInterest(Long)"] ?? "0"),
      short: integer(txForeign?.["OpenInterest(Short)"] ?? "0"),
      net:
        integer(txForeign?.["OpenInterest(Long)"] ?? "0")
        - integer(txForeign?.["OpenInterest(Short)"] ?? "0"),
      previousDate: null,
      previousNet: null,
      change: null,
    },
  };
}

async function retailFutures() {
  try {
    return await retailFuturesFromDownloads();
  } catch (reason) {
    console.warn("taifex downloads unavailable, using open data", reason);
    return retailFuturesFromOpenApi();
  }
}

async function createInstitutionalInvestorResponse(): Promise<InstitutionalInvestorResponse> {
  const [day, retail] = await Promise.all([latestCommonDay(), retailFutures()]);
  const [listedMonthRaw, otcMonth, otcYear] = await Promise.all([
    twseMonth(day.date),
    tpexPeriod(day.date, "month"),
    tpexPeriod(day.date, "year"),
  ]);
  const listedMonth = listedMonthRaw.endDate && listedMonthRaw.endDate < day.date
    ? addMaps(listedMonthRaw.values, day.listed.values)
    : listedMonthRaw.values;

  const [year, month] = day.date.split("-").map(Number);
  const previousMonths = await Promise.all(
    Array.from({ length: month - 1 }, (_, index) =>
      twseMonth(`${year}-${String(index + 1).padStart(2, "0")}-01`).then(({ values }) => values)),
  );
  const listedYear = addMaps(...previousMonths, listedMonth);

  return {
    asOfDate: day.date,
    monthLabel: `${year} 年 ${month} 月`,
    yearLabel: `${year} 年`,
    updatedAt: new Date().toISOString(),
    items: combineInstitutionPeriods(
      day.listed.values,
      day.otc.values,
      listedMonth,
      otcMonth.values,
      listedYear,
      otcYear.values,
    ),
    retailFutures: {
      ...retail,
      formula: "散戶多空比＝（推算散戶多單－推算散戶空單）÷ 全市場未平倉量 × 100%",
      notice: "散戶部位為全市場未平倉量扣除三大法人未平倉量的推算值，屬盤後籌碼指標，並非即時行情。",
      sourceUrl: "https://www.taifex.com.tw/cht/3/futContractsDate",
    },
    dataNotice: "金額為上市與上櫃市場合計；正數代表買超、負數代表賣超。資料依官方最近共同交易日統計。",
    sources: [
      { market: "上市", provider: "臺灣證券交易所", url: "https://www.twse.com.tw/fund/BFI82U?response=html" },
      { market: "上櫃", provider: "證券櫃檯買賣中心", url: "https://www.tpex.org.tw/zh-tw/mainboard/trading/major-institutional/summary/day.html" },
    ],
  };
}

function refreshInstitutionalInvestorResponse() {
  if (pending) return pending;
  pending = createInstitutionalInvestorResponse()
    .then(async (value) => {
      cached = { expiresAt: Date.now() + CACHE_MS, value };
      nextRefreshAt = 0;
      await savePersistentCache(cached);
      return value;
    })
    .catch((reason) => {
      nextRefreshAt = Date.now() + REFRESH_RETRY_MS;
      throw reason;
    })
    .finally(() => {
      pending = null;
    });
  return pending;
}

export async function getInstitutionalInvestorResponse() {
  await loadPersistentCache();
  const now = Date.now();
  if (cached?.expiresAt && cached.expiresAt > now) return cached.value;
  if (cached) {
    if (now < nextRefreshAt) return staleResponse(cached.value);
    try {
      return await refreshInstitutionalInvestorResponse();
    } catch (reason) {
      console.warn("institutional-investors refresh failed", reason);
      return staleResponse(cached.value);
    }
  }
  return refreshInstitutionalInvestorResponse();
}
