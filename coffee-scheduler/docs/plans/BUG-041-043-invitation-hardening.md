# BUG-041 and BUG-043 implementation and test plan

**Status:** implementation and required regressions validated; see evidence below
**Scope:** CS-061c invitation creation conflicts (BUG-041), anonymous invitation-accept throttling (BUG-043), and the acceptance-function correction exposed by live PostgreSQL verification.
**Out of scope:** BUG-044 invitation ordering, email delivery, invitation token format or lifetime, login throttle policy, and unrelated auth refactors.

## Shared engineering constraints

These endpoints are security-sensitive and have both an in-memory test double and a PostgreSQL production path. Keep the repository interface explicit and mandatory; do not add `isinstance`, `hasattr`, optional capability checks, or process-local production fallbacks. Treat the database path as authoritative in production. Preserve current CS-061a auth behavior and existing invitation authorization policy.

Implementation and tests should be delivered in separate logical commits: first story tests/specification, then the BUG-041 implementation and regressions, then BUG-043 implementation and regressions. Keep the plan and backlog evidence synchronized with actual test results. A test skipped because no database URL is configured is not live-database validation.

## BUG-041 — expected invite conflicts return HTTP 409

### Contract

`POST /invitations` returns HTTP 409 for an email that is already a member of the target tenant or already has a pending invitation. This must hold through the production pooled repository and real gateway route. A new invite remains successful; an expired pending invite remains replaceable according to current policy. Permission failures and unexpected database errors must not be converted to conflicts.

The database can signal expected conflict in two distinct ways:

- `SQLSTATE P0001` with exactly the known `auth_create_invitation` messages `already a member` or `pending invitation exists`.
- `SQLSTATE 23505` for the unique-index race when concurrent requests both pass the function's pre-check.

Only these cases become the repository's established conflict signal (`ValueError` today, translated by the service to `InvitationConflictError` and by the route to 409). Unknown `P0001` messages, connection errors, permission errors, and unrelated SQLSTATEs propagate unchanged.

### Implementation steps

1. Inspect the `auth_create_invitation` function, uniqueness constraints, repository conflict mapping, service translation, and route exception mapping before editing. Record the exact SQLSTATEs and primary messages; avoid matching driver class names or broad exception text.
2. In `DatabaseAuthRepository.create_invitation`, normalize only `23505` and the two known `P0001` primary messages. Preserve the original exception for every other failure. Ensure transaction rollback behavior remains correct for both pooled and autocommit connections; never return success after a failed insert.
3. Exercise the conflict path through `PooledAuthRepository` and the actual FastAPI route. Do not rely solely on repository mocks or the in-memory adapter.
4. Live acceptance testing exposed a separate issue on successful redemption: PL/pgSQL output parameters named `tenant_id` and `user_id` collide with unqualified column names in `auth_accept_invitation`. Add migration `0023` to qualify table columns and aliases. Retain function signature, return type, `SECURITY DEFINER`, pinned `search_path`, and existing `auth_svc` grant. Its downgrade must restore the exact previous function definition; it must not remove grants or change unrelated functions.

### BUG-041 tests

- Repository unit tests: known `P0001` member and pending-invite messages map to conflict; `23505` maps to conflict; unknown `P0001`, `08006`, and representative permission/schema failures remain the same exception objects and types.
- API/contract tests: duplicate pending invite and existing tenant member produce 409; unrelated operational failure is not reported as 409; unauthorized caller behavior is unchanged.
- Disposable PostgreSQL integration via the pooled production adapter and actual route: first invite returns 200; duplicate pending invite returns 409; inviting the existing member returns 409. Assert the original invitation remains intact and no extra row is written.
- Concurrency regression: issue simultaneous creates for the same tenant/email through the DB-backed path. Accept only the database-defined outcome of one success and one conflict (or the appropriate equivalent under serialized function execution); prohibit two successful invitations and any HTTP 500.
- Redemption regression: use a valid invitation token through a fresh pooled service and prove `auth_accept_invitation` succeeds after migration `0023`, creating the member and marking the invitation accepted. Check expired, revoked, and already-used tokens retain their existing opaque failure response.

### BUG-041 edge cases / safeguards

- Scope member lookup to the invitation's tenant; the same email in a different tenant must not be reported as a conflict.
- Do not convert every uniqueness failure on unrelated tables or every `P0001` into HTTP 409.
- Preserve email normalization and existing role validation semantics.
- Ensure errors do not expose SQL text, email enumeration details, invitation tokens, or database exception content to the client.
- Verify downgrade restores the old function body and upgrade restores the qualified version without altering `auth_svc` privileges.

