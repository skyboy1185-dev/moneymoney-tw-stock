import { backendJson } from "@/services/backend-client";
import { getOfficialRecentHistory } from "@/services/market-data/official-history-provider";
import { getOfficialQuotes, getOfficialSnapshotQuotes } from "@/services/market-data/official-quote-provider";
import { getOfficialStockInstitutionalFlow } from "@/services/market-data/official-stock-institutional-provider";
import { enrichOfficialStockMeta } from "@/services/market-data/official-fundamentals-provider";
import { isExpandedThemeSymbol, themesForSymbol } from "@/services/theme-stock-universe";
import { stockCatalog } from "@/services/stock-service";
import { withScanLoadLock } from "@/services/scan-load-coordinator";
import type { DailyPrice, Market, StockMeta, StockQuote } from "@/lib/types";
import type { LargeHolderHistoryResponse } from "@/lib/large-holder-types";

type Row = Record<string, unknown>;
type Company = { symbol: string; name: string; market: Market; code: string; industry: string; listingDate: string | null; isElectronic: boolean };
type QuoteRow = { symbol: string; price: number; open: number; high: number; low: number; volume: number; turnover: number; date: string; time: string; source: StockQuote["source"]; realtime?: boolean };

const INDUSTRIES: Record<string, string> = {
  "01":"水泥工業", "02":"食品工業", "03":"塑膠工業", "04":"紡織纖維", "05":"電機機械",
  "06":"電器電纜", "08":"玻璃陶瓷", "09":"造紙工業", "10":"鋼鐵工業", "11":"橡膠工業",
  "12":"汽車工業", "14":"建材營造", "15":"航運業", "16":"觀光餐旅", "17":"金融保險",
  "18":"貿易百貨", "20":"其他", "21":"化學工業", "22":"生技醫療", "23":"油電燃氣",
  "24":"半導體", "25":"電腦及週邊設備", "26":"光電", "27":"通信網路", "28":"電子零組件",
  "29":"電子通路", "30":"資訊服務", "31":"其他電子", "32":"文化創意", "33":"農業科技",
  "34":"電子商務", "35":"綠能環保", "36":"數位雲端", "37":"運動休閒", "38":"居家生活",
};
const ELECTRONIC_CODES = new Set(["24", "25", "26", "27", "28", "29", "30", "31"]);
const INDUSTRY_CODES_BY_NAME = new Map(
  Object.entries(INDUSTRIES).map(([code, name]) => [name, code]),
);
type ScanScope = "adaptive" | "rocket";
const scanCaches: Record<ScanScope, { value: unknown; expiresAt: number }> = {
  adaptive: { value: null, expiresAt: 0 }, rocket: { value: null, expiresAt: 0 },
};
const scanInFlight: Record<ScanScope, Promise<unknown> | null> = { adaptive: null, rocket: null };

export function resolveAdaptiveIndustryCode(code: string | null | undefined, industry: string): string {
  return code?.trim() || INDUSTRY_CODES_BY_NAME.get(industry.trim()) || "00";
}

