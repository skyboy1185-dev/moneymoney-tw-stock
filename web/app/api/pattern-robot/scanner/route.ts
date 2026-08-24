import { NextRequest, NextResponse } from "next/server";
import { verifyAdaptiveScannerToken } from "@/lib/private-site-auth";
import { buildPatternRobotScan } from "@/services/pattern-robot-scan-service";
import { proxyScannerRequest } from "@/services/scanner-worker-client";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 1800;

export async function GET(request: NextRequest) {
  if (!await verifyAdaptiveScannerToken(request.headers.get("x-adaptive-scanner-token"))) {
    return NextResponse.json({ error: "未授權的型態掃描請求" }, { status: 401 });
  }
  const proxied = await proxyScannerRequest(request);
  if (proxied) return proxied;
  try {
    return NextResponse.json(await buildPatternRobotScan());
  } catch (error) {
    console.error("pattern robot scan", error);
    return NextResponse.json({ error: "型態掃描行情來源暫時無法使用" }, { status: 503 });
  }
}