## BUG-043 — shared, IP-only invitation-accept throttling

### Contract

Anonymous `POST /invitations/accept` failures are bounded across all gateway processes and survive a fresh pooled connection. State is keyed only by a canonical client IP, never by email, tenant, token, or user. Different IPs are independent. A success clears only the successful IP's bucket. Invalid, unknown, expired, revoked, and already-accepted tokens count as failed attempts and retain the same opaque 401 response. A request from a locked IP receives the existing opaque 429 without attempting token acceptance.

Reserve the attempt before validating the invitation so simultaneous requests cannot all pass a check-then-record window. Preserve existing `MAX_FAILED_ATTEMPTS`, `LOCKOUT_WINDOW_SECONDS`, and `LOCKOUT_DURATION_SECONDS` policy. Exactly the threshold-th request may proceed; subsequent attempts are blocked until lockout expiry. Rolling-window timestamps at the cutoff expire consistently in both in-memory and SQL implementations.

### Implementation steps

1. Add two required typed operations to `AuthRepository`: atomically reserve an attempt and clear an IP bucket after success. Implement both in `InMemoryAuthRepository`, `DatabaseAuthRepository`, and the pooled adapter; update every test double and abstract-method contract. No optional adapter detection or silent in-memory fallback is allowed.
2. Canonicalize IPs with `ipaddress.ip_address` before touching repository state. Reject malformed addresses. In the gateway, prefer the trusted `client_ip` supplied by configured proxy middleware, falling back to the direct socket peer only when the trusted value is absent. Do not parse arbitrary forwarding headers in this route.
3. Add migration `0022` with a global `inet`-keyed table holding only recent failure timestamps, lock expiration, and cleanup time. Do not store token, email, tenant, or password data. Keep each bucket bounded by validated policy values and reclaim expired state.
4. Expose only narrowly scoped `SECURITY DEFINER` functions with pinned `search_path`. Revoke table privileges from `PUBLIC`, `app_rw`, `app_ro`, and `auth_svc`; grant only function execution to `auth_svc`. Lock per canonical IP while pruning and reserving so concurrent requests cannot lose updates. Avoid lock-order cycles during global stale-row cleanup.
5. Make invalid/null/nonpositive SQL function inputs fail closed. Validate the service policy configuration at startup or reject invalid values before invoking SQL; never allow invalid config to increase the accepted attempt count or create an unbounded timestamp array.
6. Route failure statuses through the existing opaque response shapes. If throttle reservation fails due to database/service unavailability, fail closed with 503 and do not call invitation acceptance. On successful redemption, clear only that IP; if cleanup fails after redemption has committed, log the operational failure without turning the already-committed success into a false 500. Ensure no raw IP or token is logged.
7. Downgrade migration `0022` in dependency-safe order: drop completion function, reservation function, then table/index. Verify no stale functions, table, index, grants, roles, or columns remain. Keep the linear chain `0021 -> 0022 -> 0023` and test full upgrade/downgrade/upgrade.

### BUG-043 tests

**In-memory/repository tests**

- Distinct IPv4 and IPv6 addresses maintain isolated buckets; IPv6 textual aliases canonicalize to one bucket.
- The threshold-th attempt is admitted and following attempts are locked; concurrent calls produce exactly the permitted count.
- A success clears only that IP; a success from another IP cannot unlock it.
- Failure window and lockout expire at their exact boundaries; timestamps at the rolling cutoff are pruned consistently.
- Invalid config fails closed, malformed IP is rejected before state mutation, and naive datetimes are rejected.
- Repeated requests and cleanup do not grow memory state without bound beyond active IP buckets; expired state is reclaimed.

**Gateway and live PostgreSQL tests**

- Two independent apps, services, pools, and fresh connections share state through PostgreSQL; app B observes app A's threshold/lock.
- A second IP can still redeem a valid invitation while the first IP is locked. A successful redemption clears only its own bucket.
- Concurrent reservations across pools preserve the exact count and do not cross-contaminate another IP.
- Verify invalid/expired/revoked/used token failures all count, have identical 401 shape, and never disclose token validity; locked requests return 429 without invoking redemption.
- Application roles cannot directly read or mutate the table or execute protected functions. The `auth_svc` production path can call the approved interface.
- Include fresh-service/pool restart behavior and verify the durable lock still applies.
- Verify no process-local limiter remains on the production route and no memory fallback masks DB unavailability; throttle DB errors return 503 before acceptance.

**Migration tests**

