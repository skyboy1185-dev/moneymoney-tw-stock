import { NextRequest, NextResponse } from "next/server";
import { buildFinMindNews } from "@/services/content-service";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const category = request.nextUrl.searchParams.get("category") ?? "";
  const keyword = request.nextUrl.searchParams.get("keyword") ?? "";
  try {
    return NextResponse.json(await buildFinMindNews(category, keyword));
  } catch (error) {
    return NextResponse.json({
      error: error instanceof Error ? error.message : "即時新聞暫時無法連線",
      items: [], categories: [], dataMode: "unavailable",
    }, { status: 503 });
  }
}
