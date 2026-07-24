import type { MarketDirectionProvider } from "@/lib/market-types";
import { getTaifexSessionState, type TaifexQuoteFeed } from "@/lib/taifex-session";
import { MockMarketDirectionProvider } from "./mock-provider";

type IndexResult = Awaited<ReturnType<MarketDirectionProvider["getMarketIndex"]>>;
type FuturesResult = Awaited<ReturnType<MarketDirectionProvider["getIndexFutures"]>>;

let indexCache: { value: IndexResult; expiresAt: number } | null = null;
let futuresCache: { value: FuturesResult; expiresAt: number } | null = null;

function parseNumber(value: unknown): number | null {
  const normalized = String(value ?? "").replaceAll(",", "").replace(/[▲▼%]/g, "").trim();
  const parsed = Number(normalized);
  return Number.isFinite(parsed) ? parsed : null;
}

function taipeiParts() {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Taipei", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  }).formatToParts(new Date());
  const get = (type: string) => parts.find((part) => part.type === type)?.value ?? "";
  return { date: `${get("year")}/${get("month")}/${get("day")}`, hour: Number(get("hour")), minute: Number(get("minute")) };
}

interface TaifexRealtimeResponse {
  quote?: number | string;
  change?: string;
  refer?: number | string;
  futureList?: string | [string, number][];
  isNoData?: boolean;
}

function frontMonthContract(now = new Date()) {
  const dateParts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Taipei", year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(now);
  const get = (type: Intl.DateTimeFormatPartTypes) =>
    Number(dateParts.find((part) => part.type === type)?.value ?? 0);
  let year = get("year");
  let month = get("month");
  const day = get("day");
  const firstDay = new Date(Date.UTC(year, month - 1, 1)).getUTCDay();
  const thirdWednesday = 1 + ((3 - firstDay + 7) % 7) + 14;
  if (day > thirdWednesday) {
    month += 1;
    if (month === 13) { month = 1; year += 1; }
  }
  return `${year}${String(month).padStart(2, "0")}`;
}

function parseRealtimePoints(raw: TaifexRealtimeResponse["futureList"], feed: TaifexQuoteFeed) {
  let rows: unknown = raw;
  if (typeof raw === "string") {
    try { rows = JSON.parse(raw); } catch { rows = []; }
  }
  if (!Array.isArray(rows)) return [];
  return rows.flatMap((row) => {
    if (!Array.isArray(row) || row.length < 2) return [];
    const stamp = String(row[0]);
    const price = parseNumber(row[1]);
    if (price == null) return [];
    const coordinate = feed === "night"
      ? Date.UTC(
          Number(stamp.slice(0, 4)), Number(stamp.slice(4, 6)) - 1, Number(stamp.slice(6, 8)),
          Number(stamp.slice(8, 10)), Number(stamp.slice(10, 12)),
        ) / 60_000
      : Number(stamp.slice(0, 2)) * 60 + Number(stamp.slice(2, 4));
    return Number.isFinite(coordinate) ? [{ stamp, price, coordinate }] : [];
  }).sort((a, b) => a.coordinate - b.coordinate);
}

function pointChange(points: ReturnType<typeof parseRealtimePoints>, minutes: number) {
  const latest = points.at(-1);
  if (!latest) return undefined;
  const target = latest.coordinate - minutes;
  const previous = [...points].reverse().find((point) => point.coordinate <= target);
  return previous ? latest.price - previous.price : undefined;
}

function realtimeQuoteAt(points: ReturnType<typeof parseRealtimePoints>, feed: TaifexQuoteFeed) {
  const stamp = points.at(-1)?.stamp;
  if (!stamp) return "官方取樣時間未提供";
  if (feed === "night" && /^\d{12}$/.test(stamp)) {
    return stamp.replace(/^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})$/, "$1-$2-$3 $4:$5");
  }
  const taipei = taipeiParts();
  return `${taipei.date.replaceAll("/", "-")} ${stamp.slice(0, 2)}:${stamp.slice(2, 4)}`;
}

