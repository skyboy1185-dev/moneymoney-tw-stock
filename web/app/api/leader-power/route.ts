import { NextRequest, NextResponse } from "next/server";
import { clientKey, rateLimit } from "@/lib/server-utils";
import { buildLeaderPowerResponse } from "@/services/leader-power-service";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  if (!rateLimit(`leader-power:${clientKey(request)}`, 60).allowed) {
    return NextResponse.json({ error: "更新過於頻繁，請稍候再試。" }, { status: 429 });
  }
  try {
    return NextResponse.json(await buildLeaderPowerResponse());
  } catch {
    return NextResponse.json({ error: "前 15 大權值股馬力資料暫時無法取得。" }, { status: 503 });
  }
}
