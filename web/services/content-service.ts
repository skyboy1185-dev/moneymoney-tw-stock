import { stockCatalog, stockService } from "@/services/stock-service";
import { getOfficialRelaySnapshotQuotes } from "@/services/market-data/official-quote-provider";
import type { Market } from "@/lib/types";

export interface IndustryHotspot {
  industry: string;
  changePercent: number;
  medianChangePercent: number;
  advanceRatio: number;
  leaderBreadth: number;
  strengthScore: number;
  momentum: number;
  stockCount: number;
  leaders: {
    symbol: string;
    name: string;
    changePercent: number;
    close: number;
    change: number;
    previousClose: number;
    tradeDate: string;
    source: string;
  }[];
  status: "強勢" | "偏多" | "整理" | "偏弱";
}

export interface NewsItem {
  id: string;
  title: string;
  summary: string;
  category: string;
  symbols: string[];
  source: string;
  publishedAt: string;
  sentiment: "positive" | "neutral" | "negative";
  url?: string;
}

export interface IndustryHotspotResponse {
  items: IndustryHotspot[];
  updatedAt: string;
  tradeDate: string;
  dataMode: "official";
  dataSource: string;
  quoteStatus: "intraday" | "official_close" | "stale_intraday";
  rankingStatus: "live" | "collecting" | "closed";
  coverageRatio: number;
  coverageCount: number;
  targetCount: number;
  validQuoteTime: string;
  rankingMethod: string;
}

export interface IndustryStrengthMember {
  symbol: string;
  name: string;
  changePercent: number;
}

function clamp(value: number) {
  return Math.min(100, Math.max(0, value));
}

export function calculateIndustryStrength(members: IndustryStrengthMember[]) {
  const changes = members.map((item) => item.changePercent).sort((a, b) => a - b);
  if (!changes.length) return {
    changePercent: 0, medianChangePercent: 0, advanceRatio: 0, leaderBreadth: 0, strengthScore: 0,
  };
  const trim = changes.length >= 10 ? Math.floor(changes.length * .1) : 0;
  const trimmed = changes.slice(trim, changes.length - trim || undefined);
  const average = trimmed.reduce((sum, value) => sum + value, 0) / trimmed.length;
  const middle = Math.floor(changes.length / 2);
  const median = changes.length % 2 ? changes[middle] : (changes[middle - 1] + changes[middle]) / 2;
  const advanceRatio = changes.filter((value) => value > 0).length / changes.length * 100;
  const leaderBreadth = changes.filter((value) => value >= 2).length / changes.length * 100;
  const strengthScore = clamp(
    clamp(50 + average * 10) * .4
    + clamp(50 + median * 10) * .2
    + advanceRatio * .3
    + leaderBreadth * .1,
  );
  return {
    changePercent: Number(average.toFixed(2)),
    medianChangePercent: Number(median.toFixed(2)),
    advanceRatio: Number(advanceRatio.toFixed(1)),
    leaderBreadth: Number(leaderBreadth.toFixed(1)),
    strengthScore: Number(strengthScore.toFixed(1)),
  };
}

export function latestCommonTradeDate(listedDates: string[], otcDates: string[]) {
  const otc = new Set(otcDates.filter(Boolean));
  return listedDates.filter((value) => value && otc.has(value)).sort().at(-1) ?? null;
}

export function topIndustryHotspots(items: IndustryHotspot[], limit = 3) {
  return [...items]
    .sort((a, b) => b.strengthScore - a.strengthScore || b.changePercent - a.changePercent)
    .slice(0, limit);
}

export interface NewsResponse {
  items: NewsItem[];
  categories: string[];
  dataMode: "finmind";
  message: string;
  updatedAt: string;
}

type OfficialRow = Record<string, unknown>;

