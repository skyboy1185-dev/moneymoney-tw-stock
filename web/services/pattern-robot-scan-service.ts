import type { Market, StockMeta } from "@/lib/types";
import { backendJson } from "@/services/backend-client";
import { getOfficialRecentHistory } from "@/services/market-data/official-history-provider";
import { withScanLoadLock } from "@/services/scan-load-coordinator";
import { THEME_STOCKS } from "@/services/theme-stock-universe";

type Row = Record<string, unknown>;
type Company = { symbol: string; name: string; market: Market; sector: string; listingDate: string | null };
type Candle = { date: string; open: number; high: number; low: number; close: number; volume: number; turnover: number };
type PatternUniversePayload = {
  scope: "AI_CORE_AND_EXTENDED";
  count: number;
  items: Array<{ stockCode: string; stockName: string; market: string; industry: string; themes: string[] }>;
};
type YahooPayload = { chart?: { result?: Array<{ timestamp?: number[]; indicators?: {
  quote?: Array<{ open?: (number|null)[]; high?: (number|null)[]; low?: (number|null)[]; close?: (number|null)[]; volume?: (number|null)[] }>;
  adjclose?: Array<{ adjclose?: (number|null)[] }>;
} }> } };

const CACHE_MS = 5 * 60_000;
let universeCache: { expiresAt: number; value: Awaited<ReturnType<typeof loadOfficialUniverse>> } | null = null;
let aiUniverseCache: { expiresAt: number; value: PatternUniversePayload } | null = null;
const scanCache = new Map<string, { expiresAt: number; value: unknown }>();
const scanInFlight = new Map<string, Promise<unknown>>();
let activeHistory = 0;
const queue: Array<() => void> = [];
const HISTORY_CONCURRENCY = 4;

function stringValue(row: Row, keys: string[]) {
  for (const key of keys) {
    const value = row[key];
    if (value != null && String(value).trim()) return String(value).trim();
  }
  return "";
}

function numberValue(value: unknown) {
  const parsed = Number(String(value ?? "").replaceAll(",", "").replaceAll("+", "").trim());
  return Number.isFinite(parsed) ? parsed : 0;
}

function rocDate(value: string): string | null {
  const normalized = value.replaceAll("/", "").replaceAll("-", "");
  if (/^\d{8}$/.test(normalized)) return `${normalized.slice(0,4)}-${normalized.slice(4,6)}-${normalized.slice(6,8)}`;
  if (/^\d{7}$/.test(normalized)) return `${Number(normalized.slice(0,3))+1911}-${normalized.slice(3,5)}-${normalized.slice(5,7)}`;
  return null;
}

async function json(url: string, timeout = 15_000): Promise<unknown> {
  const response = await fetch(url, {
    headers: { Accept: "application/json", "User-Agent": "Mozilla/5.0 Moneymoney-Pattern-Robot" },
    signal: AbortSignal.timeout(timeout), cache: "no-store",
  });
  if (!response.ok) throw new Error(`${url} ${response.status}`);
  return response.json();
}

