"""BUG-022 — RLS fails CLOSED on empty AND malformed tenant context (migration 0006).

The `app_current_tenant()` function returns NULL (-> 0 rows) for an unset, empty, OR non-UUID
`app.tenant_id`, so a middleware bug or hostile token can never turn a query into a raw cast 500.
`data_access.session.open_session_context` additionally rejects a non-UUID at the edge (typed error).

Run: DATABASE_URL=postgresql://coffeeshop:coffeeshop@localhost:5432/coffeeshop \
     python3 coffee-scheduler/test/test_bug022_failclosed.py
DB-touching tests skip unless DATABASE_URL is set; every mutation rolls back.

Authored by Claude (orchestration: Codex dev / Claude tests).
"""
import os
import sys
import unittest
from pathlib import Path

SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
if str(SCHEDULER_ROOT) not in sys.path:
    sys.path.insert(0, str(SCHEDULER_ROOT))

DSN = os.environ.get("DATABASE_URL")
TENANT = "00000000-0000-0000-0000-000000000001"  # maven-demo seed

try:
    import psycopg
    from psycopg import Rollback
    from data_access.session import open_session_context, SessionContextError
except Exception:  # pragma: no cover - import guard mirrors test_data_access
    psycopg = None


@unittest.skipUnless(DSN and psycopg, "DATABASE_URL and psycopg are required")
class Bug022FailClosed(unittest.TestCase):
    """Set the GUC DIRECTLY (bypassing session.py validation) to prove the DATABASE itself
    fails closed — the guarantee must not depend on a well-behaved caller."""

    def _as_app_rw(self, conn, raw_tenant):
        # SET LOCAL ROLE app_rw so RLS applies (no BYPASSRLS), then plant the raw GUC value.
        conn.execute("SET LOCAL ROLE app_rw")
        conn.execute("SELECT set_config('app.tenant_id', %s, true)", (raw_tenant,))

    # spec: F60.ac2 / BUG-022 — a non-UUID context yields zero rows, never a cast error.
    def test_malformed_context_yields_zero_rows_not_error(self):
        with psycopg.connect(DSN) as conn:
            with conn.transaction():
                self._as_app_rw(conn, "not-a-uuid")
                n = conn.execute("SELECT count(*) FROM stores").fetchone()[0]
                self.assertEqual(n, 0, "malformed app.tenant_id must fail closed (0 rows), not raise")
                raise Rollback

    # spec: F60.ac4 / BUG-022 — empty and unset context both yield zero rows.
    def test_empty_and_unset_context_yield_zero_rows(self):
        with psycopg.connect(DSN) as conn:
            with conn.transaction():
                self._as_app_rw(conn, "")
                self.assertEqual(conn.execute("SELECT count(*) FROM stores").fetchone()[0], 0)
                # reset/unset: app_current_tenant() reads the GUC as '' -> NULL -> 0 rows
                conn.execute("SELECT set_config('app.tenant_id', '', true)")
                self.assertEqual(conn.execute("SELECT count(*) FROM stores").fetchone()[0], 0)
                raise Rollback

    # spec: F60.ac2 — a valid tenant context still sees its OWN rows (fix did not over-rotate).
    def test_valid_context_sees_own_rows(self):
        with psycopg.connect(DSN) as conn:
            with conn.transaction():
                self._as_app_rw(conn, TENANT)
                n = conn.execute("SELECT count(*) FROM stores").fetchone()[0]
                self.assertGreater(n, 0, "maven-demo must still see its own stores under the new policy")
                raise Rollback

    # spec: F60.ac2 / BUG-022 — the fail-closed helper exists and is STABLE (not foldable).
    def test_app_current_tenant_function_is_stable(self):
        with psycopg.connect(DSN) as conn:
            with conn.transaction():
                row = conn.execute(
                    "SELECT provolatile FROM pg_proc WHERE proname = 'app_current_tenant'"
                ).fetchone()
                self.assertIsNotNone(row, "app_current_tenant() must exist")
                self.assertEqual(row[0], "s", "app_current_tenant() must be STABLE (provolatile='s')")
                raise Rollback


@unittest.skipUnless(DSN and psycopg, "DATABASE_URL and psycopg are required")
class Bug022EdgeGuard(unittest.TestCase):
    """The edge layer (session.py) rejects a non-UUID with a typed error before any statement."""

    # spec: F60.ac4 / BUG-022 — open_session_context raises SessionContextError on a non-UUID.
    def test_session_context_rejects_non_uuid(self):
        with psycopg.connect(DSN) as conn:
            with conn.transaction():
                with self.assertRaises(SessionContextError):
                    open_session_context(conn, role="app_rw", tenant_id="not-a-uuid")
                raise Rollback

    # spec: F60.ac4 / BUG-022 — None is rejected (never stamps the literal 'None').
    def test_session_context_rejects_none(self):
        with psycopg.connect(DSN) as conn:
            with conn.transaction():
                with self.assertRaises(SessionContextError):
                    open_session_context(conn, role="app_rw", tenant_id=None)
                raise Rollback


if __name__ == "__main__":
    unittest.main()
