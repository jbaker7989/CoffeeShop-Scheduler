# CS-061c — Invitations, membership & append-only audit log (F61.AC5) — development plan

> On approval, written to the repo as `coffee-scheduler/docs/plans/CS-061c-invitations-audit.md`
> (matching the `docs/plans/*.md` convention).

## Context
CS-061a (core auth: `tenant_users`/`saas_admins`, Argon2id, RS256 + rotating refresh) and CS-061b (hardening:
per-request revocation, reset, throttle, TOTP MFA) are **✅ Done** (migrations `0010`/`0011`, head = `0011`).
The last open slice of the P0 auth umbrella is **CS-061c**, and it is entirely **greenfield** — no
`tenant_invitations`, no `audit_log`, no email infrastructure exists anywhere in the repo.

Today a tenant has exactly the users created at signup; **there is no way to add a manager/staff member**, and
there is no tamper-evident record of auth or admin events. CS-061c closes F61.AC5: owner/manager **invites a
member by email → accept creates the member**, and an **append-only `audit_log`** records auth events, admin
actions, and impersonation (both `impersonated_by` and the target). **CS-064b (admin suspend/impersonate)
explicitly depends on this audit log**, so the schema must support impersonation now even though the
impersonation *flow* ships in CS-064b.

**Membership-model decision (locked, bug-avoidance):** keep `tenant_users` as the membership row — *accept
creates a `tenant_users` row*, exactly as architecture §14.4 states ("accept creates the `tenant_users` row").
The advisory `users`/`tenant_memberships` split (one identity across many tenants) is **explicitly deferred**
to a future story: the whole CS-061a/b stack (FKs, RLS, `auth_*` functions, ~60 tests) is built on
one-tenant-per-row, and refactoring it for a P1 convenience is disproportionate risk. We note the deferral; we
do not build it here.

## Non-negotiable patterns to replicate (from CS-061a/b — do NOT invent a new path)
1. **No direct DML on auth tables.** Every write/read goes through a `SECURITY DEFINER` SQL/plpgsql function
   named `auth_*`, `SET search_path = pg_catalog, public`, `REVOKE ALL … FROM PUBLIC` then
   `GRANT EXECUTE … TO auth_svc`. Exact idiom: `db/migrations/versions/0011_cs061b_auth_hardening.py` lines
   27–66. `auth_svc` is `NOLOGIN`, no table privileges, not superuser/BYPASSRLS.
2. **RLS = ENABLE (not FORCE)** + `CREATE POLICY tenant_isolation … USING (tenant_id = app_current_tenant())`
   on tenant-scoped auth tables (0011 lines 18–25) — deliberately ENABLE-not-FORCE so the owner-run
   `SECURITY DEFINER` functions serve anonymous/pre-tenant flows.
3. **Repository seam.** Add abstract methods to `AuthRepository` (`auth/repository.py:125`) and implement in
   **all three**: `InMemoryAuthRepository` (fixtures), `DatabaseAuthRepository` (one thin `auth_*` call each,
   `dict_row`), and `PooledAuthRepository` (`api/auth_gateway.py:56`, per-connection delegate). Never
   `isinstance`-check the concrete repo (the BUG-038 lesson).
4. **Store token digests, never tokens.** Mirror `auth_password_reset_tokens` (0011:15): single-use,
   time-boxed, `token_digest TEXT UNIQUE`, constant-time compare, `FOR UPDATE` on consume.
5. **Reversible downgrade** (the BUG-040 lesson): drop every new overloaded function signature first, then
   tables; downgrade restores `0011` exactly. Deployment-contract test asserts head.
6. **Tests = stdlib `unittest`, NO pytest.** Logic against in-memory fixtures (`for_contract_fixture`);
   migration shape via `ast.parse` static contract; live DB tests DB-skip on `GATEWAY_DATABASE_URL`
   (`postgresql://coffeeshop:coffeeshop@localhost:6432/coffeeshop`), `prepare_threshold=None`.

## Deliverables

### A. Migration `0012_cs061c_invitations_audit.py` (`revision="0012"`, `down_revision="0011"`)
**Tables**
- `tenant_invitations(id UUID PK, tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  email CITEXT NOT NULL, role TEXT NOT NULL CHECK (role IN ('owner','manager','staff','read_only')),
  token_digest TEXT NOT NULL UNIQUE, invited_by UUID NULL, status TEXT NOT NULL DEFAULT 'pending'
  CHECK (status IN ('pending','accepted','revoked','expired')), expires_at TIMESTAMPTZ NOT NULL,
  accepted_at TIMESTAMPTZ NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())`.
  - `invited_by` FK → `(tenant_id, id)` on `tenant_users` (composite, `ON DELETE SET NULL`).
  - **Partial unique index** `UNIQUE (tenant_id, email) WHERE status='pending'` — at most one live invite per
    email per tenant (duplicate pending → 409), while allowing re-invite after accept/revoke/expire.
  - RLS ENABLE + `tenant_isolation`.
