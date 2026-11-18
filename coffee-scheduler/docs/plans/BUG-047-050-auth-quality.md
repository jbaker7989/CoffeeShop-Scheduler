# BUG-047–050 Implementation Plan: Auth Throttle and Code Quality

**Status:** Approved implementation plan  
**Date:** 2026-11-18  
**Branch:** `codex/auth-quality-bug047-050`

## Objective and sequence

Fix the four stories recorded in `Backlog.md`, one at a time. Each story gets a focused
regression first, a narrowly scoped implementation, its relevant compatibility tests, and
one commit after the tests pass. The final push contains only these four story commits.

| Order | Story | Change | Primary verification |
| --- | --- | --- | --- |
| 1 | BUG-047 | Add explicit IP-bucket operations to the in-memory limiter and use them for invitation acceptance. | Limiter tests and ASGI invitation flow tests. |
| 2 | BUG-048 | Move arbitrary-claims signing from `TenantAuthService` into test-only support. | F61 auth, gateway, manager UI, and invitation suites. |
| 3 | BUG-049 | Enforce required CS-061b repository operations at adapter instantiation. | Repository-port contract, pooled adapter, and CS-061b suites. |
| 4 | BUG-050 | Expand dense auth/reset/invitation statements and remove duplicate digest work without changing behavior. | CS-061b reset/throttle and CS-061c invitation suites. |

BUG-047 fixes distinct-client isolation only. BUG-043 remains responsible for replacing the
per-process invite limiter with durable shared throttling; this plan does not claim to close
that separate distributed-state defect.

## Implementation specifications

### BUG-047 — client-IP bucket isolation

- Keep the account key `(tenant_id, normalized_email)` unchanged for account lockout.
- Add explicit client-IP bucket methods on `LoginRateLimiter` that use a tagged, normalized
  IP key so account and IP buckets cannot collide. Validate IP input with `ipaddress.ip_address`;
  invalid/missing addresses use a documented fail-closed policy and never collapse into a
  shared sentinel bucket.
- The invitation accept route checks and records failures against only the client-IP bucket;
  successful acceptance clears only that IP bucket. No endpoint-specific magic based on
  `tenant_id` or email is allowed.
- Test from two TestClient peers sharing one app: A reaches the lock threshold, B still accepts
  a valid invitation, A is throttled, and B's success does not clear A's state. Also cover
  window expiry and concurrent updates.

### BUG-048 — test-only token signer

- Remove `issue_contract_token` from `TenantAuthService`; production service methods must not
  sign arbitrary overridden claims.
- Add `coffee-scheduler/test/auth_test_support.py` containing a helper that accepts a test
  service and creates a signed adversarial token using its test token service. Keep helper
  construction in the test tree, not in the runtime `auth` package.
- Update every test call site. Add a contract asserting `TenantAuthService` exposes no
  arbitrary-claim signer and retain forged claim, wrong-membership, expiry, issuer, audience,
  role, and admin-claim rejection coverage.

### BUG-049 — enforce repository completeness

- Mark all required CS-061b repository operations (`access_state`, token/session revocation,
  reset, throttle, MFA, tenant status, and user-active operations) as abstract methods using
  standard multi-line signatures and `@abc.abstractmethod`.
- Keep the existing invitation/audit abstract methods. Do not split the interface in this
  patch; the minimal safe change is consistent runtime enforcement across all declared
  capabilities.
- Add an intentionally incomplete `AuthRepository` subclass and assert instantiation raises
  `TypeError` naming the missing operation. Confirm `InMemoryAuthRepository`,
  `DatabaseAuthRepository`, `PooledAuthRepository`, and existing production-shaped test
  adapters instantiate successfully.

### BUG-050 — remove auth-code compression

- Reformat only the security-sensitive code named in the story: `auth/ratelimit.py`,
  `auth/reset.py`, and invitation route handlers in `api/app.py`.
- Add return/parameter types to touched methods, use one statement per line, use named local
  variables for state changes, and retain existing public names and response shapes.
- Compute reset HMAC digest once per issued secret and reuse it for both lookup key and record.
- Do not change lockout semantics, reset expiry semantics, HTTP status codes, cookies, or
  behavior under BUG-043. Avoid repo-wide formatting.

## Delivery and test gates

For each story, update its status to fixed only after its tests pass, run `git diff --check`,
and commit the story independently with a `fix(...)` or `refactor(...)` subject naming the
BUG ID. If a story fails, keep it uncommitted, repair it, and rerun its focused tests before
continuing.

After all four commits, run the combined CS-061a/b/c regression set and inspect the branch
diff to ensure it contains no CS-070 SDD or unrelated workspace files. Push only
`codex/auth-quality-bug047-050` to `origin`. Do not merge; a PR/review is the integration
gate.

Required focused commands (run from `coffee-scheduler/`):

```sh
python3 -m unittest test.test_f61_login_throttle_contract test.test_f61_invitation_contract
python3 -m unittest test.test_f61_auth_contract test.test_cs061a_gateway_integration test.test_cs061a_manager_ui_compat
python3 -m unittest test.test_cs061b_production_repo_path test.test_cs061b_service_integration test.test_cs061b_gateway_integration test.test_cs061b_database_integration
python3 -m unittest test.test_f61_password_reset_contract test.test_f61_login_throttle_contract test.test_f61_invitation_contract test.test_f61_audit_contract
```

Database-dependent tests may skip only when their documented disposable database
configuration is absent; report skips explicitly. A skip is never counted as a pass.
