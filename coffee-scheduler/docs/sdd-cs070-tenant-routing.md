# CS-070 / F70 SDD: Tenant Routing and Browser-Edge Security

**Status:** Specification for implementation and independent validation  
**Date:** 2026-11-17  
**Priority / size:** P0 / S  
**Depends on:** CS-061a, CS-061b, CS-061c  
**Blocks:** CS-068b, CS-062  
**Related defects:** BUG-045 (login never captures client IP → tenant-wide lockout + blind audit; its trusted `X-Forwarded-For` / client-IP boundary is owned here and consumed by CS-061b). Also folds in the trial-status routability fix: `resolve_active_slug` treats `status IN ('active','trial')` as routable, so a trial tenant is not 404'd (and the CS-061b `auth_access_state` gate should be widened to match — tracked separately).

## 1. Purpose and outcome

CS-070 resolves each tenant request from its canonical public authority in production or its explicit path prefix in development. The routing context must be established before authentication is bound to that tenant and before protected tenant data, RLS-scoped database work, or tenant-sensitive cache entries are accessed.

The authority selects the tenant. Credentials authorize the caller for that already-resolved tenant; credentials, forwarding headers, cookies, query parameters, and request bodies never select or change tenants.

Completion means that:

- Production requests under `https://<slug>.coffeescheduler.com` and development requests under `/t/<slug>/` resolve one eligible tenant.
- Missing, unroutable, suspended, churned, and lookup-error tenants fail closed without revealing tenant state.
- Ambiguous or malformed authorities are rejected before tenant lookup or route execution.
- A credential for tenant A cannot access tenant-scoped routes resolved for tenant B.
- Safe noncanonical production host aliases redirect to one canonical HTTPS origin.
- CORS, CSRF, cookie, proxy, rewrite, RLS, and cache behavior preserve tenant boundaries.
- Existing CS-061a/b login, refresh, revocation, password-reset, throttling, and MFA behavior remains compatible.

## 2. Scope and exclusions

In scope:

- Production subdomain routing and development path-prefix routing.
- Authority parsing, canonicalization, reserved-slug rejection, proxy trust, redirects, tenant lookup, and immutable request context.
- Binding authenticated principals to the resolved tenant.
- Transaction-local RLS context lifecycle and pooled-connection cleanup.
- Exact-origin CORS, cookie-authenticated CSRF, secure cookies, Next rewrites, cache partitioning, and wildcard TLS deployment evidence.

Out of scope:

- Signup, tenant creation, email verification, trial provisioning, and authoritative write-time slug validation (CS-062).
- The full cross-tenant HTTP attack matrix and CI isolation gate (CS-068b).
- Custom domains, CNAME ownership, per-domain certificate issuance, invitations, SaaS admin/impersonation, billing, and quotas.

## 3. Definitions and interfaces

- **Request authority:** the single validated `Host` or HTTP/2 `:authority`; trusted forwarded authority is accepted only under the proxy rules below.
- **Resolved tenant:** the canonical tenant UUID and slug returned by the tenant repository after authority/path validation.
- **Tenant route context:** immutable `{tenant_id, slug, path}` attached to request scope before downstream protected processing.
- **Tenant request:** any non-global route that reads or mutates tenant-owned data.
- **Safe method:** `GET`, `HEAD`, or `OPTIONS`; only `GET` and `HEAD` are eligible for canonical redirects.

Required injectable seams (names may vary while preserving behavior):

- `TenantRoutingConfig`: mode, platform suffix, development host/prefix, trusted proxy CIDRs, and HTTPS policy.
- `TenantRoute`: immutable canonical tenant UUID, slug, and downstream application path.
- `TenantResolver.resolve(authority, path, headers, peer_ip)`: validates and resolves the tenant or raises a generic resolution error.
- `TenantRepository.resolve_active_slug(slug)`: one narrow lookup returning a routable tenant or `None`.
- `TenantRoutingMiddleware`: raw-header validation, canonical redirect, resolution, context installation, origin/CSRF enforcement, then downstream dispatch.
- `authorize_principal_tenant(route, principal)`: rejects principal/route tenant mismatch before protected data access.
- Existing `open_session_context(..., role="app_rw", tenant_id=...)`: sole DB tenant-context binding mechanism.

## 4. Acceptance criteria

### AC1 — Resolve and install tenant context before protected work

1. In production, exactly one valid DNS label immediately before the configured platform suffix resolves as the slug. The platform apex and multi-label tenant prefix do not resolve as tenants.
2. In development, only the configured development host and exact `/t/<slug>/` prefix are accepted. The prefix is removed, and the remaining application path and query are preserved.
3. Validate authority/path syntax and reserved slug before repository access. For a syntactically valid non-reserved slug, make exactly one canonical repository lookup.
4. Install immutable route context containing canonical tenant UUID, slug, and downstream path before auth comparison, route execution, RLS setup, or tenant cache access.
5. Missing, unroutable, suspended, churned, and tenant lookup-error outcomes all produce the same cache-safe 404 response. Do not reveal whether the slug exists or its status.
6. Malformed or ambiguous authority is rejected before repository lookup, handler execution, RLS setup, or cache access.
7. Repository errors fail closed; never fall back to a default, demo, or previously resolved tenant.

