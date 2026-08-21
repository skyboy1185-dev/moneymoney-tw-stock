import { NextRequest, NextResponse } from "next/server";
import { getUserId, validHoldingInput } from "@/lib/portfolio-api";
import { clientKey, rateLimit } from "@/lib/server-utils";
import { addHolding, convertWatchToHolding, listHoldings, removeHolding } from "@/services/portfolio-service";
import { loadMarketSnapshot } from "@/services/scanner-worker-client";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function unauthorized() {
  return NextResponse.json({ error: "缺少有效的使用者識別。" }, { status: 401 });
}

export async function GET(request: NextRequest) {
  const userId = getUserId(request);
  if (!userId) return unauthorized();
  if (!rateLimit(`holding-get:${clientKey(request)}:${userId}`, 90).allowed) {
    return NextResponse.json({ error: "更新過於頻繁，請稍候再試。" }, { status: 429 });
  }
  return NextResponse.json({ items: await listHoldings(userId), updatedAt: new Date().toISOString() });
}

export async function POST(request: NextRequest) {
  const userId = getUserId(request);
  if (!userId) return unauthorized();
  const body: unknown = await request.json().catch(() => null);
  if (!validHoldingInput(body)) {
    return NextResponse.json({ error: "請輸入有效的成本、張數與買進日期。" }, { status: 400 });
  }
  if (body.fromWatchlist) {
    const result = convertWatchToHolding(userId, body.symbol, body.cost, body.lots, body.buyDate);
    const status = result === "created" ? 201 : result === "duplicate" ? 409 : 404;
    return NextResponse.json({ status: result }, { status });
  }
  const snapshot = await loadMarketSnapshot(true);
  const ranking = snapshot.rankings.find((item) => item.symbol === body.symbol);
  if (!ranking) return NextResponse.json({ error: "此股票目前不在 AI 選股結果中。" }, { status: 400 });
  const result = addHolding(userId, ranking, body.cost, body.lots, body.buyDate);
  return NextResponse.json({ status: result }, { status: result === "duplicate" ? 409 : 201 });
}

export async function DELETE(request: NextRequest) {
  const userId = getUserId(request);
  if (!userId) return unauthorized();
  const symbol = request.nextUrl.searchParams.get("symbol") ?? "";
  const removed = removeHolding(userId, symbol);
  return NextResponse.json({ removed }, { status: removed ? 200 : 404 });
}
