# CS-062 — Tenant onboarding (F62) — development plan

> Feature F62 · Priority P0 · Size M. Unblocked by CS-061 (auth) + CS-070 (routing).
> Carries the trial-status auth-gate fix (trial tenants currently 403).

## Context
The platform can authenticate and route tenants, but **no tenant can be created** — every tenant exists only
because migration `0001` seeded `maven-demo`. CS-062 adds the signup→verify→trial→provision path so a new SMB
owner can self-serve a working tenant. It is the last P0 that gates the paid SaaS surface (CS-065 billing and
the rest of Wave 2). It also resolves the latent **trial-status** bug: `auth_access_state` (`0011`) admits only
`status='active'`, so a `trial` tenant authenticates at login but is 403'd on every request — which would make
the 14-day trial unusable the moment onboarding creates trial tenants.

**Current surface:** onboarding is greenfield (no signup/verify/provision code). `tenants` has no `trial_ends_at`
/ `provisioned_at`; `tenant_users` has no `email_verified_at`. No email infrastructure exists (verification is a
token stub, like CS-061c invitations). The verified NY rule set lives as `maven-demo`'s tenant-scoped
`labor_rules` (jurisdictions US-FED/NY-STATE/NYC); there is no jurisdiction **template** source yet (F19 is
unbuilt). `RESERVED_SLUGS` lives in `api/routing.py` (reuse for write-time slug validation). DB head = `0015`.

## Non-negotiable patterns (reuse — do NOT invent)
- Tenant creation needs owner privileges (`tenants` is ENABLE-not-FORCE RLS; `app_rw` cannot insert). Do it
  through a `SECURITY DEFINER` `onboarding_*` function (definer = migrator), `SET search_path = pg_catalog,
  public`, `REVOKE ALL … FROM PUBLIC` then `GRANT EXECUTE … TO auth_svc` — the exact `0011`/`0012` idiom. The
  gateway already connects as `auth_svc` for the pre-tenant/anonymous path.
- Verification tokens mirror `auth_password_reset_tokens` (`0011`): store only a peppered **digest**,
  single-use, time-boxed, `FOR UPDATE` on consume, constant-time compare.
- Reuse `RESERVED_SLUGS` from `api/routing.py` for signup slug validation (shared policy, F62.AC1 ↔ CS-070).
- Reversible downgrade (BUG-040 lesson); the deployment-contract test asserts the single linear head.
- Tests = stdlib `unittest`, NO pytest; in-memory fixtures for logic + static migration contract + DB-skip live.

## Deliverables

### A. Migration `0016_cs062_onboarding.py` (`revision="0016"`, `down_revision="0015"`)
**Schema**
- `ALTER TABLE tenants ADD trial_ends_at TIMESTAMPTZ NULL, ADD provisioned_at TIMESTAMPTZ NULL;`
- `ALTER TABLE tenant_users ADD email_verified_at TIMESTAMPTZ NULL;`
- `onboarding_verification_tokens(id, tenant_id, user_id, token_digest TEXT UNIQUE, expires_at, consumed_at,
  created_at)` — composite FK `(tenant_id,user_id)→tenant_users`, RLS ENABLE + `tenant_isolation`.
- `labor_rule_templates` — **global** (no `tenant_id`, no RLS; like `weather_codes`): `(jurisdiction, rule_id,
  …the labor_rules rule columns…)`, PK `(jurisdiction, rule_id, effective_from)`. Seed the verified **NY-STATE
  +NYC** set at migration time by snapshotting `maven-demo`'s `labor_rules` (jurisdictions US-FED/NY-STATE/NYC)
  into it. This is the F19-lite template source; any jurisdiction absent here is "unverified".

**Functions (SECURITY DEFINER → `auth_svc`)**
- `onboarding_signup(p_slug, p_name, p_jurisdiction, p_timezone, p_owner_email, p_owner_password_hash,
  p_trial_days, p_now) RETURNS TABLE(tenant_id uuid, user_id uuid)` — one transaction: INSERT `tenants`
  (`status='trial'`, `trial_ends_at = p_now + p_trial_days`), INSERT the owner `tenant_users` (role `owner`),
  return both ids. A duplicate slug hits the `tenants.slug UNIQUE` constraint → the repo maps it to **409**
  (do not pre-check-then-insert; let the constraint be the race-safe authority).
- `onboarding_issue_verification(p_tenant_id, p_user_id, p_digest, p_expires_at)` — supersede any live token,
  insert a new one (mirrors `auth_issue_password_reset`).
- `onboarding_verify_email(p_digest, p_now) RETURNS TABLE(tenant_id uuid, user_id uuid)` — `FOR UPDATE`,
  fail-closed if not found / consumed / expired; set `tenant_users.email_verified_at = p_now`; mark consumed.
