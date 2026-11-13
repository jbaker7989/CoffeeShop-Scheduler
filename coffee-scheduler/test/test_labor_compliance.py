"""CS-013 / F10 labor-compliance engine tests.

Run: python3 coffee-scheduler/test/test_labor_compliance.py
Integration cases use DATABASE_URL or the local docker-compose Postgres.
"""

import os
import sys
import unittest
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "coffee-scheduler"))

from compliance.engine import (  # noqa: E402
    SUPPORTED, Break, ComplianceConfigError, ComplianceUnavailable, CoverageConflict,
    CrewMember, DayFacts, Employee, MemoryLog, Proposal, Rule, ShiftNotPersisted,
    ShiftSpan, Snapshot, Waiver, evaluate, prepare_session, reconcile_coverage, vocab_key,
)

NY = ZoneInfo("America/New_York")
TENANT = "00000000-0000-0000-0000-000000000001"
DSN = os.environ.get("DATABASE_URL", "postgresql://coffeeshop:coffeeshop@localhost:5432/coffeeshop")


def rule(**kwargs):
    base = dict(
        rule_id="RULE", jurisdiction="NY-STATE", enforcement="HARD",
        applies_age_min=None, applies_age_max=None, applies_school_status="ANY",
        applies_day_type="ANY", applies_season=None, requires_employer_flag=None,
        metric="rate", operator=">=", value_num=Decimal("17"), value_text=None, unit="USD",
        consequence="Pay below floor = violation", waivable_by_consent=False,
        waiver_doc_required=False, priority=10, effective_from=date(2026, 1, 1),
        effective_to=None, verification_status="VERIFIED",
    )
    base.update(kwargs)
    return Rule(**base)


def employee(**kwargs):
    base = dict(
        employee_id=1003, dob=date(1980, 5, 27), hourly_rate=Decimal("25"),
        is_enrolled=False, has_other_employer=False, food_protection_cert=True,
    )
    base.update(kwargs)
    return Employee(**base)


def snapshot(rules, **kwargs):
    base = dict(
        tenant_id=TENANT, timezone="America/New_York", jurisdictions=("NY-STATE", "NYC"),
        employer_flags={"fww_covered": False}, employee=employee(), rules=tuple(rules),
        waivers=(), days={}, labor_days={2026: date(2026, 9, 7), 2027: date(2027, 9, 6)},
        wage_floor=Decimal("17"),
    )
    base.update(kwargs)
    return Snapshot(**base)


def proposal(**kwargs):
    start = datetime(2026, 11, 18, 9, 0, tzinfo=NY)
    base = dict(
        tenant_id=TENANT, location_id=5, employee_id=1003, role="Barista",
        start_at=start, end_at=start + timedelta(hours=6), breaks=[],
        week_start=date(2026, 11, 16), week_shifts=[], week_complete=True,
        crew=[CrewMember(1003, True, "lead")], crew_complete=True,
    )
    base.update(kwargs)
    return Proposal(**base)