const INDUSTRY_NAMES: Record<string, string> = {
  "01":"水泥工業", "02":"食品工業", "03":"塑膠工業", "04":"紡織纖維", "05":"電機機械",
  "06":"電器電纜", "07":"化學工業", "08":"玻璃陶瓷", "09":"造紙工業", "10":"鋼鐵工業",
  "11":"橡膠工業", "12":"汽車工業", "14":"建材營造", "15":"航運業", "16":"觀光餐旅",
  "17":"金融保險", "18":"貿易百貨", "19":"綜合", "20":"其他", "21":"化學生技醫療",
  "22":"生技醫療", "23":"油電燃氣", "24":"半導體", "25":"電腦及週邊設備", "26":"光電",
  "27":"通信網路", "28":"電子零組件", "29":"電子通路", "30":"資訊服務", "31":"其他電子",
  "32":"文化創意", "33":"農業科技", "34":"電子商務", "35":"綠能環保", "36":"數位雲端",
  "37":"運動休閒", "38":"居家生活",
};

const NEWS_TARGETS = [
  ["2330","半導體"], ["2317","其他電子"], ["2454","半導體"], ["2308","電子零組件"],
  ["2382","電腦及週邊設備"], ["6669","電腦及週邊設備"], ["3711","半導體"],
  ["2303","半導體"], ["2383","電子零組件"], ["2345","通信網路"], ["2881","金融保險"],
  ["2891","金融保險"], ["2603","航運業"], ["1301","塑膠工業"], ["3008","光電"],
] as const;

let industryCache: { value: IndustryHotspotResponse; expiresAt: number } | null = null;
let newsCache: { value: NewsResponse; expiresAt: number; date: string } | null = null;

function text(row: OfficialRow, keys: string[]) {
  for (const key of keys) if (row[key] != null && String(row[key]).trim()) return String(row[key]).trim();
  return "";
}

function numberValue(value: unknown) {
  const parsed = Number(String(value ?? "").replaceAll(",", "").replaceAll("+", "").trim());
  return Number.isFinite(parsed) ? parsed : 0;
}

function rocDate(value: string) {
  if (/^\d{7}$/.test(value)) return `${Number(value.slice(0,3))+1911}-${value.slice(3,5)}-${value.slice(5,7)}`;
  if (/^\d{8}$/.test(value)) return `${value.slice(0,4)}-${value.slice(4,6)}-${value.slice(6,8)}`;
  return value;
}

async function officialJson(url: string): Promise<OfficialRow[]> {
  const response = await fetch(url, {
    headers: { Accept: "application/json" }, signal: AbortSignal.timeout(15_000),
    next: { revalidate: 300 },
  });
  if (!response.ok) throw new Error(`官方行情來源回應 ${response.status}`);
  const payload: unknown = await response.json();
  if (!Array.isArray(payload)) throw new Error("官方行情格式錯誤");
  return payload as OfficialRow[];
}

