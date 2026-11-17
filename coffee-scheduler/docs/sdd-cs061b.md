# CS-061b — Auth hardening SDD

**Feature:** F61 AC3–AC4 (PLAN §15.2; architecture §14.2)
**Status:** specification-ready; this SDD and the red tests precede implementation.
**Scope:** per-request revocation (tenant `status` + user `is_active` recheck backed by a
revocation list), secure single-use time-boxed password reset, login rate-limiting /
lockout, and optional TOTP MFA for `owner`/`manager`.

CS-061b is the second auth slice. It builds on the completed CS-061a boundary
(`docs/sdd-cs061a.md`): Argon2id tenant identities, RS256/JWKS access tokens, opaque
rotating refresh families, the four `/auth/*` transports, and the fail-closed gateway
cutover (`api/app.py`, `api/auth_gateway.py`). CS-061b does **not** re-specify any of
that; it adds the hardening that AC3/AC4 require on top of it.

## Boundary

| Owned by CS-061b | Explicitly not owned here |
| --- | --- |
| Per-request revocation: tenant-`status` and user-`is_active` recheck on every protected request, a revocation list (per-`jti` single-token revocation + per-principal session-cutoff) so suspension/deactivation/logout take effect within one access-token TTL or sooner; password reset (request + redeem, time-boxed single-use hashed token, generic responses, session invalidation on redeem); login rate-limiting / account lockout / IP throttle; optional TOTP MFA enrolment + verification for `owner`/`manager` (encrypted secret at rest, replay/window handling, single-use recovery codes). | **CS-061a (done):** identity/credential storage, RS256 issuance/validation, refresh rotation/reuse detection, transport separation, canonical membership reconciliation. **CS-061c:** `tenant_invitations` + invite/accept, shared `users`/`tenant_memberships` redesign, the append-only `audit_log`, SaaS-admin login + impersonation + their MFA *flow*. **CS-062:** signup/tenant creation, email verification, the trial-suspension job that *sets* `tenants.status`. **CS-068b:** the end-to-end HTTP cross-tenant isolation gate. **CS-070:** CSRF/CORS policy, routing, and the frontend session UX. **F66:** per-tenant quota/usage 429s (a different limiter from login throttling). |

Non-goals that must not leak into this story's code or tests: invitation flows, the audit
log table, SaaS-admin authentication or impersonation, CSRF/CORS headers, route/middleware
ordering policy, and generic per-tenant API quota limiting. CS-061b touches only the auth
surface named above.

Relationship to CS-061a revocation: CS-061a already revokes a refresh **family** on replay
and already rejects a token whose reconciled membership is missing/inactive inside
`TenantAuthService.authenticate_access_token`. CS-061b adds the two checks CS-061a
deliberately deferred — tenant `status` and an explicit revocation list — and wires them
into the gateway's per-request path. The existing CS-061a method keeps its exact behaviour;
the new behaviour is additive (a new `authorize_access_request` entry point and new fixture
knobs), so every CS-061a contract stays green.

## Acceptance criteria

Numbered continuing from the CS-061a SDD; each is a single testable assertion.

### AC3 — per-request revocation (F61.AC3)

1. **Migration `0011_cs061b_auth_hardening.py`** has `revision = "0011"`,
   `down_revision = "0010"`, and leaves the Alembic graph with one linear head at `0011`.
2. The migration adds a **per-principal session cutoff** — `tenant_users.sessions_revoked_at
   TIMESTAMPTZ NULL` — and a **per-token revocation list** table `auth_revoked_tokens`
   (`jti UUID PRIMARY KEY`, `tenant_id`, `user_id`, `revoked_at`, `expires_at`, composite FK
   `(tenant_id, user_id) REFERENCES tenant_users (tenant_id, id)`). `expires_at` lets a
   background sweep prune entries once the token could no longer validate.
3. `TenantAuthService.authorize_access_request(token)` performs, in order: CS-061a
   signature/shape validation and canonical membership reconciliation (reuse, not
   re-implement), then **tenant status** (`active` only; `trial`/`suspended`/`churned`/any
   non-active value is refused), then **revocation** — the token is refused if its `jti` is
   in `auth_revoked_tokens` **or** its `iat` precedes the user's `sessions_revoked_at` cutoff.
   A refusal maps to the same opaque authentication failure externally.
4. A suspended tenant and a deactivated (`is_active = false`) user are both refused on the
   **next** protected request, even with a still-unexpired access token minted before the
   change — not only after the 15-minute access TTL elapses.
5. **Deactivating a user, logging out, and a completed password reset all revoke the user's
   live sessions**: each stamps `sessions_revoked_at = now()`, so every access token with
   `iat < now()` fails `authorize_access_request`, and every refresh in the user's families
   is revoked so it cannot mint a fresh access token. Single-session logout instead inserts
   that token's `jti` into `auth_revoked_tokens`.
6. The gateway's protected dependency (`api/app.py` `current_principal`) calls
   `authorize_access_request`, so `/stores` and `/me` return **403** for a suspended tenant
   and **401** for a revoked/deactivated principal, while an active principal still returns
   200. Tenant identity still comes only from the reconciled claim, never request input.

