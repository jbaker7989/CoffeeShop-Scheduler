# BUG-028 / 029 / 030 — isolation-gate hardening & function `search_path`

## Context
The CS-068 / BUG-021-022 review (2026-11-15) surfaced five issues. The two **P2** items (BUG-026 incomplete
fail-closed on `langgraph_checkpoints`; BUG-027 the gate asserts FORCE but not the policy *expression*) are the
real correctness work and are planned separately (migration `0008` + gate expression assertion). This plan
covers the three **P3 hardening** items, which are low-risk and independent of the RLS *policy* fixes:

- **BUG-028** — the gate's coverage meta-test excludes partition children by **name** (`NOT LIKE '%_default'`);
  a future period partition (e.g. `transactions_2026_01`) would be flagged and fail the gate on a legitimate
  schema change. Make the exclusion **structural**.
- **BUG-029** — the context-bleed test proves `SET LOCAL` transaction-locality on one psycopg connection, but
  never exercises the real hazard (PgBouncer handing one physical backend to two logical clients). Add a
  **PgBouncer-in-the-loop integration test**.
- **BUG-030** — `app_current_tenant()` has no pinned `search_path`. Not a vulnerability today (it is `STABLE`,
  invoker-rights), but pin `search_path = pg_catalog` as defense-in-depth.

All three are test-first (SDD/TDD): stdlib `unittest`, `spec:` tags, `python3`-runnable, DB-skipped without the
relevant env var, every mutation rolled back. Current migration head is `0007`.

## Dependencies & sequencing
- BUG-028 and BUG-029 are **test-only** — no migration, no coupling to the P2 work. Buildable now, any order.
- BUG-030 needs a migration. **Revision number:** base `down_revision` on `alembic heads` at build time.
  - If this plan lands **before** BUG-026's `0008`: BUG-030 migration = `0008` (down_revision `0007`), and
    BUG-026 later becomes `0009`.
  - If BUG-026's `0008` lands **first**: **fold** the `SET search_path = pg_catalog` into that migration's
    `CREATE OR REPLACE FUNCTION app_current_tenant()` recreate instead of a standalone migration (one function
    definition, one source of truth) — then BUG-030 is a test-only change.
  - Recommended: build BUG-030 **together with BUG-026** (both touch the same function). If building 028-030
    standalone, BUG-030 is its own migration `0008`.

---

## BUG-028 — structural partition exclusion in the coverage meta-test (test-only)

**File:** `test/test_isolation_gate.py` → `CoverageGate.test_every_tenant_id_table_is_covered_and_forced`.

Replace the name-pattern query with a catalog query that returns only **non-partition-child** relations that
carry a `tenant_id` column (ordinary tables + partitioned parents), so partition children — default *or* future
period partitions — are excluded structurally and verified elsewhere (`test_force_rls_on_every_partition`):

```sql
SELECT c.relname
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped
WHERE n.nspname = 'public'
  AND c.relkind IN ('r', 'p')                                   -- ordinary + partitioned parent
  AND NOT EXISTS (SELECT 1 FROM pg_inherits i WHERE i.inhrelid = c.oid)   -- NOT a partition child
```
The existing assertions (each in `TENANT_TABLES`, each `relforcerowsecurity`) stay as-is; partitioned parents
(`relkind='p'`) already carry FORCE from `0001`.

**Regression test (prove the brittleness is gone):** add `test_future_partition_does_not_break_coverage` — in a
rolled-back transaction, create a throwaway period partition and assert the coverage query does **not** return
it:
```python
with conn.transaction():
    conn.execute("CREATE TABLE transactions_p2099_01 PARTITION OF transactions "
                 "FOR VALUES FROM ('2099-01-01') TO ('2099-02-01')")
    names = [r[0] for r in conn.execute(<the new coverage query>).fetchall()]
    self.assertNotIn("transactions_p2099_01", names,
                     "a period partition must be excluded structurally, not flagged")
    raise Rollback      # never committed
```
(Runs as the owner; `transactions` is DEFAULT-partitioned so a disjoint future range attaches cleanly. If a
stray 2099 row ever lived in `transactions_default` the ATTACH would fail — acceptable for a rolled-back test,
and effectively impossible with 2023-era fixtures.)

**No production code changes.** `spec: F68.ac5`.

---

## BUG-029 — PgBouncer-in-the-loop pooled-isolation integration test (test-only)

**New file:** `test/test_pool_isolation.py`. Skips unless `PGBOUNCER_URL` is set
(`postgresql://coffeeshop:coffeeshop@localhost:6432/coffeeshop` — the compose `pgbouncer` service, transaction
mode, is already up on 6432).

**Critical gotcha (must handle):** psycopg3 auto-prepares statements, which **breaks** under PgBouncer
transaction pooling. Every connection in this test must disable it: `psycopg.connect(PGBOUNCER_URL,
prepare_threshold=None)`. The module docstring states this is the canonical pooled-client configuration.

Tests (all `spec: F68.ac3`):
1. `test_context_does_not_survive_across_transactions_via_pool` — on one pooled client: txn#1 sets context A
   (`SET LOCAL ROLE app_rw` + `set_config('app.tenant_id', A, true)`), reads A's rows (>0), commits; txn#2 on
   the same client sets the role but **no tenant** → asserts 0 rows. Loop this ~50× so PgBouncer reuses
   backends that previously held tenant A; every iteration must be 0 (no bleed across the pool).
