import { NextRequest, NextResponse } from "next/server";
import { clientKey, rateLimit } from "@/lib/server-utils";
import { getInstitutionalInvestorResponse } from "@/services/institutional-investor-service";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  if (!rateLimit(`institutional-investors:${clientKey(request)}`, 60).allowed) {
    return NextResponse.json({ error: "更新過於頻繁，請稍候再試。" }, { status: 429 });
  }
  try {
    return NextResponse.json(await getInstitutionalInvestorResponse());
  } catch (reason) {
    console.error("institutional-investors", reason);
    return NextResponse.json({ error: "三大法人官方資料暫時無法取得，請稍後再試。" }, { status: 503 });
  }
}
