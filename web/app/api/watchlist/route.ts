import { NextRequest, NextResponse } from "next/server";
import { getUserId } from "@/lib/portfolio-api";
import { clientKey, rateLimit } from "@/lib/server-utils";
import { addWatchlist, addWatchlistSnapshot, listWatchlist, removeWatchlist } from "@/services/portfolio-service";
import { buildMarketSnapshot } from "@/services/market-snapshot-service";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function unauthorized() {
  return NextResponse.json({ error: "缺少有效的使用者識別。" }, { status: 401 });
}

export async function GET(request: NextRequest) {
  const userId = getUserId(request);
  if (!userId) return unauthorized();
  if (!rateLimit(`watch-get:${clientKey(request)}:${userId}`, 90).allowed) {
    return NextResponse.json({ error: "更新過於頻繁，請稍候再試。" }, { status: 429 });
  }
  return NextResponse.json({ items: await listWatchlist(userId), updatedAt: new Date().toISOString() });
}

export async function POST(request: NextRequest) {
  const userId = getUserId(request);
  if (!userId) return unauthorized();
  const body = await request.json().catch(() => null) as {
    symbol?: string;
    name?: string;
    price?: number;
    score?: number;
    source?: string;
    reasons?: string[];
  } | null;
  if (!body?.symbol || !/^\d{4,6}$/.test(body.symbol)) {
    return NextResponse.json({ error: "股票代號格式錯誤。" }, { status: 400 });
  }
  if (body.source === "large-holder") {
    if (!body.name || !Number.isFinite(body.price) || Number(body.price) <= 0) {
      return NextResponse.json({ error: "大戶榜自選資料不完整。" }, { status: 400 });
    }
    const result = addWatchlistSnapshot(userId, {
      symbol: body.symbol,
      name: body.name,
      price: Number(body.price),
      score: Number.isFinite(body.score) ? Number(body.score) : 0,
      sourceName: "大戶持股增加榜",
      reasons: body.reasons ?? ["大戶持股比例本週增加"],
    });
    return NextResponse.json({ status: result }, { status: result === "duplicate" ? 409 : 201 });
  }
  const snapshot = await buildMarketSnapshot(true);
  const ranking = snapshot.rankings.find((item) => item.symbol === body.symbol);
  if (!ranking) return NextResponse.json({ error: "此股票目前不在 AI 選股結果中。" }, { status: 400 });
  const result = addWatchlist(userId, ranking);
  return NextResponse.json({ status: result }, { status: result === "duplicate" ? 409 : 201 });
}

export async function DELETE(request: NextRequest) {
  const userId = getUserId(request);
  if (!userId) return unauthorized();
  const symbol = request.nextUrl.searchParams.get("symbol") ?? "";
  const removed = removeWatchlist(userId, symbol);
  return NextResponse.json({ removed }, { status: removed ? 200 : 404 });
}
