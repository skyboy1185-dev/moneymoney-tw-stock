import { calculateIndicators } from "@/lib/indicators";
import type {
  DailyPrice,
  ScreenerFilters,
  ScreenerRow,
  StockMeta,
  StockPayload,
  TechnicalIndicator,
} from "@/lib/types";
import { targetThemeStocks, themesForSymbol } from "@/services/theme-stock-universe";

export interface StockDataProvider {
  search(query: string): Promise<StockMeta | null>;
  getStock(symbol: string): Promise<StockPayload | null>;
  screen(filters: ScreenerFilters): Promise<ScreenerRow[]>;
}

const STOCKS: StockMeta[] = [
  { symbol: "2330", name: "台積電", industry: "半導體", market: "上市", peRatio: 24.8, dividendYield: 1.72, priceToBook: 7.2, eps: 46.3, marketCap: 28.9e12, themes: themesForSymbol("2330") },
  { symbol: "2317", name: "鴻海", industry: "電子零組件", market: "上市", peRatio: 13.2, dividendYield: 3.1, priceToBook: 1.55, eps: 12.4, marketCap: 2.54e12, themes: themesForSymbol("2317") },
  { symbol: "2454", name: "聯發科", industry: "半導體", market: "上市", peRatio: 21.5, dividendYield: 3.55, priceToBook: 5.1, eps: 68.2, marketCap: 2.31e12, themes: themesForSymbol("2454") },
  { symbol: "2308", name: "台達電", industry: "電子零組件", market: "上市", peRatio: 31.7, dividendYield: 1.62, priceToBook: 6.3, eps: 15.8, marketCap: 1.24e12, themes: themesForSymbol("2308") },
  { symbol: "2881", name: "富邦金", industry: "金融保險", market: "上市", peRatio: 11.3, dividendYield: 4.18, priceToBook: 1.31, eps: 8.5, marketCap: 1.18e12 },
  { symbol: "2882", name: "國泰金", industry: "金融保險", market: "上市", peRatio: 10.8, dividendYield: 3.82, priceToBook: 1.22, eps: 6.1, marketCap: 9.4e11 },
  { symbol: "0050", name: "元大台灣50", industry: "ETF", market: "上市", peRatio: null, dividendYield: 2.9, priceToBook: null, eps: null, marketCap: 5.2e11 },
  { symbol: "2382", name: "廣達", industry: "電腦及週邊", market: "上市", peRatio: 19.6, dividendYield: 4.02, priceToBook: 4.8, eps: 16.7, marketCap: 1.13e12, themes: themesForSymbol("2382") },
  { symbol: "3008", name: "大立光", industry: "光電", market: "上市", peRatio: 16.4, dividendYield: 3.2, priceToBook: 3.1, eps: 182.5, marketCap: 3.4e11 },
  { symbol: "5274", name: "信驊", industry: "半導體", market: "上櫃", peRatio: 49.2, dividendYield: 0.91, priceToBook: 17.4, eps: 91.8, marketCap: 2.15e11 },
  { symbol: "6488", name: "環球晶", industry: "半導體", market: "上櫃", peRatio: 18.9, dividendYield: 3.65, priceToBook: 2.7, eps: 26.4, marketCap: 2.06e11 },
  { symbol: "8069", name: "元太", industry: "光電", market: "上櫃", peRatio: 29.7, dividendYield: 1.83, priceToBook: 5.2, eps: 8.6, marketCap: 2.82e11 },
  { symbol: "6669", name: "緯穎", industry: "電腦及週邊", market: "上市", peRatio: 22.3, dividendYield: 2.31, priceToBook: 7.8, eps: 118.6, marketCap: 4.95e11, themes: themesForSymbol("6669") },
  { symbol: "2313", name: "華通", industry: "電子零組件", market: "上市", peRatio: null, dividendYield: null, priceToBook: null, eps: null, marketCap: null, themes: themesForSymbol("2313") },
  { symbol: "2314", name: "台揚", industry: "通信網路", market: "上市", peRatio: null, dividendYield: null, priceToBook: null, eps: null, marketCap: null, themes: themesForSymbol("2314") },
  { symbol: "3491", name: "昇達科", industry: "通信網路", market: "上櫃", peRatio: null, dividendYield: null, priceToBook: null, eps: null, marketCap: null, themes: themesForSymbol("3491") },
  { symbol: "6285", name: "啓碁", industry: "通信網路", market: "上市", peRatio: null, dividendYield: null, priceToBook: null, eps: null, marketCap: null, themes: themesForSymbol("6285") },
  { symbol: "2368", name: "金像電", industry: "電子零組件", market: "上市", peRatio: null, dividendYield: null, priceToBook: null, eps: null, marketCap: null, themes: themesForSymbol("2368") },
  { symbol: "3037", name: "欣興", industry: "電子零組件", market: "上市", peRatio: null, dividendYield: null, priceToBook: null, eps: null, marketCap: null, themes: themesForSymbol("3037") },
  { symbol: "3189", name: "景碩", industry: "半導體", market: "上市", peRatio: null, dividendYield: null, priceToBook: null, eps: null, marketCap: null, themes: themesForSymbol("3189") },
  { symbol: "8046", name: "南電", industry: "電子零組件", market: "上市", peRatio: null, dividendYield: null, priceToBook: null, eps: null, marketCap: null, themes: themesForSymbol("8046") },
  { symbol: "2327", name: "國巨", industry: "電子零組件", market: "上市", peRatio: null, dividendYield: null, priceToBook: null, eps: null, marketCap: null, themes: themesForSymbol("2327") },
  { symbol: "2492", name: "華新科", industry: "電子零組件", market: "上市", peRatio: null, dividendYield: null, priceToBook: null, eps: null, marketCap: null, themes: themesForSymbol("2492") },
  { symbol: "3026", name: "禾伸堂", industry: "電子零組件", market: "上市", peRatio: null, dividendYield: null, priceToBook: null, eps: null, marketCap: null, themes: themesForSymbol("3026") },
  { symbol: "2337", name: "旺宏", industry: "半導體", market: "上市", peRatio: null, dividendYield: null, priceToBook: null, eps: null, marketCap: null, themes: themesForSymbol("2337") },
  { symbol: "2344", name: "華邦電", industry: "半導體", market: "上市", peRatio: null, dividendYield: null, priceToBook: null, eps: null, marketCap: null, themes: themesForSymbol("2344") },
  { symbol: "2408", name: "南亞科", industry: "半導體", market: "上市", peRatio: null, dividendYield: null, priceToBook: null, eps: null, marketCap: null, themes: themesForSymbol("2408") },
  { symbol: "8299", name: "群聯", industry: "半導體", market: "上櫃", peRatio: null, dividendYield: null, priceToBook: null, eps: null, marketCap: null, themes: themesForSymbol("8299") },
  { symbol: "1802", name: "台玻", industry: "玻璃陶瓷", market: "上市", peRatio: null, dividendYield: null, priceToBook: null, eps: null, marketCap: null, themes: themesForSymbol("1802") },
  { symbol: "1815", name: "富喬", industry: "電子零組件", market: "上櫃", peRatio: null, dividendYield: null, priceToBook: null, eps: null, marketCap: null, themes: themesForSymbol("1815") },
  { symbol: "5340", name: "建榮", industry: "電子零組件", market: "上櫃", peRatio: null, dividendYield: null, priceToBook: null, eps: null, marketCap: null, themes: themesForSymbol("5340") },
  { symbol: "2379", name: "瑞昱", industry: "半導體", market: "上市", peRatio: null, dividendYield: null, priceToBook: null, eps: null, marketCap: null, themes: themesForSymbol("2379") },
  { symbol: "3034", name: "聯詠", industry: "半導體", market: "上市", peRatio: null, dividendYield: null, priceToBook: null, eps: null, marketCap: null, themes: themesForSymbol("3034") },
  { symbol: "3443", name: "創意", industry: "半導體", market: "上市", peRatio: null, dividendYield: null, priceToBook: null, eps: null, marketCap: null, themes: themesForSymbol("3443") },
  { symbol: "3661", name: "世芯-KY", industry: "半導體", market: "上市", peRatio: null, dividendYield: null, priceToBook: null, eps: null, marketCap: null, themes: themesForSymbol("3661") },
  { symbol: "5269", name: "祥碩", industry: "半導體", market: "上市", peRatio: null, dividendYield: null, priceToBook: null, eps: null, marketCap: null, themes: themesForSymbol("5269") },
  { symbol: "1301", name: "台塑", industry: "塑膠工業", market: "上市", peRatio: 38.1, dividendYield: 2.52, priceToBook: 1.08, eps: 1.2, marketCap: 3.1e11 },
  { symbol: "2603", name: "長榮", industry: "航運業", market: "上市", peRatio: 6.8, dividendYield: 8.44, priceToBook: 1.42, eps: 32.8, marketCap: 4.2e11 }
];

