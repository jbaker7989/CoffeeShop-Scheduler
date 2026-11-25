"""Exercise revision 0021 against a dedicated PostgreSQL database."""
import os
from pathlib import Path
import subprocess
import time
import unittest
from uuid import uuid4


DB_DIR = Path(__file__).resolve().parents[1] / "db"
CONSTRAINT = "forecasts_interval_finite"


@unittest.skipUnless(os.environ.get("DATABASE_URL_BUG067"), "DATABASE_URL_BUG067 requires a dedicated PostgreSQL database")
class MigrationLockTests(unittest.TestCase):
    def setUp(self):
        self.url = os.environ["DATABASE_URL_BUG067"]
        self.tenant = uuid4()
        self.assert_success(self.alembic("upgrade"))
        self.assert_success(self.alembic("downgrade", "0020"))
        self.addCleanup(self.restore_head)

    def connect(self, **kwargs):
        import psycopg

        return psycopg.connect(self.url, **kwargs)

    def alembic_command(self, action, revision):
        url = self.url.replace("postgresql://", "postgresql+psycopg://", 1)
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
        return {
            "args": ["alembic", "-c", "alembic.ini", action, revision],
            "cwd": DB_DIR,
            "env": {**os.environ, "DATABASE_URL": url},
            "text": True,
        }

    def alembic(self, action, revision="0021"):
        return subprocess.run(
            **self.alembic_command(action, revision), capture_output=True, timeout=30
        )

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)

    def restore_head(self):
        with self.connect() as conn:
            conn.execute("DELETE FROM forecasts WHERE tenant_id = %s", (self.tenant,))
        self.assert_success(self.alembic("upgrade", "head"))

    def insert_forecast(self, conn, value):
        # CHECK constraints still apply with fixture FK triggers disabled.
        conn.execute("SET LOCAL session_replication_role = replica")
        conn.execute(
            "INSERT INTO forecasts (tenant_id, location_id, product_category, "
            "forecast_hour, lower, p70, upper, model_version, taxonomy_version) "
            "VALUES (%s, 1, 'Coffee', now(), %s::numeric, %s::numeric, %s::numeric, "
            "'bug067-fixture', 'bug067-fixture')",
            (self.tenant, value, value, value),
        )

    def constraints(self, conn):
        return dict(conn.execute(
            "SELECT conname, convalidated FROM pg_constraint WHERE conrelid = 'forecasts'::regclass"
        ).fetchall())

    def assert_non_finite_rejected(self, conn):
        from psycopg.errors import CheckViolation

        with self.assertRaises(CheckViolation) as check, conn.transaction():
            self.insert_forecast(conn, "NaN")
        self.assertEqual(check.exception.diag.constraint_name, CONSTRAINT)

    def test_enforcement_installed_before_validation(self):
        self.assert_success(self.alembic("upgrade"))
        with self.connect() as conn:
            self.assertIs(self.constraints(conn)[CONSTRAINT], True)
            self.assert_non_finite_rejected(conn)

    def assert_reader_times_out_upgrade(self):
        with self.connect() as reader:
            reader.execute("SELECT 1 FROM forecasts LIMIT 1")
            start = time.monotonic()
            result = subprocess.run(
                **self.alembic_command("upgrade", "0021"), capture_output=True, timeout=8
            )
            self.assertLess(time.monotonic() - start, 8)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("lock timeout", result.stderr)

    def test_upgrade_fails_fast_behind_long_reader(self):
        self.assert_reader_times_out_upgrade()
        with self.connect() as conn:
            self.assertNotIn(CONSTRAINT, self.constraints(conn))

    def test_retry_succeeds_after_reader_releases(self):
        self.assert_reader_times_out_upgrade()
        self.assert_success(self.alembic("upgrade"))
        with self.connect() as conn:
            self.assertIs(self.constraints(conn)[CONSTRAINT], True)

    def test_validation_allows_concurrent_reads(self):
        # Pause after the validation scan while its table lock is still held.
        # The advisory lock gives the test a stable observation point even on a tiny table.
        with self.connect() as conn:
            self.insert_forecast(conn, "1")
            conn.execute("""
                CREATE FUNCTION bug067_pause_validation() RETURNS event_trigger
                LANGUAGE plpgsql AS $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conrelid = 'forecasts'::regclass
                          AND conname = 'forecasts_interval_finite' AND convalidated
                    ) THEN
                        PERFORM pg_advisory_xact_lock(67021);
                    END IF;
                END $$;
            """)
            conn.execute("""
                CREATE EVENT TRIGGER bug067_pause_validation ON ddl_command_end
                WHEN TAG IN ('ALTER TABLE') EXECUTE FUNCTION bug067_pause_validation()
            """)

        try:
            with self.connect(autocommit=True) as reader:
                reader.execute("SELECT pg_advisory_lock(67021)")
                with subprocess.Popen(
                    **self.alembic_command("upgrade", "0021"),
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                ) as migration:
                    try:
                        deadline = time.monotonic() + 10
                        while not reader.execute("""
                            SELECT EXISTS (
                                SELECT 1 FROM pg_locks
                                WHERE locktype = 'advisory' AND objid = 67021 AND NOT granted
                                  AND %s = ANY(pg_blocking_pids(pid))
                            )
                        """, (reader.info.backend_pid,)).fetchone()[0]:
                            self.assertIsNone(migration.poll(), "Migration exited before validation paused")
                            self.assertLess(time.monotonic(), deadline, "Validation never paused")
                            time.sleep(0.05)
                        reader.execute("SET statement_timeout = '1s'")
                        self.assertIs(self.constraints(reader)[CONSTRAINT], False)
                        self.assertEqual(reader.execute(
                            "SELECT lower FROM forecasts WHERE tenant_id = %s", (self.tenant,)
                        ).fetchone()[0], 1)
                        self.assert_non_finite_rejected(reader)
                        reader.execute("SELECT pg_advisory_unlock(67021)")
                        _, stderr = migration.communicate(timeout=20)
                        self.assertEqual(migration.returncode, 0, stderr)
                    finally:
                        if migration.poll() is None:
                            migration.kill()
        finally:
            with self.connect() as conn:
                conn.execute("DROP EVENT TRIGGER bug067_pause_validation")
                conn.execute("DROP FUNCTION bug067_pause_validation()")

    def test_preflight_rejects_legacy_non_finite_row(self):
        with self.connect() as conn:
            self.insert_forecast(conn, "NaN")
        result = self.alembic("upgrade")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("BUG-065 preflight", result.stderr)
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT version_num FROM alembic_version").fetchone(), ("0020",))
            self.assertNotIn(CONSTRAINT, self.constraints(conn))
            row = conn.execute(
                "SELECT lower::text, p70::text, upper::text FROM forecasts WHERE tenant_id = %s",
                (self.tenant,),
            ).fetchone()
            self.assertEqual(row, ("NaN", "NaN", "NaN"))

    def test_downgrade_removes_only_the_constraint(self):
        self.assert_success(self.alembic("upgrade"))
        with self.connect() as conn:
            before = self.constraints(conn)
        self.assertIs(before.pop(CONSTRAINT), True)
        self.assert_success(self.alembic("downgrade", "0020"))
        with self.connect() as conn:
            self.assertEqual(self.constraints(conn), before)
            self.insert_forecast(conn, "NaN")