### AC2 — Authority parsing, proxy trust, reserved namespace

1. Inspect raw ASGI headers before conversion to a mapping. Reject repeated `Host`, repeated `:authority`, repeated `Forwarded` or `X-Forwarded-Host`, and any disagreement between `Host` and `:authority`. Identical duplicates are still ambiguous and rejected.
2. Use direct authority unless the peer IP is in configured trusted proxy CIDRs and the forwarding chain is syntactically valid. Forwarded authority from untrusted peers, malformed/multiple chains, comma lists, or conflicts with direct authority fail closed.
3. Reject user-info, commas, whitespace/control bytes, fragments, slashes, backslashes, percent-encoded authority characters, IP literals, empty labels, invalid ports, and malformed DNS labels. Do not repair malformed values into another tenant.
4. Normalize valid DNS hostnames to lowercase and remove exactly one terminal DNS dot. Production accepts no explicit port or the configured default HTTPS port only; other ports are rejected. Development port policy is explicit and cannot weaken production.
5. Enforce a case-insensitive read-side reserved-slug denylist containing at least `admin`, `www`, `api`, `app`, `auth`, `billing`, `status`, `static`, and `assets` before lookup. CS-062 must reuse the policy for authoritative write-time validation.
6. Validate configuration at startup: production platform suffix must be a DNS suffix (not empty, wildcard, IP literal, or malformed); development host/prefix must be valid; trusted proxy networks must parse; HTTPS cannot be disabled in production; reserved namespace cannot collide with configured routing hosts.

### AC3 — Credential and tenant binding

1. Authentication validates token signature, issuer, audience, expiry, and revocation according to CS-061a/b before comparing tenant identity.
2. Every protected tenant route compares the authenticated principal tenant UUID with the resolved route tenant UUID. Mismatch returns a generic 403 before data fetch, RLS transaction, or cache read.
3. Request headers, path, query, body, cookies, and JWT claims cannot override the resolved tenant UUID.
4. Routes that do not install tenant routing preserve the existing CS-061a same-origin behavior; routes that do install it must enforce route/principal binding consistently.

### AC4 — Canonical redirects

1. For production `GET` and `HEAD` only, redirect safe aliases that differ solely by hostname case, one terminal DNS dot, or an explicit default HTTPS port.
2. Return 308 to `https://<normalized-slug>.<configured-platform-suffix><raw-path>[?<raw-query>]`.
3. Construct the location only from validated slug and configured suffix. Never reflect attacker-controlled host, scheme, port, or redirect destination.
4. Preserve raw path and query octets exactly; do not decode encoded separators or otherwise alter path semantics.
5. Perform redirect before tenant lookup, handler execution, RLS, and cache access. Add `Cache-Control: no-store` and `Vary: Host`.
6. Do not redirect unsafe methods. Malformed, ambiguous, foreign, apex, reserved, and non-default-port authorities fail closed. Canonical authorities do not loop. Development path-prefix routing does not use production redirects.

### AC5 — RLS and pooled connection lifecycle

1. The DB tenant UUID is sourced only from canonical resolver output.
2. Tenant DB work binds context using the established transaction-local session-context helper and non-owner application role; no process-global tenant state or persistent connection attributes are permitted.
3. Each tenant DB operation has explicit transaction semantics. Success, exception, cancellation, and disconnect release context before a physical connection is reused.
4. A same-connection tenant-A then tenant-B test proves no tenant context or role leaks between requests.
5. Redirects, resolution failures, principal mismatch, CORS rejection, and CSRF rejection do not open tenant RLS transactions.

### AC6 — CORS, CSRF, and cookie behavior

1. Allow only the exact canonical origin `https://<resolved-slug>.<platform-suffix>`. Development origins are explicitly configured and exact.
2. Parse Origin structurally. Reject `null`, malformed, suffix/substring lookalikes, unexpected ports, scheme downgrade, and arbitrary localhost ports. Never reflect arbitrary origin and never combine credentials with wildcard origin.
3. Apply origin policy to preflight and actual requests. Rejected origins receive no permissive ACAO or credential headers.
4. Cookie-authenticated unsafe methods require a CSRF proof bound to resolved tenant and origin, compared in constant time, before handler or data access.
5. Missing, malformed, expired, mismatched, or replayed CSRF proof returns generic 403. A bearer credential does not disable CSRF checks if the request is also cookie-authenticated.
6. Preserve CS-061 cookie requirements: `HttpOnly; Secure; SameSite=Strict`; access cookie path `/`; refresh cookie path `/auth/browser`. Logout expires cookies on matching paths with matching security attributes.
7. Production refuses plaintext configuration. Development has no insecure production bypass.

### AC7 — Global routes, rewrites, caching, deployment