function text(row: Row, keys: string[]) { for (const key of keys) if (row[key] != null && String(row[key]).trim()) return String(row[key]).trim(); return ""; }
function num(value: unknown) { const parsed = Number(String(value ?? "").replaceAll(",", "").replaceAll("+", "").trim()); return Number.isFinite(parsed) ? parsed : 0; }
function rocDate(value: string) { if (/^\d{8}$/.test(value)) return value.replace(/^(\d{4})(\d{2})(\d{2})$/, "$1-$2-$3"); if (/^\d{7}$/.test(value)) return `${Number(value.slice(0,3))+1911}-${value.slice(3,5)}-${value.slice(5,7)}`; return value; }
function pct(values: number[], days: number) { const end=values.at(-1) ?? 0, start=values.at(-(days+1)) ?? 0; return start ? (end/start-1)*100 : 0; }
function avg(values: number[]) { return values.length ? values.reduce((a,b)=>a+b,0)/values.length : 0; }
function sma(values:number[], days:number) { return values.length>=days ? avg(values.slice(-days)) : null; }
function slope(values:number[], days:number) { if(values.length<days+5)return null; const a=avg(values.slice(-days-5,-5)), b=avg(values.slice(-days)); return a ? (b/a-1)*100/5 : null; }
function rsi(values:number[], days=14) { if(values.length<days+1)return null; let up=0,down=0; for(let i=values.length-days;i<values.length;i++){const d=values[i]-values[i-1]; if(d>0)up+=d; else down-=d;} return down===0?100:100-100/(1+up/down); }
function atr(prices:DailyPrice[], days=14) { if(prices.length<days+1)return null; return avg(prices.slice(-days).map((row,i)=>{const prev=prices[prices.length-days+i-1].close; return Math.max(row.high-row.low,Math.abs(row.high-prev),Math.abs(row.low-prev));})); }
function subIndustry(symbol:string, main:string) { const tags=themesForSymbol(symbol); return tags.find((tag)=>["IC設計","PCB","ABF載板","被動元件","記憶體","玻纖布","低軌衛星","廠務工程"].includes(tag)) ?? main; }

function taipeiToday() { return new Intl.DateTimeFormat("en-CA",{timeZone:"Asia/Taipei",year:"numeric",month:"2-digit",day:"2-digit"}).format(new Date()); }
function isTaiwanMarketSessionNow() {
  const parts=new Intl.DateTimeFormat("en-GB",{timeZone:"Asia/Taipei",weekday:"short",hour:"2-digit",minute:"2-digit",hour12:false}).formatToParts(new Date());
  const value=(type:string)=>parts.find((part)=>part.type===type)?.value ?? "";
  const weekday=value("weekday"), hour=Number(value("hour")), minute=Number(value("minute"));
  if(weekday==="Sat"||weekday==="Sun"||!Number.isFinite(hour)||!Number.isFinite(minute))return false;
  const minutes=hour*60+minute;
  return minutes>=9*60&&minutes<=13*60+30;
}

async function json(url:string) {
  let lastError:unknown;
  for(let attempt=1;attempt<=2;attempt+=1){
    try{
      const response=await fetch(url,{headers:{Accept:"application/json"},signal:AbortSignal.timeout(8_000),cache:"no-store"});
      if(!response.ok)throw new Error(`${url} ${response.status}`);
      return await response.json();
    }catch(error){
      lastError=error;
      if(attempt<2)await new Promise((resolve)=>setTimeout(resolve,250*attempt));
    }
  }
  throw lastError instanceof Error?lastError:new Error(`${url} 官方資料讀取失敗`);
}

async function safeRows(url:string):Promise<Row[]>{
  try{return await json(url) as Row[];}
  catch(error){console.warn("adaptive scan source unavailable",url,error);return [];}
}

async function addFallbackUniverse(
  scope: ScanScope,
  companies: Company[],
  quotes: Map<string, QuoteRow>,
) {
  // The official bulk endpoints occasionally reset their connection or return
  // Cloudflare 520. Keep a small, real-stock fallback universe so a temporary
  // upstream outage does not disable every automated strategy.
  const fallbackMetas = stockCatalog.filter((meta) =>
    scope === "rocket" || themesForSymbol(meta.symbol).length > 0,
  );
  const knownCompanies = new Set(companies.map((company) => company.symbol));
  for (const meta of fallbackMetas) {
    if (knownCompanies.has(meta.symbol)) continue;
    const code = resolveAdaptiveIndustryCode(null, meta.industry);
    companies.push({
      symbol: meta.symbol,
      name: meta.name,
      market: meta.market,
      code,
      industry: meta.industry,
      listingDate: null,
      isElectronic: ELECTRONIC_CODES.has(code),
    });
  }
  const fallbackQuotes = await getOfficialQuotes(fallbackMetas);
  for (const [symbol, quote] of fallbackQuotes) {
    if (quotes.has(symbol)) continue;
    quotes.set(symbol, {
      symbol,
      price: quote.price,
      open: quote.open,
      high: quote.high,
      low: quote.low,
      volume: quote.volume,
      turnover: quote.price * quote.volume,
      date: quote.date,
      time: quote.time,
      source: quote.source,
      realtime: quote.isRealtime,
    });
  }
}

