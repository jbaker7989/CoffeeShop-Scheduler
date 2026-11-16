"""BUG-021 — every partition (parent + each child, incl. DEFAULT) enforces tenant RLS (migration 0007).

A read routed through the partitioned parent was always isolated, but a DIRECT read of a child
partition had its own (missing) RLS. 0007 applies ENABLE + FORCE RLS + the fail-closed
`tenant_isolation` policy to every child; `db/partitions.py::ensure_partition_rls` keeps future
partitions protected. Grant-absence on the partitions remains as a second layer.

Run: DATABASE_URL=postgresql://coffeeshop:coffeeshop@localhost:5432/coffeeshop \
     python3 coffee-scheduler/test/test_bug021_partition_rls.py
DB-touching tests skip unless DATABASE_URL is set; every mutation rolls back.

Authored by Claude (orchestration: Codex dev / Claude tests).
"""
import importlib.util
import os
import sys
import unittest
from pathlib import Path

SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
if str(SCHEDULER_ROOT) not in sys.path:
    sys.path.insert(0, str(SCHEDULER_ROOT))

DSN = os.environ.get("DATABASE_URL")
PARENTS = ("rule_eval_log", "transactions", "weather_hourly")

try:
    import psycopg
    from psycopg import Rollback
    from psycopg.errors import InsufficientPrivilege
except Exception:  # pragma: no cover
    psycopg = None


def _load_ensure_partition_rls():
    path = SCHEDULER_ROOT / "db" / "partitions.py"
    spec = importlib.util.spec_from_file_location("db_partitions", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.ensure_partition_rls


_CHILDREN_SQL = """
    SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
           EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid
                   AND p.polname = 'tenant_isolation') AS has_policy
    FROM pg_inherits i
    JOIN pg_class c ON c.oid = i.inhrelid
    JOIN pg_class p ON p.oid = i.inhparent
    WHERE p.relname = ANY(%s)
"""


@unittest.skipUnless(DSN and psycopg, "DATABASE_URL and psycopg are required")
class PartitionRls(unittest.TestCase):
    # spec: F68.ac1 / BUG-021 — every child partition has ENABLE + FORCE RLS and the policy.
    def test_every_partition_has_force_rls_and_policy(self):
        with psycopg.connect(DSN) as conn:
            with conn.transaction():
                rows = conn.execute(_CHILDREN_SQL, (list(PARENTS),)).fetchall()
                self.assertGreaterEqual(len(rows), 3, "expected at least the three *_default partitions")
                for name, rls, force, has_policy in rows:
                    self.assertTrue(rls, f"{name}: ROW LEVEL SECURITY not enabled")
                    self.assertTrue(force, f"{name}: FORCE ROW LEVEL SECURITY not set")
                    self.assertTrue(has_policy, f"{name}: missing tenant_isolation policy")
                raise Rollback

    # spec: F68.ac1 / BUG-021 — grant-absence backstop: app_rw cannot read a default partition directly.
    def test_default_partition_direct_read_blocked_for_app_rw(self):
        with psycopg.connect(DSN) as conn:
            with conn.transaction():
                conn.execute("SET LOCAL ROLE app_rw")
                with self.assertRaises(InsufficientPrivilege):
                    conn.execute("SELECT count(*) FROM transactions_default").fetchone()
                raise Rollback

    # spec: F68.ac1 / BUG-021 — the reusable helper applies FORCE RLS + policy to a fresh partition-like table.
    def test_ensure_partition_rls_helper_applies_force(self):
        ensure_partition_rls = _load_ensure_partition_rls()
        with psycopg.connect(DSN) as conn:
            with conn.transaction():
                conn.execute("CREATE TEMP TABLE _probe_part (tenant_id uuid) ON COMMIT DROP")
                ensure_partition_rls(conn, "_probe_part")
                rls, force = conn.execute(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = '_probe_part'"
                ).fetchone()
                self.assertTrue(rls and force, "helper must ENABLE + FORCE RLS")
                has_policy = conn.execute(
                    "SELECT EXISTS (SELECT 1 FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid "
                    "WHERE c.relname='_probe_part' AND p.polname='tenant_isolation')"
                ).fetchone()[0]
                self.assertTrue(has_policy, "helper must create the tenant_isolation policy")
                raise Rollback


if __name__ == "__main__":
    unittest.main()
