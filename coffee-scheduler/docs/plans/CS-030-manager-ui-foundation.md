# CS-030 — Manager UI foundation (F30)

**Priority:** P1 · **Size:** M · **Est:** ~3d · **Status:** Planned (2026-11-16)
**Unblocks:** CS-061 (auth), CS-062 (onboarding), CS-068b (HTTP isolation), CS-070 (routing), CS-031–035
(screens), CS-071+ (employee app)

## Context
The data plane (F42–47), tenancy/RLS (F60 + BUG-020→030 + the CS-068a isolation gate), and observability
(CS-036/038) are done. The project's bottleneck is now that **there is no application layer** — the entire P0
SaaS tier is Blocked behind it. CS-030 establishes the shared manager-facing Next.js shell, the **backend
boundary**, the **demo auth → tenant-context** path, and the conventions every screen (CS-031–035) reuses.

**Current state:** `coffee_shop/` (repo root, tracked) is the canonical Next.js scaffold — Next 16.4.5 /
React 19.3 / Tailwind 4 / TS, app-router, **default create-next-app boilerplate, unwired**. `coffee-scheduler/
coffee-shop/` is a **stray untracked** build dir (ignore). `n8n/` is empty. The only runnable backend is Python
(`compliance/engine.py`, `data_access/`). RLS session plumbing already exists and must be **reused**:
`data_access/session.py::open_session_context` / `tenant_session` (autocommit guard, `SET LOCAL ROLE app_rw`,
`current_user` check, UUID validation, transaction-local `set_config`). Architecture reference:
`docs/architecture/saas-multi-tenancy.md` §2.5 (pooling), §3.1/§3.3 (users/login), §14.10 (web security).

## Decisions (locked 2026-11-16)
1. **Backend boundary = a minimal Python FastAPI gateway** that owns DB access via `data_access/session.py`
   (reuses the proven RLS session; no duplicated pooling logic in Node). Next.js calls it server-side. Matches
   the architecture's HTTP `/auth/login` + "middleware sets Postgres session variables per request," and is the
   boundary the Python agents/compliance/solver will later live behind.
2. **Demo auth = seeded credential → signed JWT in an HttpOnly cookie** carrying `tenant_id`/`user_id`/`role`;
   per-request middleware opens a DB transaction and sets tenant context from the **signed** token only. Lays
   the CS-061 groundwork (JWT shape per §3.3) without building `tenant_users`/argon2 tables.

## Architecture
```
Browser ──(same-origin)──► Next.js (coffee_shop/)
           cookie: session JWT      │  server-side route handlers / server actions, forward cookie
                                    ▼
                         Next rewrites /api/* ──► FastAPI gateway (coffee-scheduler/api/)
                                                   │ verify JWT → open txn →
                                                   │ open_session_context(app_rw, tenant_id)
                                                   ▼
                                                 PgBouncer :6432 (txn mode) ──► Postgres (RLS)
```
**Same-origin by design:** the browser only talks to Next; Next **rewrites** `/api/*` to the gateway
(server-to-server), keeping the session cookie first-party and sidestepping SameSite=None/CORS/CSRF cross-site
pain.

## Deliverables

### A. FastAPI gateway — `coffee-scheduler/api/` (new)
- `app.py`:
  - `POST /auth/login` (demo): body `{tenant_slug, email, password}` (§3.3 shape). Verify against a **seeded**
    demo credential (argon2id hash in env, not plaintext); resolve `tenant_slug → tenant_id` via the
    **owner/migration role** (tenants is `ENABLE`-not-`FORCE` RLS by design — BUG-020/`0004` — so the owner
    resolves any slug without tenant context; app_rw cannot, by design). Issue a short-lived **JWT (HS256,
    `SESSION_SECRET`)** as an **HttpOnly, SameSite=Lax, Secure-in-prod** cookie.
  - `POST /auth/logout` — clears the cookie.
  - `GET /me` — `{tenant_slug, role}` from the verified cookie (no DB).
  - `GET /stores` — the **one real tenant-scoped read**: lists the current tenant's stores via
    `tenant_session(role="app_rw", tenant_id=<from JWT>)` → RLS returns only maven-demo's rows.
  - `GET /healthz` — liveness.
  - **Auth dependency**: extract cookie → verify JWT → **401** on failure (fail-closed); the DB-session tenant
    id comes **only** from the signed token, never a header/param (no tenant spoofing).
- `db.py` — a `psycopg_pool.ConnectionPool` to **PgBouncer :6432** with **`prepare_threshold=None`** (disable
  prepared statements — the transaction-mode gotcha); a `resolve_tenant_id(slug)` that connects as the
  **owner** role (login-only); a request helper wrapping `open_session_context`.
- `auth.py` — JWT issue/verify, argon2id verify, cookie helpers (flags per §14.10).
- `requirements.txt` — pinned `fastapi`, `uvicorn[standard]`, `psycopg[binary,pool]`, `pyjwt`, `argon2-cffi`
  (exact versions ≥2 weeks old; confirm at build).

### B. Next.js shell — `coffee_shop/`
- Shared authenticated layout `app/(manager)/layout.tsx`: top nav (brand, nav-link placeholders for
  CS-031–035, current tenant + role, logout), Tailwind, light/dark.
