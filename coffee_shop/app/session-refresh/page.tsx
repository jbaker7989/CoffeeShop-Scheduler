"use client";

import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import {
  decideSessionRecovery,
  LOGIN_TARGET,
  RECOVERY_LOOP_WINDOW_MS,
  RETRY_TARGET,
} from "../session-recovery";

// Same-origin session recovery for the CS-061a browser transport (BUG-037).
//
// Reached when a protected request has no valid access credential. The HttpOnly refresh cookie is
// scoped to /auth/browser, so only a request to /auth/browser/refresh carries it — a server
// component/middleware cannot. This client step POSTs there exactly once; the gateway rotates the
// family and relays fresh Set-Cookie headers (access + refresh) to the browser. No token is ever
// read by JavaScript: we inspect only the response status.
//
// On success the original safe GET page is retried once (fixed /dashboard, never a user-controlled
// return URL). On any failure — expired, malformed, or replayed refresh — both cookies are cleared
// and the session fails closed to the fixed /login route. A short loop guard bounds this to one
// refresh per failure so a non-sticky retry cannot hammer the endpoint.

const GUARD_KEY = "cs061a:session-recovery-ts";

function recentlyAttempted(): boolean {
  try {
    const previous = window.sessionStorage.getItem(GUARD_KEY);
    return previous !== null && Date.now() - Number(previous) < RECOVERY_LOOP_WINDOW_MS;
  } catch {
    return false;
  }
}

function markAttempt(): void {
  try {
    window.sessionStorage.setItem(GUARD_KEY, String(Date.now()));
  } catch {
    // Private mode / disabled storage: proceed without the loop guard. A failed refresh still
    // fails closed to /login, so the only lost property is loop-dampening, never safety.
  }
}

export default function SessionRefreshPage() {
  const router = useRouter();
  // Guard against React's double-invoke in development so refresh runs at most once per mount.
  const started = useRef(false);

  useEffect(() => {
    if (started.current) {
      return;
    }
    started.current = true;

    async function recover() {
      const attempted = recentlyAttempted();
      let refreshStatus = 0;
      if (!attempted) {
        markAttempt();
        try {
          // Credentials sent same-origin; the browser attaches the Path=/auth/browser refresh
          // cookie. No body (the gateway rejects a mixed-transport body); we read only the status.
          const response = await fetch("/auth/browser/refresh", {
            method: "POST",
            credentials: "same-origin",
            cache: "no-store",
          });
          refreshStatus = response.status;
        } catch {
          refreshStatus = 0;
        }
      }

      if (decideSessionRecovery(refreshStatus, attempted) === "retry") {
        router.replace(RETRY_TARGET);
        return;
      }

      // Fail closed: clear both browser cookies, then redirect to the fixed login route.
      try {
        await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin", cache: "no-store" });
      } catch {
        // Best effort — the expired/invalid cookies are already unusable server-side.
      }
      router.replace(LOGIN_TARGET);
    }

    void recover();
  }, [router]);

  return (
    <main className="grid min-h-screen place-items-center bg-[#f7f5f0] px-5 py-10 text-[#1f271f]">
      <p aria-live="polite" className="text-sm text-[#657064]">
        Restoring your session…
      </p>
    </main>
  );
}
