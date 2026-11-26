# BUG-067 — Make the BUG-065 migration available under concurrent forecast traffic

> **Superseded implementation plan.** The accepted deployment decision allows revision 0021 to be amended
> because it has not run outside disposable databases. The implemented sequence is 0021 (`NOT VALID`) plus
> 0024 (preflight and online validation, with a committed enforcement boundary). Use the combined
> [BUG-031/067 implementation plan](./BUG-031-067-gateway-lifecycle-and-migration-availability.md) for
> current requirements, downgrade behavior, and regression evidence; the analysis below records the original
> lock findings only.

## Goal

Minimise the F8 finite-interval migration's exclusive-lock period and make its unavoidable metadata-lock contention a predictable, retryable deployment condition.

## Evidence

`0021_bug065_forecast_finite_interval.py:79` executes `LOCK TABLE forecasts IN ACCESS EXCLUSIVE MODE` after a five-second local `lock_timeout`. On PostgreSQL 16, the following verified sequence fails the upgrade after five seconds:

1. Downgrade a disposable database to `0020`.
2. In connection A: `BEGIN; SELECT 1 FROM forecasts LIMIT 1; SELECT pg_sleep(7); COMMIT;`.
3. In connection B: run `alembic upgrade head`.

Connection B raises `LockNotAvailable` at the explicit `LOCK TABLE`; retrying after A commits succeeds. The `SELECT` owns only the ordinary `ACCESS SHARE` lock, so reports and read API transactions are sufficient to block this migration. A separate PostgreSQL 16 probe established that `ALTER TABLE ... ADD CONSTRAINT ... CHECK (...) NOT VALID` also waits behind that reader and times out: `NOT VALID` reduces later validation locking, but it cannot make the initial metadata update complete while an incompatible reader remains open.

## Scope

- Keep the F8.ac3 outcome: every newly written non-finite `lower`, `p70`, or `upper` is rejected.
- Preserve a clear preflight diagnostic for legacy invalid rows and do not rewrite those rows.
- Rework only the migration/operational rollout and its PostgreSQL integration coverage. Do not loosen RLS, the ordering CHECK, FKs, or uniqueness.

## Design constraints

- `ADD CONSTRAINT ... NOT VALID` enforces the CHECK for future inserts and updates but does not scan old rows. It still needs a short `ACCESS EXCLUSIVE` metadata lock, so a long `ACCESS SHARE` reader must be drained or the attempt must fail fast and be retried.
- `VALIDATE CONSTRAINT` uses a weaker lock than ordinary `ADD CONSTRAINT` validation. Confirm the exact PostgreSQL 16 compatibility with active reads/writes in the integration test rather than relying only on comments.
- Install future-write enforcement before concurrent legacy scanning/validation. This ordering leaves no writer window even if phases need separate transactions; a pre-existing invalid row must still produce an actionable diagnostic.
- Treat a lock timeout or validation failure as a visible, actionable operational error. Do not silently retry a DDL statement.

## Test-first plan

1. Add a DB-gated integration test that reaches `0020`, opens a second transaction holding `ACCESS SHARE` on `forecasts` beyond the configured metadata `lock_timeout`, and runs the metadata-enforcement phase. Assert fast non-zero failure, a lock-timeout diagnostic, and atomic revision/constraint state; after the holder commits, assert a retry succeeds.
2. Add a phase-level concurrency test showing the chosen `NOT VALID`/validation design does not retain `ACCESS EXCLUSIVE` through the legacy scan. While a long `ACCESS SHARE` reader remains open, prove the validation phase behaves according to PostgreSQL 16's supported lock compatibility and assert the constraint is ultimately validated (`convalidated`).
3. Add a concurrency case around the selected rollout boundary: once enforcement is installed, an independent writer attempting `(0, 1, Infinity)` must receive `CheckViolation` naming `forecasts_interval_finite` and cannot commit.
4. Retain/extend the legacy-data preflight test: invalid existing data must fail with `BUG-065 preflight`, leave the revision at the phase immediately before validation, and leave the row unchanged.
5. Decide the rollout boundary before editing migration history. A forward `0022` migration cannot repair the availability of a fresh `0020 → head` upgrade because `0021` will already have taken the blocking lock. If `0021` has not been applied outside disposable environments, amend its upgrade path under the project's migration-release policy and rerun the full chain. If it is immutable in a deployed environment, retain the existing migration for compatibility and document the required maintenance/read-drain procedure; do not claim a later revision removes the original contention. Record which case applies.
6. Run focused contract tests and the migration round-trip suite against a disposable PostgreSQL 16 database. Report DB-gated skips separately.

## Completion checks

- A long read causes only the bounded metadata phase to fail fast with a documented retry path; it does not block a table scan under `ACCESS EXCLUSIVE`.
- The new finite constraint is enforced before concurrent validation and is validated after upgrade.
- Legacy bad rows retain the actionable preflight route; concurrent bad writes cannot bypass it.
- Upgrade, downgrade, and re-upgrade semantics remain documented and tested for the chosen forward-only migration path.
