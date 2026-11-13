"""PostgreSQL migration reversibility integration test for F43."""
import os
from pathlib import Path
import subprocess
import unittest


# spec: F43.ac3 — Migrations round-trip without leaving domain schema objects.
EXPECTED_TABLES = {
    "tenants", "weather_codes", "taxonomy_meta", "stores", "store_hours",
    "school_calendars", "school_calendar_days", "holidays", "employer_config",
    "stations", "fixed_tasks", "coverage_rules", "shift_rules", "cost_targets",
    "throughput_standards", "demand_adjustments", "product_taxonomy",
    "calibration_log", "employees", "employee_pii", "labor_rules",
    "consent_waivers", "events", "schedules", "shifts", "shift_assignments",
    "forecasts", "rule_eval_log", "rule_eval_log_default", "transactions",
    "transactions_default", "weather_hourly", "weather_hourly_default", "weather_daily",
}
EXPECTED_ROLES = {"app_rw", "app_ro", "secrets_rw"}
DB_DIR = Path(__file__).resolve().parents[1] / "db"


@unittest.skipUnless(os.environ.get("DATABASE_URL"), "DATABASE_URL required for PostgreSQL integration")
class MigrationRoundTripTests(unittest.TestCase):
    def alembic(self, *args: str) -> None:
        completed = subprocess.run(
            ["alembic", *args], cwd=DB_DIR, env=os.environ.copy(), text=True,
            capture_output=True,
        )
        self.assertEqual(
            completed.returncode, 0,
            f"alembic {' '.join(args)} failed:\n{completed.stdout}{completed.stderr}",
        )

    def _query(self, sql: str) -> set[str]:
        # Introspect via psycopg (a project dependency) rather than the psql CLI, which is
        # not guaranteed on the host — every other integration test uses psycopg likewise.
        import psycopg

        url = os.environ["DATABASE_URL"]
        with psycopg.connect(url) as conn:
            return {row[0] for row in conn.execute(sql).fetchall()}

    def public_tables(self) -> set[str]:
        return self._query(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
        )

    def roles(self) -> set[str]:
        return self._query(
            "SELECT rolname FROM pg_roles WHERE rolname IN ('app_rw', 'app_ro', 'secrets_rw')"
        )

    def test_upgrade_downgrade_upgrade_leaves_only_alembic_metadata_at_base(self) -> None:
        self.alembic("upgrade", "head")
        self.assertTrue(EXPECTED_TABLES <= self.public_tables())
        self.assertEqual(EXPECTED_ROLES, self.roles())

        self.alembic("downgrade", "base")
        self.assertFalse(EXPECTED_TABLES & self.public_tables())
        self.assertFalse(EXPECTED_ROLES & self.roles())

        self.alembic("upgrade", "head")
        self.assertTrue(EXPECTED_TABLES <= self.public_tables())
        self.assertEqual(EXPECTED_ROLES, self.roles())


if __name__ == "__main__":
    unittest.main()
