import { NextResponse, type NextRequest } from "next/server";
import { loginRedirectForPath } from "./app/auth-route";

export function proxy(request: NextRequest) {
  const loginRedirect = loginRedirectForPath(
    request.nextUrl.pathname,
    request.cookies.has("access_token"),
  );
  if (loginRedirect) {
    return NextResponse.redirect(new URL(loginRedirect, request.url));
  }

  // CS-070: the gateway resolves the public tenant authority, not Next's
  // internal rewrite destination.  The gateway accepts this only from its
  // configured trusted Next/ingress CIDR.
  const forwardedHeaders = new Headers(request.headers);
  // Never pass a browser-supplied RFC 7239 chain through a trusted Next hop.
  // The gateway uses the explicit single X-Forwarded-Host set below instead.
  forwardedHeaders.delete("forwarded");
  const originalHost = request.headers.get("host");
  if (originalHost) {
    forwardedHeaders.set("x-forwarded-host", originalHost);
  }

  return NextResponse.next({ request: { headers: forwardedHeaders } });
}

export const config = {
  matcher: [
    // Next requires a literal matcher. Keep these exclusions in sync with auth-route.ts.
    "/((?!(?:t/[^/]+/)?(?:login|session-refresh|api|auth|_next)(?:/|$)|.*\\.(?:avif|css|gif|ico|jpe?g|js|map|png|svg|txt|webp|woff2?|xml)$).+)",
    // Gateway rewrites still need the public Host for tenant routing (CS-070).
    "/api/:path*", "/auth/browser/:path*",
    "/t/:tenant/api/:path*", "/t/:tenant/auth/browser/:path*",
  ],
};
