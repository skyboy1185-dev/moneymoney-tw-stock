import { NextRequest, NextResponse } from "next/server";
import { payloadFor, stockService } from "@/services/stock-service";
import { clientKey, rateLimit } from "@/lib/server-utils";
import { getOfficialQuote, mergeOfficialQuote } from "@/services/market-data/official-quote-provider";
import { resolveOfficialStock } from "@/services/market-data/stock-directory";
import { getBackendStock } from "@/services/backend-client";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  if (!rateLimit(`stock:${clientKey(request)}`, 120).allowed) {
    return NextResponse.json({ error: "查詢過於頻繁，請稍候再試。" }, { status: 429 });
  }
  const query = request.nextUrl.searchParams.get("q")?.trim();
  if (!query) {
    return NextResponse.json({ error: "請輸入股票代號或名稱。" }, { status: 400 });
  }
  const backendPayload = await getBackendStock(query).catch(() => null);
  const meta = backendPayload?.meta
    ?? await stockService.search(query)
    ?? await resolveOfficialStock(query);
  if (!meta) {
    return NextResponse.json({ error: `找不到「${query}」，請確認股票代號或名稱。` }, { status: 404 });
  }
  const payload = backendPayload
    ?? await stockService.getStock(meta.symbol)
    ?? payloadFor(meta);
  if (!payload) {
    return NextResponse.json({ error: "個股資料暫時無法取得。" }, { status: 503 });
  }
  const officialQuote = await getOfficialQuote(meta);
  return NextResponse.json(officialQuote ? mergeOfficialQuote(payload, officialQuote) : {
    ...payload,
    dataMode: "demo",
    dataNotice: "官方報價暫時無法取得，目前顯示展示模式／模擬資料。",
  });
}
