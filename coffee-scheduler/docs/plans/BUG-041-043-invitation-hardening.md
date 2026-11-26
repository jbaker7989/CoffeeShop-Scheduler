# BUG-041 and BUG-043: invitation endpoint hardening

**Status:** implementation in progress  
**Scope:** CS-061c invitation creation conflicts (BUG-041) and anonymous invitation-accept throttling (BUG-043).  
**Out of scope:** invitation ordering (BUG-044), email delivery, token format/TTL changes, login throttling behavior, and unrelated auth cleanup.

## Goals and contracts

### BUG-041 — conflict response parity

`POST /invitations` must return HTTP 409 for both a current tenant member and an already-pending invitation, whether the database rejects the request through the `auth_create_invitation` PL/pgSQL guards (`P0001`) or the unique index (`23505`). Ordinary permission, connectivity, schema, and unexpected SQL errors must retain their existing failure behavior; do not turn every repository exception into a conflict. Expired pending invites remain replaceable, while revoked/accepted invitation policy remains unchanged.

### BUG-043 — shared invitation-accept rate limit

The anonymous `POST /invitations/accept` failure bucket must be shared by gateway replicas and deployments through durable PostgreSQL state. The key is canonical client IP only: different client IPs stay isolated, and successes clear only that IP's failures. Unknown, expired, revoked, already accepted, and invalid tokens return the same opaque 401 and count as failures. A currently locked IP gets the existing opaque 429 without attempting acceptance. The current threshold and rolling window remain the same unless existing configuration already defines otherwise; this story does not create a new user-controlled tenant or email key.

## Design

1. **Repository contract.** Add typed invitation-throttle check/record operations to `AuthRepository`, implemented by the in-memory test double, `DatabaseAuthRepository`, and `PooledAuthRepository`. The API must use these operations unconditionally; remove `LoginRateLimiter` from the invitation route so there is no process-local fallback.
2. **Database boundary.** Add a reversible migration with a small IP-keyed failure-state table and narrowly scoped `SECURITY DEFINER` functions. The table contains no tenant, email, invitation token, or password data. Revoke direct table access from `PUBLIC`, `app_rw`, and `app_ro`; grant only function execution to `auth_svc`. Use canonical `inet` keys and an index suitable for the rolling-window lookup. Serialize updates per IP in PostgreSQL so concurrent gateway calls cannot lose increments. Expired attempts are pruned in bounded scope; success clears only the matching IP state. Invalid throttle parameters fail closed.
3. **Conflict normalization.** In `DatabaseAuthRepository.create_invitation`, catch only SQLSTATE `P0001` from the known invitation guard messages and SQLSTATE `23505` from the pending-invitation uniqueness race. Convert these expected conflicts to the existing policy-level conflict signal. Preserve and re-raise all other SQL errors. Keep service/API mapping as `InvitationConflictError` → HTTP 409.
4. **Concurrency and expiry.** Use one transaction per durable operation. An IP's record mutation is serialized; a check observes current-window failures. Failures exactly at the lower time boundary are expired, matching the existing `attempted_at > now - window` contract. IPv4 and IPv6 are canonicalized through PostgreSQL `inet` and Python `ipaddress`; malformed IPs fail closed before repository state is touched.

## Story-first tests and acceptance matrix

### BUG-041 regressions

- In-memory/API contract: duplicate pending invite gives 409; already-member conflict gives 409; a different database error is not mislabeled as a conflict.
- Repository unit tests: `P0001` for the two documented function guard messages and `23505` map to the conflict type; unrelated SQLSTATEs and unknown `P0001` messages propagate.
- Live PostgreSQL test through `PooledAuthRepository` and the actual gateway route: first invite is 200, duplicate is 409, and invitation to the existing owner is 409. Assert no extra invitation rows and preserve the original invitation.
- Live PostgreSQL concurrency regression: concurrent duplicate creates cannot escape as 500; each result is either one success and a conflict, never two successes or a raw 500.

### BUG-043 regressions

- In-memory service/API contract: threshold locks only the originating IP; a different IP succeeds; success clears only its own IP; malformed IP fails closed; failures expire at the exact rolling-window boundary.
- Two independent gateway apps/services over the same durable DB: failures through app A are visible as a lock through app B, demonstrating state is not process-local.
- Live PostgreSQL through the actual pooled route: multiple invalid tokens from one IP produce 401s then 429; a valid token from another IP still succeeds; a successful acceptance clears only its own IP; a fresh pooled connection observes the first IP's lock.
- Concurrent records for one IP retain the full failure count; records for another IP remain independent. A post-window request is allowed. Success/failure HTTP messages stay opaque and raw invitation tokens are never persisted in throttle storage.
- DB privilege regression proves the application roles cannot read/write the throttle table or execute the mutating function; `auth_svc` can call the approved interface. Migration upgrade/downgrade/upgrade leaves no stale functions, table, index, or grants.

## Implementation order

1. Add this plan and failing-first tests for BUG-041, then fix the targeted exception translation and run F61 invitation/API contracts.
2. Add failing-first repository, API, and opt-in disposable-PostgreSQL tests for BUG-043, then implement the typed repository seam and reversible migration/functions.
3. Run migration roundtrip and live DB privilege/concurrency tests with `CS061B_DATABASE_INTEGRATION=1`; run all F61, gateway, UI, and CS-030 regressions plus the full Python suite.
4. Mark both backlog stories fixed only after their complete acceptance tests pass. Commit BUG-041 and BUG-043 separately, then push the working branch. Do not claim DB acceptance when its opt-in integration service is unavailable.

## Completion criteria

- Every acceptance test above passes, including live disposable PostgreSQL and fresh pooled-connection checks.
- No known `P0001`/`23505` conflict becomes HTTP 500; no unrelated database error becomes HTTP 409.
- No invitation throttle state exists only in a process; no IP can clear or lock another IP's bucket.
- The migration is reversible and least-privilege; all changed code is lint-clean and the full requested suites pass.
