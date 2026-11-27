"""PostgreSQL migration reversibility integration test for F43."""
import os
import subprocess
import unittest
from pathlib import Path

# spec: F43.ac3 — Migrations round-trip without leaving domain schema objects.
EXPECTED_TABLES = {
    "tenants", "weather_codes", "taxonomy_meta", "stores", "store_hours",
    "school_calendars", "school_calendar_days", "holidays", "employer_config",
    "stations", "fixed_tasks", "coverage_rules", "shift_rules", "cost_targets",
    "throughput_standards", "demand_adjustments", "product_taxonomy",
    "calibration_log", "employees", "employee_pii", "labor_rules",
    "consent_waivers", "events", "schedules", "shifts", "shift_assignments",
    "forecasts", "rule_eval_log", "rule_eval_log_default", "transactions",
    "transactions_default", "weather_hourly", "weather_hourly_default", "weather_daily", "schedule_draft_runs",
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
        self.assertTrue(self.public_tables() >= EXPECTED_TABLES)
        self.assertEqual(EXPECTED_ROLES, self.roles())
        self.alembic("downgrade", "base")
        self.assertFalse(EXPECTED_TABLES & self.public_tables())
        self.assertFalse(EXPECTED_ROLES & self.roles())

        self.alembic("upgrade", "head")
        self.assertTrue(self.public_tables() >= EXPECTED_TABLES)
        self.assertEqual(EXPECTED_ROLES, self.roles())

    # spec: F43.ac3 / F12.ac7 — Downgrade removes draft data without deleting legacy checkpoints.
    def test_schedule_draft_migration_preserves_legacy_checkpoint(self):
        import psycopg

        self.alembic("upgrade", "head")
        tenant = "00000000-0000-0000-0000-000000000001"
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
            ids = [row[0] for row in conn.execute("SELECT gen_random_uuid() FROM generate_series(1, 2)")]
            conn.execute("""
                INSERT INTO schedule_draft_runs (tenant_id, id, issuer, subject, request_hash, lease_until, stage)
                VALUES (%s,%s,'fixture','fixture','fixture',clock_timestamp(),'done')
                """, (tenant, ids[0]))
            for agent, checkpoint in zip(ids, ('{"workflow":"schedule-draft"}', '{"legacy":true}'), strict=True):
                conn.execute("INSERT INTO langgraph_checkpoints (tenant_id, agent_id, checkpoint) VALUES (%s,%s,%s::jsonb)",
                             (tenant, agent, checkpoint))
        try:
            self.alembic("downgrade", "0024")
            self.assertNotIn("schedule_draft_runs", self.public_tables())
            with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
                rows = conn.execute("SELECT agent_id FROM langgraph_checkpoints WHERE agent_id = ANY(%s)", (ids,)).fetchall()
                self.assertEqual(rows, [(ids[1],)])
            self.alembic("upgrade", "head")
            with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM schedule_draft_runs").fetchone()[0], 0)
        finally:
            self.alembic("upgrade", "head")
            with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
                conn.execute("DELETE FROM langgraph_checkpoints WHERE agent_id = ANY(%s)", (ids,))

    # spec: F43.ac3 / F61.ac8 — BUG-043 throttle state/functions reverse cleanly and re-upgrade.
    def test_bug043_throttle_objects_round_trip(self) -> None:
        import psycopg

        self.alembic("upgrade", "head")
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
            self.assertIsNotNone(conn.execute(
                "SELECT to_regclass('public.auth_invitation_accept_throttle')"
            ).fetchone()[0])
            self.assertEqual(
                {"auth_reserve_invitation_accept", "auth_complete_invitation_accept"},
                {row[0] for row in conn.execute(
                    "SELECT proname FROM pg_proc "
                    "WHERE pronamespace = 'public'::regnamespace "
                    "AND proname IN ('auth_reserve_invitation_accept', "
                    "'auth_complete_invitation_accept')"
                )},
            )

        self.alembic("downgrade", "0021")
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
            self.assertIsNone(conn.execute(
                "SELECT to_regclass('public.auth_invitation_accept_throttle')"
            ).fetchone()[0])
            self.assertEqual(
                0,
                conn.execute(
                    "SELECT count(*) FROM pg_proc "
                    "WHERE pronamespace = 'public'::regnamespace "
                    "AND proname IN ('auth_reserve_invitation_accept', "
                    "'auth_complete_invitation_accept')"
                ).fetchone()[0],
            )

        self.alembic("upgrade", "head")
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
            self.assertIsNotNone(conn.execute(
                "SELECT to_regclass('public.auth_invitation_accept_throttle')"
            ).fetchone()[0])

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


    # spec: F8.ac3 / BUG-065 / BUG-067 — the later validation preflight preserves legacy rows.
    def test_migration_0024_preflight_rejects_non_finite_legacy_rows(self) -> None:
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

        # 0021 installs future-write enforcement without scanning. The 0024 preflight
        # must fail with an actionable diagnostic and leave the data untouched.
        completed = self._run_alembic("upgrade", "head")
        self.assertNotEqual(completed.returncode, 0, "0024 validation should fail on a non-finite legacy row")
        self.assertIn("BUG-065 preflight", completed.stdout + completed.stderr)

        # 0021-0023 committed independently; failed 0024 leaves the enforcement
        # constraint installed but unvalidated, and does not rewrite the bad row.
        self.assertEqual("0023", self.current_revision())
        with psycopg.connect(url) as conn:
            self.assertEqual((1, True), conn.execute(
                "SELECT count(*), bool_and(NOT convalidated) FROM pg_constraint "
                "WHERE conname = 'forecasts_interval_finite'"
            ).fetchone())
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
        self.alembic("upgrade", "0024")
        self.assertEqual("0024", self.current_revision())
        with psycopg.connect(url) as conn:
            self.assertEqual((1, True), conn.execute(
                "SELECT count(*), bool_and(convalidated) FROM pg_constraint "
                "WHERE conname = 'forecasts_interval_finite'"
            ).fetchone())

    # spec: F8.ac3 / BUG-067 — only bounded metadata-lock install contends with long reads;
    # online validation succeeds while the same ordinary read lock remains held.
    def test_bug067_short_install_lock_and_online_validation(self) -> None:
        import time

        import psycopg
        from psycopg.errors import CheckViolation

        url = os.environ["DATABASE_URL"]
        self.alembic("upgrade", "head")
        self.alembic("downgrade", "0020")

        reader = psycopg.connect(url)
        reader.execute("BEGIN")
        reader.execute("SELECT count(*) FROM forecasts").fetchone()
        started = time.monotonic()
        blocked = self._run_alembic("upgrade", "0021")
        elapsed = time.monotonic() - started
        self.assertNotEqual(0, blocked.returncode, blocked.stdout + blocked.stderr)
        self.assertIn("lock timeout", (blocked.stdout + blocked.stderr).lower())
        self.assertLess(elapsed, 12, "metadata lock wait must remain bounded")
        self.assertEqual("0020", self.current_revision())
        reader.rollback()
        reader.close()

        self.alembic("upgrade", "0021")
        with psycopg.connect(url) as conn:
            self.assertEqual(False, conn.execute(
                "SELECT convalidated FROM pg_constraint WHERE conname = 'forecasts_interval_finite'"
            ).fetchone()[0])

        # NOT VALID still enforces every new row immediately.
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute("SET session_replication_role = replica")
            with self.assertRaises(CheckViolation) as raised:
                conn.execute(
                    "INSERT INTO forecasts (tenant_id, location_id, product_category, "
                    "forecast_hour, p70, lower, upper, model_version, taxonomy_version) VALUES "
                    "('00000000-0000-0000-0000-0000000000ab', 1, 'Coffee', now(), "
                    "'Infinity'::numeric, 0, 'Infinity'::numeric, 'bug067-write', 'bug067-write')"
                )
            self.assertIn("forecasts_interval_finite", str(raised.exception))

        self.alembic("upgrade", "0023")
        reader = psycopg.connect(url)
        reader.execute("BEGIN")
        reader.execute("SELECT count(*) FROM forecasts").fetchone()
        # VALIDATE's SHARE UPDATE EXCLUSIVE lock is compatible with ACCESS SHARE.
        validated = self._run_alembic("upgrade", "0024")
        self.assertEqual(0, validated.returncode, validated.stdout + validated.stderr)
        reader.rollback()
        reader.close()
        self.assertEqual("0024", self.current_revision())
        with psycopg.connect(url) as conn:
            self.assertEqual(True, conn.execute(
                "SELECT convalidated FROM pg_constraint WHERE conname = 'forecasts_interval_finite'"
            ).fetchone()[0])
        self.alembic("downgrade", "0023")
        with psycopg.connect(url) as conn:
            self.assertEqual(False, conn.execute(
                "SELECT convalidated FROM pg_constraint WHERE conname = 'forecasts_interval_finite'"
            ).fetchone()[0])
        self.alembic("upgrade", "0024")
        with psycopg.connect(url) as conn:
            self.assertEqual(True, conn.execute(
                "SELECT convalidated FROM pg_constraint WHERE conname = 'forecasts_interval_finite'"
            ).fetchone()[0])


if __name__ == "__main__":
    unittest.main()
