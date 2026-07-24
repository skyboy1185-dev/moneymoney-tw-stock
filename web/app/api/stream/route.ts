import { NextRequest } from "next/server";
import { buildMarketSnapshot } from "@/services/market-snapshot-service";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const autoMode = request.nextUrl.searchParams.get("auto") !== "0";
  const encoder = new TextEncoder();
  let timer: ReturnType<typeof setTimeout> | undefined;
  let closed = false;
  const stream = new ReadableStream({
    async start(controller) {
      const send = async () => {
        if (closed) return;
        try {
          const snapshot = await buildMarketSnapshot(autoMode, true);
          controller.enqueue(encoder.encode(`event: market\ndata: ${JSON.stringify(snapshot)}\n\n`));
          const interval = snapshot.marketOpen
            ? Number(process.env.MARKET_FORCE_REFRESH_SECONDS ?? 10) * 1000
            : snapshot.futuresMarketOpen
              ? Math.max(30, Number(process.env.FUTURES_REFRESH_SECONDS ?? 30)) * 1000
              : Number(process.env.MARKET_SCAN_SECONDS ?? 60) * 1000;
          timer = setTimeout(send, interval);
        } catch {
          controller.enqueue(encoder.encode(`event: error\ndata: ${JSON.stringify({ message: "資料更新失敗" })}\n\n`));
          timer = setTimeout(send, 15_000);
        }
      };
      await send();
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
