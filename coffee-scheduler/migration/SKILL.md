# Skill: migration

Every schema change is a migration. Every migration is checked against the canonical schema doc (`docs/schema.md`, owned by F47) before it runs and verified against it after. No silent drift.

## Deletion policy — soft-delete only (ABSOLUTE)

**Data is never hard-deleted. Deletion is a state change, never a row removal.** This holds in every migration, backfill script, repository, service, endpoint, and job — no exceptions in ordinary development.

- **Never emit `DELETE FROM <domain/tenant table>`** and **never `DROP TABLE`** a data-bearing table. "Removing" a record means marking it removed, not deleting the row.
- **Soft-delete marker on every domain table.** Use the existing lifecycle `status` where the table already models one (e.g. `tenants.status = 'churned'`, invitations `status = 'revoked'`); otherwise add a `deleted_at TIMESTAMPTZ NULL` (+ optional `deleted_by`). "Delete" = set the marker, keep the row.
- **Reads exclude soft-deleted rows by default** — enforce at the data layer: an RLS policy / view / query filter of `deleted_at IS NULL` (or `status NOT IN (<terminal states>)`). A soft-deleted row must not leak into normal results.
- **FKs must not erase data.** Prefer `ON DELETE RESTRICT` or `SET NULL`; never lean on `ON DELETE CASCADE` as an erasure mechanism (parents are never hard-deleted, so cascade should never fire). A new child table referencing a soft-deletable parent uses `RESTRICT`/`SET NULL`.
- **Schema objects vs data.** A migration may drop a *column/index/constraint* (schema shape) per the rules below, but may **not** delete rows or drop a table that holds tenant/domain data.
- **The one lawful exception is out-of-band and audited, not a developer action:** GDPR / right-to-be-forgotten erasure (F69) runs the documented soft-delete → retention grace → **hard delete → backup purge** pipeline, gated, recorded in `audit_log`, and owned by that compliance path alone. It is never an ad-hoc `DELETE` in application code or a routine migration. If a task seems to need a hard delete for any other reason, stop and escalate — do not write the `DELETE`.
- **Test teardown** that cleans up its own probe fixtures is not application deletion and is out of scope for this policy (but prefer transactional rollback where practical).

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
- `scripts/check_no_hard_delete.py` is the paired lifecycle gate. It rejects direct hard deletes of Tier-A/B
  domain records and data-table drops outside the audited F69 erasure path, with the same `0`/`1`/`2` contract.

Run locally:

```bash
python scripts/check_schema_drift.py --schema coffee-scheduler/docs/schema.md \
  --db-url "$DATABASE_URL"
python scripts/check_no_hard_delete.py
```

## Forbidden in a migration

- **`DELETE FROM` any domain/tenant table, ever** — soft-delete only (see the Deletion policy above). The sole exception is the audited F69 GDPR-erasure pipeline, never a routine migration.
- **`DROP TABLE` of a data-bearing table, ever** — retire it via a soft-delete marker instead. Dropping a genuinely empty, non-data scaffold table still requires a backup-and-restore plan documented in the PR description.
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

## Transaction and pooled-session safety

- `SET LOCAL`, `set_config(..., true)`, and temporary visibility GUCs last through the transaction, not through one helper call. A helper that enables a temporary override must save the previous value and restore it in `finally`, including when SQL fails. Preserve an explicitly set prior value; restore an unset value to its default.
- Validate values and bounds before SQL execution. Identifiers must come from a code-owned allowlist/catalog and be quoted; all values remain parameters. Pagination and batch sizes need explicit upper bounds to avoid accidental unbounded tenant reads.
- Include same-transaction regression checks: invoke the helper, then issue a normal query on the same connection and prove tenant/RLS/soft-delete visibility is unchanged. Cover both success and exception paths.
