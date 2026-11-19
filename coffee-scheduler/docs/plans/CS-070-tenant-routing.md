# CS-070 — Tenant routing & browser-edge security (F70) — development plan

> Detailed contract: `docs/sdd-cs070-tenant-routing.md` (the SDD, corrected). This plan layers the
> **scope decisions** (what is built+tested now vs deferred to deployment evidence) and the orchestration.
> Feature F70 · Priority P0 · Size S→M.

## Context
CS-061 (a/b/c) is done and the manager shell (CS-030) is live, but tenant selection is still implicit —
the gateway resolves a tenant only from the signed JWT, there is no routing layer, no reserved-slug denylist,
no fail-closed "unknown/suspended → 404", and no trusted client-IP boundary. CS-070 adds the request-edge
routing that **selects the tenant from the public authority (prod subdomain) / dev path-prefix before auth**,
binds the authenticated principal to that resolved tenant, and establishes the trusted-proxy boundary. It is
the last P0 blocker for **CS-062** (onboarding) and **CS-068b** (HTTP isolation gate), and it owns the fix for
**BUG-045** (login never captures the real client IP → tenant-wide lockout + blind audit).

**Current surface:** `api/db.py::resolve_tenant_id(slug)` resolves *any* slug with no status filter;
`api/app.py` reads tenant only from JWT claims; `coffee_shop/middleware.ts` gates on the access cookie;
`next.config.ts` rewrites `/api/*` and `/auth/browser/*` same-origin. No routing/host/slug logic exists.
The demo is single-tenant (`maven-demo`), same-origin, no real subdomains/DNS/TLS.

## Scope decisions (locked — minimize bugs, stay testable without DNS/TLS)
**Build + test now (the real security value):**
1. **Tenant resolver + routing config** at the gateway (FastAPI): strict authority parsing, dev `/t/<slug>/`
   path-prefix, reserved-slug denylist, trusted-proxy rule, fail-closed uniform 404. Exercised via ASGI
   `TestClient` with synthetic headers + live DB — **no DNS/TLS needed**.
2. **`resolve_active_slug(slug)`** returns a tenant only when `status IN ('active','trial')`; suspended /
   churned / unknown / lookup-error → the *same* cache-safe 404 (no tenant-state disclosure). **This folds in
   the trial-status routability fix** (a trial tenant must route, not 404).
3. **Trusted client-IP extraction → BUG-045 fix:** derive the real client IP from `X-Forwarded-For` **only**
   when the immediate peer is a configured trusted proxy (the Next server); thread it through
   `login`/`browser_login`/refresh into the CS-061b throttle **and** the CS-061c audit entry.
4. **Principal↔route tenant binding:** a dependency that returns a generic 403 when the authenticated
   principal's `tenant_id` ≠ the resolved route tenant, **before** any data fetch / RLS transaction.
5. **Origin-based CSRF for cookie-authenticated unsafe methods:** reject an unsafe (`POST`/`PUT`/`PATCH`/
   `DELETE`) cookie-authenticated request whose `Origin` (or `Referer` fallback) is not the canonical origin —
   a lightweight, real defense layered on the existing `SameSite=Strict` + same-origin design (no full
   per-request CSRF-token system).
6. **Canonical redirect (prod, `GET`/`HEAD` only):** 308 for safe host aliases (hostname case, one terminal
   dot, explicit default HTTPS port) to `https://<slug>.<suffix><raw-path>`; location built only from the
   validated slug + configured suffix (no open redirect / no host reflection); `Cache-Control: no-store`,
   `Vary: Host`; never redirect unsafe methods.
7. **Startup config validation (fail-fast):** platform suffix is a real DNS suffix, trusted-proxy CIDRs parse,
   HTTPS cannot be disabled in prod, reserved namespace cannot collide with routing hosts.
8. **Next-side plumbing:** middleware forwards the original `Host` so the gateway sees it as the trusted
   forwarded authority; keep the same-origin rewrites; document the subdomain topology.

**Deferred to deployment evidence / notes (NOT heavy code now):**
- Wildcard `*.coffeescheduler.com` TLS issuance, HTTP→HTTPS at the edge, cert-renewal monitoring → a
  deployment manifest + config assertions; a live DNS/TLS probe is optional and **reported as skipped**, never
  as a pass.