async function universe(scope: ScanScope) {
  const [listedCompanies, otcCompanies, listedQuotes, otcQuotes] = await Promise.all([
    safeRows("https://openapi.twse.com.tw/v1/opendata/t187ap03_L"), safeRows("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"),
    safeRows("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"), safeRows("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"),
  ]);
  const companies: Company[] = [
    ...listedCompanies.map((row)=>{const symbol=text(row,["公司代號"]),code=text(row,["產業別"]),isElectronic=ELECTRONIC_CODES.has(code);return {symbol,name:text(row,["公司簡稱"]),market:"上市" as const,code,industry:INDUSTRIES[code]??subIndustry(symbol,"跨產業題材"),listingDate:rocDate(text(row,["上市日期"])),isElectronic};}),
    ...otcCompanies.map((row)=>{const symbol=text(row,["SecuritiesCompanyCode"]),code=text(row,["SecuritiesIndustryCode"]),isElectronic=ELECTRONIC_CODES.has(code);return {symbol,name:text(row,["CompanyAbbreviation"]),market:"上櫃" as const,code,industry:INDUSTRIES[code]??subIndustry(symbol,"跨產業題材"),listingDate:rocDate(text(row,["DateOfListing"])),isElectronic};}),
  ].filter((row)=>/^\d{4,6}$/.test(row.symbol));
  const quotes = new Map<string,QuoteRow>();
  for(const row of listedQuotes){const symbol=text(row,["Code"]); quotes.set(symbol,{symbol,price:num(row.ClosingPrice),open:num(row.OpeningPrice),high:num(row.HighestPrice),low:num(row.LowestPrice),volume:num(row.TradeVolume),turnover:num(row.TradeValue),date:rocDate(text(row,["Date"])),time:"13:30:00",source:"TWSE OpenAPI"});}
  for(const row of otcQuotes){const symbol=text(row,["SecuritiesCompanyCode","Code"]); const price=num(row.Close); const volume=num(row.TradingShares); quotes.set(symbol,{symbol,price,open:num(row.Open),high:num(row.High),low:num(row.Low),volume,turnover:num(row.TransactionAmount)||price*volume,date:rocDate(text(row,["Date"])),time:"13:30:00",source:"TPEx OpenAPI"});}
  if(!companies.length||!quotes.size)await addFallbackUniverse(scope,companies,quotes);
  if(!companies.length||!quotes.size)throw new Error("上市與上櫃電子股／指定題材官方名錄或行情皆無法取得");
  const selected = companies.map((company)=>({company,quote:quotes.get(company.symbol)}))
    .filter((row)=>row.quote?.price)
    .filter((row)=>scope === "adaptive"
      ? row.company.isElectronic || isExpandedThemeSymbol(row.company.symbol)
      : (row.quote?.turnover ?? 0) >= 100_000_000)
    .sort((a,b)=>(b.quote?.turnover??0)-(a.quote?.turnover??0));
  return scope === "rocket" ? selected.slice(0, 600) : selected;
}

async function taiexHistory(): Promise<number[]> {
  const now=new Date(); const months=Array.from({length:4},(_,offset)=>{const d=new Date(Date.UTC(now.getUTCFullYear(),now.getUTCMonth()-offset,1));return `${d.getUTCFullYear()}${String(d.getUTCMonth()+1).padStart(2,"0")}01`;});
  const payloads=await Promise.all(months.map((date)=>json(`https://www.twse.com.tw/rwd/zh/TAIEX/MI_5MINS_HIST?date=${date}&response=json`).catch(()=>({data:[]})))) as {data?:unknown[][]}[];
  return payloads.flatMap((p)=>p.data??[]).map((row)=>num(row[4])).filter(Boolean).slice(-80);
}

