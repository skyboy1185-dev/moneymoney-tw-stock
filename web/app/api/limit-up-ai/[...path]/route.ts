import { NextRequest, NextResponse } from "next/server";
import { backendJson, BackendUnavailableError } from "@/services/backend-client";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const target = `/limit-up-ai/${path.join("/")}${request.nextUrl.search}`;
  try {
    const body = ["GET", "HEAD"].includes(request.method) ? undefined : await request.text();
    const payload = await backendJson<unknown>(target, {
      method: request.method,
      headers: {
        "Content-Type": request.headers.get("content-type") ?? "application/json",
        "x-user-id": request.headers.get("x-user-id") ?? "",
      },
      body,
    }, 30_000);
    return NextResponse.json(payload);
  } catch (error) {
    const message = error instanceof BackendUnavailableError
      ? "專抓漲停飆股 AI 後端暫時無法連線"
      : error instanceof Error ? error.message : "專抓漲停飆股 AI 資料讀取失敗";
    return NextResponse.json({ error: message }, { status: 503 });
  }
}

export const GET = proxy;
export const PUT = proxy;
export const POST = proxy;
