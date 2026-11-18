# BUG-051–BUG-053: Auth adapter, authorization, and logout hardening

## Goal

Complete three focused F61 quality and correctness fixes without changing login, refresh, or the public `authenticate_access_token` contract. Each story is implemented and verified serially, then committed independently and pushed to the existing feature branch.

## Scope and story order

| Order | Story | Change | Verification |
|---|---|---|---|
| 1 | BUG-051 | Centralize pooled repository pool/connection/delegation lifecycle; keep explicit typed adapter signatures | fake pool/repository contract tests and repository contract suites |
| 2 | BUG-052 | Return validated claims with canonical principal from one validation; use both for request authorization | validation-call-count plus claims, membership, and revocation contract tests |
| 3 | BUG-053 | Revoke a validated access token with canonical identity, revoke its known refresh family, and wire logout endpoint | service/gateway tests, cookie edge cases, and database integration when configured |

## Design details

### BUG-051 adapter contract

The adapter's private helper owns only the repeated mechanics: open the pool if closed, borrow one connection using its context manager, construct the connection-scoped `DatabaseAuthRepository`, and invoke the specified port method. Public methods stay explicit and typed to mirror the abstract methods, including keyword-only `consume_and_replace` arguments. This keeps signatures inspectable and errors transparent. The helper must not retry, catch repository exceptions, or retain a connection/repository between calls.

Test fake pool behavior for success and exceptions. Assert a closed pool opens once, each operation borrows once, kwargs reach the database repository unchanged, and connection cleanup happens if the delegated method raises.

### BUG-052 validation flow

Introduce a private token-authentication helper returning `(Principal, claims)`. It performs token validation once, fetches the identity using the signed tenant/user pair, rejects missing/inactive/mismatched canonical identity, and returns the untouched validated claims. Preserve generic external authentication errors. `authenticate_access_token` returns the principal only; `authorize_access_request` obtains UUID `jti` and integer `iat` from the same claims and continues current active-user, tenant-status, and revocation checks. Bad UUID/time values fail closed.

### BUG-053 logout and revocation flow

Change the service revocation entry point to accept the raw access credential, not caller-supplied JTI or identity. Validate/reconcile membership, require well-formed `jti` and `exp`, convert expiry to timezone-aware UTC, and pass canonical tenant/user and token JTI/expiry to `revoke_access_token`. For optional refresh input, digest it and query `get_refresh_token`; only a found record supplies the family id for `revoke_family`. Missing/unknown/expired refresh secrets must not cause a different tenant identity to be selected. Do not include secrets in audit/log output.

The logout route extracts access exactly like protected routes; a refresh secret is read only from the browser refresh cookie. When a usable access token is supplied, persist its revocation and any matching refresh-family revocation before success. When access is missing, invalid, expired, duplicate, or ambiguous, preserve idempotent browser logout and clear both cookie names at their existing paths. A storage failure for a valid credential must propagate as a server error rather than report successful revocation. Cookie deletion is still emitted for invalid credentials. Bearer logout without a refresh cookie revokes only the access token.

## Cross-cutting edge cases

- UUID parsing must reject malformed, empty, non-string, and oversized claims before repository calls.
- JWT `iat`/`exp` must be numeric and converted in UTC; malformed or out-of-range timestamps fail closed.
- A JWT valid cryptographically but with mismatched tenant, user, role, or inactive membership must never create a revocation row under untrusted identity.
- An absent, unknown, or expired refresh secret must not block access-token logout; family revocation only uses repository-returned family identity.
- Duplicate access cookies and simultaneous bearer plus cookie remain ambiguous under the existing extraction policy; logout clears cookies without revoking an arbitrarily chosen token.
- Database errors are not converted into success for a valid revocation attempt.
- All pool operations release borrowed connections regardless of outcome and preserve delegated exceptions.

## Execution and completion gates

1. Implement BUG-051 and add focused tests; run adapter and repository contract suites; commit only after pass.
2. Implement BUG-052 and add regression tests; run access-token and CS-061b authorization suites; commit only after pass.
3. Implement BUG-053 and update service/gateway tests; run logout, refresh, revocation, gateway, and database suites. Record any environment-gated skip explicitly; all deterministic tests must pass before commit.
4. Run the full project test suite and static checks available in the repository; investigate regressions. Commit and push the completed story commits to this branch.
5. Audit the full codebase for additional actionable quality defects and create separate detailed backlog stories and a plan doc. Do not mix those follow-up scope items into these three fixes.

## Out of scope

- Refresh token redesign, session policy changes, cookie names/paths, CSRF work scheduled for CS-070, or changes to access-token lifetimes.
- Broad formatting or typing rewrites outside the adapter and touched auth flow.
- Implementing follow-up audit stories unless separately requested.