- Full cross-origin CORS allow-list engine + per-route cache partitioning → the app is same-origin by design;
  implement the structural origin check (#5) and `Vary: Host`/`no-store` on redirect/error responses, and
  **note** a fuller CORS/cache-partition layer as follow-up.
- The complete cross-tenant HTTP attack matrix + CI isolation gate → **CS-068b**.
- Custom domains / CNAME / per-domain certs, signup write-time slug validation → **CS-062** (which **reuses**
  this reserved-slug policy module).

## Deliverables
- **New** `api/routing.py` — `TenantRoutingConfig`, raw-header/authority parsing, `RESERVED_SLUGS` (shared),
  trusted-proxy client-IP + forwarded-authority resolution, `TenantResolver.resolve(...)`, the canonical-
  redirect builder, and the origin-CSRF check. `api/routing_middleware` (in `routing.py` or `app.py`) installs
  the immutable `{tenant_id, slug, path}` route context before downstream work.
- **Edited** `api/db.py` — add `resolve_active_slug(slug)` (status-filtered, owner role, login/routing only);
  `api/config.py` — routing config + trusted-proxy CIDRs + platform suffix + HTTPS policy, validated fail-fast;
  `api/app.py` — install routing middleware, principal↔route binding dependency, and thread `client_ip` into
  the login routes; `auth/api.py` — pass `client_ip` into `login`/`browser_login`/refresh;
  `coffee_shop/middleware.ts` + `next.config.ts` — forward `Host`, platform config.
- **Tests** `test/test_cs070_routing.py` (authority parsing, duplicate/ambiguous authority, dev path-prefix,
  reserved slug, fail-closed 404 for unknown/suspended/churned, trial routable, principal↔route 403, ordering
  spies that prove no lookup/RLS/handler on rejected requests, canonical redirect GET/HEAD-only + no-open-
  redirect + no-loop, origin-CSRF, startup config validation) and `test/test_cs070_client_ip.py` (the BUG-045
  regression: two IPs don't share a throttle bucket; trusted XFF → real IP; untrusted XFF ignored; audit
  records the real IP). stdlib `unittest`, ASGI `TestClient`, DB-skip pattern, mutations rolled back.
- **Docs** — the corrected SDD; a routing section appended to `docs/architecture/manager-ui-integration.md`;
  `Backlog.md`/`FEATURES.md` status; resolve **BUG-045** only after its regression passes independent review.

## Edge cases & pitfalls (minimize bugs)
- **Trusted-proxy spoofing** — never trust `X-Forwarded-For`/`X-Forwarded-Host` from a peer outside the
  configured trusted CIDRs; a client-supplied forwarded header must not select a tenant or a client IP.
- **Authority ambiguity** — reject duplicate `Host`/`:authority`/`Forwarded`/`X-Forwarded-Host` and any
  `Host`↔`:authority` disagreement (identical duplicates are still ambiguous) **before** lookup.
- **Reserved slug before lookup** — the denylist (`admin,www,api,app,auth,billing,status,static,assets,…`) is
  checked before any repository access; shared with CS-062.
- **Fail-closed uniformity** — unknown / suspended / churned / lookup-error return the *same* 404 body; never
  reveal existence or status; a repository error never falls back to a default/demo/previous tenant.
- **No DB work on rejected requests** — resolution failure, principal mismatch, origin/CSRF rejection, and
  redirects must short-circuit **before** any RLS transaction opens (ordering-spy tests enforce this).
- **No tenant/role leak across pooled connections** — the resolver only *provides* `tenant_id`; binding stays
  transaction-local via `open_session_context`; a same-connection A→B test proves no leak (reuse the
  CS-068a/isolation discipline).
- **Open-redirect / host-reflection** — the 308 location is built only from the validated slug + configured
  suffix; never reflect attacker host/scheme/port; preserve raw path/query octets; canonical authority must not
  loop.
- **Trial routability vs auth gate** — `resolve_active_slug` routes `trial`; note that the CS-061b
  `auth_access_state` gate still rejects trial (`status='active'` only) — widening that gate is tracked
  separately so routing and auth agree (otherwise a trial tenant routes but 403s per request).
- **Config fail-fast** — a missing/invalid platform suffix, unparseable trusted CIDR, or HTTPS-disabled-in-prod
  must fail startup, never degrade silently.
- **No pytest** — stdlib `unittest`; DB-skip on `DATABASE_URL`/`GATEWAY_DATABASE_URL`; mutations rolled back.

## Verification (end-to-end)
1. `python3 -m unittest discover -s test -p 'test_cs070*.py' -v` — routing + client-IP contracts pass.
2. BUG-045 regression green (two IPs independent; real IP in throttle + audit).
3. Existing suites stay green: `test_f61*` (59), `test_api_foundation` (9), `test_isolation_gate` (14),
   `test_soft_delete*` (3) — no regression; `test_data_access` (7).
4. `cd coffee_shop && npm run build` (and lint) clean with the middleware/config changes.
5. Startup config validation: an invalid prod config fails fast; a valid dev config boots.
6. Deployment evidence present (manifest/config assertions); live DNS/TLS probe reported skipped if
   unavailable, never as a pass.

## Orchestration (standing pattern)
- **Codex** (`codex exec`, `gpt-5.6-terra`, high) develops the full slice **and** writes + runs the tests,
  iterating to green. Hand it this plan + the corrected `docs/sdd-cs070-tenant-routing.md` as the contract, the
  CS-061c/0012 + CS-087/0013 idioms to mirror, the "no pytest / stdlib unittest" rule, and the standing
  no-hard-delete / soft-delete rules.
- **Claude** independently reviews for introduced bugs/edge cases (trusted-proxy spoofing, ordering, open
  redirect, fail-closed uniformity, the BUG-045 regression), **files a Backlog bug story per finding**, marks
  BUG-045 resolved only after its regression passes review, and once green **commits + pushes** (trunk `main`;
  standalone `git push`; trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`).
- Additive at the request edge (new routing layer + config); reuses `open_session_context`, the CS-061
  principal reconciliation, and the existing same-origin rewrite. No change to existing migrations or RLS
  policies.