export async function buildOfficialIndustryHotspots(): Promise<IndustryHotspotResponse> {
  if (industryCache && industryCache.expiresAt > Date.now()) return industryCache.value;
  const [listedCompanies, otcCompanies, listedQuotes, otcQuotes] = await Promise.all([
    officialJson("https://openapi.twse.com.tw/v1/opendata/t187ap03_L"),
    officialJson("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"),
    officialJson("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"),
    officialJson("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"),
  ]);
  const companyMap = new Map<string, { name: string; industry: string; market: Market }>();
  for (const row of listedCompanies) {
    const symbol=text(row,["公司代號"]), code=text(row,["產業別"]);
    if (/^\d{4}$/.test(symbol) && INDUSTRY_NAMES[code]) companyMap.set(symbol,{name:text(row,["公司簡稱"]),industry:INDUSTRY_NAMES[code],market:"上市"});
  }
  for (const row of otcCompanies) {
    const symbol=text(row,["SecuritiesCompanyCode"]), code=text(row,["SecuritiesIndustryCode"]);
    if (/^\d{4}$/.test(symbol) && INDUSTRY_NAMES[code]) companyMap.set(symbol,{name:text(row,["CompanyAbbreviation"]),industry:INDUSTRY_NAMES[code],market:"上櫃"});
  }
  const allQuotes: { symbol:string; name:string; industry:string; market:Market; close:number; change:number; previousClose:number; changePercent:number; date:string; volume:number; source:string }[] = [];
  const append = (row: OfficialRow, listed: boolean) => {
    const symbol=text(row,listed?["Code"]:["SecuritiesCompanyCode"]), company=companyMap.get(symbol);
    if (!company) return;
    const close=numberValue(row[listed?"ClosingPrice":"Close"]), change=numberValue(row.Change);
    const previous=close-change, volume=numberValue(row[listed?"TradeVolume":"TradingShares"]);
    if (close<=0 || previous<=0 || volume<=0) return;
    allQuotes.push({symbol,name:company.name,industry:company.industry,market:company.market,close,change,previousClose:previous,changePercent:change/previous*100,date:rocDate(text(row,["Date"])),volume,source:listed?"TWSE 官方每日行情":"TPEx 官方每日行情"});
  };
  listedQuotes.forEach((row)=>append(row,true)); otcQuotes.forEach((row)=>append(row,false));
  const commonTradeDate=latestCommonTradeDate(
    allQuotes.filter((item)=>item.market==="上市").map((item)=>item.date),
    allQuotes.filter((item)=>item.market==="上櫃").map((item)=>item.date),
  );
  if (!commonTradeDate) throw new Error("上市與上櫃目前沒有相同交易日的官方行情，暫停族群排行");
  const quotes=allQuotes.filter((item)=>item.date===commonTradeDate);
  if (!quotes.length) throw new Error("官方產業行情目前沒有可用資料");
  const today=taipeiDate();
  const duringCash=isTaipeiCashSession();
  const metas=quotes.map((item)=>({symbol:item.symbol,name:item.name,market:item.market}));
  let snapshot=new Map<string,Awaited<ReturnType<typeof getOfficialRelaySnapshotQuotes>> extends Map<string,infer Q>?Q:never>();
  try { snapshot=await getOfficialRelaySnapshotQuotes(metas); } catch { /* collecting state below */ }
  const liveQuotes=quotes.flatMap((base)=>{
    const quote=snapshot.get(base.symbol);
    return quote?.date===today&&quote.source.startsWith("TWSE MIS")
      ? [{...base,close:quote.price,change:quote.change,previousClose:quote.previousClose,
        changePercent:quote.changePercent,date:quote.date,volume:quote.volume,source:quote.source,time:quote.time}]
      : [];
  });
  const targetCount=quotes.length;
  const coverageCount=liveQuotes.length;
  const coverageRatio=targetCount?coverageCount/targetCount*100:0;
  const completeLive=coverageRatio>=80;
  const officialToday=commonTradeDate===today;
  const rankingStatus: IndustryHotspotResponse["rankingStatus"]=completeLive?(duringCash?"live":"closed"):officialToday&&!duringCash?"closed":"collecting";
  const sourceQuotes=completeLive?liveQuotes:officialToday&&!duringCash?quotes:[];
  const quoteStatus: IndustryHotspotResponse["quoteStatus"]=rankingStatus==="live"?"intraday":rankingStatus==="closed"?"official_close":"stale_intraday";
  const validQuoteTime=liveQuotes.map((quote)=>quote.time).filter((time)=>/^\d{2}:\d{2}:\d{2}$/.test(time)).sort().at(-1)??"";
  const updatedAt=validQuoteTime?`${today}T${validQuoteTime}+08:00`:`${today}T13:30:00+08:00`;
  const totalByIndustry=new Map<string,number>();
  for(const quote of quotes) totalByIndustry.set(quote.industry,(totalByIndustry.get(quote.industry)??0)+1);
  const groups=new Map<string,typeof sourceQuotes>();
  for (const quote of sourceQuotes) groups.set(quote.industry,[...(groups.get(quote.industry)??[]),quote]);
  const items=[...groups].flatMap(([industry,members])=>{
    const industryCoverage=members.length/(totalByIndustry.get(industry)??members.length)*100;
    if(rankingStatus==="live"&&(members.length<3||industryCoverage<70)) return [];
    const strength=calculateIndustryStrength(members);
    const momentum=Math.round(strength.strengthScore);
    return [{industry,...strength,momentum,stockCount:members.length,
      leaders:[...members].sort((a,b)=>b.changePercent-a.changePercent).slice(0,3).map(({symbol,name,changePercent,close,change,previousClose,date,source})=>({symbol,name,changePercent:Number(changePercent.toFixed(2)),close,change,previousClose,tradeDate:date,source})),
      status:strength.strengthScore>=70?"強勢" as const:strength.strengthScore>=55?"偏多" as const:strength.strengthScore>=40?"整理" as const:"偏弱" as const}];
  }).sort((a,b)=>b.strengthScore-a.strengthScore || b.changePercent-a.changePercent);
  const tradeDate=rankingStatus==="collecting"?today:sourceQuotes[0]?.date??today;
  const value={items,tradeDate,updatedAt,dataMode:"official" as const,
    dataSource:rankingStatus==="live"?"TWSE MIS 全市場當日行情":"TWSE／TPEx 官方當日收盤行情",
    quoteStatus,rankingStatus,coverageRatio:Number(coverageRatio.toFixed(1)),coverageCount,targetCount,validQuoteTime,
    rankingMethod:"截尾平均40%＋中位數20%＋上漲家數比30%＋漲幅逾2%家數比10%"};
  industryCache={value,expiresAt:Date.now()+(rankingStatus==="closed"?5*60_000:30_000)};
  return value;
}

