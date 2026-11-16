import { NextResponse, type NextRequest } from "next/server";

export function middleware(request: NextRequest) {
  // CS-061a browser transport (BUG-036/037): gate on the HttpOnly access-token cookie. The access
  // cookie lives only for the 15-minute token TTL, so its absence is the normal expiry path. Route
  // to the same-origin session-recovery step (which attempts exactly one refresh using the Path-
  // scoped refresh cookie) rather than straight to /login, so a valid 7-day refresh session is
  // used. Recovery fails closed to /login on any refresh failure. Validity (signature/expiry/
  // membership) is still enforced by the gateway on /me and /stores.
  if (!request.cookies.has("access_token")) {
    return NextResponse.redirect(new URL("/session-refresh", request.url));
  }

  return NextResponse.next();
}

export const config = {
  // Route groups do not appear in URLs; dashboard is the current manager route.
  matcher: ["/dashboard/:path*"],
};
