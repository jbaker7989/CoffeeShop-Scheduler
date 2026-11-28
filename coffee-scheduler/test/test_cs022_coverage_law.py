"""CS-022 synthetic fixtures and the same gate exit path used by the CLI."""
import sys
import unittest
from dataclasses import replace
from datetime import date, datetime
from io import StringIO
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from compliance.coverage_law import CoverageRule, DateRange, LaborRule, reconcile
from compliance.coverage_law_gate import main, run_gate

TENANT = "00000000-0000-0000-0000-000000000021"
OTHER = "00000000-0000-0000-0000-000000000022"
START, MID, NEXT, END = (date(2030, 1, day) for day in (1, 10, 11, 20))
WINDOW = DateRange(START, END)
COVERAGE = CoverageRule(TENANT, "COV-FIXTURE", "See TEST-CERT", None, "LAW (reference)", WINDOW)
LEGAL = LaborRule(TENANT, "TEST-CERT", WINDOW, "HARD", "VERIFIED", START, "Synthetic § 1", "https://example.invalid/rule")
# A minimum crew is an operating assumption. Its headcount is a staffing choice.
ASSUMPTION = CoverageRule(TENANT, "COV-TEST-CREW", "Minimum crew when open", "2", "ASSUMPTION", None)


class CoverageLawTests(unittest.TestCase):
    def findings(self, coverage=COVERAGE, rules=(LEGAL,)):
        return reconcile([coverage], rules)

    def conditions(self, coverage=COVERAGE, rules=(LEGAL,)):
        return [finding.condition for finding in self.findings(coverage, rules)]

    # spec: F26.ac1 — only a whole-word LAW marker in the three runtime fields counts.
    def test_law_markers_and_non_law_assumptions(self):
        for column, marker in (("description", "LaW"), ("value", "LAW"), ("basis", "LAW (reference)")):
            with self.subTest(column=column):
                self.assertEqual(self.conditions(replace(ASSUMPTION, **{column: marker}, validity=WINDOW)), ["missing_citation"])
        for text in ("LEGAL", "lawful", "bylaw", "flaw", "LAW_REFERENCE"):
            with self.subTest(text=text):
                self.assertEqual(self.findings(replace(ASSUMPTION, description=text), ()), [])
        self.assertEqual(self.findings(ASSUMPTION, ()), [])

    # spec: F26.ac2 — exact citation tokens in the same tenant, excluding the coverage row's ID.
    def test_missing_malformed_and_inexact_citations(self):
        for description, condition in (
            ("No citation", "missing_citation"), ("See COV-FIXTURE", "missing_citation"),
            ("See TEST--CERT", "malformed_citation"), ("See TEST-CERT_", "malformed_citation"),
            ("See TEST-CERT-", "malformed_citation"), ("See TEST-CERT and TEST--OTHER", "malformed_citation"),
            ("See TEST-CERT-EXT", "missing_rule"),
        ):
            with self.subTest(description=description):
                self.assertEqual(self.conditions(replace(COVERAGE, description=description)), [condition])
        self.assertEqual(self.conditions(rules=(replace(LEGAL, tenant_id=OTHER),)), ["missing_rule"])

    # spec: F26.ac2 — deduplicate identical links and validate all distinct cited rule IDs.
    def test_multiple_distinct_citations(self):
        coverage = replace(COVERAGE, description="TEST-CERT and TEST-OTHER", value="TEST-CERT")
        other = replace(LEGAL, rule_id="TEST-OTHER")
        self.assertEqual(self.findings(coverage, (LEGAL, other)), [])
        invalid = replace(LEGAL, verification_status="VERIFY")
        self.assertEqual(
            [(f.labor_rule_id, f.condition) for f in self.findings(coverage, (invalid,))],
            [("TEST-CERT", "unverified_rule"), ("TEST-OTHER", "missing_rule")],
        )

    # spec: F26.ac3 — only VERIFIED with valid stored evidence supports a relevant version.
    def test_status_and_evidence_fail_closed(self):
        self.assertEqual(self.findings(), [])
        for status in ("VERIFY", "ASSUMPTION", None, "UNEXPECTED", "verified"):
            with self.subTest(status=status):
                self.assertEqual(self.conditions(rules=(replace(LEGAL, verification_status=status),)), ["unverified_rule"])
        for field, value in (
            ("verified_on", None), ("verified_on", "2030-01-01"), ("verified_on", datetime(2030, 1, 1)),
            ("source_citation", None), ("source_citation", "  "),
            ("source_url", None), ("source_url", "file:///rule"), ("source_url", "https:///rule"),
            ("source_url", "https://bad host/rule"), ("source_url", "https://[broken/rule"),
        ):
            with self.subTest(field=field, value=value):
                self.assertEqual(self.conditions(rules=(replace(LEGAL, **{field: value}),)), ["invalid_evidence"])

    # spec: F26.ac4 — adjacent versions join and a one-day or partial gap fails.
    def test_half_open_boundaries(self):
        early = replace(LEGAL, validity=DateRange(START, MID))
        late = replace(LEGAL, validity=DateRange(MID, END))
        outside = replace(LEGAL, validity=DateRange(None, START), verification_status="VERIFY")
        self.assertEqual(self.findings(rules=(late, outside, early)), [])
        self.assertEqual(
            [(f.condition, f.interval) for f in self.findings(rules=(early, replace(late, verification_status="VERIFY")))],
            [("unverified_rule", DateRange(MID, END))],
        )
        for rules, gap in (
            ((early, replace(late, validity=DateRange(NEXT, END))), DateRange(MID, NEXT)),
            ((late,), DateRange(START, MID)), ((early,), DateRange(MID, END)),
            ((outside,), WINDOW), ((replace(LEGAL, validity=DateRange(END, None)),), WINDOW),
        ):
            with self.subTest(gap=gap):
                self.assertEqual([(f.condition, f.interval) for f in self.findings(rules=rules)], [("date_gap", gap)])

    # spec: F26.ac4 — NULL/empty validity fails and each unbounded endpoint needs legal support.
    def test_unbounded_and_invalid_validity(self):
        for invalid in (None, DateRange(empty=True), DateRange(MID, MID), DateRange(END, START)):
            with self.subTest(invalid=invalid):
                self.assertEqual(self.conditions(replace(COVERAGE, validity=invalid)), ["invalid_coverage_validity"])
        for window in (DateRange(None, END), DateRange(START, None), DateRange()):
            with self.subTest(window=window):
                coverage = replace(COVERAGE, validity=window)
                self.assertEqual(self.findings(coverage, (replace(LEGAL, validity=window),)), [])
                self.assertIn("date_gap", self.conditions(coverage))
        self.assertEqual(self.findings(replace(COVERAGE, validity=DateRange()), (
            replace(LEGAL, validity=DateRange(MID, None)), replace(LEGAL, validity=DateRange(None, MID)),
        )), [])

    # spec: F26.ac5 — any overlap is ambiguous and relevant conflicting versions are reported.
    def test_overlaps_and_enforcement(self):
        invalid = replace(LEGAL, validity=DateRange(MID, None), enforcement="PAY_ONLY", verification_status="VERIFY")
        findings = self.findings(rules=(invalid, LEGAL))
        self.assertCountEqual([f.condition for f in findings], ["overlapping_versions", "unverified_rule", "enforcement_conflict"])
        self.assertTrue(all(f.interval == DateRange(MID, END) for f in findings))
        self.assertEqual(self.conditions(rules=(LEGAL, LEGAL)), ["overlapping_versions"])
        self.assertEqual(self.findings(rules=(replace(LEGAL, enforcement="PROCESS"),)), [])
        for enforcement in ("PAY_ONLY", "SOFT", None, "UNKNOWN"):
            with self.subTest(enforcement=enforcement):
                self.assertEqual(self.conditions(rules=(replace(LEGAL, enforcement=enforcement),)), ["enforcement_conflict"])

    # spec: F26.ac7 — deterministic rule/date/condition output without copying free text or evidence.
    def test_diagnostic_snapshot(self):
        coverage = replace(COVERAGE, description="private fixture text TEST-MISSING TEST-CERT")
        rule = replace(LEGAL, source_url="private fixture URL")
        expected = (
            f"FAIL tenant={TENANT} coverage=COV-FIXTURE labor=TEST-CERT interval=[2030-01-01,2030-01-20) condition=invalid_evidence\n"
            f"FAIL tenant={TENANT} coverage=COV-FIXTURE labor=TEST-MISSING interval=[2030-01-01,2030-01-20) condition=missing_rule\n"
        )
        for coverages in ((coverage, ASSUMPTION), (ASSUMPTION, coverage)):
            output = StringIO()
            self.assertEqual(run_gate(coverages, (rule,), output=output), 1)
            self.assertEqual(output.getvalue(), expected)

    # spec: F26.ac8 — production gate path fails on an injected unverified LAW row.
    def test_gate_mutation(self):
        injected = replace(COVERAGE, rule_id="COV-INJECTED", description="See TEST-INJECTED")
        unverified = replace(LEGAL, rule_id="TEST-INJECTED", verification_status="VERIFY")
        for coverages, rules, exit_code in (
            ((ASSUMPTION, COVERAGE), (LEGAL, unverified), 0),
            ((ASSUMPTION, COVERAGE, injected), (LEGAL, unverified), 1),
            ((ASSUMPTION, COVERAGE, injected), (LEGAL, replace(unverified, verification_status="VERIFIED")), 0),
            ((ASSUMPTION, COVERAGE), (LEGAL, unverified), 0),
        ):
            with self.subTest(exit_code=exit_code, count=len(coverages)):
                output = StringIO()
                self.assertEqual(run_gate(coverages, rules, output=output), exit_code)
                if exit_code:
                    self.assertIn("coverage=COV-INJECTED labor=TEST-INJECTED", output.getvalue())
                    self.assertIn("condition=unverified_rule", output.getvalue())

    # spec: F26.ac6 — absent DB configuration, unreadable DB or empty sources are explicit setup failures.
    def test_gate_setup_failures(self):
        for coverage, rules in (([], []), ([COVERAGE], []), ([], [LEGAL])):
            output = StringIO()
            self.assertEqual(run_gate(coverage, rules, output=output), 2)
            self.assertIn("SETUP ERROR", output.getvalue())
        for env, reason in (({}, "DATABASE_URL"), ({"DATABASE_URL": "invalid-connection-string"}, "database")):
            with self.subTest(reason=reason), patch.dict("os.environ", env, clear=True):
                output = StringIO()
                self.assertEqual(main(["--tenant-id", TENANT, "--tenant-slug", "synthetic"], output=output), 2)
                self.assertIn(reason, output.getvalue())
                self.assertNotIn("invalid-connection-string", output.getvalue())


if __name__ == "__main__":
    unittest.main()
