import { NextRequest, NextResponse } from "next/server";
import { MANUAL_STRATEGIES, screenStocksByStrategy } from "@/services/manual-strategy-service";
import { clientKey, rateLimit } from "@/lib/server-utils";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  if (!rateLimit(`manual:${clientKey(request)}`, 60).allowed) {
    return NextResponse.json({ error: "查詢過於頻繁，請稍候再試。" }, { status: 429 });
  }
  const strategyId = request.nextUrl.searchParams.get("strategy") ?? MANUAL_STRATEGIES[0].id;
  try {
    const rows = await screenStocksByStrategy(strategyId);
    return NextResponse.json({ mode: "official", strategies: MANUAL_STRATEGIES, strategyId, rows, total: rows.length, updatedAt: new Date().toISOString() });
  } catch (error) {
    return NextResponse.json({ error: error instanceof Error ? error.message : "選股服務暫時無法使用。" }, { status: 400 });
  }
}
