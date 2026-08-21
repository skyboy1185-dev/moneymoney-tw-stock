import { NextRequest, NextResponse } from "next/server";
import { verifyAdaptiveScannerToken } from "@/lib/private-site-auth";
import { buildRocketRadarScan } from "@/services/adaptive-electronic-service";
import { proxyScannerRequest } from "@/services/scanner-worker-client";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  if (!await verifyAdaptiveScannerToken(request.headers.get("x-adaptive-scanner-token"))) {
    return NextResponse.json({ error: "掃描服務驗證失敗" }, { status: 401 });
  }
  const proxied = await proxyScannerRequest(request);
  if (proxied) return proxied;
  try {
    return NextResponse.json(await buildRocketRadarScan());
  } catch (error) {
    console.error("rocket radar scan", error);
    return NextResponse.json({ error: "飆股雷達掃描暫時無法完成" }, { status: 503 });
  }
}
