import { NextRequest, NextResponse } from "next/server";
import { backendJson } from "@/services/backend-client";
import { MOCK_NEWS, type NewsItem } from "@/services/content-service";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const category = request.nextUrl.searchParams.get("category") ?? "";
  const keyword = request.nextUrl.searchParams.get("keyword") ?? "";
  const query = new URLSearchParams({ category, keyword });
  try {
    return NextResponse.json(await backendJson<{ items: NewsItem[]; categories: string[]; dataMode: "demo"; message: string }>(`/news?${query}`));
  } catch {
    const normalized = keyword.trim().toLowerCase();
    const items = MOCK_NEWS.filter((item) =>
      (!category || item.category === category)
      && (!normalized || item.title.toLowerCase().includes(normalized)
        || item.summary.toLowerCase().includes(normalized)
        || item.symbols.some((symbol) => symbol.includes(normalized))),
    );
    return NextResponse.json({
      items,
      categories: [...new Set(MOCK_NEWS.map((item) => item.category))],
      dataMode: "demo",
      message: "新聞為展示資料，不代表即時新聞。",
      source: "Next.js Mock Provider",
    });
  }
}