- `audit_log(id BIGSERIAL PK, tenant_id UUID NULL, actor_id UUID NULL, actor_type TEXT NOT NULL
  CHECK (actor_type IN ('tenant_user','saas_admin','system')), action TEXT NOT NULL, target_type TEXT NULL,
  target_id UUID NULL, impersonated_by UUID NULL, before JSONB NULL, after JSONB NULL, ip INET NULL,
  user_agent TEXT NULL, at TIMESTAMPTZ NOT NULL DEFAULT now())`.
  - **Append-only:** RLS ENABLE + `tenant_isolation` (`USING (tenant_id = app_current_tenant())`), plus a
    `WITH CHECK` on INSERT. `tenant_id NULL` is allowed (pre-tenant/anonymous auth events, SaaS-admin actions).
  - **Grants (per arch §14.3 "`app_rw` has INSERT only"):** `GRANT INSERT, SELECT ON audit_log TO app_rw`;
    **no** `UPDATE`/`DELETE` to anyone (tamper-evidence). `app_rw` can only insert rows for its *own* tenant
    (WITH CHECK). `GRANT USAGE, SELECT ON SEQUENCE audit_log_id_seq TO app_rw`.

**Functions (SECURITY DEFINER → `auth_svc`)** — the only production write port for invites and NULL-tenant
audit:
- `auth_create_invitation(p_tenant_id, p_email, p_role, p_token_digest, p_invited_by, p_expires_at)
  RETURNS uuid` — expires any stale pending invite for the same `(tenant,email)` first (set `status='expired'`
  where `expires_at<=now()`), rejects if an active `tenant_users` row already exists for that email
  (`RAISE EXCEPTION 'already a member'`), rejects a second live pending invite, inserts, returns id.
- `auth_get_invitation(p_token_digest) RETURNS TABLE(...)` — STABLE; returns the invite row incl.
  `status`/`expires_at` for validation (no tenant context needed — accept is anonymous).
- `auth_accept_invitation(p_token_digest, p_password_hash, p_now) RETURNS TABLE(tenant_id uuid, user_id uuid,
  role text)` — plpgsql, `FOR UPDATE`: fail-closed if not found / `status<>'pending'` / `expires_at<=p_now` /
  a member with that email now exists (race). On success: `INSERT INTO tenant_users(...)` with the invited
  role + supplied hash, mark invite `status='accepted', accepted_at=p_now`, return the new principal. Atomic.
- `auth_list_invitations(p_tenant_id) RETURNS TABLE(...)` — STABLE, pending/non-expired first (admin UI).
- `auth_revoke_invitation(p_tenant_id, p_invitation_id) RETURNS boolean` — set `status='revoked'` where still
  pending; `RETURN FOUND`.
- `auth_record_audit(p_tenant_id, p_actor_id, p_actor_type, p_action, p_target_type, p_target_id,
  p_impersonated_by, p_before jsonb, p_after jsonb, p_ip inet, p_user_agent text) RETURNS bigint` — single
  INSERT, returns id. This is the port for **auth-origin** events (login success/fail, refresh-reuse, reset,
  invite lifecycle) where `tenant_id` may be NULL or no app_rw tx exists.

### B. Repository (`auth/repository.py` + `api/auth_gateway.py`)
Add to `AuthRepository` ABC and implement in `InMemory`/`Database`/`Pooled`:
`create_invitation(...) -> uuid`, `get_invitation(token_digest) -> InvitationRecord | None`,
`accept_invitation(token_digest, password_hash, when) -> AcceptedInvite | None`,
`list_invitations(tenant_id) -> list[InvitationRecord]`, `revoke_invitation(tenant_id, invitation_id) -> bool`,
`record_audit(entry: AuditEntry) -> int`. New frozen dataclasses `InvitationRecord`, `AcceptedInvite`,
`AuditEntry` (no secrets; store only `token_digest`). `Database*` bodies are one `auth_*` call each.