### AC4 — password reset, login protection, and MFA (F61.AC4)

7. **Password reset request** (`PasswordResetService.request_reset(tenant_slug, email)`)
   is **enumeration-safe**: a known-active, a known-inactive, and an unknown
   `(tenant_slug, email)` all return the identical externally observable outcome, do
   comparable work, and never reveal whether the account exists. A reset secret is minted
   and its **peppered one-way digest** (never the raw secret) is stored in
   `auth_password_reset_tokens` only when the account exists; the caller cannot tell.
8. A reset token is **time-boxed and single-use**: it is valid for exactly
   `PASSWORD_RESET_TTL_SECONDS` from issue; redeeming it (`redeem_reset(secret,
   new_password)`) atomically marks it `consumed_at` and sets the new Argon2id hash. A
   second redeem, an expired token, a revoked/consumed token, a digest mismatch, a
   malformed/oversized token, and an unknown token are **all** refused, and only the newest
   outstanding token for an account is redeemable (issuing a new reset invalidates prior
   outstanding ones).
9. Redeeming a reset **invalidates the user's existing sessions** (stamps
   `sessions_revoked_at` and revokes refresh families), so a token stolen before the reset
   stops working after it (ties to AC3.5).
10. **Login rate-limiting / lockout** (`LoginRateLimiter`) keys a failure counter on the
    **account** `(tenant_id, email)` and, independently, on the client **IP**. After
    `MAX_FAILED_ATTEMPTS` failures inside `LOCKOUT_WINDOW_SECONDS`, the account is locked for
    `LOCKOUT_DURATION_SECONDS` and further logins are refused **without** performing the
    password verification. A successful login **resets** the account counter.
11. Lockout is **tenant-scoped and does not enable cross-tenant DoS**: locking
    `(tenant_A, alice@x)` never locks `(tenant_B, alice@x)` — the same email in another
    tenant is unaffected — and the shared-IP throttle is scoped/bounded so one tenant's
    traffic cannot lock a different tenant's accounts, and a failed-login flood cannot
    permanently deny a victim account outside its own window. Concurrent failures are counted
    atomically: N simultaneous bad attempts record N failures (no lost-update under race),
    and the lock engages at the threshold exactly once.
12. **Optional TOTP MFA** is available only to `owner`/`manager`
    (`MfaEnrollmentError`/role guard for `staff`/`read_only`). The TOTP secret is stored
    **encrypted at rest** (`tenant_users.mfa_secret_ciphertext BYTEA`) via `MfaSecretCipher`
    keyed from `AUTH_MFA_ENC_KEY`; the plaintext secret is never persisted, logged, or
    returned after enrolment. Verification accepts a code within a bounded
    `TOTP_WINDOW_STEPS` skew, **rejects replay** of an already-used time-step
    (`mfa_last_timestep` is monotonic), and recovery codes are single-use (stored as
    digests, consumed on use).
13. When MFA is enabled for a user, **no access/refresh pair is issued on password alone**:
    `login_json`/`login_browser` raise `MfaRequiredError` after a correct password and mint
    tokens only once a valid TOTP code **or** an unused recovery code is supplied. A wrong,
    reused, expired, or out-of-window code yields the generic authentication failure and no
    tokens (bypass resistance).
14. **DB privilege / RLS / deployment.** Every CS-061b relation follows the CS-061a
    least-privilege pattern: no role holds direct DML; all access is through `auth_*`
    `SECURITY DEFINER` functions with a pinned `search_path`, `REVOKE ALL … FROM PUBLIC`,
    and `GRANT EXECUTE … TO auth_svc` only; the new tenant-scoped tables `ENABLE` RLS with a
    `tenant_isolation` policy. `app_rw`/`app_ro` gain nothing. `.env.example` documents the
    new `AUTH_MFA_ENC_KEY`, `AUTH_PASSWORD_RESET_PEPPER`, and the login-throttle tunables,
    and reaffirms that `AUTH_DATABASE_URL` is the `auth_svc` login principal.

## Security invariants and edge cases

- **Fail-closed everywhere.** A missing cutoff, an unreadable revocation row, a malformed
  `jti`/`iat`, an absent throttle key, or a missing/short pepper or MFA key is a refusal or a
  hard configuration error — never an open default. Reuse the CS-061a "no silent default"
  (D2) discipline.
- **One opaque failure.** Reset, lockout, MFA, and revocation refusals that reach a client
  must be externally indistinguishable from an ordinary credential failure — no message, no
  status, and no timing that reveals account existence, MFA-enabled state, lock state, or the
  revocation reason. Lockout returns the generic auth failure (optionally with a
  `Retry-After`), not "account locked for user X".
- **Secrets never leak.** Reset secrets, the reset pepper, raw TOTP secrets, the MFA
  encryption key, and recovery codes are never persisted in the clear, returned after their
  one issuance, or logged. Only peppered/encrypted digests reach storage, keyed so lookups
  carry no secret-dependent comparison branch; digest/secret comparisons are constant-time.
- **Revocation precedes tenant context.** `authorize_access_request` must run the status +
  revocation checks **before** any F60 tenant context is opened for the request, so a
  revoked token never reaches tenant data.
