import { NextRequest, NextResponse } from "next/server";
import { backendJson } from "@/services/backend-client";
import { getUserId } from "@/lib/portfolio-api";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const ALLOWED = [
  /^portfolio\/settings$/,
  /^portfolio\/allocation$/,
  /^ai-stock-monitor$/,
  /^ai-stock-monitor\/\d+$/,
  /^ai-stock-monitor\/\d+\/(confirm-entry|ignore)$/,
  /^ai-stock-positions$/,
  /^ai-stock-positions\/\d+$/,
  /^ai-stock-positions\/\d+\/(calculate-allocation|confirm-add-on|decline-add-on|disable-add-on|add-ons|partial-exit|close|continue-monitoring)$/,
  /^ai-stock-alerts$/,
  /^ai-stock-alerts\/\d+\/read$/,
];

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const userId = getUserId(request);
  if (!userId) return NextResponse.json({ error: "缺少有效的使用者識別。" }, { status: 401 });
  const path = (await context.params).path.join("/");
  if (!ALLOWED.some((pattern) => pattern.test(path))) {
    return NextResponse.json({ error: "不允許的 AI 選股操作。" }, { status: 404 });
  }
  const body = ["POST", "PUT", "PATCH"].includes(request.method)
    ? await request.text()
    : undefined;
  try {
    const payload = await backendJson<unknown>(`/${path}`, {
      method: request.method,
      headers: {
        "x-user-id": userId,
        ...(body ? { "Content-Type": "application/json" } : {}),
      },
      body,
    });
    return NextResponse.json(payload);
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "AI 選股後端操作失敗" },
      { status: 400 },
    );
  }
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
