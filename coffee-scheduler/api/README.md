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
- `POST /auth/logout` — clears the browser cookies. Server-side refresh revocation is CS-061b.

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
- `COOKIE_SECURE` defaults to `false`; set `true` in production HTTPS.

Tenant-scoped reads establish RLS as `app_rw` using only the reconciled `tenant_id`.

## CS-030 compatibility / migration

The Next.js manager shell still authenticates with the legacy `session` cookie. Migrating it
to the CS-061a browser `access_token` cookie — together with CSRF/CORS and routing policy — is
**CS-070** (frontend session UX) and is tracked separately. This backend cutover does not
weaken any CS-061a contract and leaves the non-auth CS-030 behaviours (`/healthz`, RLS-scoped
`/stores`, `/me`, fail-closed 401s, no tenant spoofing) intact on the new boundary. The dormant
`api.auth` demo helper and the optional `SESSION_SECRET`/`DEMO_MANAGER_*` settings remain only
for local experimentation and are never used by the production app.