2. `test_two_concurrent_clients_never_see_each_others_rows` — two separate pooled connections, one always
   scoped to A and one to an ephemeral B (owns no rows); interleave reads over the pool and assert A sees only
   A's rows and B sees 0. Proves per-transaction scoping holds when PgBouncer multiplexes both onto a shared
   backend pool.
3. `test_pool_mode_is_transaction` (guard) — connect to the PgBouncer admin pseudo-db and `SHOW CONFIG`
   (or assert via `SHOW POOLS`) that `pool_mode = transaction`; skip gracefully if the admin interface is not
   reachable, so the suite degrades to the two behavioural tests.

**Determinism note:** the invariant (`SET LOCAL` resets at transaction end) makes every assertion 0/own-only
regardless of which backend PgBouncer picks; the loop maximizes backend reuse to make a *regression* (a
non-local `SET ROLE`, or session-mode misconfig) actually surface. Optionally, a dedicated low-`pool_size` test
profile makes reuse certain, but is not required for correctness.

This is the integration complement to the gate's fast unit-level `ContextBleed` test; it naturally joins the
CS-068b suite. `test_isolation_gate.py` is unchanged.

---

## BUG-030 — pin `search_path` on `app_current_tenant()`

**Migration** (next free revision — see sequencing; standalone = `0008`, or folded into BUG-026's `0008`).

`upgrade()`:
```python
op.execute("""
    CREATE OR REPLACE FUNCTION app_current_tenant() RETURNS uuid
    LANGUAGE plpgsql STABLE
    SET search_path = pg_catalog
    AS $$
    BEGIN
      RETURN NULLIF(current_setting('app.tenant_id', true), '')::uuid;
    EXCEPTION WHEN invalid_text_representation THEN
      RETURN NULL;
    END; $$;
""")
```
`downgrade()`: `CREATE OR REPLACE FUNCTION` the same body **without** the `SET search_path` clause (or
`ALTER FUNCTION app_current_tenant() RESET search_path`). Reversible; round-trips (F43 AC3).

Everything the body references — `current_setting`, `NULLIF`, the `uuid` type and cast — resolves under
`pg_catalog`, so pinning it there is sufficient and the function still returns correctly.

**Test** (`test/test_bug022_failclosed.py`, extend `Bug022FailClosed`; `spec: F60.ac2 / BUG-030`):
```python
def test_app_current_tenant_pins_search_path(self):
    cfg = conn.execute("SELECT proconfig FROM pg_proc WHERE proname='app_current_tenant'").fetchone()[0]
    self.assertIsNotNone(cfg)
    self.assertIn("search_path=pg_catalog", cfg)
```
Re-run the existing BUG-022 fail-closed tests after the migration to confirm the pinned `search_path` did not
change behaviour (malformed/empty → 0 rows; valid → own rows).

---

## Verification (end-to-end)
1. **BUG-030 migration:** `cd db && DATABASE_URL=…+psycopg alembic upgrade head`; round-trip `alembic
   downgrade -1 && alembic upgrade head` clean; `SELECT proconfig FROM pg_proc WHERE proname='app_current_tenant'`
   shows `{search_path=pg_catalog}`.
2. **BUG-028:** `DATABASE_URL=postgresql://…@localhost:5432/… python3 test/test_isolation_gate.py` green,
   including the new future-partition regression test.
3. **BUG-029:** `PGBOUNCER_URL=postgresql://coffeeshop:coffeeshop@localhost:6432/coffeeshop python3
   test/test_pool_isolation.py` green (skips cleanly if `PGBOUNCER_URL` unset).
4. **No regression:** re-run the full RLS set — `test_isolation_gate`, `test_bug021_partition_rls`,
   `test_bug022_failclosed`, `test_tenants_rls`, `test_f60_2_tenant_stamp`, `test_data_access` — all green.
5. Update Backlog statuses: BUG-028/029/030 → Fixed (with the verifying test files).

## Critical files
- **Edited:** `test/test_isolation_gate.py` (BUG-028), `test/test_bug022_failclosed.py` (BUG-030 test),
  `coffee-scheduler/Backlog.md` (statuses). `FEATURES.md` F60.AC7 may note the pooled integration test.
- **New:** `test/test_pool_isolation.py` (BUG-029); a migration for BUG-030 **only if** built standalone
  (`db/migrations/versions/0008_f60_fn_search_path.py`), else folded into BUG-026's `0008`.
- **Read-only / reuse:** `data_access/session.py` (`open_session_context`), `db/partitions.py`, the compose
  `pgbouncer` service (6432, transaction mode).

## Orchestration (recommended, per the established split)
- BUG-028 + BUG-029 are pure test work → **Claude** authors/executes.
- BUG-030 migration → **Codex** develops; **Claude** adds the `proconfig` assertion and verifies — *or* fold it
  into BUG-026's `0008` if that is built in the same pass.

## Risks / notes
- Keep building against the committed `0001` (the dirty working-tree `0001` + `.bak` remain uncommitted; never
  stage them). The gate's `tenants`-enumeration tripwire still guards that hazard.
- BUG-029 can be mildly non-deterministic; the loop + the `SET LOCAL` invariant keep it green while still able
  to catch a real pooling regression. Do not weaken it to a single iteration.
