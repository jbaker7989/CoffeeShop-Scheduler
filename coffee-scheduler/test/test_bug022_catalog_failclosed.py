"""BUG-022 (drift-proof) — EVERY table carrying a `tenant_isolation` RLS policy must fail
CLOSED on a malformed/empty `app.tenant_id`: zero rows, never a `uuid` cast 500.

test_bug022_failclosed.py checks named tables (stores, langgraph_checkpoints). BUG-026 showed
a hand-maintained table list drifts (0006 missed langgraph_checkpoints); this test is
catalog-driven, so a future tenant-scoped table that reverts to the throwing inline form is
caught automatically.

Run: DATABASE_URL=postgresql://coffeeshop:coffeeshop@localhost:5432/coffeeshop \
     python3 coffee-scheduler/test/test_bug022_catalog_failclosed.py
"""
import os
import sys
import unittest
from pathlib import Path

SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
if str(SCHEDULER_ROOT) not in sys.path:
    sys.path.insert(0, str(SCHEDULER_ROOT))

DSN = os.environ.get("DATABASE_URL")

try:
    import psycopg
    from psycopg import sql
    from psycopg.errors import InsufficientPrivilege, InvalidTextRepresentation
except Exception:  # pragma: no cover
    psycopg = None


@unittest.skipUnless(DSN and psycopg, "DATABASE_URL and psycopg are required")
class Bug022CatalogFailClosed(unittest.TestCase):
    def _tenant_isolation_tables(self) -> list[str]:
        with psycopg.connect(DSN) as conn:
            rows = conn.execute(
                "SELECT DISTINCT tablename FROM pg_policies "
                "WHERE schemaname = 'public' AND policyname = 'tenant_isolation' "
                "ORDER BY tablename"
            ).fetchall()
        return [r[0] for r in rows]

    def _count_under_bad_context(self, table: str, raw_tenant: str):
        """Return row count under app_rw + a hostile GUC, or an error tag. Fresh connection per
        table so one table's aborted transaction cannot poison the next."""
        with psycopg.connect(DSN) as conn:
            try:
                with conn.transaction():
                    conn.execute("SET LOCAL ROLE app_rw")
                    conn.execute("SELECT set_config('app.tenant_id', %s, true)", (raw_tenant,))
                    n = conn.execute(
                        sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
                    ).fetchone()[0]
                return ("rows", n)
            except InvalidTextRepresentation as exc:
                return ("cast_error", str(exc).splitlines()[0])
            except InsufficientPrivilege:
                return ("no_grant", None)  # e.g. employee_pii is secrets_rw only — not a cast bug

    # spec: F60.ac2 / BUG-022 / BUG-026 — no tenant-scoped table raises a cast 500 on a bad context.
    def test_every_tenant_isolation_table_fails_closed(self):
        tables = self._tenant_isolation_tables()
        self.assertGreater(len(tables), 10, "expected many tenant_isolation policies; catalog read looks wrong")

        offenders, tested, skipped = {}, [], []
        for table in tables:
            for raw in ("not-a-uuid", "", "   ", "'; DROP TABLE stores;--"):
                kind, info = self._count_under_bad_context(table, raw)
                if kind == "cast_error":
                    offenders[(table, raw)] = info
                elif kind == "no_grant":
                    skipped.append(table)
                    break
                elif info != 0:
                    offenders[(table, raw)] = f"{info} rows leaked"
            else:
                tested.append(table)

        self.assertEqual(
            offenders, {},
            f"tables NOT fail-closed on a malformed context: {offenders}",
        )
        self.assertGreater(len(tested), 0, "no tenant-scoped table was actually exercised")


if __name__ == "__main__":
    unittest.main()
