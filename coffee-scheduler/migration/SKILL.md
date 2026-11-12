# Skill: migration

Every schema change is a migration. Every migration is checked against the canonical schema doc (`docs/schema.md`, owned by F47) before it runs and verified against it after. No silent drift.

## When this skill applies

Every Alembic revision under `coffee-scheduler/db/`, every manual `ALTER TABLE`, every backfill script that transforms data, and every `pg_dump`-shaped change that touches a table. PRs that touch `db/` or `docs/schema.md` trigger the CI drift check.

## Pre-migration (PR open)

- The change is reflected in `docs/schema.md` — PK, FK, indexes, effective-dating, partitioning. Doc and migration move together in the same PR.
- The migration file has both `upgrade()` and `downgrade()`.
- A test exists for the migration, written per `test/SKILL.md`, tagged `spec: F47.ac#` for the acceptance criterion it implements (happy: round-trip works; edge: a partition / overlap case if relevant; adversarial: a row that would violate a new constraint).
- If the migration transforms data, a separate idempotent backfill script accompanies it and runs in dev first.

## Migration content checklist

- PK present or preserved.
- FKs declared with explicit `ON DELETE` behaviour (`CASCADE` / `RESTRICT` / `SET NULL`).
- Indexes named `idx_<table>_<columns>`; composite indexes list columns in selectivity order.
- Effective-dated tables use `DATERANGE` + `EXCLUDE USING GIST` where overlap is illegal.
- Partitioned tables stay partitioned — `PARTITION BY RANGE` is not silently dropped.
- JSONB columns have a `CHECK` constraint on the JSON shape if known.
- Enum types via `CREATE TYPE` with values pinned; new values use `ALTER TYPE ... ADD VALUE`, never `DROP TYPE`.
- Every new column has a default or is `NOT NULL` with a backfill, never `NULL` by accident.

## Post-migration (PR ready to merge)

Run these locally before requesting review:

- `alembic upgrade head && alembic downgrade -1 && alembic upgrade head` round-trips cleanly.
- `scripts/check_schema_drift.py` (below) reports zero drift.
- Row counts and invariants from F44's ETL still hold.
- F26 reconciliation (Coverage_Rules ↔ Labor_Rules) still passes.
- Existing tests pass; the new migration test passes.

## Drift detection (CI gate)

`scripts/check_schema_drift.py` — runs in CI on every PR that touches `db/` or `docs/schema.md`.

- Parses `docs/schema.md` for the canonical column / FK / index list per table.
- Connects to Postgres, queries `information_schema`, `pg_indexes`, `pg_constraint`.
- Fails the build if any column, FK, constraint or index in the live DB is not in the doc, or vice versa.
- Exit codes: `0` = no drift, `1` = drift detected (PR blocked), `2` = could not connect (env error, distinct from drift).

Run locally:

```bash
python scripts/check_schema_drift.py --schema coffee-scheduler/docs/schema.md \
  --db-url "$DATABASE_URL"
```

## Forbidden in a migration

- `DROP TABLE` without a backup-and-restore plan documented in the PR description.
- Removing an `EXCLUDE` constraint without first proving the data is non-overlapping.
- Dropping a partition without confirming it is empty (`SELECT count(*) FROM ...`).
- Silent type widening (`INT` → `BIGINT`, `VARCHAR(N)` → `TEXT`) without a backfill and a doc note.
- Long-running locks on a table with live traffic — `ALTER TABLE ... ADD COLUMN` with a default is the only acceptable form.

## Anti-patterns

- "I'll update the doc later." Update `docs/schema.md` in the same PR; that's what drift detection enforces.
- "The migration is small, no test needed." Every migration has a test.
- "Backfill directly in prod." Backfill scripts run in dev against a copy of the data first.
- "The constraint can be added later, the data is fine." Add the constraint in the migration; let Postgres verify.
- "Two changes in one revision." One logical change per revision; chain them.

## Cross-references

- `test/SKILL.md` — for the SDD / TDD workflow that produces migration tests.
- F47 — the canonical schema doc this skill checks against.
- F44 — the ETL whose invariants must still hold after every migration.
- F26 — the Coverage_Rules ↔ Labor_Rules reconciliation that must still pass.