async function loadOfficialUniverse() {
  const [listedCompanies, otcCompanies, listedQuotes, otcQuotes, fullListed, fullOtc, disposedListed, disposedOtc] = await Promise.all([
    json("https://openapi.twse.com.tw/v1/opendata/t187ap03_L").catch(() => []),
    json("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O").catch(() => []),
    json("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL").catch(() => []),
    json("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes").catch(() => []),
    json("https://openapi.twse.com.tw/v1/exchangeReport/TWT85U").catch(() => []),
    json("https://www.tpex.org.tw/openapi/v1/tpex_cmode").catch(() => []),
    json("https://openapi.twse.com.tw/v1/announcement/punish").catch(() => []),
    json("https://www.tpex.org.tw/openapi/v1/tpex_disposal_information").catch(() => []),
  ]) as Row[][];
  const companies: Company[] = [
    ...listedCompanies.map((row) => ({
      symbol: stringValue(row, ["公司代號", "Code"]), name: stringValue(row, ["公司簡稱", "公司名稱", "Name"]),
      market: "上市" as Market, sector: stringValue(row, ["產業別", "IndustryCode"]) || "其他",
      listingDate: rocDate(stringValue(row, ["上市日期", "DateOfListing"])),
    })),
    ...otcCompanies.map((row) => ({
      symbol: stringValue(row, ["SecuritiesCompanyCode", "公司代號"]),
      name: stringValue(row, ["CompanyAbbreviation", "公司簡稱", "CompanyName"]), market: "上櫃" as Market,
      sector: stringValue(row, ["SecuritiesIndustryCode", "產業別"]) || "其他",
      listingDate: rocDate(stringValue(row, ["DateOfListing", "上櫃日期"])),
    })),
  ].filter((item) => /^\d{4}$/.test(item.symbol) && item.name);
  if (companies.length < 500) throw new Error("上市櫃普通股公司清單不完整，取消本次掃描");
  const quoteMap = new Map<string, { price:number; open:number; high:number; low:number; volume:number; turnover:number; date:string; source:string }>();
  for (const row of listedQuotes) {
    const symbol = stringValue(row, ["Code", "證券代號"]);
    quoteMap.set(symbol, { price:numberValue(row.ClosingPrice), open:numberValue(row.OpeningPrice), high:numberValue(row.HighestPrice), low:numberValue(row.LowestPrice), volume:numberValue(row.TradeVolume), turnover:numberValue(row.TradeValue), date:rocDate(stringValue(row,["Date"])) ?? "", source:"TWSE OpenAPI" });
  }
  for (const row of otcQuotes) {
    const symbol = stringValue(row, ["SecuritiesCompanyCode", "Code"]);
    quoteMap.set(symbol, { price:numberValue(row.Close), open:numberValue(row.Open), high:numberValue(row.High), low:numberValue(row.Low), volume:numberValue(row.TradingShares), turnover:numberValue(row.TransactionAmount), date:rocDate(stringValue(row,["Date"])) ?? "", source:"TPEx OpenAPI" });
  }
  const codes = (rows: Row[]) => new Set(rows.flatMap((row) => Object.values(row).map(String).filter((value) => /^\d{4}$/.test(value))));
  const unavailable = [
    !listedCompanies.length && "TWSE company list", !otcCompanies.length && "TPEx company list",
    !listedQuotes.length && "TWSE quotes", !otcQuotes.length && "TPEx quotes",
  ].filter((value): value is string => Boolean(value));
  return { companies, quoteMap, unavailable, fullDelivery: new Set([...codes(fullListed), ...codes(fullOtc)]), disposed: new Set([...codes(disposedListed), ...codes(disposedOtc)]) };
}

async function officialUniverse() {
  if (universeCache && universeCache.expiresAt > Date.now()) return universeCache.value;
  const value = await loadOfficialUniverse();
  universeCache = { value, expiresAt: Date.now() + CACHE_MS };
  return value;
}

async function aiPatternUniverse() {
  if (aiUniverseCache && aiUniverseCache.expiresAt > Date.now()) return aiUniverseCache.value;
  let value: PatternUniversePayload;
  try {
    value = await backendJson<PatternUniversePayload>("/pattern-robot/universe", undefined, 10_000);
    if (value.scope !== "AI_CORE_AND_EXTENDED" || !value.items.length) throw new Error("AI universe is empty");
  } catch {
    // A backend outage must never widen the scan to the full market.
    value = {
      scope: "AI_CORE_AND_EXTENDED",
      count: Object.keys(THEME_STOCKS).length,
      items: Object.keys(THEME_STOCKS).map((stockCode) => ({
        stockCode, stockName: "", market: "", industry: "",
        themes: [...THEME_STOCKS[stockCode as keyof typeof THEME_STOCKS]],
      })),
    };
  }
  aiUniverseCache = { value, expiresAt: Date.now() + CACHE_MS };
  return value;
}

function taipeiDate(timestamp: number) {
  return new Intl.DateTimeFormat("en-CA", { timeZone:"Asia/Taipei", year:"numeric", month:"2-digit", day:"2-digit" }).format(new Date(timestamp * 1000));
}

async function withConcurrency<T>(task: () => Promise<T>): Promise<T> {
  if (activeHistory >= HISTORY_CONCURRENCY) await new Promise<void>((resolve) => queue.push(resolve));
  activeHistory += 1;
  try { return await task(); }
  finally { activeHistory -= 1; queue.shift()?.(); }
}