async function liveTaiex(): Promise<{ price:number; date:string; time:string }|null> {
  try {
    const query=new URLSearchParams({ex_ch:"tse_t00.tw",json:"1",delay:"0",_:String(Date.now())});
    const response=await fetch(`https://mis.twse.com.tw/stock/api/getStockInfo.jsp?${query}`,{
      headers:{Accept:"application/json",Referer:"https://mis.twse.com.tw/stock/index.jsp"},
      signal:AbortSignal.timeout(8_000),cache:"no-store",
    });
    if(!response.ok)return null;
    const row=(await response.json() as {msgArray?:Row[]}).msgArray?.[0];
    if(!row)return null;
    const price=num(row.z),date=rocDate(text(row,["d"])),time=text(row,["t","ot"]);
    return price>0&&date&&time?{price,date,time}:null;
  }catch{return null;}
}

async function specialCodes() {
  const urls=["https://openapi.twse.com.tw/v1/exchangeReport/TWT85U","https://openapi.twse.com.tw/v1/exchangeReport/TWTAWU","https://openapi.twse.com.tw/v1/announcement/punish","https://www.tpex.org.tw/openapi/v1/tpex_cmode","https://www.tpex.org.tw/openapi/v1/tpex_disposal_information"];
  const payloads=await Promise.all(urls.map((url)=>json(url).catch(()=>[]))) as Row[][];
  const sets=payloads.map((rows)=>new Set(rows.flatMap((row)=>Object.values(row).map(String).filter((v)=>/^\d{4,6}$/.test(v)))));
  return {changed:new Set([...sets[0],...sets[3]]),suspended:new Set([...sets[1],...sets[3]]),disposed:new Set([...sets[2],...sets[4]])};
}

