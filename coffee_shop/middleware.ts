import { NextResponse, type NextRequest } from "next/server";

export function middleware(request: NextRequest) {
  if (!request.cookies.has("session")) {
    return NextResponse.redirect(new URL("/login", request.url));
  }

  return NextResponse.next();
}

export const config = {
  // Route groups do not appear in URLs; dashboard is the current manager route.
  matcher: ["/dashboard/:path*"],
};
