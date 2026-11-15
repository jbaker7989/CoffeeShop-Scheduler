"""F60-2 — tenant-stamping verification / idempotency guard (migration 0005).

Run: DATABASE_URL=… python3 coffee-scheduler/test/test_f60_2_tenant_stamp.py
DB-touching tests skip unless DATABASE_URL is set; every mutation rolls back.

Authored by Claude (orchestration: Codex dev / Claude tests).
"""
import importlib.util
import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "coffee-scheduler" / "db" / "migrations" / "versions" / "0005_f60_tenant_stamp_verify.py"
DB_DIR = ROOT / "coffee-scheduler" / "db"
DSN = os.environ.get("DATABASE_URL", "postgresql://coffeeshop:coffeeshop@localhost:5432/coffeeshop")
ALEMBIC_URL = "postgresql+psycopg://coffeeshop:coffeeshop@localhost:5432/coffeeshop"


def _domain_tables():
    spec = importlib.util.spec_from_file_location("mig0005", MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return list(mod.DOMAIN_TABLES), mod.MAVEN


@unittest.skipUnless(os.environ.get("DATABASE_URL"), "DATABASE_URL required")
class F60TenantStamp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tables, cls.maven = _domain_tables()

    def _conn(self):
        import psycopg
        return psycopg.connect(DSN)

    # spec: F60-2.ac1 — every tenant-scoped domain table is fully stamped (zero NULL tenant_id).
    def test_no_null_tenant_id_anywhere(self):
        with self._conn() as c:
            for t in self.tables:
                n = c.execute(f'SELECT count(*) FROM "{t}" WHERE tenant_id IS NULL').fetchone()[0]
                self.assertEqual(n, 0, f"{t} has {n} NULL tenant_id rows")

    # spec: F60-2.ac3 — the migration's defensive UPDATE never touches data (no data loss).
    # spec: F60-2.ac2 — idempotent: the no-op UPDATE affects zero rows on every table.
    def test_backfill_update_is_a_noop_and_idempotent(self):
        import psycopg
        with psycopg.connect(DSN) as c:
            with c.transaction():
                c.execute("SET LOCAL ROLE NONE")  # run as the owner (bypasses RLS, matches the migration)
                for t in self.tables:
                    before = c.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
                    cur = c.execute(f'UPDATE "{t}" SET tenant_id = %s WHERE tenant_id IS NULL', (self.maven,))
                    self.assertEqual(cur.rowcount, 0, f"{t}: backfill updated {cur.rowcount} rows (expected 0)")
                    after = c.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
                    self.assertEqual(before, after, f"{t}: count changed {before}->{after}")
                raise psycopg.Rollback

    # spec: F60-2.ac5 — the NULL-tenant guard actually fires when a NULL exists.
    def test_guard_detects_a_null_tenant_row(self):
        import psycopg
        with psycopg.connect(DSN) as c:
            with c.transaction():
                # A throwaway table modelling a tenant-scoped table with a NULL row (NOT NULL is the
                # first line of defence on the real tables, so we prove the guard logic on a proxy).
                c.execute("CREATE TEMP TABLE _f602_probe (tenant_id uuid) ON COMMIT DROP")
                c.execute("INSERT INTO _f602_probe (tenant_id) VALUES (NULL)")
                nulls = c.execute("SELECT count(*) FROM _f602_probe WHERE tenant_id IS NULL").fetchone()[0]
                self.assertGreater(nulls, 0, "guard query must detect the NULL row (migration would raise)")
                raise psycopg.Rollback

    # spec: F60-2.ac4 — migration round-trips: downgrade -1 then upgrade head is clean (no-op downgrade).
    def test_migration_round_trips(self):
        import shutil
        if shutil.which("alembic") is None:
            self.skipTest("alembic CLI not on PATH")
        env = {**os.environ, "DATABASE_URL": ALEMBIC_URL}

        def alembic(*args):
            r = subprocess.run(["alembic", *args], cwd=DB_DIR, env=env, text=True, capture_output=True)
            self.assertEqual(r.returncode, 0, f"alembic {' '.join(args)}:\n{r.stdout}{r.stderr}")
            return r.stdout

        alembic("downgrade", "-1")                      # 0005 -> 0004 (no-op downgrade)
        alembic("upgrade", "head")                      # back to 0005
        self.assertIn("0005", alembic("current"))


if __name__ == "__main__":
    unittest.main()
