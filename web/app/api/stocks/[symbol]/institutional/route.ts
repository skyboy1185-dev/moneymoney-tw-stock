import { NextRequest, NextResponse } from "next/server";
import { clientKey, rateLimit } from "@/lib/server-utils";
import { resolveOfficialStock } from "@/services/market-data/stock-directory";
import { getOfficialStockInstitutionalFlow } from "@/services/market-data/official-stock-institutional-provider";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type RouteContext = { params: Promise<{ symbol: string }> };

export async function GET(request: NextRequest, context: RouteContext) {
  const { symbol } = await context.params;
  if (!rateLimit(`stock-institutional:${clientKey(request)}`, 120).allowed) {
    return NextResponse.json({ error: "查詢過於頻繁，請稍候再試。" }, { status: 429 });
  }
  const meta = await resolveOfficialStock(symbol);
  if (!meta) return NextResponse.json({ error: `找不到股票代號 ${symbol}。` }, { status: 404 });
  try {
    return NextResponse.json(await getOfficialStockInstitutionalFlow(meta));
  } catch (reason) {
    console.error("stock-institutional", meta.symbol, reason);
    return NextResponse.json({
      error: reason instanceof Error ? reason.message : "個股三大法人資料暫時無法取得。",
    }, { status: 503 });
  }
}
