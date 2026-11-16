"""CS-068a — Multi-tenant isolation GATE (DB layer). **CI GATE: no second tenant until green.**

This suite is the automated proof that Postgres RLS isolates tenants. It is the regression gate for
the whole tenancy foundation (migrations 0001/0004/0005/0006/0007 + data_access/session.py). It must be
run against a freshly-migrated database (`alembic upgrade head`) as a REQUIRED merge gate.

Scope = DB-level isolation (F68.ac1, the WITH CHECK + context-bleed parts of ac2/ac3, and the ac5 gate
contract). The HTTP-level ACs (401/403/404, JWT alg=none/tamper/expired, impersonation, suspended-tenant,
cache-key, PII-scrub) are CS-068b — Blocked on CS-061 (auth) + CS-070 (routing) — and are deliberately NOT
asserted here.

Harness invariants that keep this gate from passing FALSELY (the whole point of a gate):
  * Every behavioural test runs as the non-owner `app_rw` role (RLS does not apply to the owner/superuser).
    `GateHarness` proves app_rw is NOT a superuser and NOT BYPASSRLS before any isolation claim is trusted.
  * No second tenant is ever committed — cross-tenant reads are proven by switching the session to a
    DIFFERENT tenant id (which owns no rows) and showing tenant A's rows vanish. Every txn rolls back.
  * FORCE RLS is asserted at the CATALOG level on every protected relation (a superuser connection could
    never prove FORCE behaviourally).

Run: DATABASE_URL=postgresql://coffeeshop:coffeeshop@localhost:5432/coffeeshop \
     python3 coffee-scheduler/test/test_isolation_gate.py

Authored by Claude (orchestration: Codex dev / Claude owns tests + completion).
"""
import os
import sys
import unittest
import uuid
from pathlib import Path

SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
if str(SCHEDULER_ROOT) not in sys.path:
    sys.path.insert(0, str(SCHEDULER_ROOT))

DSN = os.environ.get("DATABASE_URL")
TENANT_A = "00000000-0000-0000-0000-000000000001"  # maven-demo seed (owns rows)
TENANT_B = str(uuid.uuid4())                         # ephemeral; never provisioned, owns no rows

try:
    import psycopg
    from psycopg import Rollback
    from psycopg.errors import InsufficientPrivilege
except Exception:  # pragma: no cover
    psycopg = None

# Tenant-scoped tables that MUST carry ENABLE + FORCE RLS (domain tables + restricted PII).
# `tenants` is intentionally ENABLE-not-FORCE (0004: owner/ETL resolve tenants by slug), so it is
# asserted separately. `weather_codes` is a global, tenant-less reference table (BUG-023) — no tenant_id.
TENANT_TABLES = [
    "taxonomy_meta", "stores", "store_hours", "school_calendars", "school_calendar_days",
    "holidays", "employer_config", "stations", "fixed_tasks", "coverage_rules", "shift_rules",
    "cost_targets", "throughput_standards", "demand_adjustments", "product_taxonomy",
    "calibration_log", "employees", "labor_rules", "consent_waivers", "events", "schedules",
    "shifts", "shift_assignments", "forecasts", "rule_eval_log", "transactions",
    "weather_hourly", "weather_daily", "employee_pii", "langgraph_checkpoints",
]
PARTITION_PARENTS = ("rule_eval_log", "transactions", "weather_hourly")
PROBE_TABLE = "stores"  # a plain domain table that owns maven-demo rows
TENANT_TABLE_COVERAGE_SQL = """
    SELECT c.relname
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped
    WHERE n.nspname = 'public'
      AND c.relkind IN ('r', 'p')
      AND NOT EXISTS (SELECT 1 FROM pg_inherits i WHERE i.inhrelid = c.oid)
"""


def _set_ctx(conn, tenant):
    conn.execute("SET LOCAL ROLE app_rw")
    conn.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant,))


