# Manager UI ↔ backend integration (CS-030 / F30)

## Invitations & audit

Owners and managers create a tenant-scoped invitation; the recipient accepts its single-use link,
chooses a password, and receives the normal access/refresh session. The plaintext invitation token
is returned only for the dev/test notification stub; storage contains only its digest. **All** audit
writes — authentication, NULL-tenant, and application/admin events — go through the `auth_svc`
`auth_record_audit` SECURITY DEFINER port; `app_rw` has **no** direct `audit_log` grant (migration `0014`
revoked the earlier INSERT/SELECT grant), and a `BEFORE UPDATE/DELETE` trigger (`0015`) makes the table
immutable regardless of grants. `impersonated_by` and the SaaS-admin actor type are schema support
reserved for CS-064b; this slice does not implement impersonation.

## Tenant signup, verification, trial, and provisioning (CS-062)

`POST /signup` is public and validates the same reserved-slug policy as the routing edge before one
owner-run database function atomically creates the `trial` tenant and its owner. It returns the normal
access/refresh pair plus a one-time development `verify_url`; only the peppered token digest is stored.
`POST /verify-email` consumes that token uniformly (unknown, expired, or consumed tokens fail alike).
Owners may resend at `POST /onboarding/resend-verification`. `POST /onboarding/provision` is owner-only and
uses the reusable `require_email_verified` hook; future solver `/schedule` endpoints must use that same gate.
Provisioning is idempotent and copies only a reviewed jurisdiction template. Trials authorize like active
tenants until `scripts/suspend_expired_trials.py`, running with the `auth_svc` principal, marks expired trials
`suspended`; the billing exemption is deliberately deferred to CS-065. The first-run wizard and real email are
also outside this backend slice.

The contract every manager-facing screen (CS-031–035) and the P0 auth/routing tier build on. Established by
CS-030.

## Topology
```
Browser ──(same-origin)──► Next.js (coffee_shop/)
           cookie: session JWT      │  server components forward the cookie
                                    ▼
                         Next rewrites /api/* ──► FastAPI gateway (coffee-scheduler/api/)
                                                   │ verify JWT → open txn →
                                                   │ open_session_context(app_rw, tenant_id)
                                                   ▼
                                                 PgBouncer :6432 (txn mode) ──► Postgres (RLS)
```

**Same-origin by design.** The browser only talks to Next. `next.config.ts` `rewrites()` maps `/api/:path*` →
`API_BASE_URL` (server-only env), so the browser's calls to `/api/...` are proxied to the gateway and the
gateway's `Set-Cookie` returns on the Next origin — the `session` cookie is first-party. This avoids
SameSite=None / CORS / cross-site-CSRF problems. Server components fetch the gateway **directly**
(`API_BASE_URL`) and forward the cookie read via `cookies()` from `next/headers`, with `cache: 'no-store'`
(tenant data is never cached).

## Auth / session flow
1. `POST /api/auth/login { tenant_slug, email, password }` → gateway resolves `tenant_slug → tenant_id` **as
   the owner role** (the `tenants` table is `ENABLE`-not-`FORCE` RLS — migration `0004`/BUG-020 — precisely so
   the owner can resolve any slug without tenant context; `app_rw` cannot), verifies the credential (argon2id),
   and issues an **HS256 JWT** carrying `tenant_id`/`tenant_slug`/`user_id`/`role`, set as an **HttpOnly,
   SameSite=Lax, Secure-in-prod** cookie named `session`.
2. Every protected request: the gateway reads the cookie, verifies the JWT (pinned `algorithms=["HS256"]` — no
   alg-confusion), and uses **only** the signed `tenant_id` for the DB session. A client can never supply or
   override the tenant (no header/param path). Missing/expired/tampered/foreign-signed → **401**.
3. DB access goes through `data_access.session.open_session_context(conn, role="app_rw", tenant_id=…)` inside an
   explicit transaction on a PgBouncer-safe pool (`prepare_threshold=None`). RLS scopes every row; no tenant
   context → zero rows (fail-closed).

