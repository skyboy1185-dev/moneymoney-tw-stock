import { NextResponse } from "next/server";
import { findDeductionSignalMatches } from "@/lib/deduction-signals";
import type { StockDeductionSignals } from "@/lib/deduction-signals";
import type { StockMeta } from "@/lib/types";
import { calculateThreeGatePrice } from "@/lib/three-gate-price";
import { getOfficialDeductionHistory, mergeOfficialHistoryWithQuote } from "@/services/market-data/official-history-provider";
import { getOfficialQuotes } from "@/services/market-data/official-quote-provider";

interface DeductionRequestItem {
  symbol: string;
  name: string;
  market: "上市" | "上櫃";
}

function validItem(value: unknown): value is DeductionRequestItem {
  if (!value || typeof value !== "object") return false;
  const item = value as Partial<DeductionRequestItem>;
  return /^\d{4,6}$/.test(item.symbol ?? "")
    && typeof item.name === "string"
    && item.name.length > 0
    && (item.market === "上市" || item.market === "上櫃");
}

function toMeta(item: DeductionRequestItem): StockMeta {
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
    const body = await request.json() as { items?: unknown[] };
    const items = (body.items ?? []).filter(validItem).slice(0, 24);
    if (!items.length) return NextResponse.json({ items: [] });
    const metas = items.map(toMeta);
    const quotes = await getOfficialQuotes(metas);
    const calculatedAt = new Date().toISOString();
    const results = await Promise.all(metas.map(async (meta): Promise<StockDeductionSignals | null> => {
      try {
        const quote = quotes.get(meta.symbol) ?? null;
        const prices = mergeOfficialHistoryWithQuote(
          await getOfficialDeductionHistory(meta),
          meta,
          quote,
        );
        return {
          symbol: meta.symbol,
          currentPrice: prices.at(-1)?.close ?? null,
          previousClose: quote?.previousClose ?? prices.at(-2)?.close ?? null,
          threeGate: calculateThreeGatePrice(prices, quote?.isRealtime ?? false),
          matches: findDeductionSignalMatches(prices),
          calculatedAt,
        };
      } catch {
        return null;
      }
    }));
    return NextResponse.json(
      { items: results.filter((item): item is StockDeductionSignals => item !== null) },
      { headers: { "Cache-Control": "private, max-age=300, stale-while-revalidate=900" } },
    );
  } catch {
    return NextResponse.json(
      { items: [], error: "均線扣抵訊號暫時無法取得" },
      { status: 503, headers: { "Cache-Control": "no-store, max-age=0" } },
    );
  }
}
