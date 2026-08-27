import { NextRequest, NextResponse } from "next/server";
import { getBackendBaseUrl } from "@/lib/runtime-config";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
const DAY_TRADING_PROXY_TIMEOUT_MS = 15_000;
const SHAREABLE_GET_PATHS = new Set([
  "market-regime",
  "signals/today",
  "rankings",
  "candidate-replay/today",
]);
const CACHEABLE_GET_PATHS = new Set([
  ...SHAREABLE_GET_PATHS,
  "signals",
  "performance",
]);

type CachedProxyResponse = {
  body: ArrayBuffer;
  contentType: string;
  expiresAt: number;
  status: number;
};

const readCache = new Map<string, CachedProxyResponse>();
const readInFlight = new Map<string, Promise<CachedProxyResponse>>();

function endpoint(request: NextRequest, path: string[]) {
  const backend = getBackendBaseUrl();
  return `${backend}/api/v1/day-trading/${path.join("/")}${request.nextUrl.search}`;
}

function proxyAbortSignal(request: NextRequest): { signal: AbortSignal; cleanup: () => void } {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), DAY_TRADING_PROXY_TIMEOUT_MS);
  const abort = () => controller.abort();
  if (request.signal.aborted) abort();
  else request.signal.addEventListener("abort", abort, { once: true });
  return {
    signal: controller.signal,
    cleanup: () => {
      clearTimeout(timeout);
      request.signal.removeEventListener("abort", abort);
    },
  };
}

function readCacheTtlMs(pathKey: string): number {
  if (pathKey === "rankings") return 5_000;
  return 2_000;
}

function readCacheKey(request: NextRequest, pathKey: string, userId: string | null): string {
  const userScope = SHAREABLE_GET_PATHS.has(pathKey) ? "shared" : userId ?? "";
  return `${pathKey}${request.nextUrl.search}::${userScope}`;
}

async function fetchBuffered(
  request: NextRequest,
  path: string[],
  headers: Headers,
  body: string | undefined,
  signal: AbortSignal,
): Promise<CachedProxyResponse> {
  const response = await fetch(endpoint(request, path), {
    method: request.method,
    headers,
    body,
    cache: "no-store",
    signal,
  });
  return {
    body: await response.arrayBuffer(),
    contentType: response.headers.get("content-type") ?? "application/json",
    expiresAt: 0,
    status: response.status,
  };
}

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const headers = new Headers();
  const userId = request.headers.get("x-user-id");
  if (userId) headers.set("x-user-id", userId);
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);
  const isBodyMethod = !["GET", "HEAD"].includes(request.method);
  const isStream = path.at(-1) === "stream";
  const proxySignal = isStream ? null : proxyAbortSignal(request);
  try {
    const requestBody = isBodyMethod ? await request.text() : undefined;
    if (isStream) {
      const response = await fetch(endpoint(request, path), {
        method: request.method,
        headers,
        body: requestBody,
        cache: "no-store",
        signal: request.signal,
      });
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
    const pathKey = path.join("/");
    if (request.method === "GET" && CACHEABLE_GET_PATHS.has(pathKey)) {
      const cacheKey = readCacheKey(request, pathKey, userId);
      const cached = readCache.get(cacheKey);
      if (cached && cached.expiresAt > Date.now()) {
        return new NextResponse(cached.body.slice(0), {
          status: cached.status,
          headers: { "Content-Type": cached.contentType, "X-Day-Trading-Proxy-Cache": "hit" },
        });
      }
      const inFlight = readInFlight.get(cacheKey);
      if (inFlight) {
        const shared = await inFlight;
        return new NextResponse(shared.body.slice(0), {
          status: shared.status,
          headers: { "Content-Type": shared.contentType, "X-Day-Trading-Proxy-Cache": "shared" },
        });
      }
      const promise = fetchBuffered(request, path, headers, requestBody, proxySignal?.signal ?? request.signal);
      readInFlight.set(cacheKey, promise);
      try {
        const fresh = await promise;
        if (fresh.status < 500) {
          fresh.expiresAt = Date.now() + readCacheTtlMs(pathKey);
          readCache.set(cacheKey, fresh);
          if (readCache.size > 128) readCache.clear();
        }
        return new NextResponse(fresh.body.slice(0), {
          status: fresh.status,
          headers: { "Content-Type": fresh.contentType, "X-Day-Trading-Proxy-Cache": "miss" },
        });
      } finally {
        readInFlight.delete(cacheKey);
      }
    }
    const fresh = await fetchBuffered(request, path, headers, requestBody, proxySignal?.signal ?? request.signal);
    return new NextResponse(fresh.body, {
      status: fresh.status,
      headers: { "Content-Type": fresh.contentType },
    });
  } catch (error) {
    return NextResponse.json({
      error: "當沖後端暫時無法連線，核心狀態會在恢復後自動更新。",
      detail: error instanceof Error ? error.message : undefined,
    }, { status: 503 });
  } finally {
    proxySignal?.cleanup();
  }
}

export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
export const PUT = proxy;
