import { NextResponse, type NextRequest } from "next/server";

export function middleware(request: NextRequest) {
  // CS-061a browser transport (BUG-036): gate on the HttpOnly access-token cookie. This only
  // checks presence; validity (signature/expiry/membership) is enforced by the gateway on /me
  // and /stores, which fail closed to /login on 401.
  if (!request.cookies.has("access_token")) {
    return NextResponse.redirect(new URL("/login", request.url));
  }

  return NextResponse.next();
}

export const config = {
  // Route groups do not appear in URLs; dashboard is the current manager route.
  matcher: ["/dashboard/:path*"],
};
