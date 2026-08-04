import { NextRequest, NextResponse } from "next/server";
import { clientKey, rateLimit } from "@/lib/server-utils";
import { resolveOfficialStock } from "@/services/market-data/stock-directory";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  if (!rateLimit(`stock-lookup:${clientKey(request)}`, 120).allowed) {
    return NextResponse.json({ error: "查詢過於頻繁，請稍候再試。" }, { status: 429 });
  }
  const query = request.nextUrl.searchParams.get("q")?.trim();
  if (!query) {
    return NextResponse.json({ error: "請輸入股票代號或名稱。" }, { status: 400 });
  }
  const stock = await resolveOfficialStock(query);
  if (!stock) {
    return NextResponse.json(
      { error: `找不到「${query}」，請確認股票代號或名稱。` },
      { status: 404 },
    );
  }
  return NextResponse.json({
    symbol: stock.symbol,
    name: stock.name,
    market: stock.market,
  }, {
    headers: { "Cache-Control": "public, max-age=3600, stale-while-revalidate=21600" },
  });
}
