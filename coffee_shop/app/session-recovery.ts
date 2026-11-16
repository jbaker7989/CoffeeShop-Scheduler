// Pure session-recovery policy for the CS-061a browser transport (BUG-037).
//
// The manager UI holds a 15-minute access cookie and a 7-day HttpOnly refresh cookie scoped to
// /auth/browser. When the access credential is gone/expired, exactly one same-origin refresh is
// attempted; on success the original (safe, idempotent) GET navigation is retried once, otherwise
// the session fails closed to the fixed /login route. This module is framework-free so the policy
// is unit-tested without a DOM.

// Fixed redirect targets — never a user-controlled return URL (BUG-037 AC).
export const RETRY_TARGET = "/dashboard";
export const LOGIN_TARGET = "/login";

// A second recovery arriving within this window of the previous one is treated as a loop (the
// retry did not stick), so we stop and fail closed instead of refreshing again. It is short enough
// that a legitimate refresh ~15 minutes later is always allowed.
export const RECOVERY_LOOP_WINDOW_MS = 15_000;

export type SessionRecoveryAction = "retry" | "login";

/**
 * Decide what to do after (optionally) calling the refresh endpoint.
 *
 * - If a recovery was already attempted in this cycle, do not refresh again — fail closed to login.
 *   This bounds the whole mechanism to exactly one retry per failed request.
 * - Otherwise a 200 from /auth/browser/refresh means new cookies were set: retry the page once.
 * - Any other status (401 replay/expired/unknown, network error modelled as 0) fails closed.
 */
export function decideSessionRecovery(
  refreshStatus: number,
  alreadyAttempted: boolean,
): SessionRecoveryAction {
  if (alreadyAttempted) {
    return "login";
  }
  return refreshStatus === 200 ? "retry" : "login";
}