function features(meta:StockMeta, prices:DailyPrice[], quote: QuoteRow, market20:number, electronic20:number) {
  const closes=prices.map((p)=>p.close), volumes=prices.map((p)=>p.volume), latest=prices.at(-1)!;
  const a14=atr(prices), high20=Math.max(...prices.slice(-21,-1).map((p)=>p.high)), low20=Math.min(...prices.slice(-20).map((p)=>p.low));
  const high60=Math.max(...prices.slice(-61,-1).map((p)=>p.high)), amp=low20?(high20/low20-1)*100:999;
  const pos=(latest.close-low20)/Math.max(.01,high20-low20), avgVol=avg(volumes.slice(-20)), avgTurn=avg(prices.slice(-20).map((p)=>p.close*p.volume));
  const upVol=avg(prices.slice(-20).filter((p,i,a)=>i&&p.close>=a[i-1].close).map((p)=>p.volume)), downVol=avg(prices.slice(-20).filter((p,i,a)=>i&&p.close<a[i-1].close).map((p)=>p.volume));
  const ret20=pct(closes,20), ma5=sma(closes,5),ma10=sma(closes,10),ma20=sma(closes,20),ma60=sma(closes,60);
  const range=latest.high-latest.low, upper=latest.high-Math.max(latest.open,latest.close);
  const prevClose=closes.at(-2) ?? latest.open;
  const gapPercent=prevClose ? (latest.open/prevClose-1)*100 : 0;
  let consecutiveStrongUpDays=0, consecutiveLongBullishDays=0;
  for(let i=prices.length-1;i>0;i-=1){const p=prices[i],prior=prices[i-1];if((p.close/prior.close-1)*100>=3)consecutiveStrongUpDays+=1;else break;}
  for(let i=prices.length-1;i>=0;i-=1){const p=prices[i],body=(p.close/p.open-1)*100;if(body>=3)consecutiveLongBullishDays+=1;else break;}
  return { meta, prices, stock:{
    stock_code:meta.symbol,stock_name:meta.name,market_type:meta.market,industry_code:resolveAdaptiveIndustryCode((meta as StockMeta & {industryCode?:string}).industryCode,meta.industry),main_industry:meta.industry,sub_industry:subIndustry(meta.symbol,meta.industry),listing_date:(meta as StockMeta & {listingDate?:string}).listingDate,
    is_electronic:Boolean((meta as StockMeta & {isElectronic?:boolean}).isElectronic),is_full_delivery:false,is_alternate_trading:false,is_disposed:false,is_suspended:false,is_delisted:false,has_recent_trade:latest.volume>0,abnormal_trading:false,data_completeness:prices.length>=60?.82:.5,
    quote_source:quote.source,quote_timestamp:`${quote.date}T${quote.time}+08:00`,price:latest.close,open:latest.open,high:latest.high,low:latest.low,volume_shares:latest.volume,average_volume_20d_shares:avgVol,average_turnover_20d:avgTurn,illiquid_days_5d:prices.slice(-5).filter((p)=>p.volume<1000).length,
    return_1d:pct(closes,1),return_3d:pct(closes,3),return_5d:pct(closes,5),return_20d:ret20,gap_percent:gapPercent,consecutive_strong_up_days:consecutiveStrongUpDays,consecutive_long_bullish_days:consecutiveLongBullishDays,is_highest_volume_20d:latest.volume>=Math.max(...volumes.slice(-20)),market_return_20d:market20,electronic_return_20d:electronic20,relative_strength_market:ret20-market20,relative_strength_electronic:ret20-electronic20,
    ma5,ma10,ma20,ma60,ma5_slope:slope(closes,5),ma20_slope:slope(closes,20),ma60_slope:slope(closes,60),atr14:a14,atr20_ratio:a14? a14/latest.close*100:null,adx14:null,rsi14:rsi(closes),macd_histogram:null,macd_histogram_rising:closes.at(-1)!>closes.at(-2)!,bollinger_width_percentile:null,
    range_low:low20,range_high:high20,range_amplitude:amp,range_position:pos,breakout_20d:latest.close>high20,breakout_60d:latest.close>high60,breakout_percent:(latest.close/high20-1)*100,distance_to_high_percent:Math.max(0,(high20/latest.close-1)*100),volume_ratio_5d:latest.volume/Math.max(1,avg(volumes.slice(-5))),volume_ratio_20d:latest.volume/Math.max(1,avgVol),close_location:range?(latest.close-latest.low)/range:null,upper_shadow_ratio:range?upper/range:null,
    higher_low:Math.min(...prices.slice(-5).map((p)=>p.low))>=Math.min(...prices.slice(-10,-5).map((p)=>p.low)),bottom_reversal_candle:latest.close>latest.open&&(latest.close-latest.low)>range*.45,volume_contracting:avg(volumes.slice(-5))<avgVol,down_volume_less_than_up:downVol<=upVol,
    foreign_net_5d:null as number|null,trust_net_5d:null as number|null,holder_400_change:null as number|null,holder_1000_change:null as number|null,retail_holder_change:null as number|null,margin_change:null as number|null,short_sale_change:null as number|null,revenue_yoy:null as number|null,revenue_3m_yoy:null as number|null,latest_eps:null as number|null,trailing_eps:null as number|null,gross_margin_change:null as number|null,operating_margin_change:null as number|null,fundamental_risk:false,industry_strength_score:0,industry_rank_percentile:1,industry_continuation_days:0,same_industry_strong_count:0,
  }};
}