const BASE_PRICES: Record<string, number> = {
  "2330": 1125, "2317": 181, "2454": 1430, "2308": 468, "2881": 91.6,
  "2882": 68.5, "0050": 58.4, "2382": 292, "3008": 2510, "5274": 5220,
  "6488": 437, "8069": 246, "6669": 2830, "1301": 48.6, "2603": 196,
  "2313": 199.5, "2314": 12.5, "3491": 1155, "6285": 249.5,
  "2368": 895, "3037": 848, "3189": 710, "8046": 1075,
  "2327": 625, "2492": 272, "3026": 585,
  "2337": 125.5, "2344": 160, "2408": 436, "8299": 1820,
  "1802": 52.5, "1815": 75.2, "5340": 68.9,
  "2379": 762, "3034": 518, "3443": 4050, "3661": 3460, "5269": 1385,
};

function hashCode(text: string): number {
  return text.split("").reduce((hash, char) => ((hash << 5) - hash + char.charCodeAt(0)) | 0, 0);
}

function seededRandom(seed: number): () => number {
  let state = Math.abs(seed) || 1;
  return () => {
    state = (state * 16807) % 2147483647;
    return (state - 1) / 2147483646;
  };
}

function businessDates(count: number): string[] {
  const dates: string[] = [];
  const cursor = new Date(Date.UTC(2026, 6, 24));
  while (dates.length < count) {
    const day = cursor.getUTCDay();
    if (day !== 0 && day !== 6) dates.push(cursor.toISOString().slice(0, 10));
    cursor.setUTCDate(cursor.getUTCDate() - 1);
  }
  return dates.reverse();
}

