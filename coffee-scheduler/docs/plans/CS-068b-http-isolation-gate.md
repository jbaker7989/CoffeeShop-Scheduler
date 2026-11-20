# CS-068b — HTTP/auth multi-tenant isolation gate (F68) — development plan

> Feature F68 · Priority P0 (gate) · Size M. Unblocked by CS-061 (auth) + CS-070 (routing) + CS-062 (signup).
> The HTTP counterpart to the DB gate (CS-068a, `test/test_isolation_gate.py`).

> **Status: complete (2026-11-19).** `test/test_cs068b_http_isolation.py` is the stdlib-unittest
> HTTP/auth companion gate. It self-seeds A/B tenants through signup inside a rollback-only
> transaction and covers F68 AC2-HTTP, AC4, and AC5. No CI apparatus was added; the deferred
> impersonation flow and `/billing` exemption remain out of scope.

## Context
CS-068a certifies isolation at the **database** layer (cross-tenant R/W/U/D denial, FORCE RLS, WITH CHECK,
pooled context-bleed). But nothing yet certifies the **HTTP/auth boundary** end-to-end: that tenant A's
credential cannot reach tenant B's data or resources through the gateway, that forged/tampered/`alg=none` tokens
are refused, that suspended/unknown tenants fail closed **without existence disclosure**, and that a tenant
token cannot carry an admin-impersonation escalation. CS-068b lands those slices (F68 AC2-HTTP, AC4, AC5) as a
**CI gate** that regression-proofs the whole CS-061 + CS-070 + CS-062 surface before any multi-tenant deploy.

**Current surface to exercise:** the gateway `create_app(auth_service, *, routing_config=…, …)` (`api/app.py`)
with endpoints `/signup`, `/verify-email`, `/onboarding/*`, `/auth/login` + `/auth/refresh` +
`/auth/browser/*`, `/me`, `/stores`, `/invitations` (POST/GET/DELETE), `/invitations/accept`, `/healthz`,
`/.well-known/jwks.json`; `current_principal` reconciliation (401/403); the CS-070 principal↔route 403 binding;
`resolve_active_slug` (active+trial routable, else 404). `onboarding_signup` (CS-062) lets the gate **self-seed
two tenants + owners via the signup path**, so it does not depend on fragile pre-seeded `tenant_users`.

## Scope decisions (locked)
1. **Primarily a test gate**, like CS-068a — a comprehensive, CI-runnable HTTP isolation suite. It adds no
   migration and ideally little/no production code; any gap it exposes gets a **small fix + a bug story**.
2. **Self-seeding**, not fixture-dependent: create two tenants (A, B) via `POST /signup` (rolled back), each
   with an owner + tokens, so the gate survives the shared-DB churn and is hermetic.
3. **Exercise routing ENABLED** (build `create_app` with a dev `TenantRoutingConfig`) so the host/path-scoped
   cross-tenant and principal↔route cases are real; also cover the routing-OFF same-origin mode for the
   token-reconciliation cases.
4. **No existence disclosure is a first-class assertion:** unknown vs suspended tenant, and A-accessing-B's-
   resource, must be **indistinguishable** (same status/body), per the SDD (404, not 403, for a foreign
   resource id).

## Deliverables

### A. `test/test_cs068b_http_isolation.py` — the HTTP isolation gate (stdlib `unittest`, ASGI `TestClient`)
A reusable two-tenant harness + these groups, **every test tagged `spec: F68.ac#`**, DB-skip without
`GATEWAY_DATABASE_URL`, all mutations rolled back:

- **Fail-closed auth (F68.ac2):** no token → 401; empty/malformed cookie → 401; **expired** access token → 401;
  **tampered signature** → 401; **`alg=none`** → 401; wrong `iss`/`aud` → 401; a correctly-signed token whose
  `(tenant_id,user_id)` is not a real membership (cross-tenant/forged claim) → 401 (canonical reconciliation);
  `is_saas_admin=True` on a tenant token → 401 (no admin escalation, F68.ac4).
- **Cross-tenant data (F68.ac2):** A's token → `/me`,`/stores` reflect **only** A; B's resource id (an
  invitation created by B) accessed with A's token via `GET/DELETE /invitations/{id}` → **404** (not 403 — no
  existence disclosure); A can never mutate B's row.
- **Routing boundary (F68.ac2, routing enabled):** A's token on B's resolved host/`/t/<slugB>/` → **403**
  (principal↔route binding, before any data/RLS); unknown slug/host → 404; a **suspended** tenant's host → 404
  (`resolve_active_slug`); reserved slug → 404.
- **Suspended / churned (F68.ac2):** a suspended tenant's valid token → **403** on every protected endpoint
  (`/me`,`/stores`,`/invitations`), within one access-token TTL; `/billing/*` exemption is CS-065 (noted).
- **No existence disclosure (F68.ac2):** unknown tenant and suspended tenant yield **identical** responses;
  foreign-resource 404 is byte-identical to genuinely-absent 404.
- **Refresh / rotation (F68.ac4):** a tampered/expired/foreign-signed refresh token → 401; a **reused** refresh
  token (replay) → 401 and the family is revoked (CS-061a); refresh never crosses tenants.
- **Cache-key isolation (F68.ac4):** protected/tenant responses and error/redirect responses carry
  `Cache-Control: no-store` (and `Vary: Host` where routing set it); assert no response is cacheable in a way
  that could be shared across tenants.
