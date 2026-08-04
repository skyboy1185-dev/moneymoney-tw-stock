import { NextResponse } from "next/server";
import { PRIVATE_SITE_COOKIE } from "@/lib/private-site-auth";

export async function POST() {
  const response = NextResponse.json({ ok: true });
  response.headers.set("Cache-Control", "no-store");
  response.cookies.set({
    name: PRIVATE_SITE_COOKIE,
    value: "",
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "strict",
    path: "/",
    maxAge: 0,
  });
  return response;
}
