"""F34 rules-admin service tests; all database writes are rolled back."""
import csv
import io
import os
import unittest
from datetime import date

try:
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None

from compliance import rules_admin

DSN = os.environ.get("DATABASE_URL")
TENANT = "00000000-0000-0000-0000-000000000001"


@unittest.skipUnless(DSN and psycopg, "DATABASE_URL and psycopg are required")
class RulesAdminTests(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(DSN)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        self.principal = {"tenant_id": TENANT, "id": "manager-1", "capabilities": {"rules_manager"}}

    # spec: F34.ac1
    def test_list_includes_badges_pay_only_and_reconciliation(self):
        rows = rules_admin.list_rules(self.conn, {"tenant_id": TENANT})
        self.assertEqual(31, len(rows))
        self.assertEqual({"VERIFIED", "VERIFY", "ASSUMPTION"}, {r["verification_status"] for r in rows})
        self.assertIn("PAY_ONLY", {r["enforcement"] for r in rows})
        self.assertTrue(all(r["reconciliation_status"] in {"clean", "conflict", "unverified_law"} for r in rows))

    # spec: F34.ac2
    def test_unverified_blocking_rule_is_rejected(self):
        old = rules_admin.get_rule(self.conn, "NY-CALLIN-HOSP", tenant_id=TENANT)
        with self.assertRaisesRegex(rules_admin.ValidationError, "design decision required"):
            rules_admin.patch_rule(self.conn, "NY-CALLIN-HOSP", {"enforcement": "HARD", "notes": None, "source_citation": None}, if_match=old["etag"], principal=self.principal)

    # spec: F34.ac3
    def test_effective_date_conflict_is_reported(self):
        old = rules_admin.get_rule(self.conn, "NYC-FPC-COVERAGE", tenant_id=TENANT)
        self.conn.execute("UPDATE coverage_rules SET validity = daterange(%s, %s, '[)') WHERE rule_id = 'COV-FPC'", (date(2026, 1, 1), date(2026, 2, 1)))
        with self.assertRaises(rules_admin.ReconciliationError) as caught:
            rules_admin.patch_rule(self.conn, "NYC-FPC-COVERAGE", {"effective_to": date(2026, 2, 1)}, if_match=old["etag"], principal=self.principal)
        self.assertIn("effective_date_overlap", str(caught.exception))

    # spec: F34.ac4
    def test_waiver_is_half_open_in_tenant_timezone(self):
        before = rules_admin.list_waivers(self.conn, "NY-MINOR-1617-SCH-WINDOW", tenant_id=TENANT, today=date(2027, 6, 25), tz="America/New_York")[0]
        after = rules_admin.list_waivers(self.conn, "NY-MINOR-1617-SCH-WINDOW", tenant_id=TENANT, today=date(2027, 6, 26), tz="America/New_York")[0]
        self.assertFalse(before["expired"])
        self.assertTrue(after["expired"])
        self.assertIn("Expired on 2027-06-26", after["warning"])

    # spec: F34.ac5
    def test_edit_writes_before_after_audit_and_noop_does_not(self):
        old = rules_admin.get_rule(self.conn, "NYC-WAGE-MIN-2026", tenant_id=TENANT)
        count = self.conn.execute("SELECT count(*) FROM rule_eval_log WHERE agent = 'rules-admin-agent'").fetchone()[0]
        result = rules_admin.patch_rule(self.conn, "NYC-WAGE-MIN-2026", {"notes": "F34 test edit"}, if_match=old["etag"], principal=self.principal)
        self.assertTrue(result["modified"])
        note = self.conn.execute("SELECT notes FROM rule_eval_log WHERE agent = 'rules-admin-agent' ORDER BY evaluated_at DESC LIMIT 1").fetchone()[0]
        self.assertIn('"before"', note)
        again = rules_admin.patch_rule(self.conn, "NYC-WAGE-MIN-2026", {"notes": "F34 test edit"}, if_match=result["etag"], principal=self.principal)
        self.assertFalse(again["modified"])
        self.assertEqual(count + 1, self.conn.execute("SELECT count(*) FROM rule_eval_log WHERE agent = 'rules-admin-agent'").fetchone()[0])

    # spec: F34.ac6
    def test_internal_source_url_is_advisory_and_ssrf_guarded(self):
        self.assertFalse(rules_admin.validate_source_url("https://127.0.0.1/"))
        old = rules_admin.get_rule(self.conn, "NY-TIPCREDIT-EXCLUDED", tenant_id=TENANT)
        result = rules_admin.patch_rule(self.conn, "NY-TIPCREDIT-EXCLUDED", {"source_url": "https://127.0.0.1/", "notes": "design decision"}, if_match=old["etag"], principal=self.principal)
        self.assertTrue(result["modified"])
        self.assertFalse(result["source_url_valid"])

    # spec: F34.ac7
    def test_reconciliation_runs_and_cross_tenant_is_not_found(self):
        old = rules_admin.get_rule(self.conn, "NYC-FPC-COVERAGE", tenant_id=TENANT)
        with self.assertRaises(rules_admin.ReconciliationError):
            rules_admin.patch_rule(self.conn, "NYC-FPC-COVERAGE", {"enforcement": "PAY_ONLY", "verification_status": "VERIFIED"}, if_match=old["etag"], principal=self.principal)
        with self.assertRaises(rules_admin.NotFound):
            rules_admin.get_rule(self.conn, "NYC-FPC-COVERAGE", tenant_id="00000000-0000-0000-0000-000000000002")

    # spec: F34.ac8
    def test_copy_export_is_value_equal_and_csv_safe(self):
        exported = list(csv.DictReader(io.StringIO(rules_admin.export_csv(self.conn, tenant_id=TENANT))))
        cursor = self.conn.execute("SELECT * FROM labor_rules ORDER BY rule_id, effective_from")
        headers = [field.name for field in cursor.description]
        direct = cursor.fetchall()
        self.assertEqual(len(direct), len(exported))
        def csv_value(value):
            if value is None:
                return ""
            if isinstance(value, bool):
                return "t" if value else "f"
            if hasattr(value, "lower") and hasattr(value, "upper") and hasattr(value, "bounds"):
                lower = "" if value.lower is None else str(value.lower)
                upper = "" if value.upper is None else str(value.upper)
                return f"{value.bounds[0]}{lower},{upper}{value.bounds[1]}"
            return str(value)
        expected = [{name: csv_value(value) for name, value in zip(headers, row)} for row in direct]
        key = lambda row: (row["rule_id"], row["effective_from"])
        self.assertEqual(sorted(expected, key=key), sorted(exported, key=key))

    # spec: F34.ac5
    def test_stale_etag_and_missing_capability(self):
        old = rules_admin.get_rule(self.conn, "NYC-WAGE-MIN-2026", tenant_id=TENANT)
        with self.assertRaises(rules_admin.Conflict):
            rules_admin.patch_rule(self.conn, "NYC-WAGE-MIN-2026", {}, if_match="bad", principal=self.principal)
        with self.assertRaises(rules_admin.Forbidden):
            rules_admin.patch_rule(self.conn, "NYC-WAGE-MIN-2026", {}, if_match=old["etag"], principal={"tenant_id": TENANT, "capabilities": set()})
