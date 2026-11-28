const PUBLIC_PATH = /^\/(?:login|session-refresh|api|auth|_next)(?:\/|$)/;
const STATIC_ASSET = /\.(?:avif|css|gif|ico|jpe?g|js|map|png|svg|txt|webp|woff2?|xml)$/;

// The manager route group has no URL segment. New pages are gated by default.
export function loginRedirectForPath(pathname: string, hasAccessCookie: boolean): string | null {
  const tenantPrefix = pathname.match(/^\/t\/[^/]+(?=\/|$)/)?.[0] ?? "";
  const path = pathname.slice(tenantPrefix.length) || "/";
  if (hasAccessCookie || path === "/" || PUBLIC_PATH.test(path) || STATIC_ASSET.test(path)) {
    return null;
  }
  return `${tenantPrefix}/login`;
}