- **Time-boxing is explicit and symmetric.** Reset TTL and TOTP window/period are fixed
  constants; any clock skew is small, explicit, and symmetric. TOTP replay protection is by
  monotonic time-step, not wall-clock equality.
- **No cross-tenant blast radius.** Account lockout, IP throttle, reset issuance, and MFA
  state are per-tenant/per-user; no CS-061b mechanism lets one tenant lock out, enumerate, or
  deny another tenant's users. The IP throttle must degrade (delay/limit) rather than hard-
  lock a victim account a shared NAT cannot control.
- **MFA cannot be bypassed.** With MFA enabled, neither the JSON nor the browser login path,
  nor a refresh, may yield usable tokens without a valid TOTP or recovery code; disabling MFA
  is itself a guarded, session-revoking action.
- **RLS remains the backstop.** As in CS-061a, the anonymous reset/throttle/MFA lookups run
  through owner-run `SECURITY DEFINER` functions and must not broaden `app_rw`/`app_ro`.
- **Transport hygiene.** Malformed/ambiguous input on every new path — oversized tokens,
  duplicate/empty fields, a login body missing a credential, a non-UUID `jti` — is rejected
  before any cryptographic or database work, mirroring the CS-061a input-bounds rules. CSRF
  and CORS are explicitly out of scope (CS-070).

## Required seams and tests

The implementation may add adapters but must make these concepts independently testable
without a database or real secrets, extending the CS-061a fixture style
(`TenantAuthService.for_contract_fixture(...)`):

- `auth.revocation` — `RevocationList` / `InMemoryRevocationList`, and
  `auth.service.TenantSuspendedError`, `auth.service.SessionRevokedError`,
  `TenantAuthService.authorize_access_request`. New fixture knobs: `tenant_status=…`,
  `user_is_active=…` (exists), `sessions_revoked_at=…`, and a seam to revoke a `jti`.
- `auth.reset` — `PasswordResetService`, `PasswordResetError`,
  `PASSWORD_RESET_TTL_SECONDS`, a minted-token shape carrying `secret`/`digest`/`expires_at`,
  and `request_reset` / `redeem_reset`.
- `auth.ratelimit` — `LoginRateLimiter`, `AccountLockedError`, `LoginAttemptKey`
  (`tenant_id`/`email`/`ip`), and the `MAX_FAILED_ATTEMPTS`, `LOCKOUT_WINDOW_SECONDS`,
  `LOCKOUT_DURATION_SECONDS` constants.
- `auth.mfa` — `TOTPService` (`verify` with window + monotonic replay guard),
  `MfaSecretCipher` (`from_environment`, `encrypt`/`decrypt`), `RecoveryCodeService`,
  `MfaRequiredError`, `MfaReplayError`, `MfaEnrollmentError`, and the `TOTP_PERIOD_SECONDS`,
  `TOTP_WINDOW_STEPS` constants.

Test files (red before implementation, each `spec: F61.ac3`/`F61.ac4` tagged):

- `test/test_f61_revocation_contract.py` — AC3 status/`is_active`/revocation-list checks,
  pre-suspension token rejected next request, deactivation/logout/reset revoke sessions.
- `test/test_f61_password_reset_contract.py` — AC4 enumeration safety, hashed single-use
  time-boxed token, reuse/expiry/invalidation, session invalidation on redeem.
- `test/test_f61_login_throttle_contract.py` — AC4 account+IP keys, lockout threshold/window,
  reset-on-success, no cross-tenant DoS, concurrency/race.
- `test/test_f61_mfa_contract.py` — AC4 owner/manager-only, encryption at rest,
  replay/window, recovery-code single-use, bypass resistance.
- `test/test_f61_hardening_deployment_contract.py` — AC3.1/AC3.2/AC4.14 migration graph,
  schema fragments, `auth_*` privilege/RLS pattern, and `.env.example` additions (no DB).
- `test/test_cs061b_gateway_integration.py` — AC3.6 per-request enforcement through the ASGI
  gateway (suspended → 403, revoked/deactivated → 401, active → 200), plus malformed input.

## Verification and handoff

Before implementation, run:

```sh
python3 -m unittest \
  coffee-scheduler/test/test_f61_revocation_contract.py \
  coffee-scheduler/test/test_f61_password_reset_contract.py \
  coffee-scheduler/test/test_f61_login_throttle_contract.py \
  coffee-scheduler/test/test_f61_mfa_contract.py \
  coffee-scheduler/test/test_f61_hardening_deployment_contract.py \
  coffee-scheduler/test/test_cs061b_gateway_integration.py
```

The command is expected to be **red** because migration `0011`, the `auth.revocation` /
`auth.reset` / `auth.ratelimit` / `auth.mfa` modules, the `TenantAuthService` hardening
seams, the gateway wiring, and the new `.env.example` keys do not exist yet. The developer
must turn them green by supplying that production code without removing or weakening any
negative case, and must report the migration, the new public API, the non-secret environment
variable names, and green test evidence. CS-061b does not change final story status; the
coordinator owns the status move once implementation lands.
