# F60-2: Tenant-stamping verification & idempotency guard migration

> **Reviewed & corrected 2026-11-14.** The original draft was a "backfill existing rows" migration with
> several fatal bugs and a moot premise. Corrections (full list in §9):
> - **Premise corrected.** The schema is `tenant_id UUID NOT NULL` from inception (greenfield `0001`), so
>   there are **no NULL rows to backfill**. F60-2 is therefore a **defensive verification + idempotency
>   guard**, not a bulk backfill. (A bulk backfill only applies when migrating a *pre-existing* non-tenant DB,
>   which never happened here.)
> - **Revision `0005` ← `0004`** (the draft's `0002` collides with the existing coverage-dating migration;
>   the chain is `0001→0002→0003→0004`).
> - **No `CREATE INDEX CONCURRENTLY`** — it cannot run inside Alembic's transaction, cannot run on
>   partitioned parents, and the `(tenant_id, …)` indexes already exist from `0001`.
> - **Reversible no-op `downgrade()`** (the draft raised `NotImplementedError`, which breaks the F43 AC3
>   round-trip test).
> - Stdlib `unittest` tests (no pytest); explicit `RuntimeError` instead of bare `assert`.

## Summary
A small, reversible Alembic migration (`0005`) that **verifies** every F47 domain table is fully
tenant-stamped (zero `NULL tenant_id`) and performs an **idempotent, defensive no-op backfill**
(`UPDATE … WHERE tenant_id IS NULL`) that is a true no-op on the current schema but makes the guarantee
explicit and safe for any future path that could leave a row unstamped. It changes no schema and adds no
indexes.

## Goals
- **G1** — Prove every domain-table row carries `tenant_id` (the `NOT NULL` constraint already guarantees
  this; the migration makes it an auditable, versioned check).
- **G2** — Idempotent: re-running affects **zero** rows and asserts the same invariant.
- **G3** — Safe: no `CONCURRENTLY`, no long locks, no schema change; a plain cheap `UPDATE … WHERE
  tenant_id IS NULL` (no-op) + a `COUNT(*) WHERE tenant_id IS NULL` check per table.
- **G4** — Row counts unchanged (no data loss), verified before/after.
- **G5** — Reversible (trivial no-op downgrade) so the F43 round-trip test stays green.

## In scope
- Migration `0005_f60_tenant_stamp_verify.py` (revision `0005`, down_revision `0004`).
- Per tenant-scoped domain table: a defensive `UPDATE … SET tenant_id = <maven> WHERE tenant_id IS NULL`
  (no-op today) and an invariant check `SELECT count(*) … WHERE tenant_id IS NULL` that **raises** if > 0.
- A before/after `count(*)` equality check per table (explicit `RuntimeError`, not `assert`).
- Stdlib tests (§7).

## Out of scope
- Any schema change, new index, or `tenant_id` column add (owned by `0001`/CS-047).
- Standalone backfill script (unnecessary — greenfield).
- RLS (done: `0001` + BUG-020 `0004`), ETL changes (F44/CS-044 already takes `--tenant-slug`).
- `CREATE INDEX CONCURRENTLY` / production-scale lock tuning (not applicable — no index work here).

## Approach — migration `0005`

```python
revision = "0005"
down_revision = "0004"

MAVEN = "00000000-0000-0000-0000-000000000001"

# Tenant-scoped domain tables (exclude GLOBAL reference tables, which have no tenant_id):
#   global/exempt: tenants (root, self-scoped), weather_codes
DOMAIN_TABLES = [
    "taxonomy_meta", "stores", "store_hours", "school_calendars", "school_calendar_days",
    "holidays", "employer_config", "stations", "fixed_tasks", "coverage_rules", "shift_rules",
    "cost_targets", "throughput_standards", "demand_adjustments", "product_taxonomy",
    "calibration_log", "employees", "employee_pii", "labor_rules", "consent_waivers", "events",
    "schedules", "shifts", "shift_assignments", "forecasts", "rule_eval_log", "transactions",
    "weather_hourly", "weather_daily",
]

def upgrade() -> None:
    conn = op.get_bind()
    for t in DOMAIN_TABLES:
        before = conn.exec_driver_sql(f'SELECT count(*) FROM "{t}"').scalar()
        # Defensive, idempotent no-op on the current NOT NULL schema:
        conn.exec_driver_sql(f"UPDATE \"{t}\" SET tenant_id = %s WHERE tenant_id IS NULL", (MAVEN,))
        after = conn.exec_driver_sql(f'SELECT count(*) FROM "{t}"').scalar()
        if before != after:
            raise RuntimeError(f"{t}: row count changed during verification ({before} -> {after})")
        nulls = conn.exec_driver_sql(f'SELECT count(*) FROM "{t}" WHERE tenant_id IS NULL').scalar()
        if nulls:
            raise RuntimeError(f"{t}: {nulls} rows have NULL tenant_id after backfill")

def downgrade() -> None:
    # Verification/no-op migration — nothing to undo. Reversible by construction.
    pass
```

