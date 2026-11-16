# CS-061a — Core tenant authentication SDD

**Feature:** F61 AC1–AC2  
**Status:** implementation-ready; specification and red tests precede implementation  
**Scope:** tenant-user identity storage, credential verification, asymmetric access JWTs,
opaque refresh rotation, and the four backend auth transports.

## Boundary

CS-061a is the first authenticated boundary over F60.  A successful tenant-user login
creates an access identity whose signed `tenant_id`, `user_id`, and `role` are the only
inputs permitted to open F60 tenant context.  A body, query, header, cookie value other
than the signed credential, or route parameter cannot select a tenant.

This story is deliberately smaller than the F61 umbrella:

| Owned by CS-061a | Explicitly not owned here |
| --- | --- |
| Tenant users, role allowlist, Argon2id credentials, refresh-family storage; tenant-user login/refresh; RS256 access tokens and key rotation; browser versus JSON transport separation; canonical membership reconciliation | **CS-061b:** per-request revocation/status recheck, password reset, rate limiting/lockout, MFA enrolment/verification. **CS-061c:** invitations, shared membership redesign, audit log, SaaS-admin login and impersonation. **CS-062:** signup/tenant creation. **CS-068b:** end-to-end HTTP isolation gate. **CS-070:** CORS/CSRF/routing policy and frontend session UX. |

`saas_admins` is schema preparation only. It must remain a distinct, globally scoped
identity population and must not gain a tenant login, admin, or impersonation route in
this story. Its optional MFA material is encrypted-at-rest only; no MFA flow is implied.

## Acceptance criteria

### AC1 — identity and credential persistence

1. Migration `0010_cs061a_auth.py`, with revision `0010` and `down_revision = "0009"`,
   creates `tenant_users`, `saas_admins`, and `auth_refresh_tokens`; the Alembic graph has
   one linear head at `0010`.
2. `tenant_users` has UUID identity, a non-null cascading tenant FK, case-insensitive
   email, non-null password hash, active flag, timestamps, a unique `(tenant_id, email)`,
   and an allowlisted role of exactly `owner`, `manager`, `staff`, or `read_only`.
3. The same email may exist in different tenants but may not be duplicated (including by
   case) within one tenant. `saas_admins` has no `tenant_id` and has a globally unique
   email, active flag, password hash, creation timestamp, and encrypted MFA field.
4. Password generation is Argon2id; verifier input that is not an Argon2id encoding fails
   closed. Plaintext passwords, raw refresh secrets, JWT private keys, and refresh pepper
   are never persisted or returned/logged.
5. Refresh records retain only a unique one-way, peppered digest, family id, tenant/user
   linkage, issue/expiry timestamps, and rotation/revocation state. A composite tenant/user
   FK prevents a refresh record from naming a user from another tenant.
6. Anonymous credential and refresh lookup uses a dedicated least-privilege auth-service
   principal and narrowly scoped, search-path-pinned database operations. `app_rw` and
   `app_ro` have no direct credential-table reads or writes; the required deploy
   configuration must prohibit using their URLs (or the superuser URL) for auth.

### AC2 — authentication, tokens, and transport

7. Valid active credentials for `POST /auth/login` return a 15-minute RS256 access token
   plus a 7-day opaque refresh token in JSON (`access_token`, `token_type="bearer"`,
   `expires_in=900`, `refresh_token`) and set no auth cookie. Unknown email, wrong
   password, inactive user, malformed credential, and non-Argon2id stored hash produce
   the same externally safe authentication failure; lookup misses perform equivalent
   password-verification work so timing does not enumerate users.
8. Every access token has a known `kid`, uses RS256 only, and contains `iss`, `aud`,
   UUID `sub`, `tenant_id`, `user_id`, UUID `jti`, `iat`, `nbf`, `exp`, allowlisted `role`,
   and `is_saas_admin: false`; `sub == user_id`. Validation pins RS256 before verification,
   requires issuer/audience/signature/time claims, and rejects `alg=none`, HS/ES or any
   other algorithm, an unknown/retired `kid`, a bad signature/tampered payload, invalid
   issuer/audience, expired/not-yet-valid token, malformed UUIDs, missing `jti`, role
   outside the allowlist, or any admin claim other than literal false.
