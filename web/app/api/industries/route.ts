import { NextResponse } from "next/server";
import { backendJson } from "@/services/backend-client";
import { buildMockIndustryHotspots, type IndustryHotspot } from "@/services/content-service";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    return NextResponse.json(await backendJson<{ items: IndustryHotspot[]; updatedAt: string; dataMode: "demo" }>("/industries/hotspots"));
  } catch {
    return NextResponse.json({
      items: await buildMockIndustryHotspots(),
      updatedAt: new Date().toISOString(),
      dataMode: "demo",
      source: "Next.js Mock Provider",
    });
  }
}
