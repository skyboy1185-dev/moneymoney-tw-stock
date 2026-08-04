import { NextRequest } from "next/server";
import { backendJson } from "@/services/backend-client";
import type { ChipFlowResponse } from "@/lib/chip-flow-types";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
const ACTIVE_REFRESH_MS = 2_000;

function isTaipeiMarketSession() {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Taipei",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(new Date());
  const weekday = parts.find((part) => part.type === "weekday")?.value;
  const hour = Number(parts.find((part) => part.type === "hour")?.value ?? 0);
  const minute = Number(parts.find((part) => part.type === "minute")?.value ?? 0);
  const value = hour * 60 + minute;
  return !["Sat", "Sun"].includes(weekday ?? "") && value >= 540 && value <= 810;
}

export async function GET(
  _request: NextRequest,
  context: { params: Promise<{ symbol: string }> },
) {
  const { symbol } = await context.params;
  const encoder = new TextEncoder();
  let timer: ReturnType<typeof setTimeout> | undefined;
  let closed = false;
  const stream = new ReadableStream({
    async start(controller) {
      const send = async () => {
        if (closed) return;
        let payload: Partial<ChipFlowResponse>;
        try {
          payload = await backendJson<ChipFlowResponse>(
            `/stocks/${encodeURIComponent(symbol)}/chip-flow/intraday`,
            undefined,
            8_000,
          );
        } catch (error) {
          payload = {
            stockId: symbol,
            status: "disconnected",
            latest: null,
            series: [],
            statusMessage: error instanceof Error
              ? `盤中籌碼服務暫時無法連線：${error.message}`
              : "盤中籌碼服務暫時無法連線。",
          };
        }
        controller.enqueue(encoder.encode(
          `event: CHIP_FLOW_UPDATE\ndata: ${JSON.stringify(payload)}\n\n`,
        ));
        if (!isTaipeiMarketSession()) {
          controller.enqueue(encoder.encode(
            `event: CHIP_FLOW_END\ndata: ${JSON.stringify({ stockId: symbol, reason: "market_closed" })}\n\n`,
          ));
          closed = true;
          controller.close();
          return;
        }
        const nextDelay = payload.status === "realtime" || payload.status === "no_data"
          ? ACTIVE_REFRESH_MS
          : payload.status === "disconnected"
            ? 5_000
            : 30_000;
        timer = setTimeout(send, nextDelay);
      };
      controller.enqueue(encoder.encode("retry: 2000\n\n"));
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