function generatePrices(meta: StockMeta, count = 5280): DailyPrice[] {
  const random = seededRandom(hashCode(meta.symbol));
  const dates = businessDates(count);
  const target = BASE_PRICES[meta.symbol] ?? 100;
  let close = target * (0.54 + random() * 0.16);
  const raw = dates.map((date, index) => {
    const cycle = Math.sin(index / (19 + Number(meta.symbol.slice(-1)))) * 0.003;
    const drift = 0.00042 + cycle;
    const shock = (random() - 0.5) * 0.035;
    const open = close * (1 + (random() - 0.5) * 0.012);
    close = Math.max(8, close * (1 + drift + shock));
    const high = Math.max(open, close) * (1 + random() * 0.014);
    const low = Math.min(open, close) * (1 - random() * 0.014);
    const volume = Math.round((2_500_000 + random() * 42_000_000) * (1 + Math.abs(shock) * 9));
    return { symbol: meta.symbol, name: meta.name, date, open, high, low, close, volume };
  });
  // 為 MVP 固定保留可驗證的「今日翻紅／翻綠」案例；只改變當日及過去資料，
  // 訊號仍由共用的 calculateIndicators 依時間順序計算，沒有預先寫入訊號或使用未來資料。
  const forcedSignal = ["2330", "2454"].includes(meta.symbol)
    ? "entry"
    : ["2317", "2881"].includes(meta.symbol) ? "exit" : null;
  if (forcedSignal) {
    const patternDays = 36;
    const patternStart = raw.length - patternDays;
    const base = raw[patternStart - 1].close;
    for (let offset = 0; offset < patternDays - 1; offset += 1) {
      const progress = offset + 1;
      const entryPeak = base * (1 + 0.006 * 20);
      const patternedClose = forcedSignal === "entry"
        ? progress <= 20
          ? base * (1 + 0.006 * progress)
          : entryPeak * (1 - 0.002 * (progress - 20))
        : base * (1 + 0.005 * progress);
      const previousClose = offset === 0 ? base : raw[patternStart + offset - 1].close;
      raw[patternStart + offset] = {
        ...raw[patternStart + offset],
        open: previousClose,
        close: patternedClose,
        high: Math.max(previousClose, patternedClose) * 1.004,
        low: Math.min(previousClose, patternedClose) * 0.996,
      };
    }
    const lastIndex = raw.length - 1;
    const previousClose = raw[lastIndex - 1].close;
    const lastClose = previousClose * (forcedSignal === "entry" ? 1.098 : 0.94);
    raw[lastIndex] = {
      ...raw[lastIndex],
      open: previousClose,
      close: lastClose,
      high: Math.max(previousClose, lastClose) * 1.003,
      low: Math.min(previousClose, lastClose) * 0.997,
      volume: Math.round(raw[lastIndex].volume * 1.8),
    };
  }
  const scale = target / raw.at(-1)!.close;
  return raw.map((price) => ({
    ...price,
    open: Math.round(price.open * scale * 100) / 100,
    high: Math.round(price.high * scale * 100) / 100,
    low: Math.round(price.low * scale * 100) / 100,
    close: Math.round(price.close * scale * 100) / 100,
  }));
}

const cache = new Map<string, StockPayload>();

export function payloadFor(meta: StockMeta): StockPayload {
  const cached = cache.get(meta.symbol);
  if (cached) return cached;
  const prices = generatePrices(meta);
  const payload = {
    meta,
    prices,
    indicators: calculateIndicators(prices),
    updatedAt: "2026-07-24T13:30:00+08:00",
  };
  cache.set(meta.symbol, payload);
  return payload;
}

function latestSignal(indicators: TechnicalIndicator[]) {
  return [...indicators].reverse().find((item) => item.macdSignal !== null) ?? null;
}

