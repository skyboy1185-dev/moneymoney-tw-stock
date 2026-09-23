import { NextRequest, NextResponse } from "next/server";
import { PRIVATE_SITE_COOKIE, shouldUseSecureCookie } from "@/lib/private-site-auth";

export async function POST(request: NextRequest) {
  const response = NextResponse.json({ ok: true });
  response.headers.set("Cache-Control", "no-store");
  response.cookies.set({
    name: PRIVATE_SITE_COOKIE,
    value: "",
    httpOnly: true,
    secure: shouldUseSecureCookie(request.headers, request.nextUrl.protocol),
    sameSite: "strict",
    path: "/",
    maxAge: 0,
  });
  return response;
}
