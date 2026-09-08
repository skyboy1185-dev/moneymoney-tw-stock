import { NextRequest, NextResponse } from "next/server";
import { getBackendBaseUrl } from "@/lib/runtime-config";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const userId = request.headers.get("x-user-id");
  if (!userId || userId.length < 8 || userId.length > 80) return NextResponse.json({ error: "無法確認目前使用者" }, { status: 400 });
  try {
    const response = await fetch(`${getBackendBaseUrl()}/api/v1/robot-health`, {
      headers: { "x-user-id": userId }, cache: "no-store", signal: AbortSignal.timeout(20_000),
    });
    return new NextResponse(await response.arrayBuffer(), {
      status: response.status,
      headers: { "content-type": response.headers.get("content-type") ?? "application/json", "cache-control": "no-store" },
    });
  } catch {
    return NextResponse.json({ error: "機器人狀態暫時無法讀取" }, { status: 503, headers: { "cache-control": "no-store" } });
  }
}