Notes:
- The migration body generates SQL **only from a hard-coded table allow-list** — never from user input —
  and parameterises the `MAVEN` value. Identifiers are double-quoted.
- It runs inside Alembic's normal transaction (no `CONCURRENTLY`), so it is atomic and fast (counts only).
- Append-only tables (`calibration_log`, `rule_eval_log`) are included — the `UPDATE … WHERE tenant_id IS
  NULL` is a no-op (zero matching rows), so it does not violate append-only; the invariant check still runs.

## Acceptance criteria (F60-2, corrected)
| AC | Criterion |
|---|---|
| **F60-2.1** | After `alembic upgrade head`, every domain table has **zero** `NULL tenant_id` (invariant holds). |
| **F60-2.2** | Running the migration logic a **second** time affects **zero** rows (idempotent). |
| **F60-2.3** | `count(*)` per domain table is unchanged across the migration (no data loss). |
| **F60-2.4** | The migration **round-trips**: `upgrade → downgrade → upgrade` is clean (no-op downgrade; F43 AC3). |
| **F60-2.5** | On a crafted table with a `NULL tenant_id` row, the migration **raises** (the guard fires), not silently passes. |

*(The draft's "200K-row, lock <5s" AC is dropped: no index/lock work occurs here, and the environment has
no production-sized DB; the migration is count-only.)*

## Dependencies
| Dep | Status |
|---|---|
| `0001` tenant-aware schema (`tenant_id NOT NULL`) | ✅ Done |
| `0004` tenants RLS (BUG-020) | ✅ Done |
| F43 round-trip reversibility test | ✅ Exists (`test_migrations_roundtrip.py`) |

## Runbook
1. `cd coffee-scheduler/db && DATABASE_URL=…+psycopg alembic upgrade head`
2. Verify: `SELECT count(*) FROM employees WHERE tenant_id IS NULL` → 0 (for every domain table).
3. Idempotency: `alembic upgrade head` again → no change.
4. Round-trip: `alembic downgrade -1 && alembic upgrade head` → clean.

## 7. Test plan (stdlib — **no pytest**)
Stdlib `unittest` + psycopg, tagged `spec: F60-2.acN`, `python3`-runnable with `unittest.main()`,
DB-skipped without `DATABASE_URL`, mutations rolled back:
- **ac1/ac3**: after `upgrade head`, every `DOMAIN_TABLES` table has 0 NULL `tenant_id` and the row counts
  equal a baseline captured as the owner.
- **ac2**: run the upgrade twice; the second run changes no rows.
- **ac4**: `upgrade → downgrade -1 → upgrade` round-trips (extend `test_migrations_roundtrip.py` or a sibling).
- **ac5**: in a transaction, insert a row with `NULL tenant_id` into a temp/throwaway context and assert the
  migration's invariant check raises (or, since `NOT NULL` forbids a real NULL insert, assert the invariant
  query returns 0 and document that `NOT NULL` is the first line of defence). Roll back.
- Confirm fixtures intact afterward (reload the ETL if the round-trip wiped data).

## 8. Honest note on value
Because the schema enforces `tenant_id NOT NULL` from inception, this migration is **defensive, not
corrective** — it can never find a row to fix on the current schema. Its value is (a) a versioned, auditable
assertion of the tenant-stamping invariant, and (b) a guard that would catch a regression if a future
migration ever relaxed `NOT NULL` or added a column without stamping it. If the team prefers, the same
guarantee can live purely as a **test** (no migration) — that is a reasonable alternative to flag.

## 9. Review corrections applied (2026-11-14)
1. Premise corrected (greenfield `NOT NULL` → verification, not backfill).
2. Revision `0002`→`0005`, `down_revision = 0004` (collision fixed).
3. Removed all `CREATE INDEX CONCURRENTLY` (txn-illegal, partition-illegal, redundant).
4. `downgrade()` is a reversible no-op (was `NotImplementedError`, which breaks F43 AC3).
5. Removed leftover pseudocode (`btree_gist`, role re-creation, unformatted `{seq_of_tables}` SELECT).
6. Bare `assert` → explicit `RuntimeError` (survives `python -O`).
7. Batching claim dropped (count-only; nothing to batch); tests are stdlib, not pytest.
8. GLOBAL tables (`weather_codes`, `tenants`) excluded from the tenant-stamp list; `employee_pii` included
   (it is tenant-scoped, just restricted-role).