### C. Service (`auth/service.py`)
`TenantAuthService` methods:
- `create_invitation(principal, email, role) -> InvitationIssued` — **RBAC** (see edge cases): only
  `owner`/`manager` invite; `role` requested must be `<=` inviter's role and `owner` only by an `owner`.
  Generate a 256-bit token, store its digest, set TTL (`INVITE_TTL`, default 72h), audit `invitation.created`,
  return the plaintext token **once** (for the link).
- `accept_invitation(token, password, display_name?) -> IssuedTokens` — validate password policy (reuse
  `passwords.py`), Argon2id-hash, call `repo.accept_invitation`, audit `invitation.accepted`, then issue the
  normal access+refresh pair so accept logs the user in.
- `list_invitations(principal)`, `revoke_invitation(principal, id)` — RBAC-gated, audited.
- **Audit weaving:** add `record_audit` calls at the existing auth events in `service.py`
  (`authenticate_credentials` success/failure, refresh **reuse-detected**, password-reset issued/consumed,
  session revoke). Keep them best-effort-but-logged — an audit insert failure must not silently swallow; wrap
  so a genuine auth result is never lost, but a failed audit is surfaced (log + metric), never a fail-open.
- Pluggable `InvitationNotifier` protocol with a default **log/no-op** implementation (prints the invite link).
  Real email (SES/SMTP) is **out of scope** → filed as a follow-up story; the link is returned in the API
  response for dev/test so the flow is testable end-to-end without a mail server.

### D. API (`api/app.py` + `auth/api.py`)
New endpoints (JSON; tenant/role from the reconciled `Principal`, never the body):
- `POST /invitations` `{email, role}` → owner/manager only (403 otherwise) → `{invitation_id, expires_at,
  accept_url}`.
- `GET /invitations` → list for the caller's tenant (owner/manager).
- `DELETE /invitations/{id}` → revoke (owner/manager), 404 if not pending.
- `POST /invitations/accept` `{token, password, display_name?}` — **public** (no auth; the token is the
  credential) → sets the browser cookie / returns the token pair exactly like login. Rate-limit by IP (reuse
  `ratelimit.py`).
Wire via the existing `create_auth_router`/`create_app` pattern; add a small `require_role(...)` dependency
built on `current_principal`.

### E. Docs
- `docs/architecture/manager-ui-integration.md` — append an **Invitations & audit** section (the invite→accept
  flow, the audit write ports: `app_rw` INSERT-only for app/admin actions vs `auth_svc` `auth_record_audit`
  for auth-origin/NULL-tenant events, and the impersonation columns reserved for CS-064b).
- The plan doc itself at `docs/plans/CS-061c-invitations-audit.md`.

## Edge cases & pitfalls (minimize bugs)
- **Role escalation via invite** — the invited `role` must be `<=` the inviter's role; only an `owner` may
  invite an `owner`; `staff`/`read_only` cannot invite at all. Enforce in the service **and** re-assert nothing
  in the request body can set tenant/inviter (both come from the signed `Principal`).
- **Accept race / duplicate member** — `auth_accept_invitation` runs `FOR UPDATE` and re-checks member
  existence inside the tx; the `UNIQUE(tenant_id, email)` on `tenant_users` is the final backstop (map the
  unique-violation to 409, not 500).
- **Token handling** — only the digest is stored; the plaintext token is shown exactly once; expired/accepted/
  revoked/unknown → uniform fail-closed (avoid an oracle that distinguishes "wrong" from "expired").
- **NULL-tenant audit rows** — anonymous auth events (failed login before tenant resolves, SaaS-admin actions)
  have `tenant_id NULL`; `app_rw` + RLS WITH CHECK cannot insert those, which is *why* `auth_record_audit`
  (SECURITY DEFINER, `auth_svc`) exists. App/admin tenant-scoped rows go through `app_rw` INSERT directly.
- **Append-only really append-only** — grant `app_rw` only `INSERT, SELECT`; never `UPDATE`/`DELETE`; a test
  asserts `has_table_privilege('app_rw','audit_log','UPDATE')` is false and an `UPDATE`/`DELETE` as `app_rw`
  raises.
- **Partial-unique correctness** — `UNIQUE (tenant_id, email) WHERE status='pending'` must not block re-invite
  after revoke/expire; covered by a test (invite → revoke → re-invite succeeds).
- **Reversibility (BUG-040)** — downgrade drops all six `auth_*` functions by exact signature, then both
  tables + the sequence grant; `test_*_deployment_contract` asserts head moved to `0012` and the chain is
  linear.
