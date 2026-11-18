"""CS-087 live lifecycle proof for the representative Tier-A ``stores`` table."""
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
    from data_access import open_session_context, restore, soft_delete
except ImportError:  # pragma: no cover - environment-dependent skip
    psycopg = None


@unittest.skipUnless(DSN and psycopg, "DATABASE_URL and psycopg are required")
class SoftDeleteLifecycleTests(unittest.TestCase):
    # spec: F69.ac2 — ordinary deletion is a marker update, invisible by default and reversible.
    def test_store_soft_delete_visibility_restore_and_idempotence(self):
        with psycopg.connect(DSN) as conn:
            with conn.transaction():
                open_session_context(conn, role="app_rw", tenant_id=TENANT)
                location_id = conn.execute(
                    "SELECT location_id FROM stores ORDER BY location_id LIMIT 1"
                ).fetchone()[0]
                self.assertEqual(
                    1,
                    soft_delete(
                        conn, "stores", tenant_id=TENANT,
                        where=("location_id = %s", (location_id,)),
                        actor_id="00000000-0000-0000-0000-000000000099",
                    ),
                    "soft_delete returns UPDATE rowcount rather than a hidden RETURNING row",
                )
                self.assertEqual(
                    0,
                    conn.execute("SELECT count(*) FROM stores WHERE location_id = %s", (location_id,)).fetchone()[0],
                )
                # UPDATE reads are also subject to the restrictive SELECT policy.
                self.assertEqual(
                    0,
                    conn.execute(
                        "UPDATE stores SET deleted_at = NULL WHERE location_id = %s AND deleted_at IS NOT NULL",
                        (location_id,),
                    ).rowcount,
                )
                conn.execute("SET LOCAL app.show_deleted = 'on'")
                row = conn.execute(
                    "SELECT deleted_at, deleted_by FROM stores WHERE location_id = %s", (location_id,)
                ).fetchone()
                self.assertIsNotNone(row[0])
                self.assertEqual("00000000-0000-0000-0000-000000000099", str(row[1]))
                conn.execute("SET LOCAL app.show_deleted = DEFAULT")
                self.assertEqual(
                    1,
                    restore(conn, "stores", tenant_id=TENANT, where=("location_id = %s", (location_id,))),
                )
                self.assertEqual(
                    1,
                    conn.execute("SELECT count(*) FROM stores WHERE location_id = %s", (location_id,)).fetchone()[0],
                )
                self.assertEqual(
                    1,
                    soft_delete(
                        conn, "stores", tenant_id=TENANT,
                        where=("location_id = %s", (location_id,)),
                        actor_id="00000000-0000-0000-0000-000000000098",
                    ),
                )
                self.assertEqual(
                    0,
                    soft_delete(
                        conn, "stores", tenant_id=TENANT,
                        where=("location_id = %s", (location_id,)),
                        actor_id="00000000-0000-0000-0000-000000000097",
                    ),
                    "second delete must preserve the original marker",
                )
                conn.execute("RESET ROLE")
                # The migration/ETL connection is a BYPASSRLS role; it must retain
                # physical visibility even though app_rw no longer sees the row.
                self.assertTrue(conn.execute(
                    "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user"
                ).fetchone()[0])
                physical = conn.execute(
                    "SELECT deleted_by FROM stores WHERE tenant_id = %s AND location_id = %s",
                    (TENANT, location_id),
                ).fetchone()
                self.assertEqual("00000000-0000-0000-0000-000000000098", str(physical[0]))
                raise Rollback


if __name__ == "__main__":
    unittest.main()
