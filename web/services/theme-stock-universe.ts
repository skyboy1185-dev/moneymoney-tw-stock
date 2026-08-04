import type { StockMeta, StockTheme } from "@/lib/types";

export const TARGET_STOCK_THEMES: readonly StockTheme[] = ["AI", "低軌衛星", "玻纖布", "廠務工程"];

export const THEME_STOCKS = {
  "2330": ["AI"],
  "2317": ["AI"],
  "2454": ["AI", "IC設計"],
  "2308": ["AI"],
  "2382": ["AI"],
  "6669": ["AI"],
  "2313": ["AI", "PCB", "低軌衛星"],
  "2314": ["低軌衛星"],
  "2345": ["低軌衛星"],
  "2367": ["PCB", "低軌衛星"],
  "2383": ["PCB", "低軌衛星"],
  "2419": ["低軌衛星"],
  "3025": ["低軌衛星"],
  "3062": ["低軌衛星"],
  "3138": ["低軌衛星"],
  "3163": ["低軌衛星"],
  "3363": ["低軌衛星"],
  "3491": ["低軌衛星"],
  "3596": ["低軌衛星"],
  "3704": ["低軌衛星"],
  "4906": ["低軌衛星"],
  "4977": ["低軌衛星"],
  "5388": ["低軌衛星"],
  "6271": ["低軌衛星"],
  "6285": ["AI", "低軌衛星"],
  "6442": ["低軌衛星"],
  "6451": ["低軌衛星"],
  "6546": ["低軌衛星"],
  "8011": ["低軌衛星"],
  "8086": ["低軌衛星"],
  "2368": ["AI", "PCB"],
  "3037": ["AI", "PCB", "ABF載板"],
  "3189": ["AI", "ABF載板"],
  "8046": ["AI", "PCB", "ABF載板"],
  "2327": ["AI", "被動元件"],
  "2492": ["AI", "被動元件"],
  "3026": ["AI", "被動元件"],
  "2337": ["AI", "記憶體"],
  "2344": ["AI", "記憶體"],
  "2408": ["AI", "記憶體"],
  "8299": ["AI", "記憶體"],
  "1802": ["AI", "玻纖布"],
  "1815": ["AI", "玻纖布"],
  "5340": ["AI", "玻纖布"],
  "1303": ["玻纖布"],
  "5475": ["玻纖布"],
  "2404": ["廠務工程"],
  "3402": ["廠務工程"],
  "5536": ["廠務工程"],
  "6139": ["廠務工程"],
  "6196": ["廠務工程"],
  "6613": ["廠務工程"],
  "6667": ["廠務工程"],
  "6691": ["廠務工程"],
  "6903": ["廠務工程"],
  "7703": ["廠務工程"],
  "2379": ["AI", "IC設計"],
  "3034": ["AI", "IC設計"],
  "3443": ["AI", "IC設計"],
  "3661": ["AI", "IC設計"],
  "5269": ["AI", "IC設計"],
} as const satisfies Record<string, readonly StockTheme[]>;

export function themesForSymbol(symbol: string): StockTheme[] {
  return [...(THEME_STOCKS[symbol as keyof typeof THEME_STOCKS] ?? [])];
}

export function isTargetThemeSymbol(symbol: string): boolean {
  return symbol in THEME_STOCKS;
}

export function isExpandedThemeSymbol(symbol: string): boolean {
  return themesForSymbol(symbol).some((theme) =>
    theme === "低軌衛星" || theme === "玻纖布" || theme === "廠務工程"
  );
}

export function targetThemeStocks(stocks: StockMeta[]): StockMeta[] {
  return stocks
    .filter((stock) => isTargetThemeSymbol(stock.symbol))
    .map((stock) => ({ ...stock, themes: themesForSymbol(stock.symbol) }));
}