- **PgBouncer** — repository uses the existing pool (`prepare_threshold=None`); no new pool.
- **Audit failure isolation** — a failed audit write must be logged/surfaced (observability), never silently
  dropped and never allowed to fail-open an auth decision.
- **No email infra** — invite email is a logged stub + returned link; real delivery is a separate story, not a
  silent TODO.

## Verification (end-to-end)
1. `python3 -m unittest discover -s coffee-scheduler/test -p 'test_f61*.py' -v` — all existing ~60 auth
   contracts still green (no regression) + the new invitation/audit contracts.
2. New in-memory contract tests (no DB): invite happy path; role-escalation rejection; duplicate-pending 409;
   accept happy path (creates member + logs in); expired/revoked/unknown token fail-closed; accept race →
   member-exists; audit entries emitted for login success/failure + invite lifecycle.
3. New static deployment contract: `0012` present, `down_revision='0011'`, single linear head, downgrade drops
   the new functions/tables.
4. Live DB (when `GATEWAY_DATABASE_URL` reachable): apply `alembic upgrade head`; prove `app_rw` has
   INSERT+SELECT but **not** UPDATE/DELETE on `audit_log`; prove `auth_svc` executes the new functions and
   `app_rw` cannot; full invite→accept→login via the ASGI `TestClient`; then `alembic downgrade 0011` and
   re-`upgrade head` clean.
5. CS-068a isolation gate (`test/test_isolation_gate.py`) still green — the two new tenant-scoped tables must
   be caught by the fail-closed coverage sweep (add them to the gate's expected TENANT_TABLES).

## Critical files
- **New:** `coffee-scheduler/db/migrations/versions/0012_cs061c_invitations_audit.py`;
  `coffee-scheduler/test/test_f61_invitation_contract.py`, `test_f61_audit_contract.py`,
  `test_f61_invitations_deployment_contract.py`; `coffee-scheduler/docs/plans/CS-061c-invitations-audit.md`.
- **Edited:** `auth/repository.py` (ABC + 3 impls + dataclasses), `api/auth_gateway.py` (`PooledAuthRepository`
  delegates), `auth/service.py` (invite/accept/list/revoke + audit weaving + `InvitationNotifier`),
  `auth/api.py` + `api/app.py` (endpoints + `require_role`), `test/test_isolation_gate.py` (TENANT_TABLES),
  `docs/architecture/manager-ui-integration.md`, `coffee-scheduler/Backlog.md` + `FEATURES.md` (status),
  `coffee-scheduler/api/.env.example` (document `INVITE_TTL`, notifier mode).
- **Reuse (do not reimplement):** the `0011` function/grant/downgrade idiom; `auth/passwords.py` (Argon2id +
  policy), `auth/tokens.py` (secure token + digest), `auth/ratelimit.py`; `auth_svc` role; `app_current_tenant()`.

## Scope guard — explicitly OUT
- `users`/`tenant_memberships` identity split (one person → many tenants) → **deferred** (note it; do not build).
- Real invite **email delivery** (SES/SMTP) → follow-up story; CS-061c ships a logged stub + returned link.
- The **impersonation flow** (issuing an impersonation JWT, the admin UI) → **CS-064b**; CS-061c only lands the
  `audit_log` schema (`impersonated_by`/`actor_type='saas_admin'`) it depends on.
- Billing/onboarding, SaaS-admin app → CS-062/064/065.

## Orchestration (per the user's instruction: Codex does dev **and** tests)
- **Codex** (`codex exec`, `gpt-5.6-terra`, high reasoning) develops the full slice **and** writes + runs the
  test stories (migration `0012`, repository/service/API, in-memory + static + live DB tests), iterating until
  `test_f61*.py` and `test_api_foundation.py` pass and `test_isolation_gate.py` stays green. Hand Codex this
  plan verbatim as the spec, plus the `0011` idiom to mirror and the "no pytest / stdlib unittest" rule.
- **Claude** orchestrates, then reviews the delivered code for introduced bugs & uncovered edge cases
  (RBAC/escalation, append-only grants, NULL-tenant audit path, accept race, reversibility), **files a bug
  story in `Backlog.md` for each finding**, and once tests are green **commits and pushes to GitHub**
  (trunk-based on `main`; standalone `git push`; commit trailer `Co-Authored-By: Claude Opus 4.8
  <noreply@anthropic.com>`).
- Additive only (new migration + new `auth_*` functions + new endpoints); independent of existing RLS/data
  migrations.
