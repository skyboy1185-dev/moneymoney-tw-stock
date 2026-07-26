import { NextRequest, NextResponse } from "next/server";
import { getUserId } from "@/lib/portfolio-api";
import { BackendUnavailableError, backendJson } from "@/services/backend-client";
import { clientKey, rateLimit } from "@/lib/server-utils";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type RouteContext = { params: Promise<{ path: string[] }> };

async function proxy(request: NextRequest, context: RouteContext) {
  const { path } = await context.params;
  const suffix = path.map(encodeURIComponent).join("/");
  const search = request.nextUrl.search;
  const userId = getUserId(request);
  if (!rateLimit(`large-holders:${clientKey(request)}:${userId ?? "public"}`, 120).allowed) {
    return NextResponse.json({ error: "更新過於頻繁，請稍候再試。" }, { status: 429 });
  }
  if (request.method !== "GET" && !userId) {
    return NextResponse.json({ error: "缺少有效的使用者識別。" }, { status: 401 });
  }
  const body = request.method === "GET" || request.method === "DELETE"
    ? undefined
    : await request.text();
  try {
    const payload = await backendJson<unknown>(`/large-holders/${suffix}${search}`, {
      method: request.method,
      body,
      headers: {
        ...(body ? { "Content-Type": "application/json" } : {}),
        ...(userId ? { "x-user-id": userId } : {}),
      },
    }, request.nextUrl.searchParams.get("refresh") === "true" || suffix === "sync" ? 90_000 : 12_000);
    return request.method === "DELETE"
      ? new NextResponse(null, { status: 204 })
      : NextResponse.json(payload, { status: request.method === "POST" ? 201 : 200 });
  } catch (error) {
    const message = error instanceof Error ? error.message : "大戶持股服務暫時無法連線";
    return NextResponse.json(
      { error: message },
      { status: error instanceof BackendUnavailableError ? 503 : 400 },
    );
  }
}

export const GET = proxy;
export const POST = proxy;
export const DELETE = proxy;
