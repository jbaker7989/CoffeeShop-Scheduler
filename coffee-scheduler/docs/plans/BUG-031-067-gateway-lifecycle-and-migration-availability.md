# BUG-031 and BUG-067 — gateway pool lifecycle and forecast migration availability

## Goal and decisions

Implement both operational fixes while preserving CS-030 gateway behavior and F8.ac3's finite-forecast contract. The production gateway keeps its PostgreSQL pool lazy: application startup and `/healthz` do not require a database connection. FastAPI lifespan shutdown owns closing the module-level pool only when production database accessors are in use; injected test adapters do not close process-wide resources. Migration 0021 is safe to amend because it has not been applied outside disposable databases. Its short `ACCESS EXCLUSIVE` metadata lock remains bounded by `lock_timeout`; a later revision performs legacy preflight and constraint validation without holding that lock over the table scan.

## BUG-067 migration sequence

1. Revision 0021 adds the per-column finite-value CHECK as `NOT VALID`. PostgreSQL skips scanning existing rows but enforces the check on all inserted or updated rows after the migration commits. Keep `SET LOCAL lock_timeout = '5s'` so a long reader causes a quick, retryable migration failure; do not wait indefinitely or introduce an explicit table lock.
2. Revisions 0022 and 0023 retain their existing invitation behavior. Revision 0024 runs the legacy-row preflight with an actionable `BUG-065 preflight` error, then executes `VALIDATE CONSTRAINT`. Validation uses PostgreSQL's weaker validation lock and can coexist with ordinary reads and writes.
3. If legacy data is invalid, revision 0024 rolls back only itself. The database remains at 0023 with revision 0021's unvalidated enforcement check installed, the offending data unchanged, and invalid new writes blocked. Clean legacy rows and retry `alembic upgrade head`.
4. Downgrading 0024 atomically drops and recreates the same CHECK as `NOT VALID`, restoring revision 0021's state because PostgreSQL has no operation to unvalidate a constraint. Downgrading 0021 removes only this constraint; all pre-existing forecast constraints remain untouched.
5. Integration coverage must prove: 0021 fails within its bounded timeout behind an `ACCESS SHARE` reader and succeeds after it commits; the installed `NOT VALID` check immediately rejects a non-finite write; 0024 validation succeeds while an `ACCESS SHARE` reader remains open; legacy invalid data leaves revision 0023 and remains unchanged; cleanup permits retry; and full upgrade/downgrade/re-upgrade leaves the expected schema. Keep F43's full migration roundtrip assertions intact.

## BUG-031 lifecycle sequence

1. Determine whether the app uses a default DB accessor before resolving defaults. Keep default database imports lazy and do not open the pool during lifespan startup.
2. In lifespan `finally`, close the module pool when either the default store accessor or a default routing lookup is in use. This covers normal shutdown and request/startup exceptions while leaving the existing lazy-open behavior intact.
3. When database functions are injected, do not import or close `api.db`'s shared pool. This avoids tests and embedded callers shutting down a pool they do not own.
4. Use FastAPI `TestClient` as a context manager so tests exercise lifespan. Verify startup plus health leaves the pool unopened, default production access closes once after normal and exceptional exits, and injected adapters do not close it. The database-backed API foundation test must also enter/exit the client lifecycle.
5. Liveness stays independent of authentication and PostgreSQL; no startup connectivity check is introduced.

## Acceptance and regression evidence

- F8.ac3 / BUG-067: values `NaN`, positive infinity, and negative infinity remain forbidden in each interval column; valid finite boundaries remain accepted.
- F30.ac1 / BUG-031: pool remains unopened at startup and health check.
- F30.ac2 / BUG-031: default pool closes from lifespan even after an exception; injected adapters leave the shared production pool alone.
- Run the focused lifecycle tests, forecast contract tests, full non-migration test suite, lint/spec traceability, and disposable PostgreSQL migration roundtrip. Report integration skips distinctly; they are not evidence that the migration concurrency behavior passed.

## Scope guard

Do not fold BUG-068's expanded per-column legacy-preflight fixture matrix into BUG-067 unless its complete criteria and tests are implemented; a passing all-NaN fixture alone does not prove each preflight predicate arm. Do not change the forecast contract, RLS policies, application health semantics, pool open strategy, unrelated UI work, or other migration objects.
