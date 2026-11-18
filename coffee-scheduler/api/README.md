# Manager UI gateway

Install the pinned dependencies from `requirements.txt`, provide the CS-061a auth
configuration (below), then run:

```sh
uvicorn api.app:app --port 8000
```

## Auth boundary (CS-061a, BUG-035 cutover)

The gateway mounts the CS-061a auth router and its JWKS, and authenticates protected
endpoints from **validated, reconciled** CS-061a access-token claims. Exactly one handler
owns each `/auth/*` route; the legacy CS-030 demo `POST /auth/login` (HS256 `session`
cookie, `DEMO_MANAGER_*` credentials) has been removed.

Routes:

- `POST /auth/login`, `POST /auth/refresh` — JSON transport (tokens in the body).
- `POST /auth/browser/login`, `POST /auth/browser/refresh` — browser transport
  (`HttpOnly; Secure; SameSite=Strict` cookies; refresh scoped to `/auth/browser`, the
  short-lived access cookie to `/`). The response body carries token metadata only.
- `GET /.well-known/jwks.json` — public verification keys (no private material).
- `GET /stores`, `GET /me` — require a single access credential: an
  `Authorization: Bearer <token>` header **or** the browser `access_token` cookie (never
  both, never duplicated). The tenant id is taken only from the reconciled claims; a request
  body/query/cookie cannot select a tenant.
- `POST /auth/logout` — revokes the presented access token and matching browser refresh family when available, then clears both browser cookies.

Startup **fails closed**: a missing or invalid auth DSN, JWT keyring, issuer/audience, or
refresh pepper raises at app construction. There is no fallback to `SESSION_SECRET` or the
demo credentials.

### Required configuration

- `AUTH_DATABASE_URL` — a dedicated LOGIN principal that is a member of the NOLOGIN
  `auth_svc` role (`GRANT auth_svc TO <login>`). Must not use `app_rw`, `app_ro`, or the
  superuser URL.
- `AUTH_JWT_PRIVATE_KEY_PEM`, `AUTH_JWT_PUBLIC_KEYS_PEM` (JSON `kid -> PEM`),
  `AUTH_JWT_ACTIVE_KID`, `AUTH_JWT_ISSUER`, `AUTH_JWT_AUDIENCE`, `AUTH_REFRESH_PEPPER`.
- `GATEWAY_DATABASE_URL` (RLS-scoped `app_rw` store reads) defaults to the local PgBouncer.

Tenant-scoped reads establish RLS as `app_rw` using only the reconciled `tenant_id`.

## Manager UI integration (BUG-035 + BUG-036 + BUG-037)

The Next.js manager shell (`coffee_shop/`) now authenticates with the CS-061a browser transport:

- The login page posts to `/auth/browser/login` (same-origin), so the gateway sets the
  `HttpOnly; SameSite=Strict` access/refresh cookies and no token is ever read by client JS.
- `middleware.ts` gates on the `access_token` cookie; the server components (`(manager)/layout`,
  `dashboard`) forward only that cookie to `/me` and `/stores`, and the gateway derives the tenant
  from the reconciled claims. Logout posts `/api/auth/logout`, which clears both cookies.
- The frontend proxies the browser-auth routes at their own `/auth/browser/:path*` rewrite so the
  refresh cookie (`Path=/auth/browser`) is sent to the refresh endpoint on the same origin; the
  gateway keeps the CS-061a cookie-path defaults (no coupling to the frontend's `/api` prefix) and
  always sets `HttpOnly; Secure; SameSite=Strict`. Local development must terminate TLS too;
  plaintext HTTP is not an exception for bearer credential cookies.

Access tokens live 15 minutes. When one expires (the access cookie's own lifetime), a protected
request has no access credential, so middleware/the server components route to a same-origin
`/session-refresh` step (BUG-037). That client step POSTs once to `/auth/browser/refresh` — the
only path the `Path=/auth/browser` refresh cookie is sent to — the gateway rotates the family and
relays fresh `Set-Cookie` headers, and the original safe GET page is retried once (the fixed
`/dashboard`, never a user-controlled return URL). Any refresh failure or replay clears both
cookies and fails closed to `/login`. No token is ever read by client JavaScript (the step inspects
only the response status), only safe/idempotent GETs are retried (no unsafe mutation is replayed),
and a short loop guard bounds recovery to a single refresh per failure. The recovery *policy* is
unit-tested (`coffee_shop/test/session-recovery.test.mts`) and the server side of the sequence in
`test_cs061a_manager_ui_compat.py`.

The broader CSRF/CORS + tenant-routing policy remains a **CS-070** follow-up. The dormant
`api.auth` demo helper and the optional `SESSION_SECRET` / `DEMO_MANAGER_*` settings remain only
for local experimentation and are never used by the production app; the legacy `session` cookie is
accepted nowhere.