async function fetchTaifexRealtime(): Promise<FuturesResult> {
  const state = getTaifexSessionState();
  const feed = state.preferredFeed;
  const action = feed === "night" ? "futureQuoteRealTimeNight" : "futureQuoteRealTime";
  const response = await fetch(`https://www.taifex.com.tw/eventTaifexTradingCenter/api/index/${action}`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      Referer: "https://www.taifex.com.tw/eventTaifexTradingCenter/cht/index.do",
      "User-Agent": "Mozilla/5.0 Moneymoney-TWSE-Dashboard",
    },
    signal: AbortSignal.timeout(8_000),
    cache: "no-store",
  });
  if (!response.ok) throw new Error("TAIFEX 即時行情服務回應失敗");
  const payload = await response.json() as TaifexRealtimeResponse;
  const price = parseNumber(payload.quote);
  const reference = parseNumber(payload.refer);
  if (price == null || reference == null) throw new Error("TAIFEX 即時行情資料不完整");
  const points = parseRealtimePoints(payload.futureList, feed);
  const change = price - reference;
  return {
    price,
    change,
    changePercent: reference ? change / reference * 100 : 0,
    contract: frontMonthContract(),
    session: state.open && !payload.isNoData
      ? feed === "night" ? "夜盤交易中" : "日盤交易中"
      : feed === "night" ? "夜盤已收盤・最近有效行情" : "日盤已收盤・最近有效行情",
    sessionOpen: state.open && !payload.isNoData,
    source: "TAIFEX 官方行情（30 秒取樣）",
    quoteAt: realtimeQuoteAt(points, feed),
    isOfficial: true,
    change1m: pointChange(points, 1),
    change3m: pointChange(points, 3),
    change10m: pointChange(points, 10),
  };
}

async function fetchTwseIndex(): Promise<IndexResult> {
  const response = await fetch("https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_t00.tw&json=1&delay=0", {
    headers: {
      Accept: "application/json",
      Referer: "https://mis.twse.com.tw/stock/fibest.jsp?stock=t00",
      "User-Agent": "Mozilla/5.0 Moneymoney-TWSE-Dashboard",
    },
    signal: AbortSignal.timeout(6_000),
    cache: "no-store",
  });
  if (!response.ok) throw new Error("TWSE MIS 指數服務回應失敗");
  const row = (await response.json()).msgArray?.[0];
  const previous = parseNumber(row?.y);
  const current = parseNumber(row?.z) ?? previous;
  if (current == null || previous == null) throw new Error("TWSE MIS 指數資料不完整");
  const change = current - previous;
  const date = String(row.d ?? "").replace(/^(\d{4})(\d{2})(\d{2})$/, "$1-$2-$3");
  return {
    price: current, change, changePercent: previous ? change / previous * 100 : 0,
    source: "TWSE MIS", quoteAt: `${date} ${row.t || "13:30:00"}`, isOfficial: true,
  };
}

function cleanCell(html: string) {
  return html.replace(/<[^>]+>/g, "").replace(/&nbsp;/g, " ").replace(/\s+/g, " ").trim();
}

function parseTaifexRows(html: string) {
  return [...html.matchAll(/<tr\b[^>]*>([\s\S]*?)<\/tr>/gi)].map((match) =>
    [...match[1].matchAll(/<td\b[^>]*>([\s\S]*?)<\/td>/gi)].map((cell) => cleanCell(cell[1])),
  );
}