@unittest.skipUnless(DSN and psycopg, "DATABASE_URL and psycopg are required")
class GateHarness(unittest.TestCase):
    """If these fail, every behavioural isolation claim below is untrustworthy — fix the DB first."""

    # spec: F68.ac5 — the gate runs as app_rw, a non-superuser role WITHOUT BYPASSRLS.
    def test_app_rw_is_non_superuser_without_bypassrls(self):
        with psycopg.connect(DSN) as conn:
            super_, bypass = conn.execute(
                "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'app_rw'"
            ).fetchone()
            self.assertFalse(super_, "app_rw must NOT be a superuser (would bypass RLS)")
            self.assertFalse(bypass, "app_rw must NOT have BYPASSRLS (would bypass RLS)")

    # spec: F68.ac5 — setting the role actually takes effect (current_user becomes app_rw).
    def test_set_local_role_takes_effect(self):
        with psycopg.connect(DSN) as conn:
            with conn.transaction():
                _set_ctx(conn, TENANT_A)
                self.assertEqual(conn.execute("SELECT current_user").fetchone()[0], "app_rw")
                raise Rollback


@unittest.skipUnless(DSN and psycopg, "DATABASE_URL and psycopg are required")
class CrossTenantIsolation(unittest.TestCase):
    # spec: F68.ac1 — a different tenant cannot READ tenant A's rows.
    def test_cross_tenant_read_denied(self):
        with psycopg.connect(DSN) as conn:
            with conn.transaction():
                _set_ctx(conn, TENANT_A)
                n_a = conn.execute(f'SELECT count(*) FROM "{PROBE_TABLE}"').fetchone()[0]
                self.assertGreater(n_a, 0, "precondition: maven-demo must own some rows")
                conn.execute("SELECT set_config('app.tenant_id', %s, true)", (TENANT_B,))
                n_b = conn.execute(f'SELECT count(*) FROM "{PROBE_TABLE}"').fetchone()[0]
                self.assertEqual(n_b, 0, "tenant B must not see tenant A's rows")
                raise Rollback

    # spec: F68.ac1 — a different tenant cannot UPDATE or DELETE tenant A's rows (rowcount 0).
    def test_cross_tenant_update_and_delete_touch_nothing(self):
        with psycopg.connect(DSN) as conn:
            with conn.transaction():
                _set_ctx(conn, TENANT_B)  # B owns no rows; A's rows are invisible to it
                upd = conn.execute(f'UPDATE "{PROBE_TABLE}" SET location_id = location_id')
                self.assertEqual(upd.rowcount, 0, "cross-tenant UPDATE must affect 0 rows")
                dele = conn.execute(f'DELETE FROM "{PROBE_TABLE}"')
                self.assertEqual(dele.rowcount, 0, "cross-tenant DELETE must affect 0 rows")
                raise Rollback

    # spec: F68.ac1 — FORCE RLS present on every tenant-scoped table (catalog proof; owner can't bypass).
    def test_force_rls_on_every_tenant_table(self):
        with psycopg.connect(DSN) as conn:
            for t in TENANT_TABLES:
                rls, force = conn.execute(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = %s", (t,)
                ).fetchone()
                self.assertTrue(rls, f"{t}: RLS not enabled")
                self.assertTrue(force, f"{t}: FORCE RLS not set")

    # spec: F68.ac1 / BUG-021 — FORCE RLS present on every partition child too.
    def test_force_rls_on_every_partition(self):
        with psycopg.connect(DSN) as conn:
            rows = conn.execute(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
                "FROM pg_inherits i JOIN pg_class c ON c.oid=i.inhrelid "
                "JOIN pg_class p ON p.oid=i.inhparent WHERE p.relname = ANY(%s)",
                (list(PARTITION_PARENTS),),
            ).fetchall()
            self.assertGreaterEqual(len(rows), 3)
            for name, rls, force in rows:
                self.assertTrue(rls and force, f"{name}: partition missing FORCE RLS")


@unittest.skipUnless(DSN and psycopg, "DATABASE_URL and psycopg are required")
class WithCheckRejection(unittest.TestCase):
    # spec: F68.ac2 — re-homing a row to another tenant (write with foreign tenant_id) is rejected.
    def test_rehome_to_other_tenant_rejected(self):
        with psycopg.connect(DSN) as conn:
            with conn.transaction():
                _set_ctx(conn, TENANT_A)
                with self.assertRaises(InsufficientPrivilege):  # "new row violates row-level security policy"
                    conn.execute(f'UPDATE "{PROBE_TABLE}" SET tenant_id = %s', (TENANT_B,))
                raise Rollback


