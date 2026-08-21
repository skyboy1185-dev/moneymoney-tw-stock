import { NextRequest, NextResponse } from "next/server";
import { buildMarketSnapshot } from "@/services/market-snapshot-service";
import { clientKey, rateLimit } from "@/lib/server-utils";
import { proxyScannerRequest } from "@/services/scanner-worker-client";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  if (!rateLimit(`ai:${clientKey(request)}`, 120).allowed) {
    return NextResponse.json({ error: "更新過於頻繁，請稍候再試。" }, { status: 429 });
  }
  const proxied = await proxyScannerRequest(request);
  if (proxied) return proxied;
  const autoMode = request.nextUrl.searchParams.get("auto") !== "0";
  const forceRefresh = request.nextUrl.searchParams.get("refresh") === "1";
  return NextResponse.json(await buildMarketSnapshot(autoMode, forceRefresh));
}
