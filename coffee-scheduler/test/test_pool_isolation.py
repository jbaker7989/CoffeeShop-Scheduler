"""BUG-029 — prove tenant context stays isolated through PgBouncer transaction pooling.

Run: PGBOUNCER_URL=postgresql://coffeeshop:coffeeshop@localhost:6432/coffeeshop \
     python3 coffee-scheduler/test/test_pool_isolation.py

Every connection disables psycopg3 prepared statements with ``prepare_threshold=None``:
auto-prepared statements do not survive PgBouncer transaction pooling. This is the canonical
pooled-client configuration. DB-touching tests skip unless ``PGBOUNCER_URL`` is set.
"""
import os
import sys
import unittest
import uuid
from pathlib import Path

SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
if str(SCHEDULER_ROOT) not in sys.path:
    sys.path.insert(0, str(SCHEDULER_ROOT))

PGBOUNCER_URL = os.environ.get("PGBOUNCER_URL")
TENANT_A = "00000000-0000-0000-0000-000000000001"  # maven-demo seed (owns rows)
TENANT_B = str(uuid.uuid4())  # ephemeral; never provisioned, owns no rows

try:
    import psycopg
    from psycopg.conninfo import make_conninfo
except Exception:  # pragma: no cover - permits skip without optional DB dependency
    psycopg = None
    make_conninfo = None


def _pooled_connection():
    return psycopg.connect(PGBOUNCER_URL, prepare_threshold=None)


def _set_ctx(conn, tenant):
    conn.execute("SET LOCAL ROLE app_rw")
    conn.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant,))


@unittest.skipUnless(PGBOUNCER_URL and psycopg, "PGBOUNCER_URL and psycopg are required")
class PoolIsolation(unittest.TestCase):
    # spec: F68.ac3 — tenant context cannot survive a committed transaction through the pool.
    def test_context_does_not_survive_across_transactions_via_pool(self):
        with _pooled_connection() as conn:
            for _ in range(50):
                with conn.transaction():
                    _set_ctx(conn, TENANT_A)
                    count = conn.execute("SELECT count(*) FROM stores").fetchone()[0]
                    self.assertGreater(count, 0, "precondition: maven-demo must own stores")
                with conn.transaction():
                    conn.execute("SET LOCAL ROLE app_rw")
                    leaked = conn.execute("SELECT count(*) FROM stores").fetchone()[0]
                    self.assertEqual(
                        leaked, 0,
                        "tenant context bled into a new transaction through PgBouncer",
                    )

    # spec: F68.ac3 — separate pooled clients can only see rows for their own transaction context.
    def test_two_concurrent_clients_never_see_each_others_rows(self):
        with _pooled_connection() as client_a, _pooled_connection() as client_b:
            for _ in range(10):
                with client_a.transaction():
                    _set_ctx(client_a, TENANT_A)
                    count_a = client_a.execute("SELECT count(*) FROM stores").fetchone()[0]
                    self.assertGreater(count_a, 0, "tenant A must see its own stores")
                with client_b.transaction():
                    _set_ctx(client_b, TENANT_B)
                    count_b = client_b.execute("SELECT count(*) FROM stores").fetchone()[0]
                    self.assertEqual(count_b, 0, "tenant B must not see tenant A's stores")

    # spec: F68.ac3 — the integration target must actually be a transaction-mode PgBouncer pool.
    def test_pool_mode_is_transaction(self):
        try:
            admin_dsn = make_conninfo(PGBOUNCER_URL, dbname="pgbouncer")
            with psycopg.connect(admin_dsn, prepare_threshold=None) as conn:
                rows = conn.execute("SHOW CONFIG").fetchall()
        except Exception as exc:
            self.skipTest(f"PgBouncer admin interface is unavailable: {exc}")

        pool_mode = next((value for key, value, *_ in rows if key == "pool_mode"), None)
        self.assertEqual(pool_mode, "transaction", "PgBouncer must use transaction pool mode")


if __name__ == "__main__":
    unittest.main()