- **PII / disclosure scrub (F68.ac4):** 401/403/404 bodies are **generic** (no email, tenant_id, slug,
  internal error text, or stack); a bad-login response never reveals whether the email/tenant exists.
- **CI-gate meta (F68.ac5):** a test (or a `scripts/ci`-free, stdlib) assertion that **every** method in this
  module carries a `spec: F68.ac#` tag (mirrors CS-068a's self-maintaining coverage meta-test).

### B. Small production fixes (only if the gate exposes a real gap)
If a case fails (e.g. a foreign-resource returns 403 instead of 404 → existence disclosure, a missing
`no-store` on an error, or a PII leak in an error body), apply the **minimal** fix in `api/app.py` /
`auth/api.py` / `api/routing.py` and cover it with the failing-first gate test. No speculative code.

### C. Docs
- `docs/plans/CS-068b-http-isolation-gate.md` (this file); update CS-068/F68 status (AC2-HTTP + AC4 + AC5 met);
  note the gate is the HTTP companion to CS-068a and the CI-gate-before-multi-tenant-deploy.

## Edge cases & pitfalls (minimize bugs)
- **403 vs 404 discipline** — a *foreign resource id* under one's own valid session is **404** (not found in
  my tenant scope, no disclosure); a *token used against another tenant's route/host* is **403** (principal↔
  route mismatch). The gate asserts each precisely; don't conflate them.
- **Self-seeded tenants must be isolated from maven-demo** — create ephemeral A/B with unique slugs; roll back;
  never assert against shared seed data that the sibling session may wipe.
- **alg-confusion / forged tokens** — build the adversarial tokens with the service's own
  `issue_contract_token` / `sign_unchecked` test seam (already used by CS-061a contracts) so the signature is
  genuine and the rejection is proven to be on claim/alg/reconciliation, not a lucky signature mismatch.
- **Routing on vs off** — reconciliation/fail-closed cases run with routing **off** (same-origin, unprefixed),
  the host/principal-binding cases with routing **on** (dev `/t/<slug>/`); don't accidentally test only one.
- **Suspended within TTL** — mint A's token while active, then flip A to suspended, and prove the **next**
  request is 403 (per-request `auth_access_state` recheck), not deferred to token expiry.
- **No network / no real email** — signup/verify use the dev token stub; the gate reads the returned
  `verify_url`/token, never a mail server.
- **Shared-DB resilience** — the gate self-seeds and rolls back; it must not fail merely because the sibling
  session wiped `tenant_users`/`labor_rules` (it creates its own). It still needs the DB at head `0016`.
- **No pytest** — stdlib `unittest`; the "CI gate" is a runnable unittest module + the spec-tag meta-test, not
  a new GitHub Actions apparatus (that CI tooling is out of scope — do not add `.github`/`ruff`/`scripts/ci`).

## Verification (end-to-end)
1. `GATEWAY_DATABASE_URL=… DATABASE_URL=… python3 -m unittest discover -s test -p 'test_cs068b*.py' -v` — all
   pass; the spec-tag meta-test proves every case is tagged `spec: F68.ac#`.
2. Existing suites stay green: `test_isolation_gate` (CS-068a, 14), `test_f61*` (65+), `test_api_foundation`
   (9), `test_cs070*` (16), `test_cs062*` (9), `test_soft_delete*` (3), `test_data_access` (7);
   `scripts/check_no_hard_delete.py` clean.
3. If any gate case drove a production fix, its failing-first test is green and no prior assertion is weakened.

## Critical files
- **New:** `test/test_cs068b_http_isolation.py`; `docs/plans/CS-068b-http-isolation-gate.md`.
- **Edited (only if a gap is found):** `api/app.py` / `auth/api.py` / `api/routing.py` (minimal fix + its
  failing-first test); `Backlog.md`, `FEATURES.md` (status).
- **Reuse:** `create_app` (routing_config), `onboarding_signup` (self-seed tenants), the CS-061a
  `issue_contract_token`/`sign_unchecked` adversarial token seam, `current_principal` reconciliation, the
  CS-070 principal↔route binding, `resolve_active_slug`; the CS-068a `test_isolation_gate.py` structure +
  spec-tag meta-test pattern.

## Scope guard — explicitly OUT
- The full **impersonation flow** (issuing admin impersonation tokens, `impersonated_by` logging) → **CS-064b**;
  CS-068b only asserts a *tenant* token cannot escalate to admin.
- `/billing/*` suspension exemption → **CS-065**.
- A GitHub Actions / `ruff` / `scripts/ci` CI apparatus → out of scope (a separate deliberate task if wanted).
- New isolation *features* — CS-068b certifies the existing boundary; it adds tests, not capabilities.

## Orchestration (Codex dev + tests; Claude review)
- **Codex** (`codex exec`, `gpt-5.6-terra`, high) develops the gate **and** runs it, iterating until the CS-068b
  suite + the spec-tag meta-test pass and all existing suites stay green; any production gap it finds gets a
  minimal failing-first fix. Hand it this plan + the CS-068a structure + the CS-061a adversarial-token seam +
  the "no pytest / stdlib unittest / no CI-apparatus" rules.
- **Claude** independently reviews (403-vs-404 discipline, the forged-token rejections are real, suspended-within-
  TTL, no existence disclosure, cache/PII assertions), **files a Backlog bug story per finding**, re-runs the
  gate against a restored DB, and once green **commits + pushes** (trunk `main`; standalone `git push`; trailer
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`).
