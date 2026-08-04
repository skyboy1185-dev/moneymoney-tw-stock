import { NextRequest, NextResponse } from "next/server";
import { clientKey, rateLimit } from "@/lib/server-utils";
import {
  buildLeaderPowerResponse,
  type LeaderPowerView,
} from "@/services/leader-power-service";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  if (!rateLimit(`leader-power:${clientKey(request)}`, 60).allowed) {
    return NextResponse.json({ error: "更新過於頻繁，請稍候再試。" }, { status: 429 });
  }
  const requestedView = request.nextUrl.searchParams.get("view");
  const view: LeaderPowerView = requestedView === "electronics"
    ? "electronics"
    : "weighted";
  try {
    return NextResponse.json(await buildLeaderPowerResponse(view));
  } catch {
    return NextResponse.json({ error: `${view === "electronics" ? "電子股" : "權值股"}馬力資料暫時無法取得。` }, { status: 503 });
  }
}
