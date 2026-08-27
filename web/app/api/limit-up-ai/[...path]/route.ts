import { NextRequest, NextResponse } from "next/server";
import { getBackendBaseUrl } from "@/lib/runtime-config";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function responsePayload(response: Response): Promise<unknown> {
  const payload = await response.clone().json().catch(async () => {
    const text = await response.text().catch(() => "");
    return text ? { error: text } : {};
  });
  return payload && typeof payload === "object" ? payload : { error: String(payload) };
}

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const target = `/limit-up-ai/${path.join("/")}${request.nextUrl.search}`;
  try {
    const body = ["GET", "HEAD"].includes(request.method) ? undefined : await request.text();
    const headers = new Headers({
      Accept: "application/json",
      "Content-Type": request.headers.get("content-type") ?? "application/json",
      "x-user-id": request.headers.get("x-user-id") ?? "demo-user",
    });
    const cookie = request.headers.get("cookie");
    const authorization = request.headers.get("authorization");
    if (cookie) headers.set("cookie", cookie);
    if (authorization) headers.set("authorization", authorization);
    const response = await fetch(`${getBackendBaseUrl()}/api/v1${target}`, {
      method: request.method,
      headers,
      body,
      cache: "no-store",
      signal: AbortSignal.timeout(30_000),
    });
    const payload = await responsePayload(response);
    if (!response.ok) {
      if (response.status === 401) {
        return NextResponse.json({ error: "請先登入後再使用漲停機器人。" }, { status: 401 });
      }
      return NextResponse.json(payload, { status: response.status });
    }
    return NextResponse.json(payload);
  } catch (error) {
    const message = error instanceof Error ? error.message : "專抓漲停飆股 AI 後端暫時無法連線";
    return NextResponse.json({ error: message }, { status: 503 });
  }
}

export const GET = proxy;
export const PUT = proxy;
export const POST = proxy;