async function patternHistory(company: Company) {
  return withConcurrency(async () => {
    const suffix = company.market === "上市" ? "TW" : "TWO";
    try {
      const payload = await json(`https://query1.finance.yahoo.com/v8/finance/chart/${company.symbol}.${suffix}?interval=1d&range=2y&events=div%2Csplits`, 20_000) as YahooPayload;
      const result = payload.chart?.result?.[0];
      const timestamps = result?.timestamp ?? [];
      const quote = result?.indicators?.quote?.[0];
      const adjustedClose = result?.indicators?.adjclose?.[0]?.adjclose ?? [];
      if (!quote || timestamps.length < 180) throw new Error("Yahoo history incomplete");
      const rows = timestamps.map((timestamp, index) => {
        const open=quote.open?.[index], high=quote.high?.[index], low=quote.low?.[index], close=quote.close?.[index], volume=quote.volume?.[index];
        if (![open,high,low,close,volume].every((value) => typeof value === "number" && Number.isFinite(value)) || !close || close <= 0) return null;
        const actual = { date:taipeiDate(timestamp), open:open!, high:high!, low:low!, close, volume:volume!, turnover:close*volume! };
        const factor = adjustedClose[index] && adjustedClose[index]! > 0 ? adjustedClose[index]! / close : 1;
        return { actual, adjusted:{ ...actual, open:open!*factor, high:high!*factor, low:low!*factor, close:close*factor } };
      }).filter((item): item is NonNullable<typeof item> => Boolean(item));
      if (rows.length < 180) throw new Error("有效日K不足180筆");
      return { actual:rows.map((row)=>row.actual), adjusted:rows.map((row)=>row.adjusted), source:"Yahoo Finance adjusted-close corporate-action factors" };
    } catch {
      const meta = { symbol:company.symbol, name:company.name, market:company.market, industry:company.sector, peRatio:null, dividendYield:null, priceToBook:null, eps:null, marketCap:null } satisfies StockMeta;
      const rows = await getOfficialRecentHistory(meta, false);
      if (rows.length < 180) throw new Error("官方日K不足180筆");
      const actual = rows.map((row) => ({ ...row, turnover:row.close*row.volume }));
      // Taiwan daily limits are ±10%; a >15% overnight discontinuity can safely
      // be treated as a corporate action and backward-adjusted without turning a
      // normal limit move into an adjustment.
      const factors = Array(actual.length).fill(1);
      let cumulative = 1;
      for (let index=actual.length-1; index>0; index-=1) {
        const ratio=actual[index].open/actual[index-1].close;
        if (ratio<.85 || ratio>1.15) cumulative*=ratio;
        factors[index-1]=cumulative;
      }
      const adjusted=actual.map((row,index)=>({ ...row, open:row.open*factors[index], high:row.high*factors[index], low:row.low*factors[index], close:row.close*factors[index] }));
      return { actual, adjusted, source:"TWSE/TPEx/FinMind history; discontinuity-adjusted fallback" };
    }
  });
}

async function marketRegime() {
  try {
    const payload=await json("https://query1.finance.yahoo.com/v8/finance/chart/%5ETWII?interval=1d&range=6mo") as YahooPayload;
    const closes=(payload.chart?.result?.[0]?.indicators?.quote?.[0]?.close??[]).filter((value):value is number=>typeof value==="number");
    const latest=closes.at(-1)??0, ma20=closes.slice(-20).reduce((a,b)=>a+b,0)/20, ma60=closes.slice(-60).reduce((a,b)=>a+b,0)/60;
    const ret20=closes.length>20?(latest/(closes.at(-21)??latest)-1)*100:0;
    if(latest>ma20&&ma20>ma60&&ret20>4)return {regime:"strong_bull",score:90};
    if(latest>ma20&&ma20>=ma60)return {regime:"bull",score:75};
    if(latest<ma20&&ma20<ma60&&ret20< -4)return {regime:"strong_bear",score:10};
    if(latest<ma20&&ma20<=ma60)return {regime:"bear",score:25};
    return {regime:"neutral",score:50};
  } catch { return {regime:"neutral",score:50}; }
}