## CS-070 tenant routing and browser edge

The public authority chooses the tenant before authentication or an RLS-backed fetch: production uses
`https://<slug>.coffeescheduler.com`, while local development uses the exact
`http://localhost/t/<slug>/` prefix (or the explicitly configured development host/port).  The FastAPI edge
validates raw `Host`/`:authority` headers, rejects duplicate or malformed forwarding headers, checks the shared
reserved-slug namespace, and resolves only `active` or `trial` tenants.  Unknown, suspended, churned, and lookup
failure outcomes are one cache-safe 404; no request value, cookie, JWT claim, or login body can select another
tenant.  A signed principal is then compared to the immutable resolved route before any data fetch opens the
transaction-local `open_session_context` RLS scope.

Next remains the same-origin browser endpoint.  Its middleware copies the public `Host` to
`X-Forwarded-Host` for `/api/*` and browser-auth rewrites; the gateway honors it only if the immediate peer is in
`TRUSTED_PROXY_CIDRS`.  The same trust boundary is used for `X-Forwarded-For`, so login throttle and audit events
record the real client address rather than a shared proxy address.  Cookie-authenticated unsafe tenant requests
require an exact canonical `Origin` (or `Referer` fallback).  Production GET/HEAD aliases differing only by case,
one terminal dot, or `:443` redirect once (308) to the canonical HTTPS authority with `Cache-Control: no-store`
and `Vary: Host`; mutation aliases fail closed.  TLS issuance, HTTP-to-HTTPS edge redirects, and full CORS/cache
partitioning remain deployment follow-ups documented in the CS-070 manifest rather than application behavior.

## Two kinds of "role" — do not conflate
- **UI/RBAC role** (`owner`/`manager`/`staff`/`read_only`) — carried in the JWT, governs what the UI exposes.
- **Postgres role** — always `app_rw` for tenant-scoped data (the owner role is used only for login-time slug
  resolution). The isolation guarantee is RLS + `app_rw`, not the UI role.

## Fail-closed guarantees
- No/invalid session → 401 at the gateway; Next middleware also presence-gates `/dashboard` and the
  `(manager)` layout redirects to `/login` on a 401 from `/me` (defense in depth).
- No tenant context in a DB transaction → RLS returns 0 rows (the safe direction), never a cross-tenant leak.
- Manager UI “delete” actions are soft-deletes: normal list/detail reads see only live rows through the
  restrictive lifecycle policy, while an explicitly privileged transaction may inspect or restore deleted rows.

## LangGraph-state binding convention (for F12 / CS-015 — documented, not built)
When the agent runtime lands, a screen binds to a graph run by **`thread_id`**. Graph state persists in the
**tenant-scoped `langgraph_checkpoints` table** (migration `0003`, RLS-protected, FORCE), read/written through
the gateway with the **same** tenant context (`open_session_context`), so a manager only ever sees their own
tenant's graph state. The gateway will expose a `/graph/...` surface keyed by `thread_id`; no agent runtime is
wired in CS-030.

## Out of scope (owned elsewhere)
Real per-tenant auth (`tenant_users`, argon2 at scale, refresh/rotation, rate limiting) → **CS-061**;
subdomain/path routing, reserved-slug denylist, explicit CSRF tokens, wildcard TLS → **CS-070**; the screens
themselves → **CS-031–035**; onboarding/billing → CS-062/065/066.

## Run locally
1. `docker compose up -d` (postgres + pgbouncer); ETL seeds maven-demo.
2. Gateway: set `SESSION_SECRET` (≥32 chars), then `uvicorn api.app:app --port 8000` (from `coffee-scheduler/`).
3. Next: `cd coffee_shop && API_BASE_URL=http://localhost:8000 npm run dev`; open `/login`, sign in with the
   seeded demo manager, land on `/dashboard` listing the tenant's stores. See `coffee-scheduler/api/README.md`
   for gateway env vars.
