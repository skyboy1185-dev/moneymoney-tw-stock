import { resampleCandles } from "@/lib/technical";
import type { MarketDataProvider, Timeframe } from "@/lib/market-types";
import type { DailyPrice } from "@/lib/types";
import { stockCatalog, stockService } from "@/services/stock-service";

function taipeiNow() {
  return new Date(new Date().toLocaleString("en-US", { timeZone: "Asia/Taipei" }));
}

export class MockMarketDataProvider implements MarketDataProvider {
  async getQuote(symbol: string): Promise<DailyPrice | null> {
    const stock = await stockService.getStock(symbol);
    return stock?.prices.at(-1) ?? null;
  }

  async getQuotes(symbols: string[]): Promise<DailyPrice[]> {
    return (await Promise.all(symbols.map((symbol) => this.getQuote(symbol)))).filter((item): item is DailyPrice => item !== null);
  }

  async getHistoricalCandles(symbol: string, timeframe: Timeframe): Promise<DailyPrice[]> {
    const stock = await stockService.getStock(symbol);
    return stock ? resampleCandles(stock.prices, timeframe) : [];
  }

  async getStockList() {
    return stockCatalog.map(({ symbol, name, market }) => ({ symbol, name, market }));
  }

  async getMarketStatus() {
    const now = taipeiNow();
    const minutes = now.getHours() * 60 + now.getMinutes();
    const weekday = now.getDay();
    const open = weekday >= 1 && weekday <= 5 && minutes >= 540 && minutes <= 810;
    return {
      open,
      label: open ? "模擬盤中更新" : "非交易時間・顯示最近有效模擬資料",
      updatedAt: new Date().toISOString(),
    };
  }
}

export const marketDataProvider = new MockMarketDataProvider();
