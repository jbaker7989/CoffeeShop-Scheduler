# BUG-068 — Cover every BUG-065 preflight column and special value independently

## Goal

Make the migration regression demonstrate that `0021` detects legacy `NaN`, `Infinity`, and `-Infinity` in each forecast interval column before PostgreSQL reaches the generic CHECK-constraint failure.

## Evidence

`test_migrations_roundtrip.py:107` uses one committed legacy fixture with all three interval fields set to `NaN`. The `PREFLIGHT_SQL` predicate in `0021_bug065_forecast_finite_interval.py` is an `OR` over the three columns. Consequently, deleting any one column arm leaves the all-NaN fixture matched by the other two arms and leaves the current test passing.

For example, if the `upper` arm is removed, `(0, 1, Infinity)` is ordered under the old `forecasts_interval_valid` CHECK. The faulty preflight does not identify it; the subsequent `ADD CONSTRAINT` instead aborts with an opaque `forecasts_interval_finite` violation. This contradicts the migration's stated clear-preflight behavior but is not detected today.

## Scope

- Change only regression coverage and its test helpers unless testing exposes a real migration defect.
- Exercise the migration from exactly revision `0020` against committed legacy rows, because Alembic runs in a separate process/transaction.
- Keep the database disposable and remove fixtures even after assertion failures.

## Test matrix

For each of `lower`, `p70`, and `upper`, create a row with each special numeric value. At revision `0020`, first drop `forecasts_interval_valid` in the disposable test database: that old CHECK rejects lower-only `NaN`/infinity, p70-only `NaN`/infinity, and every `-Infinity` placement before the new migration can inspect them. This is test-fixture setup only; restore the old CHECK after fixture cleanup before the next normal migration step.

| Field | `NaN` | `Infinity` | `-Infinity` |
| --- | --- | --- | --- |
| `lower` | `(NaN, 1, 2)` | `(Infinity, 1, 2)` | `(-Infinity, 1, 2)` |
| `p70` | `(0, NaN, 2)` | `(0, Infinity, 2)` | `(0, -Infinity, 2)` |
| `upper` | `(0, 1, NaN)` | `(0, 1, Infinity)` | `(0, 1, -Infinity)` |

The finite companion values make the bad field unambiguous. The key assertion is the preflight message, not 0017's ordering behavior. Assign a unique tenant or forecast key per case so cleanup and diagnosis are exact.

## Test-first sequence

1. Factor the existing `test_migration_0021_preflight_rejects_non_finite_legacy_rows` setup/cleanup into a helper that reaches `0020`, temporarily drops only `forecasts_interval_valid` in the disposable database, installs one fixture, invokes the upgrade subprocess, and inspects revision/constraint/row state.
2. Add subtests for the nine field/value pairs. Each subtest asserts a non-zero upgrade, output containing `BUG-065 preflight`, revision `0020`, no `forecasts_interval_finite` constraint, and unchanged special values in the identified row.
3. Include the ordered `upper=Infinity` fixture `(0, 1, Infinity)` as a named case; this proves the operator gets the project diagnostic instead of generic DDL validation.
4. Delete each fixture with the owner connection, restore `forecasts_interval_valid` with its original expression, then upgrade to head and assert the finite constraint is installed. Make cleanup robust with `try`/`finally` so a failed assertion does not contaminate later migration tests.
5. Run the migration round-trip suite with `DATABASE_URL` set against disposable PostgreSQL 16; report it separately from non-DB unit coverage.

## Completion checks

- Removing any one `PREFLIGHT_SQL` column arm makes at least one matrix subtest fail.
- Every special value in every field produces the named actionable preflight output before the constraint is added.
- Tests leave the disposable database at head with no fixture rows.
