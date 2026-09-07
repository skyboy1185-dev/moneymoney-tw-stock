import { NextRequest, NextResponse } from "next/server";
import { getBackendBaseUrl } from "@/lib/runtime-config";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 20_000);
  try {
    const headers = new Headers({ "content-type": request.headers.get("content-type") ?? "application/json" });
    const userId = request.headers.get("x-user-id");
    if (userId) headers.set("x-user-id", userId);
    const response = await fetch(`${getBackendBaseUrl()}/api/v1/day-trading-v2/${path.join("/")}${request.nextUrl.search}`, {
      method: request.method,
      headers,
      body: ["GET", "HEAD"].includes(request.method) ? undefined : await request.text(),
      cache: "no-store",
      signal: controller.signal,
    });
    return new NextResponse(await response.arrayBuffer(), {
      status: response.status,
      headers: { "content-type": response.headers.get("content-type") ?? "application/json" },
    });
  } catch (error) {
    return NextResponse.json({ error: "當沖機器人2後端連線失敗", detail: error instanceof Error ? error.message : undefined }, { status: 503 });
  } finally {
    clearTimeout(timeout);
  }
}

export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
export const PUT = proxy;
