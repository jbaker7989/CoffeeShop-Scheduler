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


class RulesAdminRegressionTests(unittest.TestCase):
    """Database-free regressions for rules-admin edit semantics."""

    @staticmethod
    def _rule(*, effective_from=date(2026, 1, 1), effective_to=None, verification_status="VERIFIED"):
        return {
            "rule_id": "NY-TEST-RULE", "jurisdiction": "NY", "enforcement": "HARD",
            "applies_age_min": None, "applies_age_max": None, "applies_school_status": None,
            "applies_day_type": None, "applies_season": None, "requires_employer_flag": None,
            "metric": "test", "operator": None, "value_num": None, "value_text": None,
            "unit": None, "consequence": None, "waivable_by_consent": False,
            "waiver_doc_required": False, "priority": 0, "effective_from": effective_from,
            "effective_to": effective_to, "verification_status": verification_status,
        }

    # spec: BUG-014 / F34.ac2,ac6
    def test_verification_gate_assesses_the_edit_transition(self):
        before = {
            "enforcement": "SOFT_COST", "verification_status": "VERIFY",
            "notes": "old decision", "source_citation": None, "source_url": None,
        }
        after = {**before, "enforcement": "HARD"}
        with self.assertRaisesRegex(rules_admin.ValidationError, "design decision required"):
            rules_admin._verification_gate(before, after, {"enforcement": "HARD"})

        # Existing unverified states must not make unrelated typo fixes impossible.
        rules_admin._verification_gate(after, {**after, "priority": 9}, {"priority": 9})
        rules_admin._verification_gate(before, {**before, "value_text": "changed"}, {"value_text": "changed"})

    # spec: BUG-015 / F34.ac3
    def test_date_reconciliation_flags_coverage_gaps_not_normal_overlap(self):
        legal = self._rule(effective_from=date(2026, 1, 1), effective_to=date(2026, 3, 1))
        overlap = {"rule_id": "COV-TEST", "description": "LAW NY-TEST-RULE", "value": None,
                   "basis": None, "validity": _DateRange(date(2026, 1, 15), date(2026, 2, 15))}
        gap = {**overlap, "validity": _DateRange(date(2025, 12, 1), date(2026, 2, 1))}
        unbounded = {**overlap, "validity": _DateRange(None, None)}

        self.assertEqual([], rules_admin.reconcile_for_admin(legal, [legal], [unbounded]))
        self.assertEqual([], rules_admin.reconcile_for_admin(legal, [legal], [overlap]))
        conflicts = rules_admin.reconcile_for_admin(legal, [legal], [gap])
        self.assertIn("effective_date_gap", {item["conflict_type"] for item in conflicts})


class _DateRange:
    """Small stand-in for psycopg's Range used by pure reconciliation tests."""
    def __init__(self, lower, upper):
        self.lower = lower
        self.upper = upper


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

    # spec: BUG-015 / F34.ac3
    def test_effective_date_gap_is_reported(self):
        old = rules_admin.get_rule(self.conn, "NYC-FPC-COVERAGE", tenant_id=TENANT)
        self.conn.execute("UPDATE coverage_rules SET validity = daterange(%s, %s, '[)') WHERE rule_id = 'COV-FPC'", (date(2026, 1, 1), date(2026, 2, 1)))
        with self.assertRaises(rules_admin.ReconciliationError) as caught:
            rules_admin.patch_rule(self.conn, "NYC-FPC-COVERAGE", {"effective_to": date(2026, 3, 1)}, if_match=old["etag"], principal=self.principal)
        self.assertIn("effective_date_gap", str(caught.exception))

    # spec: BUG-016 / F34 effective-dated rule administration
    def test_each_effective_dated_version_is_viewable_and_editable(self):
        first, second = date(2030, 1, 1), date(2031, 1, 1)
        self.conn.execute(
            "INSERT INTO labor_rules (tenant_id, rule_id, effective_from, effective_to) VALUES (%s, 'F34-VERSION-TEST', %s, %s), (%s, 'F34-VERSION-TEST', %s, NULL)",
            (TENANT, first, second, TENANT, second),
        )
        older = rules_admin.get_rule(self.conn, "F34-VERSION-TEST", tenant_id=TENANT, effective_from=first)
        newer = rules_admin.get_rule(self.conn, "F34-VERSION-TEST", tenant_id=TENANT, effective_from=second)
        self.assertEqual(first, older["effective_from"])
        self.assertEqual(second, newer["effective_from"])
        result = rules_admin.patch_rule(
            self.conn, "F34-VERSION-TEST", {"notes": "older version"}, if_match=older["etag"],
            principal=self.principal, effective_from=first,
        )
        self.assertTrue(result["modified"])
        self.assertEqual("older version", rules_admin.get_rule(
            self.conn, "F34-VERSION-TEST", tenant_id=TENANT, effective_from=first,
        )["notes"])
        self.assertIsNone(rules_admin.get_rule(
            self.conn, "F34-VERSION-TEST", tenant_id=TENANT, effective_from=second,
        )["notes"])

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
