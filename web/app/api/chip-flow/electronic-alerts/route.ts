import { NextRequest, NextResponse } from "next/server";
import type { ElectronicChipFlowAlertsResponse } from "@/lib/electronic-chip-flow-alerts";
import { backendJson } from "@/services/backend-client";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  try {
    const pinned = request.nextUrl.searchParams.get("pinned") ?? "";
    const pinnedSymbols = pinned.split(",")
      .filter((symbol) => /^\d{4}$/.test(symbol))
      .slice(0, 20);
    const tracking = request.nextUrl.searchParams.get("tracking") ?? "";
    const trackingSymbols = tracking.split(",")
      .filter((symbol) => /^\d{4}$/.test(symbol))
      .slice(0, 20);
    const requestedClientId = request.nextUrl.searchParams.get("clientId") ?? "";
    const clientId = /^[A-Za-z0-9_-]{8,64}$/.test(requestedClientId)
      ? requestedClientId
      : "legacy-client";
    const query = encodeURIComponent(pinnedSymbols.join(","));
    const trackingQuery = encodeURIComponent(trackingSymbols.join(","));
    const payload = await backendJson<ElectronicChipFlowAlertsResponse>(
      `/stocks/chip-flow/electronic-alerts?pinned=${query}&tracking=${trackingQuery}&clientId=${encodeURIComponent(clientId)}`,
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
      providerRateLimited: false,
      providerRetrySeconds: 0,
      isEstimate: true,
      windowMinutes: 5,
      minRecentNetLots: 10,
      minBuySellRatio: 1.5,
      minPositiveSteps: 2,
      scannedCount: 0,
      baselineCycleScannedCount: 0,
      baselineCycleTargetSeconds: 90,
      lastFullScanAt: null,
      candidateCount: 0,
      disposedExcludedCount: 0,
      disposedExcludedSymbols: [],
      restrictionStatus: "degraded",
      payloadCacheHit: false,
      payloadCacheHits: 0,
      payloadCacheMisses: 0,
      popularCandidateCount: 0,
      fastCandidateCount: 0,
      cpoCandidateCount: 0,
      packagingTestCandidateCount: 0,
      powerCandidateCount: 0,
      popularUniverseSource: "證交所／櫃買中心成交量排行",
      popularUniverseUpdatedAt: null,
      hotScanCount: 0,
      highFrequencyTrackingCount: 0,
      pinnedTrackingCount: 0,
      expandedTrackingCount: 0,
      rankingLimit: 10,
      longCount: 0,
      autoTopTrackingCount: 0,
      extraPinnedTrackingLimit: 10,
      extraPinnedTrackingCount: 0,
      refreshSeconds: 2,
      warningCount: 0,
      strengtheningCount: 0,
      jointIncreaseCount: 0,
      alerts: [],
      trackedAlerts: [],
      shortCount: 0,
      shortStrengtheningCount: 0,
      shortAlerts: [],
      trackedShortAlerts: [],
      lastError: "盤中大單監測服務暫時無法連線。",
      notice: "多空數量是近段時間累積成交張數；大單採動態門檻，小單僅作散戶動向推估。",
      updatedAt: new Date().toISOString(),
    } satisfies ElectronicChipFlowAlertsResponse, { status: 503 });
  }
}