- `onboarding_suspend_expired_trials(p_now) RETURNS integer` — `UPDATE tenants SET status='suspended' WHERE
  status='trial' AND trial_ends_at <= p_now`; returns the row count (idempotent — re-run suspends nothing new).
- `onboarding_provision(p_tenant_id, p_jurisdiction, p_now) RETURNS boolean` — **idempotent + jurisdiction-
  gated**: if `tenants.provisioned_at` is already set → return false (no-op). If no `labor_rule_templates` row
  exists for `p_jurisdiction` → `RAISE EXCEPTION 'jurisdiction has no verified rule set'` (refuse — never
  NY-default, F62.AC4 edge). Else copy the template rules into the tenant's `labor_rules` (re-stamped
  `tenant_id`), seed default `labor_standards` (`throughput_standards`/`cost_targets` defaults), set
  `provisioned_at = p_now`, return true. All in the function's transaction.
- **Trial fix:** `CREATE OR REPLACE FUNCTION auth_access_state(...)` widening `tenant_active := t.status IN
  ('active','trial')` (was `='active'`), so trial tenants authenticate. Downgrade restores the `='active'` form.

### B. Gateway service + API (`coffee-scheduler/api/` + a new `onboarding` module)
- `onboarding` service (mirrors `auth/service.py` seams): `signup(slug, name, jurisdiction, email, password, …)`
  — validate slug **format** + **reserved denylist** (reuse `RESERVED_SLUGS`) → 422/409; Argon2id-hash the
  password (reuse `auth/passwords.py`); call `onboarding_signup`; issue a verification token (stub notifier,
  link returned in dev like CS-061c); then issue the normal access+refresh pair (owner is logged in, trial,
  unverified). `verify_email(token)`, `resend_verification(principal)`, `provision(principal)` (owner-only).
- Endpoints (`api/app.py` / a router): `POST /signup {slug,name,jurisdiction,email,password}` (public) → 201 +
  tokens + `verify_url` (dev); `POST /verify-email {token}` (public); `POST /onboarding/provision` (owner,
  verified) → idempotent; `POST /onboarding/resend-verification` (owner). All tenant/role from the reconciled
  principal, never the body (except signup, which has no tenant yet).
- **Verified-gate:** a reusable `require_email_verified` dependency (403 "email not verified" when
  `email_verified_at` is NULL) to protect solver-class actions. The solver itself is F11 (unbuilt); apply the
  guard to `provision` and document it as the hook the future `/schedule` endpoint uses (F62.AC2).
- Repository seam: add the `onboarding_*` calls to the `AuthRepository`/`DatabaseAuthRepository`/pooled path
  (or a parallel `OnboardingRepository`) — one thin `onboarding_*` call each; map the slug `UniqueViolation`
  **and** the P0001 `RaiseException` conflicts to typed errors the API maps to 409/422 (the BUG-041 lesson:
  map `RaiseException`, not only `UniqueViolation`).

### C. Nightly suspension job
- `scripts/suspend_expired_trials.py` — connects as `auth_svc`, calls `onboarding_suspend_expired_trials(now)`,
  logs the count; idempotent and safe to run repeatedly (cron/ops wiring is deployment, out of scope here).

### D. Docs
- `docs/plans/CS-062-tenant-onboarding.md` (this file); a signup/verify/trial/provision section in
  `docs/architecture/manager-ui-integration.md`; `docs/schema.md` updated (new columns/tables/template) in the
  same change (migration-skill rule); `Backlog.md`/`FEATURES.md` status; `api/.env.example` (`TRIAL_DAYS` etc.).

## Edge cases & pitfalls (minimize bugs)
- **Atomic signup** — tenant + owner commit or roll back together (one function/transaction); a failure after
  the tenant insert must not leave an owner-less tenant.
