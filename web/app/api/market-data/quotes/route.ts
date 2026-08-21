import { NextResponse } from "next/server";
import { backendJson } from "@/services/backend-client";
import type { ElectronicChipFlowQuote } from "@/lib/electronic-chip-flow-alerts";

interface QuoteRequestItem {
  symbol: string;
  name: string;
  market: "上市" | "上櫃";
}

function validItem(value: unknown): value is QuoteRequestItem {
  if (!value || typeof value !== "object") return false;
  const item = value as Partial<QuoteRequestItem>;
  return /^\d{4,6}$/.test(item.symbol ?? "")
    && typeof item.name === "string"
    && item.name.length > 0
    && (item.market === "上市" || item.market === "上櫃");
}

export async function POST(request: Request) {
  try {
    const body = await request.json() as { items?: unknown[] };
    const items = (body.items ?? []).filter(validItem).slice(0, 40);
    if (!items.length) return NextResponse.json({ items: [] });
    const payload = await backendJson<{ items: ElectronicChipFlowQuote[] }>(
      "/market-data/quotes",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items }),
      },
      12_000,
    );
    return NextResponse.json(payload, {
      headers: { "Cache-Control": "no-store, max-age=0" },
    });
  } catch {
    return NextResponse.json(
      { items: [], error: "即時行情暫時無法取得" },
      { status: 503, headers: { "Cache-Control": "no-store, max-age=0" } },
    );
  }
}
