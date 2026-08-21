import { NextRequest, NextResponse } from "next/server";
import { backendJson, BackendUnavailableError } from "@/services/backend-client";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 180;

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const target = `/long-term/${path.join("/")}${request.nextUrl.search}`;
  try {
    const body = ["GET", "HEAD"].includes(request.method) ? undefined : await request.text();
    const payload = await backendJson<unknown>(target, {
      method: request.method,
      headers: { "Content-Type": request.headers.get("content-type") ?? "application/json" },
      body,
    }, path[0] === "backtest" ? 150_000 : 20_000);
    return NextResponse.json(payload);
  } catch (error) {
    const message = error instanceof BackendUnavailableError
      ? "長線選股服務暫時無法連線，請稍後再試。"
      : error instanceof Error ? error.message : "長線選股操作失敗。";
    return NextResponse.json({ error: message }, { status: 503 });
  }
}

export const GET = proxy;
export const POST = proxy;
