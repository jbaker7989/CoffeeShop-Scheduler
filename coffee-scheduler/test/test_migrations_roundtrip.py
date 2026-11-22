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
        completed = self._run_alembic(*args)
        self.assertEqual(
            completed.returncode, 0,
            f"alembic {' '.join(args)} failed:\n{completed.stdout}{completed.stderr}",
        )

    def _run_alembic(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["alembic", *args], cwd=DB_DIR, env=os.environ.copy(), text=True,
            capture_output=True,
        )

    def current_revision(self) -> str | None:
        import psycopg

        url = os.environ["DATABASE_URL"]
        with psycopg.connect(url) as conn:
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        return None if row is None else row[0]

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

    def coverage_rules_validity_type(self) -> str | None:
        import psycopg

        url = os.environ["DATABASE_URL"]
        with psycopg.connect(url) as conn:
            row = conn.execute(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'coverage_rules' "
                "AND column_name = 'validity'"
            ).fetchone()
        return None if row is None else row[0]

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

    # spec: F34.ac3 — Coverage rules retain an effective daterange through a migration round-trip.
    def test_coverage_rules_validity_round_trips(self) -> None:
        self.alembic("upgrade", "head")
        self.assertEqual("daterange", self.coverage_rules_validity_type())

        # `validity` is added by 0002, so the round-trip must downgrade past it. `-1` only
        # reaches 0015 at head 0016, which leaves the column in place.
        self.alembic("downgrade", "0001")
        self.assertIsNone(self.coverage_rules_validity_type())

        self.alembic("upgrade", "head")
        self.assertEqual("daterange", self.coverage_rules_validity_type())


    # spec: F8.ac3 / BUG-065 — migration 0021 preflights legacy non-finite rows and
    # fails atomically (Alembic stays at 0020, the row is untouched) rather than adding
    # the constraint or silently rewriting forecast values.
    def test_migration_0021_preflight_rejects_non_finite_legacy_rows(self) -> None:
        import psycopg

        url = os.environ["DATABASE_URL"]
        fixture_tenant = "00000000-0000-0000-0000-0000000000aa"

        # Land exactly on 0020 (the revision before the finite constraint) from anywhere.
        self.alembic("upgrade", "head")
        self.alembic("downgrade", "0020")
        self.assertEqual("0020", self.current_revision())

        # Insert an all-NaN forecast that satisfies the 0017 ordering CHECK. session
        # replication role = replica skips FK triggers so the row needs no seed data; the
        # CHECK constraints still apply, so this proves NaN slips past 0017 as the bug.
        with psycopg.connect(url) as conn, conn.transaction():
            conn.execute("SET session_replication_role = replica")
            conn.execute(
                "INSERT INTO forecasts (tenant_id, location_id, product_category, "
                "forecast_hour, p70, lower, upper, model_version, taxonomy_version) VALUES "
                "(%s, 1, 'Coffee', now(), 'NaN'::numeric, 'NaN'::numeric, 'NaN'::numeric, "
                "'preflight-fixture', 'preflight-fixture')",
                (fixture_tenant,),
            )

        # Upgrading to 0021 must fail with the actionable preflight message.
        completed = self._run_alembic("upgrade", "head")
        self.assertNotEqual(completed.returncode, 0, "0021 upgrade should fail on a non-finite legacy row")
        self.assertIn("BUG-065 preflight", completed.stdout + completed.stderr)

        # The migration transaction rolled back atomically: still at 0020, no finite
        # constraint, and the offending row is unchanged (not rewritten).
        self.assertEqual("0020", self.current_revision())
        with psycopg.connect(url) as conn:
            self.assertEqual(
                0,
                conn.execute(
                    "SELECT count(*) FROM pg_constraint WHERE conname = 'forecasts_interval_finite'"
                ).fetchone()[0],
            )
            self.assertEqual(
                1,
                conn.execute(
                    "SELECT count(*) FROM forecasts "
                    "WHERE tenant_id = %s AND lower = 'NaN'::numeric "
                    "AND p70 = 'NaN'::numeric AND upper = 'NaN'::numeric",
                    (fixture_tenant,),
                ).fetchone()[0],
            )

        # Remove the fixture, then the upgrade succeeds and the constraint lands.
        with psycopg.connect(url) as conn, conn.transaction():
            conn.execute("SET session_replication_role = replica")
            conn.execute("DELETE FROM forecasts WHERE tenant_id = %s", (fixture_tenant,))
        self.alembic("upgrade", "head")
        self.assertEqual("0021", self.current_revision())
        with psycopg.connect(url) as conn:
            self.assertEqual(
                1,
                conn.execute(
                    "SELECT count(*) FROM pg_constraint WHERE conname = 'forecasts_interval_finite'"
                ).fetchone()[0],
            )


if __name__ == "__main__":
    unittest.main()