- **Duplicate slug race** — rely on `tenants.slug UNIQUE`; map both `UniqueViolation` and the function's
  `RAISE` (P0001) to 409 at the repository (BUG-041 class — don't map only one).
- **Reserved / malformed slug** — reject before the DB using the shared `RESERVED_SLUGS` + the `_SLUG` format;
  case-insensitive; signup and CS-070 routing must agree on the policy.
- **Trial routability vs auth gate** — `resolve_active_slug` already routes `trial`; this plan widens
  `auth_access_state` to match, so a trial tenant both routes **and** authorizes (otherwise trial is unusable).
- **Idempotent provisioning** — gated by `tenants.provisioned_at`; a second call is a no-op (not a duplicate
  seed, not an error). A test runs it twice and asserts identical row counts.
- **Unverified jurisdiction refused** — a jurisdiction with no `labor_rule_templates` rows raises; the tenant is
  **not** seeded with NY rules. Signup may still create the tenant (trial), but provisioning refuses until a
  verified template exists (F19). Decide + document: signup accepts only jurisdictions present in the template
  registry **or** creates the tenant but blocks provisioning — **chosen:** signup accepts any well-formed
  jurisdiction, provisioning refuses unverified ones (so the owner can sign up while rules are pending).
- **Email verification fail-closed** — unknown/consumed/expired token → uniform failure; the plaintext token is
  shown once (dev), only the digest stored; unverified owner can sign in (AC2) but is blocked from solver-class
  actions by `require_email_verified`.
- **Suspension reachability** — a suspended tenant is blocked by the widened `auth_access_state` (403) and by
  `resolve_active_slug` (404 when routing is enabled). The `/billing/*` exemption (F62.AC3) is **CS-065** scope
  (no billing routes exist) — noted, not built.
- **Owner-privilege containment** — only the `SECURITY DEFINER` `onboarding_*` functions (granted `auth_svc`)
  insert `tenants`; `app_rw` never gains tenant-table DML.
- **No hard deletes** — onboarding never issues `DELETE`; the guard stays clean.
- **No pytest** — stdlib `unittest`; in-memory fixtures + static migration contract + DB-skip live.

## Verification (end-to-end)
1. `alembic upgrade head` → `0016`; `alembic downgrade 0015 && alembic upgrade head` clean round-trip.
2. `test/test_cs062_onboarding.py` (in-memory + live DB): signup creates tenant+owner atomically in `trial`
   with `trial_ends_at`; duplicate slug → 409; reserved/malformed slug → rejected; verify-email happy +
   fail-closed; unverified owner blocked from the verified-gated action, verified allowed; provisioning seeds
   NY rules + standards, is idempotent on re-run, and **refuses** an unverified jurisdiction (no NY default);
   `onboarding_suspend_expired_trials` flips expired trials and is idempotent.
3. `test/test_cs062_trial_access.py`: a `trial` tenant now passes `auth_access_state` (authorizes `/me`/`/stores`),
   while `suspended`/`churned` still 403 — proves the trial fix without regressing suspension.
4. `test/test_cs062_onboarding_deployment_contract.py` (static): `0016` present, `down_revision='0015'`, single
   linear head, downgrade drops the new functions/tables/columns and restores `auth_access_state`.
5. Existing suites green: `test_f61*` (65), `test_api_foundation` (9), `test_isolation_gate` (14),
   `test_soft_delete*` (3), `test_cs070*` (16), `test_data_access` (7); `scripts/check_no_hard_delete.py` clean.

## Critical files
- **New:** `db/migrations/versions/0016_cs062_onboarding.py`; `api/onboarding.py` (service + router) or an
  `onboarding/` module; `scripts/suspend_expired_trials.py`; `test/test_cs062_onboarding.py`,
  `test_cs062_trial_access.py`, `test_cs062_onboarding_deployment_contract.py`; this plan.
- **Edited:** `api/app.py` (signup/verify/provision routes + `require_email_verified`), `api/db.py` /
  repository (onboarding calls + conflict mapping), `auth/repository.py` or new `OnboardingRepository`,
  `docs/schema.md`, `docs/architecture/manager-ui-integration.md`, `api/.env.example`, `Backlog.md`,
  `FEATURES.md`.
- **Reuse:** the `0011`/`0012` SECURITY-DEFINER + grant + reversible-downgrade idiom; `auth/passwords.py`,
  `auth/tokens.py` (verification token digest), `RESERVED_SLUGS` (`api/routing.py`); `auth_svc` role;
  `app_current_tenant()`; maven-demo's NY `labor_rules` as the template seed source.

## Scope guard — explicitly OUT
- First-run **wizard UI** (frontend add-store/employee/hours/plan) → frontend follow-up (CS-031–035); CS-062
  delivers the backend signup/verify/provision + a minimal API.
- Real **email delivery** → stub (link returned in dev), like CS-061c; real SES/SMTP is a later story.
- The full **F19 jurisdiction rule library** → CS-062 ships the template **mechanism** + the verified NY set;
  other jurisdictions are refused until F19 registers them.
- `/billing/*` suspension exemption, plan selection/payment → **CS-065**.
- The actual **solver** endpoint (`require_email_verified` protects it) → **F11**.
- `tenant_secrets` encrypted config → **CS-063**.

## Orchestration (Codex dev + tests; Claude review)
- **Codex** (`codex exec`, `gpt-5.6-terra`, high) develops the slice **and** writes + runs the tests, iterating
  until `0016` round-trips, the three CS-062 tests pass, and all existing suites + the no-hard-delete guard stay
  green. Hand it this plan + the `0011`/`0012` idiom + the "no pytest / stdlib unittest" + soft-delete rules.
- **Claude** reviews for introduced bugs/edge cases (atomic signup, the slug 409 double-mapping, the trial-gate
  widening not regressing suspension, idempotent + jurisdiction-refusing provisioning, owner-privilege
  containment), **files a Backlog bug story per finding**, and once green **commits + pushes** (trunk `main`;
  standalone `git push`; trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`).