function taipeiDate() {
  return new Intl.DateTimeFormat("en-CA",{timeZone:"Asia/Taipei",year:"numeric",month:"2-digit",day:"2-digit"}).format(new Date());
}

export function isTaipeiCashSession(now = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Taipei", weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false,
  }).formatToParts(now);
  const weekday = parts.find((part) => part.type === "weekday")?.value;
  const hour = Number(parts.find((part) => part.type === "hour")?.value ?? 0);
  const minute = Number(parts.find((part) => part.type === "minute")?.value ?? 0);
  const clock = hour * 60 + minute;
  return !["Sat", "Sun"].includes(weekday ?? "") && clock >= 540 && clock <= 810;
}

function newsSentiment(title: string): NewsItem["sentiment"] {
  if (/(大漲|成長|創高|買超|利多|突破|上修|強攻|看旺)/.test(title)) return "positive";
  if (/(大跌|衰退|利空|賣超|下修|重挫|警示|虧損|裁員)/.test(title)) return "negative";
  return "neutral";
}

export async function buildFinMindNews(category = "", keyword = ""): Promise<NewsResponse> {
  const date=taipeiDate();
  let base: NewsResponse;
  if (newsCache && newsCache.expiresAt>Date.now() && newsCache.date===date) base=newsCache.value;
  else {
    const results=await Promise.allSettled(NEWS_TARGETS.map(async([symbol,industry])=>{
      const query=new URLSearchParams({dataset:"TaiwanStockNews",data_id:symbol,start_date:date});
      const response=await fetch(`https://api.finmindtrade.com/api/v4/data?${query}`,{headers:{Accept:"application/json"},signal:AbortSignal.timeout(12_000),cache:"no-store"});
      if(!response.ok) throw new Error(`FinMind 新聞回應 ${response.status}`);
      const payload=await response.json() as {status?:number;data?:OfficialRow[]};
      if(payload.status!==200||!Array.isArray(payload.data)) return [];
      return payload.data.map((row):NewsItem=>({
        id:`${symbol}-${text(row,["date"])}-${text(row,["link","title"])}`,
        title:text(row,["title"]),summary:"點擊標題可開啟原始新聞來源。",category:industry,symbols:[symbol],
        source:text(row,["source"])||"FinMind",publishedAt:text(row,["date"]).replace(" ","T")+"+08:00",
        sentiment:newsSentiment(text(row,["title"])),url:text(row,["link"]),
      })).filter((item)=>item.title&&item.url?.startsWith("http"));
    }));
    const merged=results.flatMap((result)=>result.status==="fulfilled"?result.value:[]);
    const unique=[...new Map(merged.map((item)=>[item.url||item.title,item])).values()]
      .sort((a,b)=>b.publishedAt.localeCompare(a.publishedAt)).slice(0,50);
    if(!unique.length) throw new Error("FinMind 今日尚無可用相關新聞");
    base={items:unique,categories:[...new Set(unique.map((item)=>item.category))].sort(),dataMode:"finmind",message:"FinMind 彙整當日相關新聞；標題與連結版權屬原新聞來源。",updatedAt:new Date().toISOString()};
    newsCache={value:base,date,expiresAt:Date.now()+10*60_000};
  }
  const normalized=keyword.trim().toLocaleLowerCase("zh-TW");
  return {...base,items:base.items.filter((item)=>(!category||item.category===category)&&(!normalized||item.title.toLocaleLowerCase("zh-TW").includes(normalized)||item.symbols.some((symbol)=>symbol.includes(normalized))))};
}

