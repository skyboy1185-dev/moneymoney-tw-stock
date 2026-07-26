import { NextRequest, NextResponse } from "next/server";
import { clientKey, rateLimit } from "@/lib/server-utils";
import { getOfficialQuote } from "@/services/market-data/official-quote-provider";
import { buildOfficialStockPayload } from "@/services/market-data/official-history-provider";
import { resolveOfficialStock } from "@/services/market-data/stock-directory";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  if (!rateLimit(`stock:${clientKey(request)}`, 120).allowed) {
    return NextResponse.json({ error: "查詢過於頻繁，請稍候再試。" }, { status: 429 });
  }
  const query = request.nextUrl.searchParams.get("q")?.trim();
  if (!query) {
    return NextResponse.json({ error: "請輸入股票代號或名稱。" }, { status: 400 });
  }
  const meta = await resolveOfficialStock(query);
  if (!meta) {
    return NextResponse.json({ error: `找不到「${query}」，請確認股票代號或名稱。` }, { status: 404 });
  }
  try {
    const officialQuote = await getOfficialQuote(meta);
    const payload = await buildOfficialStockPayload(meta, officialQuote);
    return NextResponse.json(payload);
  } catch (error) {
    return NextResponse.json({
      error: "官方歷史行情暫時無法取得；為避免顯示錯誤技術指標，本頁不會改用模擬 K 線。",
      detail: error instanceof Error ? error.message : "unknown",
    }, { status: 503 });
  }
}
