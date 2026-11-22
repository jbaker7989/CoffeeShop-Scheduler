# BUG-065 — Reject non-finite forecast intervals in PostgreSQL

## Goal

Make the database enforce F8.ac3's finite-value requirement independently of the Python validator. Migration `0017` currently checks interval ordering, but PostgreSQL `numeric` accepts special values whose comparison behavior can satisfy that CHECK.

## Scope

- Add a new linear Alembic migration after the current repository head. Do not rewrite an already-applied migration.
- Add an explicit finite-value constraint for `forecasts.lower`, `forecasts.p70`, and `forecasts.upper`.
- Add DB-backed negative cases to `test/test_forecast_contract.py` and preserve existing finite-boundary coverage.
- Keep interval order semantics, RLS, foreign keys, and the soft-delete-aware uniqueness contract unchanged.

## Specification and test-first sequence

1. Add `spec: F8.ac3` tests that insert otherwise-valid forecast rows containing PostgreSQL `NaN`, `Infinity`, and `-Infinity`; verify each special value is rejected in each interval column.
2. Include the critical cases `(NaN, NaN, NaN)`, `(0, NaN, NaN)`, and `(0, 1, Infinity)` so the existing ordering-only CHECK cannot accidentally make the regression pass.
3. Confirm finite boundaries remain accepted, including `lower = p70 = upper = 0` and an ordinary ordered decimal interval.
4. Run the DB-backed test against PostgreSQL 16 with a valid tenant, store, category, and taxonomy version. Roll back each insertion so cases remain isolated.
5. Add the minimal migration constraint and verify upgrade → downgrade → upgrade. Do not modify the existing Python finite-value guard unless testing reveals a regression.

## Implementation notes

- PostgreSQL treats `numeric 'NaN'` as comparable and greater than ordinary numeric values; interval inequalities alone do not establish finiteness.
- The migration must test each column individually. A single expression that checks only `p70` leaves `lower` or `upper` able to carry special values.
- Use an explicit finite predicate that works on the project's PostgreSQL version. If implemented with numeric comparisons against infinities, verify that `NaN` is rejected too; otherwise use an explicit special-value exclusion. Keep the constraint named and reversible.
- Existing rows must be checked before adding the constraint. If any are invalid, fail with a clear migration error rather than silently rewriting forecast values.
- Current head is `0020` (not `0017`); the new migration is `0021` with `down_revision = "0020"`. The `0017` ordering CHECK is the defective predicate, but the new constraint must chain after the real repository head.
- Attribution caveat: every `-Infinity` placement, and a lone `NaN` in `lower`, *also* violates the `0017` ordering CHECK (`lower >= 0 AND lower <= p70 AND p70 <= upper`), because no such tuple can be both non-negative and ordered. A bare `CheckViolation` on those inserts therefore does **not** prove the new finite constraint is doing the work — the old CHECK may be firing instead. To prove the new constraint rejects these independently, the regression must either assert the violated constraint name is `forecasts_interval_finite`, or evaluate the finite predicate directly in SQL for each special value in each column. The ordering-passing tuples `(NaN,NaN,NaN)`, `(0,NaN,NaN)`, `(0,1,NaN)`, and `(0,1,Infinity)` cannot be rejected by the ordering CHECK at all, so a plain `CheckViolation` on those is already sufficient attribution.

## Edge cases

- `NaN` in only `lower`, only `p70`, or only `upper`.
- `NaN` in all three fields, which can satisfy the current ordered comparisons.
- Positive and negative infinity in each field.
- Negative finite `lower`, inverted bounds, and equal finite bounds to ensure the existing interval rule still works.
- A failing row must not poison the enclosing test transaction or prevent subsequent constraint cases from running.
- Downgrade removes only the new constraint and leaves migration `0017` constraints intact.

## Completion evidence

- Focused forecast contract tests pass with PostgreSQL configured; report any database-gated skip separately.
- Migration round-trip succeeds on a disposable PostgreSQL database.
- `git diff --check` is clean, and the Backlog status records the migration and test evidence.