function hasRecentSignal(indicators: TechnicalIndicator[], signal: "entry" | "exit", days: number): boolean {
  return indicators.slice(-days).some((item) => item.macdSignal === signal);
}

function rowFor(meta: StockMeta): ScreenerRow {
  const data = payloadFor(meta);
  const latest = data.prices.at(-1)!;
  const previous = data.prices.at(-2)!;
  const indicator = data.indicators.at(-1)!;
  const signal = latestSignal(data.indicators);
  const volumes = data.prices.map((price) => price.volume);
  const average = (days: number) => volumes.slice(-days).reduce((sum, value) => sum + value, 0) / days;
  const highs = (days: number) => Math.max(...data.prices.slice(-days).map((price) => price.high));
  const flags: string[] = [];
  if (indicator.macdSignal === "entry") flags.push("macdEntryToday");
  if (indicator.macdSignal === "exit") flags.push("macdExitToday");
  if (hasRecentSignal(data.indicators, "entry", 3)) flags.push("macdEntry3d");
  if (hasRecentSignal(data.indicators, "entry", 5)) flags.push("macdEntry5d");
  if (indicator.ma5 != null && latest.close > indicator.ma5) flags.push("aboveMa5");
  if (indicator.ma20 != null && latest.close > indicator.ma20) flags.push("aboveMa20");
  if (indicator.ma60 != null && latest.close > indicator.ma60) flags.push("aboveMa60");
  if (indicator.ma5 != null && indicator.ma20 != null && indicator.ma5 > indicator.ma20) flags.push("ma5AboveMa20");
  if (indicator.ma20 != null && indicator.ma60 != null && indicator.ma20 > indicator.ma60) flags.push("ma20AboveMa60");
  if (indicator.ma5 != null && indicator.ma20 != null && indicator.ma60 != null && indicator.ma5 > indicator.ma20 && indicator.ma20 > indicator.ma60) flags.push("bullishAlignment");
  if (indicator.ma5 != null && indicator.ma20 != null && indicator.ma60 != null && indicator.ma5 < indicator.ma20 && indicator.ma20 < indicator.ma60) flags.push("bearishAlignment");
  if (latest.volume > average(5)) flags.push("volumeAbove5");
  if (latest.volume > average(20)) flags.push("volumeAbove20");
  if (latest.high >= highs(20)) flags.push("high20");
  if (latest.high >= highs(60)) flags.push("high60");
  return {
    symbol: meta.symbol,
    name: meta.name,
    industry: meta.industry,
    market: meta.market,
    price: latest.close,
    changePercent: ((latest.close - previous.close) / previous.close) * 100,
    volume: latest.volume,
    ma5: indicator.ma5,
    ma20: indicator.ma20,
    ma60: indicator.ma60,
    dif: indicator.dif,
    signal: indicator.signal,
    histogram: indicator.histogram,
    latestSignal: signal?.macdSignal ?? null,
    signalDate: signal?.date ?? null,
    flags,
  };
}

export class MockStockDataProvider implements StockDataProvider {
  async search(query: string): Promise<StockMeta | null> {
    const normalized = query.trim().toLowerCase();
    return STOCKS.find((stock) => stock.symbol === normalized || stock.name.toLowerCase() === normalized)
      ?? STOCKS.find((stock) => stock.symbol.includes(normalized) || stock.name.toLowerCase().includes(normalized))
      ?? null;
  }

  async getStock(symbol: string): Promise<StockPayload | null> {
    const meta = STOCKS.find((stock) => stock.symbol === symbol);
    return meta ? payloadFor(meta) : null;
  }

  async screen(filters: ScreenerFilters): Promise<ScreenerRow[]> {
    return STOCKS.map(rowFor).filter((row) => {
      const minPrice = Number(filters.minPrice);
      const maxPrice = Number(filters.maxPrice);
      const minVolume = Number(filters.minVolume);
      const minChange = Number(filters.minChange);
      const maxChange = Number(filters.maxChange);
      if (filters.minPrice && row.price < minPrice) return false;
      if (filters.maxPrice && row.price > maxPrice) return false;
      if (filters.minVolume && row.volume < minVolume * 1000) return false;
      if (filters.minChange && row.changePercent < minChange) return false;
      if (filters.maxChange && row.changePercent > maxChange) return false;
      if (filters.industry && row.industry !== filters.industry) return false;
      if (filters.market && row.market !== filters.market) return false;
      return filters.technical.every((condition) => row.flags.includes(condition));
    });
  }
}

export const stockService: StockDataProvider = new MockStockDataProvider();
export const stockCatalog: StockMeta[] = STOCKS.map((stock) => ({ ...stock }));
export const thematicStockCatalog: StockMeta[] = targetThemeStocks(stockCatalog);
