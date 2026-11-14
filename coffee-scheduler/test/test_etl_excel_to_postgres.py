"""CS-044 / F44 specifications for the Excel-to-Postgres loader.

Run with: python3 coffee-scheduler/test/test_etl_excel_to_postgres.py
Set DATABASE_URL to run the database integration test.
"""
import copy
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
SCRIPT = ROOT / "scripts" / "etl_excel_to_postgres.py"


def load_etl():
    spec = importlib.util.spec_from_file_location("etl_excel_to_postgres", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_private_pii_workbook(path, rows):
    """Create textual PII cells without committing a PII fixture."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Staff_PII"
    sheet.append(["employee_id", "ssn", "zipcode-address"])
    for row in rows:
        sheet.append(row)
    workbook.save(path)


def snapshot_data_dir_with_private_pii(tmp_dir, pii_rows):
    """Expose the public fixture set plus a synthetic private workbook."""
    for source in DATA.glob("*.xlsx"):
        (tmp_dir / source.name).symlink_to(source)
    private_dir = tmp_dir / "private"
    private_dir.mkdir()
    write_private_pii_workbook(private_dir / "coffee_shop_staff_pii.xlsx", pii_rows)
    return tmp_dir


class ExcelToPostgresSpecifications(unittest.TestCase):
    # spec: F44.ac1
    def test_public_snapshot_covers_every_supported_table(self):
        etl = load_etl()
        snapshot = etl.read_snapshot(DATA, include_pii=False)
        self.assertNotIn("employee_pii", snapshot.tables)
        self.assertEqual(len(snapshot.tables["employees"]), 40)
        self.assertEqual(len(snapshot.tables["stores"]), 3)
        self.assertEqual(len(snapshot.tables["store_hours"]), 21)
        self.assertEqual(len(snapshot.tables["transactions"]), 149_116)
        self.assertEqual(len(snapshot.tables["weather_hourly"]), 9_231)
        self.assertEqual(len(snapshot.tables["weather_daily"]), 543)
        self.assertEqual(len(snapshot.tables["weather_codes"]), 28)

    # spec: F34.ac3 — coverage rules load with an unbounded (always-in-effect) validity,
    # never NULL, so a fresh load matches migration 0002's backfill.
    def test_coverage_rules_carry_unbounded_validity(self):
        etl = load_etl()
        snapshot = etl.read_snapshot(DATA, include_pii=False)
        coverage = snapshot.tables["coverage_rules"]
        self.assertTrue(coverage)
        for row in coverage:
            validity = row.get("validity")
            self.assertIsNotNone(validity, f"{row.get('rule_id')} has no validity")
            # unbounded both ends = always in effect
            self.assertIsNone(validity.lower)
            self.assertIsNone(validity.upper)

    # spec: F44.ac2
    @unittest.skipUnless(os.environ.get("DATABASE_URL"), "DATABASE_URL required for PostgreSQL integration")
    def test_second_load_is_an_identical_snapshot(self):
        etl = load_etl()
        first = etl.load_snapshot(os.environ["DATABASE_URL"], "maven-demo", DATA)
        second = etl.load_snapshot(os.environ["DATABASE_URL"], "maven-demo", DATA)
        self.assertEqual(first.target_counts, second.target_counts)
        self.assertEqual(second.anomalies, [])

    # spec: F44.ac3
    def test_bulk_tables_use_copy_staging_path(self):
        etl = load_etl()
        self.assertEqual(etl.BULK_TABLES,
                         {"transactions", "weather_hourly", "weather_daily", "rule_eval_log"})

    # spec: F44.ac4
    def test_invalid_store_fails_before_any_database_mutation(self):
        etl = load_etl()
        snapshot = etl.read_snapshot(DATA, include_pii=False)
        invalid = copy.deepcopy(snapshot)
        invalid.tables["transactions"][0]["store_id"] = 999
        with self.assertRaisesRegex(etl.ETLValidationError, "store_id.*999"):
            etl.validate_snapshot(invalid)

    # spec: F44.ac4
    def test_missing_rule_id_fails_closed(self):
        etl = load_etl()
        snapshot = etl.read_snapshot(DATA, include_pii=False)
        invalid = copy.deepcopy(snapshot)
        invalid.tables["labor_rules"][0]["rule_id"] = None
        with self.assertRaisesRegex(etl.ETLValidationError, "rule_id"):
            etl.validate_snapshot(invalid)

    # spec: F44.ac5
    def test_pii_requires_explicit_opt_in(self):
        etl = load_etl()
        public = etl.read_snapshot(DATA, include_pii=False)
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = snapshot_data_dir_with_private_pii(Path(tmp), [
                [1001, "001020003", "02134"],
            ])
            private = etl.read_snapshot(data_dir, include_pii=True)
        self.assertNotIn("employee_pii", public.tables)
        self.assertEqual(len(private.tables["employee_pii"]), 1)

    # spec: F44.ac4 — BUG-005: text identifiers retain leading zeros.
    def test_text_like_values_are_not_shape_coerced(self):
        etl = load_etl()
        self.assertEqual(etl.null("02134"), "02134")
        self.assertEqual(etl.null("001-02-0003"), "001-02-0003")

    # spec: F44.ac4 — BUG-005 / D7: read by declared destination type, not value shape.
    def test_snapshot_preserves_textual_pii_identifiers_and_integer_targets(self):
        etl = load_etl()
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = snapshot_data_dir_with_private_pii(Path(tmp), [
                [1001, "001020003", "02134"],
            ])
            snapshot = etl.read_snapshot(data_dir, include_pii=True)

        pii = snapshot.tables["employee_pii"][0]
        self.assertEqual(pii["ssn"], "001020003")
        self.assertIsInstance(pii["ssn"], str)
        self.assertEqual(pii["zipcode"], "02134")
        self.assertIsInstance(pii["zipcode"], str)
        # `skill_level` targets an integer column; numeric whole numbers stay integers.
        self.assertEqual(snapshot.tables["employees"][0]["skill_level"], 1)
        self.assertIsInstance(snapshot.tables["employees"][0]["skill_level"], int)

    # spec: F44.ac3 — BUG-006: all row keys participate in COPY staging.
    def test_columns_are_the_union_of_every_row(self):
        etl = load_etl()
        self.assertEqual(etl.columns([{"a": 1}, {"a": 2, "b": 99}]), ["a", "b"])

    # spec: F44.ac4 — BUG-007: private rows receive the same preflight validation.
    def test_duplicate_private_employee_is_rejected_before_load(self):
        etl = load_etl()
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = snapshot_data_dir_with_private_pii(Path(tmp), [
                [1001, "001020003", "02134"],
            ])
            snapshot = etl.read_snapshot(data_dir, include_pii=True)
        snapshot.tables["employee_pii"].append(dict(snapshot.tables["employee_pii"][0]))
        with self.assertRaisesRegex(etl.ETLValidationError, "employee_pii.*duplicate"):
            etl.validate_snapshot(snapshot)

    # spec: F44.ac2 — BUG-008: multiple evaluations can share a legacy shift.
    def test_legacy_shift_derivation_deduplicates_many_evaluations(self):
        etl = load_etl()
        logs = [{"tenant_id": etl.TENANT, "schedule_id": etl.legacy_uuid("SCH-1"),
                 "shift_id": etl.legacy_uuid("SH-1")},
                {"tenant_id": etl.TENANT, "schedule_id": etl.legacy_uuid("SCH-1"),
                 "shift_id": etl.legacy_uuid("SH-1")}]
        schedules, shifts = etl.derive_legacy_schedule_rows(logs)
        self.assertEqual(len(schedules), 1)
        self.assertEqual(len(shifts), 1)

    # spec: F44.ac2 — BUG-009: tenant loads never prune global references.
    def test_global_weather_codes_are_upsert_only(self):
        etl = load_etl()
        self.assertNotIn("weather_codes", etl.tables_to_prune({"weather_codes": [], "employees": []}))


if __name__ == "__main__":
    unittest.main()