export const MOCK_NEWS: NewsItem[] = [
  { id: "n1", title: "晶圓代工與先進封裝供應鏈維持高能見度", summary: "AI 伺服器需求帶動先進製程與封裝相關供應鏈關注度。", category: "半導體", symbols: ["2330", "2454"], source: "展示新聞中心", publishedAt: "2026-07-24T12:20:00+08:00", sentiment: "positive" },
  { id: "n2", title: "伺服器供應鏈觀察出貨與匯率變化", summary: "市場關注下半年伺服器出貨節奏、零組件供應與匯率影響。", category: "電腦及週邊", symbols: ["2317", "2382", "6669"], source: "展示新聞中心", publishedAt: "2026-07-24T11:05:00+08:00", sentiment: "neutral" },
  { id: "n3", title: "金融股除息行情與資產品質成焦點", summary: "投資人持續評估股利政策、利差與資產品質表現。", category: "金融保險", symbols: ["2881", "2882"], source: "展示新聞中心", publishedAt: "2026-07-24T09:40:00+08:00", sentiment: "neutral" },
  { id: "n4", title: "航運報價波動，市場關注旺季需求", summary: "運價與供需變化使航運族群波動放大，需留意風險。", category: "航運業", symbols: ["2603"], source: "展示新聞中心", publishedAt: "2026-07-23T16:10:00+08:00", sentiment: "negative" },
  { id: "n5", title: "電子紙應用擴張帶動光電族群話題", summary: "零售、物流與低耗能顯示應用持續擴張。", category: "光電", symbols: ["8069", "3008"], source: "展示新聞中心", publishedAt: "2026-07-23T14:35:00+08:00", sentiment: "positive" },
];

export async function buildMockIndustryHotspots(): Promise<IndustryHotspot[]> {
  const rows = await Promise.all(stockCatalog.map(async (stock) => {
    const payload = await stockService.getStock(stock.symbol);
    if (!payload) return null;
    const latest = payload.prices.at(-1)!;
    const previous = payload.prices.at(-2)!;
    return {
      ...stock,
      changePercent: (latest.close - previous.close) / previous.close * 100,
    };
  }));
  const groups = new Map<string, NonNullable<(typeof rows)[number]>[]>();
  rows.filter((row): row is NonNullable<typeof row> => row !== null).forEach((row) => {
    groups.set(row.industry, [...(groups.get(row.industry) ?? []), row]);
  });
  return [...groups.entries()].map(([industry, members]) => {
    const strength = calculateIndustryStrength(members);
    return {
      industry,
      ...strength,
      momentum: Math.round(strength.strengthScore),
      stockCount: members.length,
      leaders: members.sort((a, b) => b.changePercent - a.changePercent).slice(0, 3)
        .map(({ symbol, name, changePercent }) => ({ symbol, name, changePercent, close: 0, change: 0, previousClose: 0, tradeDate: "", source: "展示資料" })),
      status: strength.strengthScore >= 70 ? "強勢" as const : strength.strengthScore >= 55 ? "偏多" as const : strength.strengthScore >= 40 ? "整理" as const : "偏弱" as const,
    };
  }).sort((a, b) => b.strengthScore - a.strengthScore || b.changePercent - a.changePercent);
}