class LaborComplianceSpecifications(unittest.TestCase):
    # spec: F10.ac1
    def test_complete_adult_shift_passes_applicable_rules(self):
        rules = [
            rule(rule_id="NYC-WAGE-MIN-2026"),
            rule(rule_id="NY-SPREAD-10H", enforcement="SOFT_COST", metric="span_hours",
                 operator="TRIGGER_WHEN_GT", value_num=Decimal("10"), unit="HOURS",
                 consequence="Add 1 hr pay at basic minimum wage"),
            rule(rule_id="NY-MEAL-NOON", metric="break_minutes", operator=">=", value_num=Decimal("30"),
                 value_text="11:00-14:00", unit="MINUTES",
                 consequence="Shift >6 hrs spanning 11:00-14:00 needs 30-min meal in that window"),
        ]
        evaluation = evaluate(proposal(), snapshot=snapshot(rules))
        self.assertTrue(evaluation.results)
        self.assertTrue(all(row.result == "PASS" for row in evaluation.results))
        self.assertEqual(evaluation.binding, ())
        self.assertTrue(evaluation.claims_legal_accuracy)

    # spec: F10.ac2
    def test_hard_violation_is_binding_and_does_not_change_the_shift(self):
        low = rule(rule_id="NYC-WAGE-MIN-2026")
        start = datetime(2026, 11, 18, 9, 0, tzinfo=NY)
        item = proposal(start_at=start, end_at=start + timedelta(hours=6))
        evaluation = evaluate(item, snapshot=snapshot([low], employee=employee(hourly_rate=Decimal("16.50"))))
        failed = [row for row in evaluation.results if row.rule_id == "NYC-WAGE-MIN-2026"]
        self.assertEqual(failed[0].result, "FAIL")
        self.assertIn("NYC-WAGE-MIN-2026", evaluation.binding[0].rule_id)
        self.assertEqual(item.end_at, start + timedelta(hours=6))

    # spec: F10.ac3
    def test_stricter_hard_bound_is_the_only_binding_row(self):
        rules = [
            rule(rule_id="LOOSE", metric="hours_per_day", operator="<=", value_num=Decimal("8"), unit="HOURS", priority=1),
            rule(rule_id="TIGHT", metric="hours_per_day", operator="<=", value_num=Decimal("3"), unit="HOURS", priority=1),
        ]
        start = datetime(2026, 11, 18, 9, 0, tzinfo=NY)
        evaluation = evaluate(proposal(start_at=start, end_at=start + timedelta(hours=9)), snapshot=snapshot(rules))
        self.assertEqual({row.rule_id for row in evaluation.results}, {"LOOSE", "TIGHT"})
        self.assertTrue(all(row.result == "FAIL" for row in evaluation.results))
        self.assertEqual([row.rule_id for row in evaluation.binding], ["TIGHT"])

    # spec: F10.ac4
    def test_waiver_expires_on_valid_to_and_applies_the_day_before(self):
        window = rule(
            rule_id="NY-MINOR-1617-SCH-WINDOW", applies_age_min=16, applies_age_max=17,
            applies_school_status="IN_SESSION", applies_day_type="DAY_BEFORE_SCHOOL_DAY",
            metric="work_window", operator="BETWEEN", value_num=None, value_text="06:00-22:00", unit="TIME",
            waivable_by_consent=True, waiver_doc_required=True,
        )
        person = employee(employee_id=1033, dob=date(2010, 5, 29), is_enrolled=True, hourly_rate=Decimal("16.50"))
        waiver = Waiver("WV-0001", 1033, window.rule_id, date(2026, 9, 10), date(2027, 6, 26), "file://waivers/WV-0001.pdf")
        days = {
            date(2027, 6, 25): DayFacts(True, True),
            date(2027, 6, 26): DayFacts(True, True),
        }
        snap = snapshot([window], employee=person, waivers=(waiver,), days=days)

        def on(day):
            start = datetime(day.year, day.month, day.day, 16, 0, tzinfo=NY)
            return evaluate(proposal(employee_id=1033, start_at=start, end_at=start + timedelta(hours=6, minutes=30)), snapshot=snap)

        self.assertEqual(on(date(2027, 6, 25)).results[0].result, "PASS")
        self.assertEqual(on(date(2027, 6, 25)).results[0].waived_by, "WV-0001")
        self.assertEqual(on(date(2027, 6, 26)).results[0].result, "FAIL")
        self.assertIsNone(on(date(2027, 6, 26)).results[0].waived_by)

    # spec: F10.ac5
    def test_local_date_not_utc_date_and_not_today(self):
        school = rule(rule_id="SCHOOL-ONLY", applies_day_type="SCHOOL_DAY", metric="hours_per_day",
                      operator="<=", value_num=Decimal("3"), unit="HOURS")
        person = employee(is_enrolled=True, dob=date(2010, 5, 29))
        # 21:45 EST is 02:45 UTC the next day. Only the local date is in the calendar.
        start = datetime(2026, 1, 15, 21, 45, tzinfo=NY)
        snap = snapshot([school], employee=person, days={date(2026, 1, 15): DayFacts(False, False)})
        evaluation = evaluate(
            proposal(start_at=start, end_at=start + timedelta(hours=1), week_start=date(2026, 1, 12)),
            snapshot=snap,
        )
        self.assertEqual(evaluation.results, ())

    # spec: F10.ac5
    def test_age_changes_on_the_birthday(self):
        minor = rule(rule_id="UNDER-18", applies_age_max=17, metric="hours_per_day", operator="<=",
                     value_num=Decimal("1"), unit="HOURS")
        person = employee(dob=date(2008, 11, 18), is_enrolled=False)
        snap = snapshot([minor], employee=person)

        def on(day):
            start = datetime(day.year, day.month, day.day, 9, 0, tzinfo=NY)
            return evaluate(proposal(start_at=start, end_at=start + timedelta(hours=4), week_start=day - timedelta(days=day.weekday())), snapshot=snap)

        self.assertEqual(on(date(2026, 11, 17)).results[0].result, "FAIL")
        self.assertEqual(on(date(2026, 11, 18)).results, ())

    # spec: F10.ac6
    def test_enrolled_employee_without_school_row_raises(self):
        person = employee(is_enrolled=True)
        with self.assertRaises(ComplianceUnavailable):
            evaluate(proposal(), snapshot=snapshot([rule()], employee=person, days={}))

    # spec: F10.ac7
    def test_incomplete_week_or_crew_raises(self):
        weekly = rule(rule_id="WEEK", metric="hours_per_week", operator="<=", value_num=Decimal("40"), unit="HOURS")
        with self.assertRaises(ComplianceConfigError):
            evaluate(proposal(week_complete=False), snapshot=snapshot([weekly]))
        on_site = rule(rule_id="NYC-FPC-COVERAGE", jurisdiction="NYC", metric="certified_staff_on_site",
                       operator=">=", value_num=Decimal("1"), unit="HEADCOUNT", verification_status="VERIFY")
        with self.assertRaises(ComplianceConfigError):
            evaluate(proposal(crew=None, crew_complete=False), snapshot=snapshot([on_site]))

    # spec: F10.ac8
    def test_fair_workweek_follows_the_employer_flag(self):
        rest = rule(
            rule_id="NYC-FWW-CLOPEN", jurisdiction="NYC", enforcement="SOFT_COST",
            requires_employer_flag="fww_covered=TRUE", metric="rest_hours_between_shifts",
            operator=">=", value_num=Decimal("11"), unit="HOURS",
            consequence="Close-then-open needs consent + premium",
        )
        prior_end = datetime(2026, 11, 18, 1, 0, tzinfo=NY)
        start = datetime(2026, 11, 18, 9, 0, tzinfo=NY)
        item = proposal(start_at=start, end_at=start + timedelta(hours=4),
                        week_shifts=[ShiftSpan(prior_end - timedelta(hours=6), prior_end)])
        hidden = evaluate(item, snapshot=snapshot([rest], employer_flags={"fww_covered": False}))
        self.assertEqual(hidden.results, ())
        shown = evaluate(item, snapshot=snapshot([rest], employer_flags={"fww_covered": True}))
        self.assertEqual(shown.results[0].rule_id, "NYC-FWW-CLOPEN")
        self.assertEqual(shown.results[0].result, "FAIL")

    # spec: F10.ac9
    def test_sick_callout_cannot_require_the_employee_to_find_cover(self):
        essta = rule(
            rule_id="NYC-ESSTA-NO-REPLACE", jurisdiction="NYC", enforcement="PROCESS",
            metric="process", operator=None, value_num=None, value_text="Cannot require sick employee to find replacement",
            unit="PROCESS", consequence="Agent owns coverage search",
        )
        forced = evaluate(
            proposal(call_out_reason="sick", requires_employee_to_find_cover=True),
            snapshot=snapshot([essta]),
        )
        self.assertEqual(forced.results[0].result, "FAIL")
        self.assertIn("NYC-ESSTA-NO-REPLACE", forced.results[0].reason)
        self.assertIn("NYC-ESSTA-NO-REPLACE", forced.binding[0].rule_id)
        clean = evaluate(proposal(call_out_reason="sick"), snapshot=snapshot([essta]))
        self.assertEqual(clean.results[0].result, "PASS")

    # spec: F10.ac10
    def test_persist_appends_and_rejects_a_missing_shift(self):
        log = MemoryLog()
        log.shifts.add((TENANT, "shift-1"))
        item = proposal(shift_id="shift-1")
        first_clock = datetime(2026, 11, 18, 12, 0, tzinfo=NY)
        second_clock = first_clock + timedelta(minutes=5)
        evaluate(item, snapshot=snapshot([rule()]), persist=True, clock=first_clock, log=log)
        evaluate(item, snapshot=snapshot([rule()]), persist=True, clock=second_clock, log=log)
        self.assertEqual(len(log.rows), 2)
        latest = log.latest(TENANT, "shift-1", 1003, "RULE")
        self.assertEqual(latest["evaluated_at"], second_clock)
        self.assertEqual(log.rows[0]["result"], "PASS")
        missing = proposal(shift_id="missing")
        with self.assertRaises(ShiftNotPersisted):
            evaluate(missing, snapshot=snapshot([rule()]), persist=True, log=log)

    # spec: F10.ac11
    def test_set_role_failure_does_not_continue_as_superuser(self):
        class Boom(Exception):
            pass

        class Conn:
            def execute(self, sql, params=None):
                if "SET ROLE" in sql:
                    raise Boom("denied")
                raise AssertionError(sql)

        with self.assertRaises(ComplianceUnavailable):
            prepare_session(Conn(), TENANT)

    # spec: F10.ac12
    def test_unknown_metric_raises_before_a_pass(self):
        bad = rule(rule_id="NEW", metric="not_a_metric", operator=">=", unit="HOURS")
        with self.assertRaises(ComplianceConfigError):
            evaluate(proposal(), snapshot=snapshot([bad]))

    # spec: F10.ac12
    def test_unknown_vocabulary_cannot_be_hidden_behind_an_employer_flag(self):
        bad = rule(
            rule_id="HIDDEN-NEW", metric="not_a_metric", operator=">=", unit="HOURS",
            requires_employer_flag="fww_covered=TRUE",
        )
        with self.assertRaises(ComplianceConfigError):
            evaluate(proposal(), snapshot=snapshot([bad], employer_flags={"fww_covered": False}))

    # spec: F10.ac5
    def test_naive_or_backwards_shift_timestamps_raise(self):
        naive_start = datetime(2026, 11, 18, 9, 0)
        with self.assertRaises(ComplianceConfigError):
            evaluate(
                proposal(start_at=naive_start, end_at=naive_start + timedelta(hours=6)),
                snapshot=snapshot([rule()]),
            )
        start = datetime(2026, 11, 18, 9, 0, tzinfo=NY)
        with self.assertRaises(ComplianceConfigError):
            evaluate(
                proposal(start_at=start, end_at=start - timedelta(hours=1)),
                snapshot=snapshot([rule()]),
            )

    # spec: F10.ac12
    def test_sheet_vocabulary_matches_the_pinned_handlers(self):
        import pandas as pd
        frame = pd.read_excel(ROOT / "data" / "coffee_shop_labor_rules.xlsx", sheet_name="Labor_Rules")

        def cell(value):
            if value is None or (isinstance(value, float) and value != value):
                return ""
            return str(value)

        found = {vocab_key(row.metric, cell(row.operator), cell(row.unit)) for row in frame.itertuples(index=False)}
        self.assertEqual(found, SUPPORTED)

    # spec: F10.ac13
    def test_law_coverage_citation_must_name_a_blocking_rule(self):
        legal = rule(rule_id="NYC-FPC-COVERAGE", jurisdiction="NYC", enforcement="HARD")
        reconcile_coverage([legal], [{
            "rule_id": "COV-FPC",
            "description": "Food Protection Certificate holder (law — see labor rules NYC-FPC-COVERAGE)",
            "value": "1",
            "basis": "LAW (reference)",
        }])
        with self.assertRaises(CoverageConflict):
            reconcile_coverage([legal], [{"rule_id": "COV-X", "description": "LAW — see MISSING-RULE", "value": "", "basis": ""}])
        soft = rule(rule_id="NY-SPREAD-10H", enforcement="SOFT_COST")
        with self.assertRaises(CoverageConflict):
            reconcile_coverage([soft], [{"rule_id": "COV-X", "description": "LAW see NY-SPREAD-10H", "value": "", "basis": ""}])

    # spec: F10.ac14
    def test_unverified_rule_still_fails_and_blocks_a_legal_accuracy_claim(self):
        low = rule(rule_id="NYC-WAGE-MIN-2026", verification_status="VERIFY")
        evaluation = evaluate(proposal(), snapshot=snapshot([low], employee=employee(hourly_rate=Decimal("16.50"))))
        self.assertEqual(evaluation.results[0].result, "FAIL")
        self.assertFalse(evaluation.claims_legal_accuracy)

    # spec: F10.ac2
    def test_early_start_without_a_midway_break_fails(self):
        meal = rule(
            rule_id="NY-MEAL-EARLY-LATE-START", metric="break_minutes", operator=">=",
            value_num=Decimal("45"), value_text="start 13:00-06:00", unit="MINUTES",
            consequence="Shift >6 hrs starting 13:00-06:00 needs 45-min meal midway",
            verification_status="VERIFY",
        )
        start = datetime(2026, 11, 18, 5, 15, tzinfo=NY)
        end = start + timedelta(hours=6, minutes=45)
        failed = evaluate(proposal(start_at=start, end_at=end), snapshot=snapshot([meal]))
        self.assertEqual(failed.results[0].result, "FAIL")
        early = Break(start, start + timedelta(minutes=45))
        still = evaluate(proposal(start_at=start, end_at=end, breaks=[early]), snapshot=snapshot([meal]))
        self.assertEqual(still.results[0].result, "FAIL")
        mid = Break(start + timedelta(hours=3), start + timedelta(hours=3, minutes=45))
        passed = evaluate(proposal(start_at=start, end_at=end, breaks=[mid]), snapshot=snapshot([meal]))
        self.assertEqual(passed.results[0].result, "PASS")

    # spec: F10.ac2
    def test_call_in_and_overtime_premiums_are_priced(self):
        callin = rule(
            rule_id="NY-CALLIN-HOSP", enforcement="SOFT_COST", metric="hours_paid_min", operator=">=",
            value_num=Decimal("3"), value_text="lesser of 3 hrs or scheduled shift", unit="HOURS",
            consequence="Employee reporting and sent home paid min hours",
        )
        start = datetime(2026, 11, 18, 9, 0, tzinfo=NY)
        priced = evaluate(
            proposal(start_at=start, end_at=start + timedelta(hours=2), sent_home=True),
            snapshot=snapshot([callin], employee=employee(hourly_rate=Decimal("20"))),
        )
        self.assertEqual(priced.results[0].result, "PREMIUM")
        self.assertEqual(priced.results[0].premium_cost_usd, Decimal("40.00"))
        ot = rule(
            rule_id="NY-OT-WEEKLY", enforcement="SOFT_COST", metric="hours_per_week", operator="TRIGGER_WHEN_GT",
            value_num=Decimal("40"), unit="MULTIPLIER",
            consequence="Hours >40/wk paid at 1.5x regular rate",
        )
        extra = evaluate(
            proposal(start_at=start, end_at=start + timedelta(hours=42)),
            snapshot=snapshot([ot], employee=employee(hourly_rate=Decimal("20"))),
        )
        self.assertEqual(extra.results[0].premium_cost_usd, Decimal("20.00"))

    # spec: F10.ac12
    def test_prose_occupation_rule_raises_for_a_15_year_old(self):
        occupation = rule(
            rule_id="FED-MINOR-1415-OCCUPATION", jurisdiction="US-FED", applies_age_min=14, applies_age_max=15,
            metric="role_eligibility", operator=None, value_num=None,
            value_text="Restricted equipment/cooking tasks", unit="ROLE", verification_status="VERIFY",
        )
        person = employee(dob=date(2011, 4, 21), is_enrolled=True)
        day = date(2026, 11, 18)
        with self.assertRaises(ComplianceConfigError):
            evaluate(proposal(), snapshot=snapshot(
                [occupation], employee=person, days={day: DayFacts(True, False)},
            ))


