import { NextRequest } from "next/server";
import { loadMarketSnapshot } from "@/services/scanner-worker-client";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const autoMode = request.nextUrl.searchParams.get("auto") !== "0";
  const encoder = new TextEncoder();
  let timer: ReturnType<typeof setTimeout> | undefined;
  let closed = false;

  const stream = new ReadableStream({
    start(controller) {
      const stop = () => {
        if (closed) return;
        closed = true;
        if (timer) clearTimeout(timer);
      };
      const enqueue = (payload: string) => {
        if (closed) return false;
        try {
          controller.enqueue(encoder.encode(payload));
          return true;
        } catch {
          // The browser can disconnect while a market refresh is awaiting I/O.
          // Treat a closed controller as a normal cancellation, not a process error.
          stop();
          return false;
        }
      };
      const schedule = (callback: () => void, delay: number) => {
        if (!closed) timer = setTimeout(callback, delay);
      };
      const send = async () => {
        if (closed) return;
        try {
          // Use the shared cache/single-flight refresh instead of forcing one
          // complete scan for every connected browser tab.
          const snapshot = await loadMarketSnapshot(autoMode);
          if (!enqueue(`event: market\ndata: ${JSON.stringify(snapshot)}\n\n`)) return;
          const interval = snapshot.marketOpen
            ? Math.max(30, Number(process.env.MARKET_FORCE_REFRESH_SECONDS ?? 30)) * 1000
            : snapshot.futuresMarketOpen
              ? Math.max(30, Number(process.env.FUTURES_REFRESH_SECONDS ?? 30)) * 1000
              : Number(process.env.MARKET_SCAN_SECONDS ?? 60) * 1000;
          schedule(() => void send(), interval);
        } catch {
          if (!enqueue(`event: error\ndata: ${JSON.stringify({ message: "資料更新失敗，系統將自動重試" })}\n\n`)) return;
          schedule(() => void send(), 15_000);
        }
      };

      request.signal.addEventListener("abort", stop, { once: true });
      enqueue("retry: 5000\n\n");
      if (request.signal.aborted) stop();
      else void send();
    },
    cancel() {
      closed = true;
      if (timer) clearTimeout(timer);
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      "Connection": "keep-alive",
      "X-Accel-Buffering": "no",
    },
  });
}
