"""CS-087 catalog coverage and hard-delete CI guard tests."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
if str(SCHEDULER_ROOT) not in sys.path:
    sys.path.insert(0, str(SCHEDULER_ROOT))

DSN = os.environ.get("DATABASE_URL")

try:
    import psycopg
    from data_access.soft_delete import TIER_A, TIER_C
except ImportError:  # pragma: no cover - environment-dependent skip
    psycopg = None
    TIER_A = TIER_C = ()


class HardDeleteGuardTests(unittest.TestCase):
    # spec: F69.ac2 — the mechanical guard is clean for source and catches planted destructive SQL.
    def test_guard_clean_tree_and_planted_violation(self):
        guard = SCHEDULER_ROOT / "scripts" / "check_no_hard_delete.py"
        clean = subprocess.run([sys.executable, str(guard)], cwd=SCHEDULER_ROOT, text=True, capture_output=True)
        self.assertEqual(0, clean.returncode, clean.stdout + clean.stderr)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("db/migrations", "data_access", "api", "auth", "compliance", "scripts"):
                (root / name).mkdir(parents=True, exist_ok=True)
            (root / "data_access" / "planted.py").write_text("DELETE FROM stores\n", encoding="utf-8")
            planted = subprocess.run(
                [sys.executable, str(guard), "--root", str(root)], cwd=SCHEDULER_ROOT,
                text=True, capture_output=True,
            )
            self.assertEqual(1, planted.returncode, planted.stdout + planted.stderr)
            self.assertIn("planted.py", planted.stderr)
            (root / "data_access" / "planted.py").write_text("DROP TABLE stores\n", encoding="utf-8")
            dropped = subprocess.run(
                [sys.executable, str(guard), "--root", str(root)], cwd=SCHEDULER_ROOT,
                text=True, capture_output=True,
            )
            self.assertEqual(1, dropped.returncode, dropped.stdout + dropped.stderr)


@unittest.skipUnless(DSN and psycopg, "DATABASE_URL and psycopg are required")
class SoftDeleteCoverageTests(unittest.TestCase):
    # spec: F69.ac2 — every editable relation has the uniform marker/index/restrictive policy; exclusions remain clean.
    def test_all_tier_a_relations_and_no_tier_c_relations_have_lifecycle_objects(self):
        migration = (SCHEDULER_ROOT / "db/migrations/versions/0013_cs087_soft_delete.py").read_text(encoding="utf-8")
        self.assertIn("for table in TIER_A", migration)
        self.assertIn("current_setting('app.show_deleted', true)", migration)
        with psycopg.connect(DSN) as conn:
            for table in TIER_A:
                columns = {row[0] for row in conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name=%s", (table,)
                )}
                self.assertTrue({"deleted_at", "deleted_by"} <= columns, table)
                self.assertIsNotNone(conn.execute(
                    "SELECT 1 FROM pg_indexes WHERE schemaname='public' AND tablename=%s AND indexname=%s",
                    (table, f"idx_{table}_live"),
                ).fetchone(), table)
                self.assertEqual(("RESTRICTIVE", "SELECT"), conn.execute(
                    "SELECT permissive, cmd FROM pg_policies WHERE schemaname='public' "
                    "AND tablename=%s AND policyname='soft_delete_hidden'", (table,),
                ).fetchone(), table)
            for table in TIER_C:
                columns = {row[0] for row in conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name=%s", (table,)
                )}
                self.assertFalse({"deleted_at", "deleted_by"} & columns, table)
                self.assertIsNone(conn.execute(
                    "SELECT 1 FROM pg_indexes WHERE schemaname='public' AND tablename=%s AND indexname=%s",
                    (table, f"idx_{table}_live"),
                ).fetchone(), table)
                self.assertIsNone(conn.execute(
                    "SELECT 1 FROM pg_policies WHERE schemaname='public' "
                    "AND tablename=%s AND policyname='soft_delete_hidden'", (table,),
                ).fetchone(), table)


if __name__ == "__main__":
    unittest.main()
