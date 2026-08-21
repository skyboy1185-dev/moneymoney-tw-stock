import { NextResponse } from "next/server";
import { resolveRuntimeConfig } from "@/lib/runtime-config";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const config = resolveRuntimeConfig();
    return NextResponse.json({
      mode: config.mode,
      backendConfigured: Boolean(config.backendBaseUrl),
      backendTarget: config.mode === "railway" ? "Railway service network" : "local FastAPI",
      scannerWorkerConfigured: Boolean(config.scannerWorkerUrl),
      railwayEnvironment: config.railwayEnvironment,
    }, { headers: { "Cache-Control": "no-store, max-age=0" } });
  } catch (error) {
    return NextResponse.json({
      mode: "invalid",
      backendConfigured: false,
      error: error instanceof Error ? error.message : "環境設定錯誤",
    }, { status: 500, headers: { "Cache-Control": "no-store, max-age=0" } });
  }
}