@unittest.skipUnless(DSN and psycopg, "DATABASE_URL and psycopg are required")
class ContextBleed(unittest.TestCase):
    # spec: F68.ac3 — a prior SET LOCAL never survives onto the next use of the same physical connection.
    def test_no_context_bleed_across_transactions(self):
        with psycopg.connect(DSN) as conn:
            with conn.transaction():  # txn #1: tenant A context
                _set_ctx(conn, TENANT_A)
                self.assertGreater(conn.execute(f'SELECT count(*) FROM "{PROBE_TABLE}"').fetchone()[0], 0)
                raise Rollback
            with conn.transaction():  # txn #2: SAME connection, NO context set
                conn.execute("SET LOCAL ROLE app_rw")
                leaked = conn.execute(f'SELECT count(*) FROM "{PROBE_TABLE}"').fetchone()[0]
                self.assertEqual(leaked, 0, "tenant A context bled into a fresh transaction (pool-unsafe!)")
                raise Rollback


@unittest.skipUnless(DSN and psycopg, "DATABASE_URL and psycopg are required")
class FailClosedContext(unittest.TestCase):
    # spec: F68.ac1 / BUG-022 — empty and malformed tenant context both yield 0 rows, never an error.
    def test_empty_and_malformed_context_fail_closed(self):
        with psycopg.connect(DSN) as conn:
            for bad in ("", "not-a-uuid"):
                with conn.transaction():
                    conn.execute("SET LOCAL ROLE app_rw")
                    conn.execute("SELECT set_config('app.tenant_id', %s, true)", (bad,))
                    n = conn.execute(f'SELECT count(*) FROM "{PROBE_TABLE}"').fetchone()[0]
                    self.assertEqual(n, 0, f"context {bad!r} must fail closed (0 rows), not raise")
                    raise Rollback


@unittest.skipUnless(DSN and psycopg, "DATABASE_URL and psycopg are required")
class CoverageGate(unittest.TestCase):
    """Self-maintaining: a FUTURE tenant table that ships without RLS, or is not in this suite, fails here."""

    # spec: F68.ac5 — every base table with a tenant_id column is accounted for AND forced.
    def test_every_tenant_id_table_is_covered_and_forced(self):
        known = set(TENANT_TABLES)
        with psycopg.connect(DSN) as conn:
            tables = [r[0] for r in conn.execute(TENANT_TABLE_COVERAGE_SQL).fetchall()]
            self.assertTrue(tables, "expected to find tenant_id tables")
            for t in tables:
                self.assertIn(t, known, f"{t} has tenant_id but is NOT in the isolation gate allow-list")
                force = conn.execute(
                    "SELECT relforcerowsecurity FROM pg_class WHERE relname = %s", (t,)
                ).fetchone()[0]
                self.assertTrue(force, f"{t} has tenant_id but no FORCE RLS")

    # spec: F68.ac5 / BUG-028 — a future period partition is excluded structurally, not flagged.
    def test_future_partition_does_not_break_coverage(self):
        with psycopg.connect(DSN) as conn:
            with conn.transaction():
                conn.execute(
                    "CREATE TABLE transactions_p2099_01 PARTITION OF transactions "
                    "FOR VALUES FROM ('2099-01-01') TO ('2099-02-01')"
                )
                names = [r[0] for r in conn.execute(TENANT_TABLE_COVERAGE_SQL).fetchall()]
                self.assertNotIn(
                    "transactions_p2099_01", names,
                    "a period partition must be excluded structurally, not flagged",
                )
                raise Rollback

    # spec: F68.ac5 / BUG-020 — tenants enumeration tripwire: app_rw sees ONLY its own tenant row.
    #   Fails loudly if a broken `tenants ... USING (true)` policy is ever applied (the dirty-0001 hazard).
    def test_tenants_enumeration_is_scoped_to_own_row(self):
        with psycopg.connect(DSN) as conn:
            with conn.transaction():
                _set_ctx(conn, TENANT_A)
                n = conn.execute("SELECT count(*) FROM tenants").fetchone()[0]
                self.assertEqual(n, 1, "app_rw must see exactly its own tenant row (cross-tenant leak!)")
                raise Rollback


if __name__ == "__main__":
    unittest.main()