async function buildAdaptiveElectronicScanUncached(scope: ScanScope) {
  const [rows,indexHistory,special,indexLive]=await Promise.all([universe(scope),taiexHistory(),specialCodes(),liveTaiex()]);
  const histories=await Promise.all(rows.map(async({company,quote})=>{try{const meta={symbol:company.symbol,name:company.name,market:company.market,industry:company.industry,industryCode:company.code,listingDate:company.listingDate,isElectronic:company.isElectronic,peRatio:null,dividendYield:null,priceToBook:null,eps:null,marketCap:null,themes:themesForSymbol(company.symbol)} as StockMeta & {industryCode:string;listingDate:string|null;isElectronic:boolean}; const prices=await getOfficialRecentHistory(meta); return {meta:{...meta,listingDate:meta.listingDate??prices.at(0)?.date??null},quote:quote!,prices};}catch{return null;}}));
  let valid=histories.filter((item):item is NonNullable<typeof item>=>Boolean(item&&item.prices.length>=60));
  const currentTime=new Date().toLocaleTimeString("en-GB",{timeZone:"Asia/Taipei",hour12:false});
  valid=valid.map((item)=>{const latest=item.prices.at(-1);if(!latest||latest.date<=item.quote.date)return item;return {...item,quote:{symbol:item.meta.symbol,price:latest.close,open:latest.open,high:latest.high,low:latest.low,volume:latest.volume,turnover:latest.close*latest.volume,date:latest.date,time:currentTime,source:"Yahoo Finance 準即時",realtime:false}};});
  if(!valid.length)throw new Error("掃描股票皆無法取得足夠的官方歷史行情");
  const applyLiveQuote=(item:(typeof valid)[number],live:StockQuote|undefined)=>{if(!live?.isRealtime)return item;const candle={symbol:item.meta.symbol,name:item.meta.name,date:live.date,open:live.open,high:live.high,low:live.low,close:live.price,volume:live.volume};return {...item,quote:{symbol:item.meta.symbol,price:live.price,open:live.open,high:live.high,low:live.low,volume:live.volume,turnover:live.price*live.volume,date:live.date,time:live.time,source:live.source,realtime:true},prices:[...item.prices.filter((p)=>p.date!==live.date),candle].sort((a,b)=>a.date.localeCompare(b.date))};};
  const liveQuotes=await getOfficialSnapshotQuotes(valid.map((item)=>item.meta));
  valid=valid.map((item)=>applyLiveQuote(item,liveQuotes.get(item.meta.symbol)));
  let hasLiveMarket=valid.some((item)=>item.quote.realtime===true);
  if(!hasLiveMarket&&isTaiwanMarketSessionNow()){
    const priority=valid.slice().sort((a,b)=>(b.quote?.turnover??0)-(a.quote?.turnover??0)).slice(0,260);
    const fallbackQuotes=await getOfficialQuotes(priority.map((item)=>item.meta));
    const prioritySymbols=new Set(priority.map((item)=>item.meta.symbol));
    valid=valid.map((item)=>prioritySymbols.has(item.meta.symbol)?applyLiveQuote(item,fallbackQuotes.get(item.meta.symbol)):item);
    hasLiveMarket=valid.some((item)=>item.quote.realtime===true);
  }
  const freshDate=taipeiToday();
  const freshValid=hasLiveMarket?valid.filter((item)=>item.quote.realtime===true||item.quote.date===freshDate):[];
  if(freshValid.length>=20)valid=freshValid;
  const stockTradeDate=valid.map((item)=>item.quote.date).sort().at(-1);
  const indexCloses=indexLive&&hasLiveMarket&&indexLive.date===stockTradeDate?[...indexHistory,indexLive.price]:indexHistory;
  const electronic20=avg(valid.map((item)=>pct(item.prices.map((p)=>p.close),20))), market20=pct(indexCloses,20);
  const base=valid.map((item)=>features(item.meta,item.prices,item.quote,market20,electronic20));
  const dayReturns=base.map((item)=>item.stock.return_1d), advances=dayReturns.filter((v)=>v>0).length/base.length*100;
  const advanceRatio2d=base.filter((item)=>pct(item.prices.map((p)=>p.close),2)>0).length/base.length*100;
  const higherLowRatio=base.filter((item)=>item.stock.higher_low).length/base.length*100;
  const newLowRatio=base.filter((item)=>item.stock.price<=item.stock.range_low).length/base.length*100;
  const grouped=new Map<string,typeof base>(); for(const item of base){const key=item.stock.sub_industry;grouped.set(key,[...(grouped.get(key)??[]),item]);}
  const industries=[...grouped].map(([name,items])=>({sub_industry:name,return_1d:avg(items.map((x)=>x.stock.return_1d)),return_3d:avg(items.map((x)=>pct(x.prices.map((p)=>p.close),3))),return_5d:avg(items.map((x)=>x.stock.return_5d)),return_20d:avg(items.map((x)=>x.stock.return_20d)),advance_ratio:items.filter((x)=>x.stock.return_1d>0).length/items.length*100,new_high_ratio:items.filter((x)=>x.stock.breakout_20d).length/items.length*100,volume_growth:avg(items.map((x)=>(x.stock.volume_ratio_20d-1)*100)),foreign_net_buy:null,investment_trust_net_buy:null,large_holder_change:null,relative_taiex:avg(items.map((x)=>x.stock.relative_strength_market)),relative_electronic:avg(items.map((x)=>x.stock.relative_strength_electronic)),continuation_days:0}));
  const sectorContinuation=industries.some((item)=>item.return_1d>0&&item.return_3d>0&&item.return_5d>0)?3:industries.some((item)=>item.return_1d>0&&item.return_3d>0)?2:industries.some((item)=>item.return_1d>0)?1:0;
  const ranked=[...industries].sort((a,b)=>(b.return_5d??0)-(a.return_5d??0)); for(const item of base){item.stock.industry_rank_percentile=(ranked.findIndex((x)=>x.sub_industry===item.stock.sub_industry)+1)/Math.max(1,ranked.length);item.stock.same_industry_strong_count=grouped.get(item.stock.sub_industry)?.filter((x)=>x.stock.return_1d>0).length??0; item.stock.is_alternate_trading=special.changed.has(item.meta.symbol);item.stock.is_suspended=special.suspended.has(item.meta.symbol);item.stock.is_disposed=special.disposed.has(item.meta.symbol);}
  const enrich=base.sort((a,b)=>b.stock.average_turnover_20d-a.stock.average_turnover_20d).slice(0,25);
  await Promise.all(enrich.map(async(item)=>{try{const flow=await getOfficialStockInstitutionalFlow(item.meta);const last=flow.items.slice(-5);item.stock.foreign_net_5d=last.reduce((s,x)=>s+x.foreign,0);item.stock.trust_net_5d=last.reduce((s,x)=>s+x.trust,0);}catch{} try{const f=await enrichOfficialStockMeta(item.meta,item.stock.price);item.stock.trailing_eps=f.eps;}catch{} try{const h=await backendJson<LargeHolderHistoryResponse>(`/large-holders/stocks/${item.meta.symbol}/history?weeks=2`);if(h.dataMode==="official_tdcc"&&h.items.length>=2){item.stock.holder_400_change=h.items.at(-1)!.ratioOver400-h.items.at(-2)!.ratioOver400;item.stock.holder_1000_change=h.items.at(-1)!.ratioOver1000-h.items.at(-2)!.ratioOver1000;}}catch{}}));
  const closes=indexCloses, latest=closes.at(-1)??null,ma5=sma(closes,5),ma20=sma(closes,20),ma60=sma(closes,60); const updated=new Date().toISOString(), tradeDate=stockTradeDate??updated.slice(0,10);
  const payload={market:{trade_date:tradeDate,updated_at:updated,market_open:hasLiveMarket,official_data:true,taiex_close:latest,otc_close:null,electronic_close:null,semiconductor_close:null,taiex_return_1d:pct(closes,1),otc_return_1d:null,electronic_return_1d:avg(dayReturns),taiex_return_5d:pct(closes,5),taiex_return_10d:pct(closes,10),taiex_return_20d:market20,taiex_return_60d:pct(closes,60),electronic_return_20d:electronic20,taiex_above_ma5:latest&&ma5?latest>ma5:null,taiex_above_ma20:latest&&ma20?latest>ma20:null,taiex_above_ma60:latest&&ma60?latest>ma60:null,electronic_above_ma20:base.filter((x)=>x.stock.ma20&&x.stock.price>x.stock.ma20).length/base.length>.5,electronic_above_ma60:base.filter((x)=>x.stock.ma60&&x.stock.price>x.stock.ma60).length/base.length>.5,ma5_slope:slope(closes,5),ma20_slope:slope(closes,20),ma60_slope:slope(closes,60),atr20_ratio:null,adx14:null,volume_ratio_20d:avg(base.map((x)=>x.stock.volume_ratio_20d)),advance_ratio:advances,advance_ratio_2d:advanceRatio2d,limit_up_count:dayReturns.filter((x)=>x>=9.5).length,limit_down_count:dayReturns.filter((x)=>x<=-9.5).length,new_high_20d_ratio:base.filter((x)=>x.stock.breakout_20d).length/base.length*100,new_low_20d_ratio:newLowRatio,new_low_ratio_change:null,electronic_turnover_share:null,foreign_net_5d:null,trust_net_5d:null,futures_bias:null,taiex_new_low:latest?latest<=Math.min(...closes.slice(-20,-1)):null,electronic_new_low:newLowRatio>=25,taiex_breakout_20d:latest?latest>Math.max(...closes.slice(-21,-1)):null,taiex_breakout_60d:latest?latest>Math.max(...closes.slice(-61,-1)):null,higher_low:higherLowRatio>=55,panic_volume_contracted:null,up_volume_expanding:avg(dayReturns)>0&&avg(base.map((x)=>x.stock.volume_ratio_20d))>=1,foreign_selling_shrinking:null,otc_relative_strength:null,electronic_long_black_days:0,sector_continuation_days:sectorContinuation,bollinger_width_percentile:null,source_status:{taiex:indexLive&&indexLive.date===tradeDate?"TWSE MIS 加權指數盤中＋TWSE 歷史資料":"TWSE 發行量加權股價指數歷史資料",electronic:"TWSE/TPEx 全電子股＋指定題材樣本聚合",stocks:"TWSE/TPEx + FinMind 彙整官方日成交"},missing_fields:["otc_index_history","electronic_index_history","market_institutional_5d","futures_bias"]},industries,stocks:base.map((x)=>x.stock),data_sources:["TWSE 上市公司基本資料","TPEx 上櫃公司基本資料","TWSE/TPEx 每日行情","TWSE 發行量加權股價指數歷史資料","TWSE MIS 盤中成交","TWSE/TPEx 個股三大法人","TDCC 股權分散表"]};
  scanCaches[scope].value=payload;scanCaches[scope].expiresAt=Date.now()+60_000;return payload;
}

export async function buildAdaptiveElectronicScan() {
  if(scanCaches.adaptive.value && scanCaches.adaptive.expiresAt>Date.now())return scanCaches.adaptive.value;
  if(!scanInFlight.adaptive){
    scanInFlight.adaptive=withScanLoadLock(() => buildAdaptiveElectronicScanUncached("adaptive"))
      .catch((error)=>{
        if(scanCaches.adaptive.value)return scanCaches.adaptive.value;
        throw error;
      })
      .finally(()=>{scanInFlight.adaptive=null;});
  }
  return scanInFlight.adaptive;
}

export async function buildRocketRadarScan() {
  if(scanCaches.rocket.value && scanCaches.rocket.expiresAt>Date.now())return scanCaches.rocket.value;
  if(!scanInFlight.rocket){
    scanInFlight.rocket=withScanLoadLock(() => buildAdaptiveElectronicScanUncached("rocket"))
      .catch((error)=>{
        if(scanCaches.rocket.value)return scanCaches.rocket.value;
        throw error;
      })
      .finally(()=>{scanInFlight.rocket=null;});
  }
  return scanInFlight.rocket;
}