class PostgresIsolation(unittest.TestCase):
    # spec: F10.ac11
    def test_wrong_tenant_is_not_a_legal_shift(self):
        start = datetime(2026, 11, 18, 9, 0, tzinfo=NY)
        item = proposal(start_at=start, end_at=start + timedelta(hours=6), tenant_id=str(uuid.uuid4()))
        with self.assertRaises(ComplianceUnavailable):
            evaluate(item, dsn=DSN)

    # spec: F10.ac10
    def test_persist_writes_rule_eval_log_under_rls(self):
        import psycopg
        sid, shid = str(uuid.uuid4()), str(uuid.uuid4())
        start = datetime(2026, 11, 18, 9, 0, tzinfo=NY)
        try:
            with psycopg.connect(DSN) as conn:
                conn.execute(
                    "INSERT INTO schedules (tenant_id, schedule_id, location_id, week_start, status) VALUES (%s,%s,5,%s,'test')",
                    (TENANT, sid, start.date()),
                )
                conn.execute(
                    """INSERT INTO shifts (tenant_id, shift_id, schedule_id, location_id, start_at, end_at, role)
                       VALUES (%s,%s,%s,5,%s,%s,'Barista')""",
                    (TENANT, shid, sid, start, start + timedelta(hours=6)),
                )
                conn.commit()
        except Exception as exc:
            self.skipTest(f"postgres unavailable: {exc}")
        try:
            item = proposal(
                role="Shift lead", start_at=start, end_at=start + timedelta(hours=6),
                week_start=start.date(), shift_id=shid, schedule_id=sid,
            )
            evaluation = evaluate(item, persist=True, dsn=DSN)
            self.assertTrue(any(row.rule_id == "NYC-FPC-COVERAGE" and row.result == "PASS" for row in evaluation.results))
            self.assertFalse(evaluation.claims_legal_accuracy)
            with psycopg.connect(DSN) as conn:
                count = conn.execute("SELECT count(*) FROM rule_eval_log WHERE shift_id = %s", (shid,)).fetchone()[0]
                coverage = [
                    {"rule_id": row[0], "description": row[1], "value": row[2], "basis": row[3]}
                    for row in conn.execute("SELECT rule_id, description, value, basis FROM coverage_rules")
                ]
                legal = [
                    rule(rule_id=row[0], enforcement=row[1], jurisdiction=row[2])
                    for row in conn.execute("SELECT rule_id, enforcement, jurisdiction FROM labor_rules")
                ]
            self.assertEqual(count, len(evaluation.results))
            reconcile_coverage(legal, coverage)
        finally:
            with psycopg.connect(DSN) as conn:
                conn.execute("DELETE FROM rule_eval_log WHERE shift_id = %s", (shid,))
                conn.execute("DELETE FROM shifts WHERE shift_id = %s", (shid,))
                conn.execute("DELETE FROM schedules WHERE schedule_id = %s", (sid,))
                conn.commit()


if __name__ == "__main__":
    unittest.main()
