import { NextRequest, NextResponse } from "next/server";
import {
  createPrivateSiteSession,
  PRIVATE_SITE_COOKIE,
  PRIVATE_SITE_SESSION_SECONDS,
  secureCredentialEqual,
} from "@/lib/private-site-auth";

type Attempt = { count: number; resetAt: number };
const attempts = new Map<string, Attempt>();
const MAX_ATTEMPTS = 5;
const WINDOW_MS = 15 * 60 * 1000;

function clientKey(request: NextRequest): string {
  return request.headers.get("x-forwarded-for")?.split(",")[0]?.trim()
    || request.headers.get("x-real-ip")
    || "unknown";
}
export async function POST(request: NextRequest) {
  const now = Date.now();
  const key = clientKey(request);
  const current = attempts.get(key);
  if (current && current.resetAt > now && current.count >= MAX_ATTEMPTS) {
    return NextResponse.json(
      { error: "登入失敗次數過多，請 15 分鐘後再試。" },
      { status: 429, headers: { "Cache-Control": "no-store" } },
    );
  }

  let body: { username?: string; password?: string };
  try {
    body = await request.json() as { username?: string; password?: string };
  } catch {
    return NextResponse.json({ error: "登入資料格式錯誤。" }, { status: 400 });
  }
  const username = String(body.username ?? "").trim();
  const password = String(body.password ?? "");
  const expectedUsername = process.env.PRIVATE_SITE_USERNAME ?? "admin";
  const expectedPassword = process.env.PRIVATE_SITE_PASSWORD ?? "111";
  const [usernameValid, passwordValid] = await Promise.all([
    secureCredentialEqual(username, expectedUsername),
    secureCredentialEqual(password, expectedPassword),
  ]);

  if (!usernameValid || !passwordValid) {
    attempts.set(key, {
      count: current && current.resetAt > now ? current.count + 1 : 1,
      resetAt: current && current.resetAt > now ? current.resetAt : now + WINDOW_MS,
    });
    return NextResponse.json(
      { error: "帳號或密碼錯誤。" },
      { status: 401, headers: { "Cache-Control": "no-store" } },
    );
  }

  attempts.delete(key);
  const response = NextResponse.json({ ok: true });
  response.headers.set("Cache-Control", "no-store");
  response.cookies.set({
    name: PRIVATE_SITE_COOKIE,
    value: await createPrivateSiteSession(expectedUsername),
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "strict",
    path: "/",
    maxAge: PRIVATE_SITE_SESSION_SECONDS,
  });
  return response;
}
