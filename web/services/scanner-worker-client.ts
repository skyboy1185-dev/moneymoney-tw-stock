import type { MarketSnapshot } from "@/lib/market-types";
import { buildMarketSnapshot } from "@/services/market-snapshot-service";
import { getScannerWorkerUrl } from "@/lib/runtime-config";

export function isScannerWorker() {
  return process.env.APP_ROLE === "scanner";
}

function workerHeaders(request?: Request) {
  const headers = new Headers(request?.headers);
  headers.delete("host");
  headers.delete("connection");
  headers.delete("content-length");
  headers.set("accept", "application/json");
  const scannerToken = process.env.ADAPTIVE_ELECTRONIC_SCANNER_TOKEN;
  if (scannerToken) headers.set("x-adaptive-scanner-token", scannerToken);
  return headers;
}

export async function proxyScannerRequest(request: Request): Promise<Response | null> {
  const workerUrl = getScannerWorkerUrl();
  if (!workerUrl || isScannerWorker()) return null;
  const source = new URL(request.url);
  const encoder = new TextEncoder();
  let heartbeat: ReturnType<typeof setInterval> | undefined;
  let closed = false;
  const stream = new ReadableStream({
    start(controller) {
      const enqueue = (value: string) => {
        if (closed) return false;
        try {
          controller.enqueue(encoder.encode(value));
          return true;
        } catch {
          closed = true;
          if (heartbeat) clearInterval(heartbeat);
          return false;
        }
      };
      // Leading JSON whitespace is valid and prevents Railway from considering
      // a long whole-market scan to be an idle/dead upstream request.
      enqueue(" \n");
      heartbeat = setInterval(() => enqueue(" \n"), 5_000);
      void fetch(`${workerUrl}${source.pathname}${source.search}`, {
        method: request.method,
        headers: workerHeaders(request),
        cache: "no-store",
        signal: AbortSignal.timeout(900_000),
      }).then(async (response) => {
        const payload = await response.text();
        if (!enqueue(payload)) return;
        closed = true;
        if (heartbeat) clearInterval(heartbeat);
        try { controller.close(); } catch { /* caller already disconnected */ }
      }).catch((error) => {
        enqueue(JSON.stringify({
          error: error instanceof Error ? error.message : "Scanner worker unavailable",
        }));
        closed = true;
        if (heartbeat) clearInterval(heartbeat);
        try { controller.close(); } catch { /* caller already disconnected */ }
      });
    },
    cancel() {
      closed = true;
      if (heartbeat) clearInterval(heartbeat);
    },
  });
  return new Response(stream, {
    status: 200,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
      "X-Accel-Buffering": "no",
    },
  });
}

export async function loadMarketSnapshot(
  autoMode = true,
  forceRefresh = false,
): Promise<MarketSnapshot> {
  const workerUrl = getScannerWorkerUrl();
  if (!workerUrl || isScannerWorker()) {
    return buildMarketSnapshot(autoMode, forceRefresh);
  }
  const query = new URLSearchParams({
    auto: autoMode ? "1" : "0",
    ...(forceRefresh ? { refresh: "1" } : {}),
  });
  const response = await fetch(`${workerUrl}/api/ai?${query}`, {
    headers: workerHeaders(),
    cache: "no-store",
    signal: AbortSignal.timeout(900_000),
  });
  if (!response.ok) throw new Error(`Scanner worker ${response.status}`);
  return response.json() as Promise<MarketSnapshot>;
}