9. Key material comes only from `AUTH_JWT_PRIVATE_KEY_PEM`,
   `AUTH_JWT_PUBLIC_KEYS_PEM`, and an active key id at process/configuration load.
   Verification selects only the header `kid`; no algorithm inference, symmetric fallback,
   remote JWK fetch, or private-key response/logging is permitted. Rotation publishes old
   and new public keys concurrently, issues with the active `kid`, and fails closed when a
   retired key is removed.
10. Before F60 context is opened, a verified token is reconciled to the canonical
    `(tenant_id, user_id)` record. Missing/inactive membership or a stored tenant/role
    different from the signed claim is authentication failure; the token never permits a
    cross-tenant or role escape.
11. `POST /auth/refresh` accepts only a JSON opaque refresh token, atomically consumes it,
    and returns a replacement access/refresh pair in the same canonical family. The old
    token is single-use. Replay, revoked, expired, malformed, unknown, or digest-mismatched
    input is rejected; replay atomically revokes every member of the family, including its
    newest child. The child tenant/user/family derives from the locked parent, never client
    input.
12. `POST /auth/browser/login` and `/auth/browser/refresh` use the same credential and
    rotation semantics but send refresh only in a `HttpOnly; Secure; SameSite=Strict;
    Path=/auth/browser` cookie. Browser JSON bodies contain token metadata only and never
    an access or refresh secret; the short-lived access credential may likewise be an
    HttpOnly cookie. JSON login/refresh never set or accept the browser refresh cookie.
    Mixed transport is rejected.

## Security invariants and edge cases

- Access JWT lifetime is exactly 900 seconds; refresh lifetime is exactly seven days from
  each mint. Any optional clock skew is small, explicit, and symmetric for `exp` and `nbf`.
- Access tokens are bearer credentials, but only their validated, reconciled claims may
  establish tenant context. A caller-supplied `tenant_id` must be ignored/rejected.
- Rotation is one atomic persistence operation (lock, consume, child insert or family burn);
  concurrent presentation of the same refresh secret cannot yield two usable children.
- Refresh secrets are opaque random bytes, never JWTs. Digest comparison is constant-time.
- Login error status/body must not distinguish tenant, email, password, inactive status, or
  damaged credential hash. Internal observability must not contain secrets.
- RLS remains the database isolation backstop: auth’s anonymous lookup cannot broaden the
  ordinary application role. Auth tables follow F60 RLS/ownership conventions where
  compatible with owner-only security-definer operations.
- Browser cookies are not a substitute for CS-070 CSRF/CORS work; this story fixes only
  confidential transport and route separation.

## Required seams and tests

The implementation may add adapters but must make these concepts independently testable:

- `auth.passwords.Argon2idPasswordService` and `InvalidPasswordHash`;
- `auth.jwt.RS256KeyRing`, `RS256TokenService`, and `TokenValidationError`;
- `auth.tokens.OpaqueRefreshTokenService` and lifetime constant;
- `auth.service.TenantAuthService`, `AuthenticationError`, and `RefreshReplayError`;
- `auth.api.create_auth_router` exposing exactly the four tenant-user auth routes.

`test/test_f61_auth_contract.py` covers identity/schema, Argon2id, JWT/`kid` validation,
safe enumeration failures, canonical membership reconciliation, token transport, rotation,
replay, and no cross-tenant escape without requiring a database or secrets.
`test/test_f61_auth_deployment_contract.py` covers migration graph and deploy-principal
configuration without attempting local infrastructure. Database integration may be added by
the implementer behind `DATABASE_URL`, but it must not weaken these contracts.

## Verification and handoff

Before implementation, run:

```sh
python3 -m unittest coffee-scheduler/test/test_f61_auth_contract.py coffee-scheduler/test/test_f61_auth_deployment_contract.py
```

The command is expected to be red because migration `0010`, the `auth` package, and auth
deployment configuration do not exist. The developer must report the supplied migration,
public API, non-secret environment variable names, and green test evidence without removing
negative cases or replacing them with mocks.
