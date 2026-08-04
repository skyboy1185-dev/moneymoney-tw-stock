import { NextResponse } from "next/server";
import { buildOfficialIndustryHotspots } from "@/services/content-service";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    return NextResponse.json(await buildOfficialIndustryHotspots());
  } catch (error) {
    return NextResponse.json({
      error: error instanceof Error ? error.message : "官方產業行情暫時無法連線",
      items: [], dataMode: "unavailable",
    }, { status: 503 });
  }
}
