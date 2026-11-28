"""BUG-024 integration tests. DATABASE_URL must permit creating a scratch database."""

import os
import sys
import unittest
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.errors import CheckViolation, NotNullViolation

SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
if str(SCHEDULER_ROOT) not in sys.path:
    sys.path.insert(0, str(SCHEDULER_ROOT))

PARENT = ("public", "legacy-events")
CHILD = ("public", "legacy-events-early")
DEFAULT = ("public", "legacy-events-default")
CUTOVER_TENANT = UUID("00000000-0000-0000-0000-000000000001")


@unittest.skipUnless(os.environ.get("DATABASE_URL"), "DATABASE_URL required for PostgreSQL integration")
class PartitionMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = os.environ["DATABASE_URL"]
        database = f"bug024_{uuid4().hex}"
        admin = cls.enterClassContext(psycopg.connect(url, autocommit=True))
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        cls.addClassCleanup(admin.execute, sql.SQL("DROP DATABASE {}").format(sql.Identifier(database)))
        cls.url = make_conninfo(url, dbname=database)

    def setUp(self):
        self.conn = self.enterContext(psycopg.connect(self.url, autocommit=True))
        self.conn.execute('CREATE TABLE "legacy-events" (event_id integer) PARTITION BY RANGE (event_id)')
        self.conn.execute(
            'CREATE TABLE "legacy-events-early" PARTITION OF "legacy-events" FOR VALUES FROM (0) TO (100)'
        )
        self.conn.execute('CREATE TABLE "legacy-events-default" PARTITION OF "legacy-events" DEFAULT')
        self.addCleanup(self.conn.execute, 'DROP TABLE "legacy-events"')

    # spec: F60.ac1 / BUG-024 — The partitioned tenant index is valid and attached to every leaf.
    def test_concurrent_leaf_indexes_attach_to_parent(self):
        from db.partition_migrations import (
            attach_partition_index,
            create_leaf_index_concurrently,
            create_parent_index,
        )

        self.conn.execute('ALTER TABLE "legacy-events" ADD COLUMN tenant_id uuid')
        columns = ("tenant_id", "event_id")
        create_parent_index(self.conn, PARENT, "events_tenant_idx", columns)
        for table, index in ((CHILD, "early_tenant_idx"), (DEFAULT, "default_tenant_idx")):
            create_leaf_index_concurrently(self.conn, table, index, columns)
            attach_partition_index(self.conn, ("public", "events_tenant_idx"), ("public", index))

        self.assertEqual((True,), self.conn.execute("""
            SELECT indisvalid FROM pg_index WHERE indexrelid = 'public.events_tenant_idx'::regclass
        """).fetchone())
        self.assertEqual(
            {("early_tenant_idx", CHILD[1], True), ("default_tenant_idx", DEFAULT[1], True)},
            set(self.conn.execute("""
                SELECT i.relname, t.relname, x.indisvalid
                FROM pg_inherits h
                JOIN pg_class i ON i.oid = h.inhrelid
                JOIN pg_index x ON x.indexrelid = i.oid
                JOIN pg_class t ON t.oid = x.indrelid
                WHERE h.inhparent = 'public.events_tenant_idx'::regclass
            """).fetchall()),
        )

    # spec: F60.ac1 / BUG-024 — New writes cannot leave a NULL during tenant backfill.
    def test_backfill_window_stamps_writes_and_rejects_explicit_null(self):
        from db.partition_migrations import begin_tenant_backfill, finish_tenant_backfill, validate_tenant_backfill

        self.conn.execute('INSERT INTO "legacy-events" VALUES (1), (101)')
        begin_tenant_backfill(self.conn, PARENT, CUTOVER_TENANT)
        writer = self.enterContext(psycopg.connect(self.url, autocommit=True))
        update = 'UPDATE "legacy-events" SET tenant_id = %s WHERE event_id = %s AND tenant_id IS NULL'
        self.assertEqual(1, self.conn.execute(update, (CUTOVER_TENANT, 1)).rowcount)
        self.assertEqual((None,), writer.execute(
            'SELECT tenant_id FROM "legacy-events" WHERE event_id = 101'
        ).fetchone())
        with self.assertRaises(CheckViolation):
            validate_tenant_backfill(self.conn, PARENT)

        for table, event_id in ((PARENT, 2), (CHILD, 3), (DEFAULT, 102)):
            with self.subTest(table=table):
                relation = sql.Identifier(*table)
                assigned = writer.execute(
                    sql.SQL("INSERT INTO {} (event_id) VALUES (%s) RETURNING tenant_id").format(relation),
                    (event_id,),
                ).fetchone()
                self.assertEqual((CUTOVER_TENANT,), assigned)
                with self.assertRaises(CheckViolation):
                    writer.execute(
                        sql.SQL("INSERT INTO {} (event_id, tenant_id) VALUES (%s, NULL)").format(relation),
                        (event_id + 10,),
                    )

        self.assertEqual(1, self.conn.execute(update, (CUTOVER_TENANT, 101)).rowcount)
        validate_tenant_backfill(self.conn, PARENT)
        finish_tenant_backfill(self.conn, PARENT)
        self.assertEqual((5, 5), writer.execute(
            'SELECT count(*), count(tenant_id) FROM "legacy-events"'
        ).fetchone())
        for table, event_id in ((PARENT, 4), (CHILD, 5), (DEFAULT, 103)):
            with self.subTest(table=table), self.assertRaises(NotNullViolation):
                writer.execute(
                    sql.SQL("INSERT INTO {} (event_id) VALUES (%s)").format(sql.Identifier(*table)), (event_id,),
                )


if __name__ == "__main__":
    unittest.main()
