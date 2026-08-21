import { NextRequest, NextResponse } from "next/server";
import {
  PRIVATE_SITE_COOKIE,
  verifyAdaptiveScannerToken,
  verifyPrivateSiteSession,
} from "@/lib/private-site-auth";

const PUBLIC_PATHS = new Set(["/login", "/api/auth/login", "/api/auth/logout"]);

function secured(response: NextResponse): NextResponse {
  response.headers.set("Cache-Control", "private, no-store, max-age=0");
  response.headers.set("X-Robots-Tag", "noindex, nofollow, noarchive");
  return response;
}

export async function proxy(request: NextRequest) {
  const pathname = request.nextUrl.pathname;
  if (
    (
      pathname === "/api/adaptive-electronic/scan"
      || pathname === "/api/rocket-radar/scan"
      || pathname === "/api/ai"
    )
    && await verifyAdaptiveScannerToken(request.headers.get("x-adaptive-scanner-token"))
  ) {
    return secured(NextResponse.next());
  }
  const authenticated = await verifyPrivateSiteSession(
    request.cookies.get(PRIVATE_SITE_COOKIE)?.value,
  );

  if (PUBLIC_PATHS.has(pathname)) {
    if (pathname === "/login" && authenticated) {
      return secured(NextResponse.redirect(new URL("/", request.url)));
    }
    return secured(NextResponse.next());
  }

  if (authenticated) return secured(NextResponse.next());

  if (pathname.startsWith("/api/")) {
    return secured(NextResponse.json(
      { error: "網站目前為非公開模式，請先登入。" },
      { status: 401 },
    ));
  }

  const loginUrl = new URL("/login", request.url);
  const destination = `${pathname}${request.nextUrl.search}`;
  if (destination !== "/") loginUrl.searchParams.set("next", destination);
  return secured(NextResponse.redirect(loginUrl));
}

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|robots.txt|sitemap.xml).*)",
  ],
};
