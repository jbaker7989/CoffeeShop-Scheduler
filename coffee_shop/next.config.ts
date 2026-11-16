import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /* config options here */
  cacheComponents: true,
  partialPrefetching: true,
  reactCompiler: true,
  async rewrites() {
    const apiBaseUrl = process.env.API_BASE_URL ?? "http://localhost:8000";

    return [
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