async function buildUncached(page: number, pageSize: number) {
  const [{companies,quoteMap,unavailable,fullDelivery,disposed}, regime, aiUniverse] = await Promise.all([
    officialUniverse(), marketRegime(), aiPatternUniverse(),
  ]);
  const aiCodes = new Set(aiUniverse.items.map((item) => item.stockCode));
  const scopedCompanies = companies.filter((company) => aiCodes.has(company.symbol));
  const now=new Date();
  const taipeiParts=new Intl.DateTimeFormat("en-CA",{timeZone:"Asia/Taipei",year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false}).formatToParts(now);
  const part=(type:string)=>taipeiParts.find((item)=>item.type===type)?.value??"";
  const today=`${part("year")}-${part("month")}-${part("day")}`;
  const currentTime=`${part("hour")}:${part("minute")}:${part("second")}`;
  const closeComplete=currentTime>="13:40:00";
  let validHistoryCount=0;
  const pageCount=Math.max(1,Math.ceil(scopedCompanies.length/pageSize));
  const pageCompanies=scopedCompanies.slice((page-1)*pageSize,page*pageSize);
  const candidates=await Promise.all(pageCompanies.map(async(company)=>{
    const quote=quoteMap.get(company.symbol);
    if(!quote?.price||!quote.volume)return null;
    let history:Awaited<ReturnType<typeof patternHistory>>;
    try{history=await patternHistory(company);validHistoryCount+=1;}catch{return null;}
    const actual=[...history.actual];
    const adjusted=[...history.adjusted];
    const latest=actual.at(-1);
    if(quote.date&&latest&&quote.date>=latest.date){
      const candle={date:quote.date,open:quote.open||quote.price,high:quote.high||quote.price,low:quote.low||quote.price,close:quote.price,volume:quote.volume,turnover:quote.turnover||quote.price*quote.volume};
      actual.splice(actual.findIndex((row)=>row.date===quote.date),actual.some((row)=>row.date===quote.date)?1:0,candle);
      actual.sort((a,b)=>a.date.localeCompare(b.date));
      adjusted.splice(adjusted.findIndex((row)=>row.date===quote.date),adjusted.some((row)=>row.date===quote.date)?1:0,candle);
      adjusted.sort((a,b)=>a.date.localeCompare(b.date));
    }
    const current=actual.at(-1)!;
    const avgTurnover=actual.slice(-20).reduce((sum,row)=>sum+row.turnover,0)/Math.min(20,actual.length);
    const vwap=current.volume?current.turnover/current.volume:null;
    const stock={stock_code:company.symbol,stock_name:company.name,market_type:company.market,sector_name:company.sector,listing_date:company.listingDate,
      is_etf:false,is_etn:false,is_warrant:false,is_disposed:disposed.has(company.symbol),is_full_delivery:fullDelivery.has(company.symbol),
      current_price:current.close,current_volume:current.volume,current_turnover:current.turnover,vwap,
      quote_time:`${quote.date||today}T${currentTime}+08:00`,quote_realtime:quote.date===today&&!closeComplete,quote_source:quote.source,
      close_complete:quote.date<today||closeComplete,adjusted_prices:adjusted.slice(-200),actual_prices:actual.slice(-30),average_turnover_20d:avgTurnover,history_source:history.source};
    const listingDays=stock.listing_date?Math.floor((Date.parse(today)-Date.parse(stock.listing_date))/86_400_000):9999;
    return !stock.is_disposed&&!stock.is_full_delivery&&stock.current_volume>0&&stock.average_turnover_20d>=30_000_000&&listingDays>=168&&stock.adjusted_prices.length>=180?stock:null;
  }));
  const stocks=candidates.filter((item):item is NonNullable<typeof item>=>Boolean(item));
  const latestDate=stocks.map((item)=>item.actual_prices.at(-1)?.date??"").sort().at(-1)
    ?? [...quoteMap.values()].map((quote)=>quote.date).filter(Boolean).sort().at(-1) ?? today;
  const weekday=Number(new Intl.DateTimeFormat("en-US",{timeZone:"Asia/Taipei",weekday:"short"}).format(now)!=="Sun"&&new Intl.DateTimeFormat("en-US",{timeZone:"Asia/Taipei",weekday:"short"}).format(now)!=="Sat");
  return {trade_date:latestDate,generated_at:now.toISOString(),is_trading_day:Boolean(weekday)&&latestDate===today,market_regime:regime.regime,market_score:regime.score,stocks,page,page_count:pageCount,
    sources:["TWSE listed company/open data","TPEx listed company/open data","TWSE/TPEx daily quote","Yahoo Finance adjusted close","TWSE MIS/official close"],
    source_status:{scope:"AI core and extended supply chain",universe:`${scopedCompanies.length}/${companies.length} curated AI-related listed/OTC ordinary shares`,history:`${validHistoryCount} stocks read with >=180 daily bars; ${stocks.length} passed eligibility`,adjustment:"adjusted close factor; official discontinuity fallback",degraded:unavailable.length?unavailable.join(", "):"none"}};
}

export async function buildPatternRobotScan(page = 1, pageSize = 60) {
  const key=`${page}:${pageSize}`;
  const cached=scanCache.get(key);
  if(cached&&cached.expiresAt>Date.now())return cached.value;
  const pending=scanInFlight.get(key);
  if(pending)return pending;
  const request=withScanLoadLock(()=>buildUncached(page,pageSize))
    .then((value)=>{scanCache.set(key,{value,expiresAt:Date.now()+CACHE_MS});return value;})
    .finally(()=>{scanInFlight.delete(key);});
  scanInFlight.set(key,request);
  return request;
}