- Static contract test pins revision order, key type, bounded state shape, security-definer settings, grants/revokes, and complete downgrade statements.
- Disposable PostgreSQL upgrade to head, verify table/functions/privileges, downgrade to `0021`, verify absence of every introduced object, then upgrade to head and verify operation again.
- Keep the existing F43 reversibility checks intact; do not weaken broad schema-cleanup assertions to accommodate the new table.

### BUG-043 edge cases / safeguards

- Trusted proxy configuration must be the only source of forwarded client IP; direct test clients use their socket address. A missing/invalid client IP fails closed rather than sharing a placeholder bucket.
- Preserve IPv4 versus IPv4-mapped IPv6 semantics deliberately; test and document whether these are distinct keys under `inet` and Python canonicalization.
- Use aware UTC timestamps and handle clock equality consistently between Python and PostgreSQL. Do not let a clock rollback produce negative-age pruning or early unlock behavior.
- Validate integer types and practical upper bounds (at most 100 failures and 86,400 seconds for either window or lockout) so malformed values cannot exhaust memory, overflow PostgreSQL `make_interval`, or create very long denial periods. Repository and SQL entry points reject invalid values closed before state changes; any future environment parsing must also reject invalid startup config.
- Ensure cleanup and reservation are safe under concurrent transactions on different IPs and under duplicate requests for the same IP. Never hold a global lock while waiting on per-IP locks.
- Keep `auth_svc` function grants least-privilege and pin the definer's search path. Verify function ownership and table access assumptions on a pristine database.
- Return the same outward error for every invalid invitation token class; rate-limit status is the only intentional distinction. Never use acceptance result details to clear state for another IP.

## Work sequence and evidence

1. Keep the story-first unit and integration tests committed before their corresponding production fixes where possible; confirm they fail for the intended reason on the old implementation.
2. Complete BUG-041 exception mapping and `0023` correction; run focused unit/API tests plus live DB duplicate, member, concurrency, and valid-redemption tests.
3. Complete BUG-043 repository methods, API wiring, and `0022`; run in-memory/gateway contract tests, migration contract, and disposable PostgreSQL pooled/privilege/concurrency tests.
4. Run migration roundtrip with the live disposable DB, preserving the existing F43 reversibility assertions. Then run the complete F61, gateway, UI, CS-030, and full Python suites. Record exact commands and counts; do not count skipped DB tests as passing evidence.
5. Update BUG-041/043 backlog statuses only after their complete criteria pass. The tests-first specifications are already isolated in their story commits. Commit the dependent migrations `0022`/`0023` and integrated production path together as one coherent implementation unit, then push only after all required checks pass.

## Completion criteria

- Expected duplicate/member conflicts return 409 through the real pooled route; unrelated failures remain failures, not conflicts.
- Valid invitation redemption succeeds on a pristine upgraded database; expired/revoked/used tokens retain their opaque failure contract.
- Throttle state is durable, canonical-IP scoped, atomic under concurrency, bounded, independently resettable, and inaccessible outside approved functions.
- Fresh pooled services observe persisted state; throttle storage outage fails closed before token acceptance.
- `0021 -> head -> 0021 -> head` and the existing full F43 migration reversibility suite pass without weakening assertions or leaving stale objects.
- All story, F61, gateway, UI, CS-030, and full Python tests pass; exact test counts and commits are recorded before reporting completion.

## Validation evidence (2026-11-25)

- Full Python suite: `python3 -m unittest discover -s test` — **554 tests passed, 84 skipped**. The skips are environment-gated optional database/integration cases; the BUG-041/043 database gates below were run explicitly.
- Focused contracts: BUG-041 conflict repository **3/3**; BUG-043 in-memory/gateway **8/8**; migration static contract **2/2**; F61 invitation contract **6/6**; F61 repository contract **3/3**.
- Disposable PostgreSQL: `test_cs061c_invitation_database_integration.py` **7/7** with `CS061B_DATABASE_INTEGRATION=1`; `test_cs061b_database_integration.py` **3/3**; `test_migrations_roundtrip.py` **4/4**, including the existing F43 full roundtrip and BUG-043 object removal/recreation.
- UI: `npm test` **6/6**. Ruff on new migration and story-test files passed. `npm run lint` and `npx tsc --noEmit` were attempted but could not run because this checkout lacks `eslint` and `typescript`; the test suite itself passed.
- A new trusted-IP regression failed first with HTTP 401 because the route fell back to the test client's socket IP when scope contained `0.0.0.0`; after the route was corrected to reject unspecified trusted IPs, the regression passed with HTTP 503.
