import { NextRequest, NextResponse } from "next/server";
import { backendJson } from "@/services/backend-client";
import type { ChipFlowResponse } from "@/lib/chip-flow-types";

export const dynamic = "force-dynamic";

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ symbol: string }> },
) {
  const { symbol } = await context.params;
  if (!/^\d{4,6}$/.test(symbol)) {
    return NextResponse.json({
      stockId: symbol,
      status: "invalid_symbol",
      latest: null,
      series: [],
      statusMessage: "股票代號格式不正確。",
    }, { status: 400 });
  }
  const date = request.nextUrl.searchParams.get("date");
  const suffix = date ? `?date=${encodeURIComponent(date)}` : "";
  try {
    const payload = await backendJson<ChipFlowResponse>(
      `/stocks/${encodeURIComponent(symbol)}/chip-flow/intraday${suffix}`,
      undefined,
      8_000,
    );
    return NextResponse.json(payload, {
      headers: { "Cache-Control": "no-store, max-age=0" },
    });
  } catch (error) {
    return NextResponse.json({
      stockId: symbol,
      status: "disconnected",
      latest: null,
      series: [],
      statusMessage: error instanceof Error
        ? `盤中籌碼服務暫時無法連線：${error.message}`
        : "盤中籌碼服務暫時無法連線。",
    }, { status: 503 });
  }
}
