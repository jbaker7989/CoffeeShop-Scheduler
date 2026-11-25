# BUG-033 — Close out the retired demo manager credential path

## Goal

Ensure no shipped production gateway can authenticate with a publicly known manager
password when configuration is absent. CS-061a replaced CS-030 demo authentication with
tenant-backed Argon2id credentials, RS256 tokens, and the dedicated auth database role;
BUG-035 records the gateway cutover. This task verifies that boundary and keeps the dormant
demo helper from being accidentally remounted.

## Current-state finding

- `api.app` mounts the CS-061a router; there is no active CS-030 `/auth/login` handler.
- `api.config` reads `DEMO_MANAGER_PASSWORD_HASH` only as an optional legacy setting; it
  contains no plaintext password fallback.
- The dormant `api.auth` helper does not perform manager lookup or password authentication;
  its functions are generic HS256/cookie helpers. Its production safety must come from route
  ownership and required CS-061a startup configuration, not a fake password setting.
- Therefore the correct disposition is “superseded/resolved by CS-061a / BUG-035,” while
  preserving regression guards. Do not add a second CS-030 login flow or make an unused
  `DEMO_MANAGER_PASSWORD_HASH` a prerequisite for CS-061a startup.

## Test plan

1. Extend gateway integration coverage to assert exactly one production `/auth/login` route,
   owned by the CS-061a auth router, and assert the legacy demo response/cookie shape is not
   reachable.
2. Add a config regression proving `DEMO_MANAGER_PASSWORD` and the known string
   `demo-pass-change-me` do not provide or synthesize a password/hash in the application
   config, whether `COOKIE_SECURE` is true or false.
3. Keep startup fail-closed coverage for missing `AUTH_DATABASE_URL`, issuer/audience,
   RS256 keys, and refresh pepper. Verify missing legacy demo settings do not weaken or
   bypass those checks.
4. Run the focused gateway/config/auth deployment tests and the full test suite. Check source
   for any reintroduced plaintext default or legacy route mount.

## Edge cases to cover

- `COOKIE_SECURE` unset, false, or true.
- Both legacy manager password/hash variables unset, one set, or both set: they must never
  affect CS-061a route ownership or production auth startup.
- Environment reload/import behavior: tests should isolate config imports and restore any
  modified environment variables.
- JSON login must return the CS-061a token response and no legacy `session` cookie; browser
  login remains the separate CS-061a browser transport.
- No duplicate route ordering can let a legacy handler shadow the CS-061a handler.

## Completion

Mark BUG-033 resolved as superseded by CS-061a/BUG-035 only after the route/config regressions
pass and the production startup fail-closed tests remain green. Keep this plan as audit history.