1. Explicitly classify health, `/.well-known/`, Next static assets, and global auth/API paths. They do not accidentally acquire tenant prefixes. Tenant data routes cannot bypass routing via an overly broad exemption.
2. Next rewrites proxy only configured same-origin paths, preserve safe path/query semantics, never use attacker-controlled Host as upstream authority, and retain CS-061 refresh cookie path behavior.
3. Tenant responses are cache-safe and partitioned by canonical tenant context/authority and route. Cross-tenant responses cannot share a cache entry. Redirect and error responses cannot poison tenant caches.
4. Deployment configuration provides evidence for apex and wildcard `*.coffeescheduler.com` TLS, HTTP-to-HTTPS redirect, ingress routing, and certificate expiry/renewal monitoring.
5. Local manifest/config assertions are mandatory. Live DNS/certificate issuance is an optional external probe; if unavailable, report it as skipped, never as a pass.

## 5. Failure semantics

| Condition | Result | Required ordering / prohibited behavior |
|---|---|---|
| Unknown, suspended, churned, or lookup-error tenant | Generic cache-safe 404 | No handler, RLS, cache read, or status disclosure |
| Duplicate or malformed authority | Opaque 400 (or generic 404 only where resolver contract specifies) | Reject before lookup; never select last header |
| Valid principal for another resolved tenant | Generic 403 | Before data access, RLS, or cache |
| Origin or CSRF rejected | Generic 403/no permissive CORS | Before handler or tenant data access |
| Valid safe alias host | 308 canonical HTTPS redirect | Before lookup/handler/RLS/cache; preserve raw path/query |
| Unsafe request on alias authority | Fail closed | Never redirect/replay mutation |
| Invalid production config | Startup failure | No default tenant or insecure fallback |
| DB error/cancellation | Rollback and release | Never leak tenant context to pool borrower |

## 6. Test plan

Run the SDD contract and CS-070 gateway integration suites first, then CS-061a/b auth and gateway regressions, CS-030 API tests, Next lint/build, and available DB/RLS tests. Keep all negative assertions intact.

Required test groups:

- **Authority parsing:** lowercase/uppercase, terminal dot, default/non-default ports, apex, extra labels, empty/oversized labels, IDNA policy, IP literals, user-info, comma lists, control bytes, encoded slash/dot, backslash, malformed percent sequences.
- **Duplicate authorities:** identical/conflicting Host in both orders; duplicate `:authority`; Host/`:authority` agreement and disagreement; duplicate forwarded headers; trusted versus untrusted proxy peers.
- **Development routing:** correct host/prefix, missing slug, duplicate slashes/prefixes, encoded separators, host/path conflicts, and path/query preservation.
- **Ordering spies:** invalid authority and reserved slug do not call lookup; resolution failure, principal mismatch, origin/CSRF failure, and redirects do not reach handler, data, RLS, or cache.
- **Tenant/auth:** tenant A credential on A succeeds; same credential on B fails before fetch; spoofed tenant hints have no effect; unknown and suspended response shape is identical.
- **Redirects:** raw path/query, GET/HEAD only, no loop, no open redirect, malformed/non-default/foreign authority rejection, and no cacheable alias response.
- **Client IP / BUG-045:** two distinct client IPs do not share a login-throttle bucket; a trusted `X-Forwarded-For` from the Next proxy yields the real client IP; an untrusted peer's forwarded IP is ignored; audited login records the real IP, not `0.0.0.0`.
- **CORS/CSRF/cookies:** exact-origin preflight/actual response; hostile/null/malformed origin; credentials; safe/unsafe methods; missing/wrong/replayed proof; simultaneous bearer and cookie; login/refresh/logout cookie flags and paths.
- **Rewrites/cache:** upstream Host safety, route exemptions, path/query fidelity, tenant cache separation, `Vary: Host`, and no-store behavior.
- **RLS/pool:** same-connection tenant switch, exception/cancellation cleanup, and no DB session for rejected requests. Use disposable PostgreSQL/PgBouncer where available; report unavailable harness explicitly.
- **Deployment:** config validation, TLS ingress manifest, HTTPS redirect policy; live TLS probe may be skipped only as external evidence.
- **Compatibility:** CS-061a/b browser auth and refresh; CS-030 API; Next build/lint and browser tests when dependencies are installed.

Any new reproducible defect must receive a detailed BUG record containing root cause, exact reproduction, impact, priority, and testable acceptance criteria. It blocks integration until fixed and revalidated.

## 7. Delivery and definition of done

1. **Codex owns development AND the tests**; **Claude** independently reviews the delivered code for introduced bugs/edge cases, files a Backlog bug story per finding, runs the suites, and commits/pushes. (This matches the project's standing division of labor and overrides any earlier draft that reversed it.) Keep specification, tests, implementation, and fixes in traceable commits.
2. Independent validation must test the exact candidate tree and return explicit results with commands, counts, and skips. The run must include the **BUG-045** client-IP/throttle regression.
3. Do not mark a BUG resolved solely because an implementation report says it is fixed. Resolve only after its regression passes in independent validation.
4. After approval, integrate only the approved candidate; update CS-070 and BUG statuses; run focused smoke tests; commit status changes; push `main` and verify upstream synchronization.
5. CS-070 is done only when all in-scope local tests pass, no known in-scope BUG remains open, deployment evidence is present, and the approved commit is pushed.
6. Do not start CS-068b until this gate passes.
