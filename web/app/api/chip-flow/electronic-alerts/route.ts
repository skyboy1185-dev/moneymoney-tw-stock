import { NextResponse } from "next/server";
import type { ElectronicChipFlowAlertsResponse } from "@/lib/electronic-chip-flow-alerts";
import { backendJson } from "@/services/backend-client";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const payload = await backendJson<ElectronicChipFlowAlertsResponse>(
      "/stocks/chip-flow/electronic-alerts",
      undefined,
      8_000,
    );
    return NextResponse.json(payload, {
      headers: { "Cache-Control": "no-store, max-age=0" },
    });
  } catch {
    return NextResponse.json({
      tradeDate: "",
      status: "disconnected",
      marketOpen: false,
      source: "",
      isEstimate: true,
      windowMinutes: 5,
      minRecentNetLots: 10,
      minBuySellRatio: 1.5,
      minPositiveSteps: 2,
      scannedCount: 0,
      candidateCount: 0,
      alerts: [],
      lastError: "盤中大單監測服務暫時無法連線。",
      notice: "大單狂進依逐筆成交方向與動態大單門檻推估，不代表真實投資人身分。",
      updatedAt: new Date().toISOString(),
    } satisfies ElectronicChipFlowAlertsResponse, { status: 503 });
  }
}
