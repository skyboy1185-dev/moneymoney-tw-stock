import type { MarketDirectionProvider } from "@/lib/market-types";

function wave(offset = 0) {
  const minute = Math.floor(Date.now() / 60_000);
  return Math.sin((minute + offset) / 7);
}

export class MockMarketDirectionProvider implements MarketDirectionProvider {
  async getMarketIndex() {
    const changePercent = 0.84 + wave(1) * 0.18;
    const price = 24_186.32;
    const change = price * changePercent / 100;
    return { price, change, changePercent };
  }

  async getIndexFutures() {
    const changePercent = 0.72 + wave(2) * 0.22;
    const price = 24_132;
    const change = price * changePercent / 100;
    return { price, change, changePercent };
  }

  async getTradeTicks() {
    return Array.from({ length: 80 }, (_, index) => {
      const amount = 60_000 + ((index * 7919) % 1_850_000);
      const smallLimit = Number(process.env.SMALL_ORDER_MAX_AMOUNT ?? 300_000);
      const side = amount <= smallLimit
        ? (index % 4 === 0 ? "buy" as const : "sell" as const)
        : (index % 5 === 0 ? "sell" as const : "buy" as const);
      return { price: 100 + index * .02, amount, side };
    });
  }

  async getMarketBreadth() {
    return { up: 624, down: 318, flat: 87 };
  }

  async getOrderStatistics() {
    const ticks = await this.getTradeTicks();
    const largeLimit = Number(process.env.LARGE_ORDER_AMOUNT ?? 1_000_000);
    const smallLimit = Number(process.env.SMALL_ORDER_MAX_AMOUNT ?? 300_000);
    // 模擬單一批次擴展為全市場樣本；分類及買賣方向仍逐筆計算。
    const marketScale = 50;
    const net = (items: typeof ticks) => items.reduce((sum, tick) => sum + tick.amount * (tick.side === "buy" ? 1 : -1), 0) * marketScale;
    return {
      largeOrderNet: net(ticks.filter((tick) => tick.amount >= largeLimit)) + wave(3) * 120_000_000,
      smallOrderNet: net(ticks.filter((tick) => tick.amount <= smallLimit)) + wave(4) * 50_000_000,
    };
  }
}

export const marketDirectionProvider = new MockMarketDirectionProvider();
