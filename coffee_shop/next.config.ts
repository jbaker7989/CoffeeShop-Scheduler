import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /* config options here */
  cacheComponents: true,
  partialPrefetching: true,
  reactCompiler: true,
  async rewrites() {
    // CS-070 topology: browser requests stay same-origin on
    // <tenant>.coffeescheduler.com; middleware.ts carries that public Host to
    // this fixed, server-configured gateway origin as X-Forwarded-Host.  Never
    // derive an upstream URL from a request header.
    const apiBaseUrl = process.env.API_BASE_URL ?? "http://localhost:8000";

    return [
      // Development topology: keep the tenant path selector intact until it
      // reaches FastAPI, while rendering the existing unprefixed Next routes.
      {
        source: "/t/:tenant/api/:path*",
        destination: `${apiBaseUrl}/t/:tenant/:path*`,
      },
      {
        source: "/t/:tenant/auth/browser/:path*",
        destination: `${apiBaseUrl}/t/:tenant/auth/browser/:path*`,
      },
      { source: "/t/:tenant/dashboard/:path*", destination: "/dashboard/:path*" },
      { source: "/t/:tenant/login", destination: "/login" },
      { source: "/t/:tenant/session-refresh", destination: "/session-refresh" },
      {
        source: "/api/:path*",
        destination: `${apiBaseUrl}/:path*`,
      },
      // CS-061a browser auth (BUG-036): proxy the browser login/refresh routes at their own path
      // so the gateway's HttpOnly refresh cookie (Path=/auth/browser) is sent to the refresh
      // endpoint on this same origin. These carry only HttpOnly cookies — no token is ever read by
      // client JavaScript.
      {
        source: "/auth/browser/:path*",
        destination: `${apiBaseUrl}/auth/browser/:path*`,
      },
    ];
  },
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
