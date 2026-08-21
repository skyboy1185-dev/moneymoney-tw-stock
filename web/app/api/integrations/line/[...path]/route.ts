import { NextRequest, NextResponse } from "next/server";
import { getBackendBaseUrl } from "@/lib/runtime-config";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const backend = getBackendBaseUrl();
  const endpoint = `${backend}/api/v1/integrations/line/${path.join("/")}${request.nextUrl.search}`;
  const hasBody = !["GET", "HEAD"].includes(request.method);
  try {
    const response = await fetch(endpoint, {
      method: request.method,
      headers: { "Content-Type": request.headers.get("content-type") ?? "application/json" },
      body: hasBody ? await request.text() : undefined,
      cache: "no-store",
      signal: request.signal,
    });
    return new NextResponse(response.body, {
      status: response.status,
      headers: { "Content-Type": response.headers.get("content-type") ?? "application/json" },
    });
  } catch {
    return NextResponse.json({ error: "LINE 通知後端暫時無法連線" }, { status: 503 });
  }
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const DELETE = proxy;
