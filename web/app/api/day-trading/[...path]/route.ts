import { NextRequest, NextResponse } from "next/server";
import { getBackendBaseUrl } from "@/lib/runtime-config";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function endpoint(request: NextRequest, path: string[]) {
  const backend = getBackendBaseUrl();
  return `${backend}/api/v1/day-trading/${path.join("/")}${request.nextUrl.search}`;
}

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const headers = new Headers();
  const userId = request.headers.get("x-user-id");
  if (userId) headers.set("x-user-id", userId);
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);
  const isBodyMethod = !["GET", "HEAD"].includes(request.method);
  try {
    const response = await fetch(endpoint(request, path), {
      method: request.method,
      headers,
      body: isBodyMethod ? await request.text() : undefined,
      cache: "no-store",
      signal: request.signal,
    });
    if (path.at(-1) === "stream") {
      return new Response(response.body, {
        status: response.status,
        headers: {
          "Content-Type": "text/event-stream",
          "Cache-Control": "no-cache, no-transform",
          "X-Accel-Buffering": "no",
          Connection: "keep-alive",
        },
      });
    }
    return new NextResponse(response.body, {
      status: response.status,
      headers: { "Content-Type": response.headers.get("content-type") ?? "application/json" },
    });
  } catch {
    return NextResponse.json({ error: "當沖機器人後端暫時無法連線" }, { status: 503 });
  }
}

export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
export const PUT = proxy;
