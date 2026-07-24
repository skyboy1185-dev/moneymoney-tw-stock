import { NextRequest, NextResponse } from "next/server";
import type { ScreenerFilters } from "@/lib/types";
import { stockService } from "@/services/stock-service";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const params = request.nextUrl.searchParams;
  const filters: ScreenerFilters = {
    minPrice: params.get("minPrice") ?? "",
    maxPrice: params.get("maxPrice") ?? "",
    minVolume: params.get("minVolume") ?? "",
    minChange: params.get("minChange") ?? "",
    maxChange: params.get("maxChange") ?? "",
    industry: params.get("industry") ?? "",
    market: params.get("market") ?? "",
    technical: params.getAll("technical"),
  };
  try {
    const rows = await stockService.screen(filters);
    return NextResponse.json({ rows, total: rows.length, updatedAt: "2026-07-24T13:30:00+08:00" });
  } catch {
    return NextResponse.json({ error: "選股資料暫時無法取得，請稍後再試。" }, { status: 500 });
  }
}
