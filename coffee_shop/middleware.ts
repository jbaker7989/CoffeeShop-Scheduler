import { NextResponse, type NextRequest } from "next/server";

export function middleware(request: NextRequest) {
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

  // CS-061a browser transport (BUG-036/037): gate on the HttpOnly access-token cookie. The access
  // cookie lives only for the 15-minute token TTL, so its absence is the normal expiry path. Route
  // to the same-origin session-recovery step (which attempts exactly one refresh using the Path-
  // scoped refresh cookie) rather than straight to /login, so a valid 7-day refresh session is
  // used. Recovery fails closed to /login on any refresh failure. Validity (signature/expiry/
  // membership) is still enforced by the gateway on /me and /stores.
  const developmentDashboard = request.nextUrl.pathname.match(/^\/t\/[^/]+(\/dashboard(?:\/|$))/);
  if ((request.nextUrl.pathname.startsWith("/dashboard") || developmentDashboard) && !request.cookies.has("access_token")) {
    const prefix = developmentDashboard ? request.nextUrl.pathname.split("/").slice(0, 3).join("") : "";
    return NextResponse.redirect(new URL(`${prefix}/session-refresh`, request.url));
  }

  return NextResponse.next({ request: { headers: forwardedHeaders } });
}

export const config = {
  // Route groups do not appear in URLs; dashboard is the current manager route.
  // Auth/API rewrites must forward the original Host too; only dashboard is
  // access-cookie gated above.
  matcher: [
    "/dashboard/:path*", "/api/:path*", "/auth/browser/:path*",
    "/t/:tenant/dashboard/:path*", "/t/:tenant/api/:path*", "/t/:tenant/auth/browser/:path*",
  ],
};