- `/login` (public) — email/password form → `/api/auth/login` (Next rewrite → gateway).
- `/dashboard` (auth-gated) — the authenticated entry point; server-side fetches `/api/stores`, renders the
  tenant's stores (proves the pattern).
- `middleware.ts` — gate the `(manager)` group; no/invalid cookie → redirect `/login`.
- `next.config.ts` — `rewrites()` mapping `/api/:path*` → `API_BASE_URL` (server-side env).
- `.env.example` — `API_BASE_URL` (Next) + gateway `DATABASE_URL`/`SESSION_SECRET`/`DEMO_*` documented.

### C. Docs
- `docs/architecture/manager-ui-integration.md` — the documented integration pattern: the same-origin
  Next→gateway boundary, the auth/session/JWT/cookie flow, DB access + RLS through the gateway via
  `data_access/session.py`, the app-role vs DB-role distinction (UI role governs RBAC; DB role is always
  `app_rw`), and the **LangGraph-state binding convention** for F12/CS-015 (documented, not built): a screen
  binds to a graph run by `thread_id`; state persists in the tenant-scoped `langgraph_checkpoints` table
  (migration `0003`, RLS-protected), read through the gateway with the same tenant context.

## Edge cases & pitfalls
- **Tenant spoofing** — DB-session tenant id from the **signed** JWT only, never a client header/field.
- **Login slug resolution chicken-and-egg** — resolve `slug → tenant_id` at login as the **owner** role
  (the reason `0004` used ENABLE-not-FORCE); keep that owner connection login-only; data queries use `app_rw`.
- **Secure-cookie-on-localhost trap** — `Secure` cookies aren't sent over `http://localhost`; set `Secure`
  **prod-only** (env-gated); `HttpOnly` + `SameSite=Lax` always.
- **PgBouncer prepared-statement gotcha** — gateway pool sets `prepare_threshold=None`.
- **Transaction discipline** — all DB access via `tenant_session`/`open_session_context` (asserts not
  autocommit, pins `SET LOCAL ROLE`); no bare autocommit `SELECT`.
- **Fail-closed** — no/expired/tampered JWT → **401**; no tenant context → RLS denies (0 rows).
- **CSRF** — `SameSite=Lax` + same-origin covers the demo; explicit CSRF tokens are **CS-061** (flag only).
- **Secrets** — `SESSION_SECRET`/`DATABASE_URL`/`DEMO_*` via env; a weak/missing `SESSION_SECRET` must fail
  startup, not default to a known key; ship `.env.example`, commit no real values.
- **Stray scaffold** — `coffee-scheduler/coffee-shop/` is not the app; `coffee_shop/` is canonical.
- **Next 16 / React 19** — confirm `npm run build` succeeds.
- **No pytest** — API tests are stdlib `unittest` + Starlette `TestClient`.

## Verification (end-to-end)
1. `docker compose up -d` (postgres + pgbouncer healthy); ETL has seeded maven-demo (3 stores).
2. `uvicorn api.app:app` with env; `GET /healthz` 200.
3. `DATABASE_URL=postgresql://…@localhost:5432/… python3 coffee-scheduler/test/test_api_foundation.py` —
   login sets cookie; no-cookie `/stores` → **401**; tampered-cookie → **401**; valid cookie `/stores` returns
   **maven-demo's 3 stores**; forged tenant in a tampered cookie fails signature → 401 (no spoof).
4. `cd coffee_shop && npm run build` succeeds; `npm run dev`; unauth `/dashboard` → redirect `/login`; demo
   login → dashboard lists the tenant's stores (full auth→context→RLS→data path, same-origin).
5. CS-068a isolation gate still green (no RLS regression).

## Critical files
- **New:** `coffee-scheduler/api/{app.py,db.py,auth.py,requirements.txt}`,
  `coffee-scheduler/test/test_api_foundation.py`, `docs/architecture/manager-ui-integration.md`,
  `coffee_shop/middleware.ts`, `coffee_shop/app/(manager)/layout.tsx`, `coffee_shop/app/login/page.tsx`,
  `coffee_shop/app/(manager)/dashboard/page.tsx`, `coffee_shop/.env.example`.
- **Edited:** `coffee_shop/app/layout.tsx`, `coffee_shop/next.config.ts`, replace boilerplate
  `app/page.tsx`/`globals.css`.
- **Reuse (do not reimplement):** `data_access/session.py`; `docker-compose.yml`; the `0004`
  ENABLE-not-FORCE tenants design for slug resolution.

## Scope guard — OUT
Real per-tenant auth (`tenant_users`, argon2 at scale, refresh/rotation) → **CS-061**; subdomain/path routing,
reserved-slug denylist, CORS/CSRF hardening, wildcard TLS → **CS-070**; the actual screens → **CS-031–035**;
LangGraph runtime / agent invocation → **F12/CS-015 + F37** (convention documented only); onboarding/billing →
CS-062/065/066.

## Orchestration
- Gateway (Python, reuses session.py) → Codex develops; Claude authors `test_api_foundation.py` (fail-closed +
  RLS-scoped + no-spoof) and validates.
- Next.js shell + middleware + rewrites → Codex develops; Claude reviews the auth-gate/redirect and the
  cookie-flag/Secure-on-localhost handling, runs the end-to-end check.
- Additive only (new `api/` tree + rewiring `coffee_shop/`), independent of the RLS/DB migrations.
