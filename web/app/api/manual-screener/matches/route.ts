import { NextResponse } from "next/server";
import { MANUAL_STRATEGIES } from "@/lib/manual-strategies";
import type { StockManualStrategyMatches } from "@/lib/manual-strategy-matches";
import type { StockMeta } from "@/lib/types";
import { evaluateManualStrategy } from "@/services/manual-strategy-service";
import {
  getOfficialDeductionHistory,
  mergeOfficialHistoryWithQuote,
} from "@/services/market-data/official-history-provider";
import { getOfficialQuotes } from "@/services/market-data/official-quote-provider";

interface StrategyMatchRequestItem {
  symbol: string;
  name: string;
  market: "上市" | "上櫃";
}

function validItem(value: unknown): value is StrategyMatchRequestItem {
  if (!value || typeof value !== "object") return false;
  const item = value as Partial<StrategyMatchRequestItem>;
  return /^\d{4,6}$/.test(item.symbol ?? "")
    && typeof item.name === "string"
    && item.name.length > 0
    && (item.market === "上市" || item.market === "上櫃");
}

function validAsOfDate(value: unknown): string | null {
  return typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : null;
}

function toMeta(item: StrategyMatchRequestItem): StockMeta {
  return {
    ...item,
    industry: "",
    peRatio: null,
    dividendYield: null,
    priceToBook: null,
    eps: null,
    marketCap: null,
  };
}

export async function POST(request: Request) {
  try {
    const body = await request.json() as { items?: unknown[]; asOfDate?: unknown };
    const uniqueItems = [...new Map(
      (body.items ?? []).filter(validItem).map((item) => [item.symbol, item]),
    ).values()].slice(0, 24);
    if (!uniqueItems.length) return NextResponse.json({ items: [] });
    const asOfDate = validAsOfDate(body.asOfDate);
    const metas = uniqueItems.map(toMeta);
    const quotes = await getOfficialQuotes(metas);
    const calculatedAt = new Date().toISOString();
    const items = await Promise.all(metas.map(async (meta): Promise<StockManualStrategyMatches> => {
      try {
        const quote = quotes.get(meta.symbol) ?? null;
        const prices = mergeOfficialHistoryWithQuote(
          await getOfficialDeductionHistory(meta),
          meta,
          quote,
        );
        const effectivePrices = asOfDate ? prices.filter((price) => price.date <= asOfDate) : prices;
        const latestPriceDate = effectivePrices.at(-1)?.date ?? null;
        const signalStatus = quote?.isRealtime && quote.date === latestPriceDate
          ? "temporary" as const
          : "confirmed" as const;
        const matches = MANUAL_STRATEGIES.flatMap((strategy) => {
          const row = evaluateManualStrategy(meta, prices, strategy, signalStatus, asOfDate);
          if (!row) return [];
          return [{
            strategyId: strategy.id,
            strategyName: strategy.name,
            timeframe: strategy.timeframe,
            signalMode: strategy.signalMode,
            signalDate: row.signalDate,
            signalStatus: row.signalStatus ?? signalStatus,
          }];
        });
        return {
          symbol: meta.symbol,
          matches,
          calculatedAt,
          asOfDate,
          latestPriceDate,
          error: null,
        };
      } catch {
        return {
          symbol: meta.symbol,
          matches: [],
          calculatedAt,
          asOfDate,
          latestPriceDate: null,
          error: "此股票的策略行情暫時無法取得",
        };
      }
    }));
    return NextResponse.json(
      { items },
      { headers: { "Cache-Control": "private, max-age=300, stale-while-revalidate=900" } },
    );
  } catch {
    return NextResponse.json(
      { items: [], error: "AI選股策略比對暫時無法取得" },
      { status: 503, headers: { "Cache-Control": "no-store, max-age=0" } },
    );
  }
}
