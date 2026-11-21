"""BUG-025 regression: the session role and tenant GUC must be transaction-local,
so neither leaks onto a pooled backend for the next client (PgBouncer transaction
pooling). Complements test_data_access.py F45.ac7 (autocommit refusal).

Run: DATABASE_URL=... python3 coffee-scheduler/test/test_bug025_pooling_session.py
Optionally set DATABASE_URL_POOLED (e.g. ...@localhost:6432/...) to also assert the
property end-to-end through PgBouncer.
"""
import os
import sys
import unittest
from pathlib import Path


SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
if str(SCHEDULER_ROOT) not in sys.path:
    sys.path.insert(0, str(SCHEDULER_ROOT))

DSN = os.environ.get("DATABASE_URL")
POOLED = os.environ.get("DATABASE_URL_POOLED")
TENANT = "00000000-0000-0000-0000-000000000001"

try:
    import psycopg
    from data_access.session import open_session_context
except ImportError:  # pragma: no cover
    psycopg = None


@unittest.skipUnless(DSN and psycopg, "DATABASE_URL and psycopg are required")
class Bug025TransactionLocalSession(unittest.TestCase):
    def _current_user(self, conn) -> str:
        return conn.execute("SELECT current_user").fetchone()[0]

    def _tenant_guc(self, conn):
        return conn.execute("SELECT current_setting('app.tenant_id', true)").fetchone()[0]

    # spec: BUG-025 — SET LOCAL ROLE does not persist past the transaction (no pool leak).
    def test_role_does_not_leak_past_the_transaction(self):
        with psycopg.connect(DSN) as conn:
            with conn.transaction():
                open_session_context(conn, role="app_rw", tenant_id=TENANT)
                self.assertEqual("app_rw", self._current_user(conn))
            # The transaction committed; a later transaction on the SAME backend
            # (as PgBouncer would hand to the next client) must NOT still be app_rw.
            with conn.transaction():
                self.assertNotEqual(
                    "app_rw", self._current_user(conn),
                    "role leaked past the transaction — plain SET ROLE, not SET LOCAL ROLE",
                )

    # spec: BUG-025 — the tenant GUC is transaction-local and does not leak.
    def test_tenant_guc_does_not_leak_past_the_transaction(self):
        with psycopg.connect(DSN) as conn:
            with conn.transaction():
                open_session_context(conn, role="app_rw", tenant_id=TENANT)
                self.assertEqual(TENANT, self._tenant_guc(conn))
            with conn.transaction():
                leaked = self._tenant_guc(conn)
                self.assertIn(
                    leaked, (None, ""),
                    f"tenant GUC leaked past the transaction: {leaked!r}",
                )

    # spec: BUG-025 — end-to-end through PgBouncer: a fresh pooled client that sets no
    # context is fail-closed (no inherited role/tenant visibility).
    @unittest.skipUnless(POOLED, "DATABASE_URL_POOLED (PgBouncer) not configured")
    def test_no_leak_across_pooled_clients(self):
        # Client A: scoped read through the pooler.
        with psycopg.connect(POOLED) as conn, conn.transaction():
            open_session_context(conn, role="app_rw", tenant_id=TENANT)
            scoped = conn.execute("SELECT count(*) FROM employees").fetchone()[0]
        self.assertGreater(scoped, 0)
        # Client B: a different pooled connection that sets NO context must see no
        # rows under app_rw and must not inherit A's role as its effective identity.
        for _ in range(5):
            with psycopg.connect(POOLED) as conn, conn.transaction():
                conn.execute("SET LOCAL ROLE app_rw")  # explicit, this txn only
                self.assertEqual(
                    0, conn.execute("SELECT count(*) FROM employees").fetchone()[0],
                    "pooled backend leaked a tenant GUC from a prior client",
                )


if __name__ == "__main__":
    unittest.main()
