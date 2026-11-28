"""BUG-046 regression tests for scoped soft-delete visibility."""
import os
import sys
import unittest
from pathlib import Path

SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
if str(SCHEDULER_ROOT) not in sys.path:
    sys.path.insert(0, str(SCHEDULER_ROOT))

DSN = os.environ.get("DATABASE_URL")
TENANT = "00000000-0000-0000-0000-000000000001"

try:
    import psycopg
    from psycopg import Rollback
    from psycopg.errors import UndefinedColumn

    from data_access import list_including_deleted, open_session_context, restore, soft_delete
except ImportError:  # pragma: no cover - environment-dependent skip
    psycopg = None


@unittest.skipUnless(DSN and psycopg, "DATABASE_URL and psycopg are required")
class SoftDeleteVisibilityTests(unittest.TestCase):
    def _delete_stores(self, conn, count):
        location_ids = [
            row[0] for row in conn.execute(
                "SELECT location_id FROM stores ORDER BY location_id LIMIT %s", (count,)
            )
        ]
        self.assertEqual(count, len(location_ids))
        self.assertEqual(
            count,
            soft_delete(
                conn, "stores", tenant_id=TENANT,
                where=("location_id = ANY(%s)", (location_ids,)),
                actor_id="00000000-0000-0000-0000-000000000099",
            ),
        )
        return location_ids

    # spec: F69.ac2 — listing deleted rows preserves default visibility for later reads.
    def test_list_including_deleted_does_not_leak_visibility(self):
        with psycopg.connect(DSN) as conn, conn.transaction():
            open_session_context(conn, role="app_rw", tenant_id=TENANT)
            location_ids = self._delete_stores(conn, 1)
            rows = list_including_deleted(
                conn, "stores", tenant_id=TENANT,
                where=("location_id = ANY(%s)", (location_ids,)),
            )
            self.assertEqual(1, len(rows))
            self.assertEqual([], conn.execute(
                "SELECT location_id FROM stores WHERE location_id = ANY(%s)", (location_ids,)
            ).fetchall())
            raise Rollback

    # spec: F69.ac2 — restoring one row leaves other deleted rows hidden.
    def test_restore_does_not_leak_visibility(self):
        with psycopg.connect(DSN) as conn, conn.transaction():
            open_session_context(conn, role="app_rw", tenant_id=TENANT)
            conn.execute("SET LOCAL app.show_deleted = 'off'")
            location_ids = self._delete_stores(conn, 2)
            self.assertEqual(1, restore(
                conn, "stores", tenant_id=TENANT,
                where=("location_id = %s", (location_ids[0],)),
            ))
            self.assertEqual([(location_ids[0],)], conn.execute(
                "SELECT location_id FROM stores WHERE location_id = ANY(%s) ORDER BY location_id",
                (location_ids,),
            ).fetchall())
            self.assertEqual("off", conn.execute("SHOW app.show_deleted").fetchone()[0])
            raise Rollback

    # spec: F69.ac2 — nested administrative reads preserve an enabled caller setting.
    def test_admin_operations_preserve_enabled_visibility(self):
        with psycopg.connect(DSN) as conn, conn.transaction():
            open_session_context(conn, role="app_rw", tenant_id=TENANT)
            location_ids = self._delete_stores(conn, 1)
            where = ("location_id = ANY(%s)", (location_ids,))
            conn.execute("SET LOCAL app.show_deleted = 'on'")
            self.assertEqual(1, len(list_including_deleted(conn, "stores", tenant_id=TENANT, where=where)))
            self.assertEqual("on", conn.execute("SHOW app.show_deleted").fetchone()[0])
            self.assertEqual(1, restore(conn, "stores", tenant_id=TENANT, where=where))
            self.assertEqual("on", conn.execute("SHOW app.show_deleted").fetchone()[0])
            raise Rollback

    # spec: F69.ac2 — failed operations leave later ordinary reads usable and scoped.
    def test_admin_operations_restore_visibility_after_sql_error(self):
        with psycopg.connect(DSN) as conn, conn.transaction():
            open_session_context(conn, role="app_rw", tenant_id=TENANT)
            location_ids = self._delete_stores(conn, 1)
            for operation in (list_including_deleted, restore):
                with self.subTest(operation=operation.__name__):
                    with self.assertRaises(UndefinedColumn):
                        operation(conn, "stores", tenant_id=TENANT, where="nonexistent_column")
                    self.assertEqual([], conn.execute(
                        "SELECT location_id FROM stores WHERE location_id = ANY(%s)", (location_ids,)
                    ).fetchall())
            raise Rollback


if __name__ == "__main__":
    unittest.main()
