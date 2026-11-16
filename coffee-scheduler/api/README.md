# Manager UI gateway

Install the pinned dependencies from `requirements.txt`, set `SESSION_SECRET` to a
unique value of at least 32 characters, then run:

```sh
uvicorn api.app:app --port 8000
```

Configuration:

- `GATEWAY_DATABASE_URL` defaults to `postgresql://coffeeshop:coffeeshop@localhost:6432/coffeeshop`.
- `SESSION_SECRET` is required and has no default.
- `COOKIE_SECURE` defaults to `false`; set it to `true` in production HTTPS.
- `JWT_TTL_SECONDS` defaults to `3600`.
- `DEMO_TENANT_SLUG` defaults to `maven-demo` and `DEMO_MANAGER_EMAIL` to `manager@maven-demo.coffee`.
- Prefer `DEMO_MANAGER_PASSWORD_HASH` (an Argon2id hash). If it is unset, startup hashes
  `DEMO_MANAGER_PASSWORD`, which defaults to `demo-pass-change-me`, for local demo use.

The session is an HttpOnly, SameSite=Lax cookie (and Secure when configured). Tenant-scoped
queries use the tenant id only from a verified signed token and establish RLS as `app_rw`.
Login is the sole exception: the owner connection resolves the tenant slug before a tenant
context exists; it is not used for tenant-scoped data reads.