async function fetchTaifexDaily(): Promise<FuturesResult> {
  const taipei = taipeiParts();
  const minutes = taipei.hour * 60 + taipei.minute;
  const marketCode = minutes < 525 ? "1" : "0";
  const session = marketCode === "1" ? "盤後交易最近資料" : "一般交易時段";
  const endpoint = `https://www.taifex.com.tw/cht/3/futDailyMarketExcel?queryDate=${encodeURIComponent(taipei.date)}&commodity_id=TX&marketCode=${marketCode}`;
  const response = await fetch(endpoint, {
    headers: { Accept: "text/html", "User-Agent": "Mozilla/5.0 Moneymoney-TWSE-Dashboard" },
    signal: AbortSignal.timeout(8_000),
    cache: "no-store",
  });
  if (!response.ok) throw new Error("TAIFEX 日行情服務回應失敗");
  const rows = parseTaifexRows(await response.text());
  const row = rows.find((cells) => cells[0] === "TX" && /^\d{6}$/.test(cells[1] ?? ""));
  if (!row) throw new Error("找不到臺股期貨近月契約");
  const price = parseNumber(row[5]);
  const change = parseNumber(row[6]);
  const changePercent = parseNumber(row[7]);
  if (price == null || change == null || changePercent == null) throw new Error("TAIFEX 近月行情資料不完整");
  return {
    price, change, changePercent, contract: row[1], session,
    sessionOpen: false,
    source: "TAIFEX 官方日行情", quoteAt: `${taipei.date.replaceAll("/", "-")} ${session}`,
    isOfficial: true,
  };
}

async function fetchTaifexOpenApi(): Promise<FuturesResult> {
  const response = await fetch("https://openapi.taifex.com.tw/v1/DailyMarketReportFut", {
    signal: AbortSignal.timeout(8_000), next: { revalidate: 60 },
  });
  if (!response.ok) throw new Error("TAIFEX OpenAPI 回應失敗");
  const rows = await response.json() as Record<string, string>[];
  const latestDate = rows.reduce((latest, row) => row.Contract === "TX" && row.Date > latest ? row.Date : latest, "");
  const contracts = rows.filter((row) => row.Contract === "TX" && row.Date === latestDate && /^\d{6}$/.test(row["ContractMonth(Week)"] ?? ""));
  const regular = contracts.find((row) => row.TradingSession.includes("一般")) ?? contracts[0];
  if (!regular) throw new Error("TAIFEX OpenAPI 無近月行情");
  const price = parseNumber(regular.Last);
  const change = parseNumber(regular.Change);
  const changePercent = parseNumber(regular["%"]);
  if (price == null || change == null || changePercent == null) throw new Error("TAIFEX OpenAPI 行情不完整");
  const date = latestDate.replace(/^(\d{4})(\d{2})(\d{2})$/, "$1-$2-$3");
  return {
    price, change, changePercent, contract: regular["ContractMonth(Week)"],
    session: "最近完成交易日", sessionOpen: false, source: "TAIFEX OpenAPI", quoteAt: `${date} 最近完成交易日`,
    isOfficial: true,
  };
}

export class OfficialMarketDirectionProvider implements MarketDirectionProvider {
  private fallback = new MockMarketDirectionProvider();

  async getMarketIndex() {
    if (indexCache && indexCache.expiresAt > Date.now()) return indexCache.value;
    try {
      const value = await fetchTwseIndex();
      indexCache = { value, expiresAt: Date.now() + 8_000 };
      return value;
    } catch {
      return { ...(await this.fallback.getMarketIndex()), source: "Mock Provider", quoteAt: "展示資料", isOfficial: false };
    }
  }

  async getIndexFutures() {
    if (futuresCache && futuresCache.expiresAt > Date.now()) return futuresCache.value;
    try {
      const value = await fetchTaifexRealtime()
        .catch(() => fetchTaifexDaily())
        .catch(() => fetchTaifexOpenApi());
      const liveCacheSeconds = Math.max(30, Number(process.env.FUTURES_REFRESH_SECONDS ?? 30));
      futuresCache = { value, expiresAt: Date.now() + (value.sessionOpen ? liveCacheSeconds * 1000 - 1_000 : 60_000) };
      return value;
    } catch {
      return { ...(await this.fallback.getIndexFutures()), contract: "展示", session: "展示資料", sessionOpen: false, source: "Mock Provider", quoteAt: "展示資料", isOfficial: false };
    }
  }

  getTradeTicks() { return this.fallback.getTradeTicks(); }
  getMarketBreadth() { return this.fallback.getMarketBreadth(); }
  getOrderStatistics() { return this.fallback.getOrderStatistics(); }
}

export const officialMarketDirectionProvider = new OfficialMarketDirectionProvider();
