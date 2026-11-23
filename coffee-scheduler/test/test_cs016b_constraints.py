"""Offline and rollback-gated checks for CS-016b hard constraints."""

from __future__ import annotations

import os
import sys
import unittest
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ortools.sat.python import cp_model  # noqa: E402

from agents.scheduler.constraints import (  # noqa: E402
    PER_ASSIGNMENT_METRICS,
    LegalityVerdict,
    SolverModelError,
    assert_feasible,
    build_model,
    diagnose_infeasibility,
    legality_from_compliance,
)
from agents.scheduler.inputs import (  # noqa: E402
    CoverageRuleInput,
    EmployeeInput,
    ShiftRuleInput,
    SolverInputs,
    StoreInput,
)
from agents.scheduler.shifts import ShiftSlot, build_slot_grid  # noqa: E402
from compliance.engine import ComplianceUnavailable  # noqa: E402
from contracts.forecast import ForecastRecord, ForecastSet, StoreOpenWindow  # noqa: E402


def _allow_all(*_args, **_kwargs) -> LegalityVerdict:
    """Legality stub that permits every candidate assignment."""
    return LegalityVerdict(True)

DAY = date(2026, 11, 23)


def _inputs(*, crew: int = 1, break_relief: int = 1, station_count: int = 2, employees=()) -> SolverInputs:
    demand = ForecastSet(
        "forecast-output-v1", "TAX-2023H1-v1", "test", datetime(2026, 11, 1, tzinfo=ZoneInfo("America/New_York")),
        (ForecastRecord(3, "Coffee", datetime(2026, 11, 23, 8, tzinfo=ZoneInfo("America/New_York")), Decimal("1"), Decimal("1"), Decimal("1"), "test", "TAX-2023H1-v1"),),
    )
    people = employees or tuple(
        EmployeeInput(index, "Barista", 3, 3, False, True, True, True, Decimal("16"), "open", 0, 20, date(1990, 1, 1), False, None, "", False)
        for index in (1, 2)
    )
    return SolverInputs(
        (StoreInput(3, "Test", 2, station_count, {0: StoreOpenWindow(8, 10, frozenset({0}))}),), (), (),
        (
            CoverageRuleInput("minimum crew", None, "minimum crew", str(crew), "HEADCOUNT", "coverage"),
            CoverageRuleInput("key holder", None, "key holder", "1", "HEADCOUNT", "coverage"),
            CoverageRuleInput("fpc", None, "fpc", "1", "HEADCOUNT", "coverage"),
            CoverageRuleInput("peak skill", None, "peak skill", "1", "HEADCOUNT", "peak"),
            CoverageRuleInput("break relief", None, "break relief", str(break_relief), "HEADCOUNT", "coverage"),
        ),
        (
            ShiftRuleInput("SR-MIN-LEN", "min shift", "2", "HOURS", "shift"),
            ShiftRuleInput("SR-MAX-LEN", "max shift", "8", "HOURS", "shift"),
            ShiftRuleInput("SR-MAX-CONSEC", "max consecutive", "5", "DAYS", "shift"),
            ShiftRuleInput("SR-MIN-REST", "rest", "10", "HOURS", "shift"),
        ),
        (), (), None, people, (), demand, (DAY, DAY), "TAX-2023H1-v1", frozenset({"Coffee"}), frozenset({3}), frozenset(),
    )


def _solve(model):
    solver = cp_model.CpSolver()
    solver.parameters.random_seed = 0
    solver.parameters.num_search_workers = 1
    return solver.Solve(model.model)


class ConstraintTests(unittest.TestCase):
    # spec: F11.ac8
    def test_ac8_legality_exclusion(self):
        inputs = _inputs()
        slots = (ShiftSlot(3, DAY, Decimal("8"), Decimal("10")),)
        def legality(employee, _):
            return LegalityVerdict(False, (("LAW-1", "blocked"),)) if employee.employee_id == 1 else LegalityVerdict(True)

        model = build_model(inputs, slots=slots, legality=legality)
        self.assertEqual(_solve(model), cp_model.OPTIMAL)
        self.assertEqual(model.excluded[(1, 0)].binding, (("LAW-1", "blocked"),))

    # spec: F11.ac9
    def test_ac9_coverage_groups(self):
        slots = (ShiftSlot(3, DAY, Decimal("8"), Decimal("10")),)
        base = _inputs()
        cases = (
            ({"crew": 3}, "coverage_min_crew"),
            ({"break_relief": 3}, "coverage_break_relief"),
            ({"employees": tuple(replace(employee, is_key_holder=False) for employee in base.employees)}, "coverage_key_holder"),
            ({"employees": tuple(replace(employee, food_protection_cert=False) for employee in base.employees)}, "coverage_fpc"),
            ({"employees": tuple(replace(employee, skill_level=2) for employee in base.employees)}, "coverage_peak_skill"),
        )
        for changed, group in cases:
            model = build_model(_inputs(**changed), slots=slots)
            self.assertEqual(_solve(model), cp_model.INFEASIBLE)
            self.assertIn(group, diagnose_infeasibility(model))

    # spec: F11.ac10
    def test_ac10_station_capacity(self):
        slots = (ShiftSlot(3, DAY, Decimal("8"), Decimal("10")),)
        model = build_model(_inputs(crew=2, station_count=1), slots=slots)
        self.assertEqual(_solve(model), cp_model.INFEASIBLE)
        self.assertIn("station_capacity", diagnose_infeasibility(model))

    # spec: F11.ac11
    def test_ac11_shift_rules_fail_closed(self):
        bad = replace(_inputs(), shift_rules=(replace(_inputs().shift_rules[0], value="hours"),))
        with self.assertRaisesRegex(SolverModelError, "SR-MIN-LEN"):
            build_slot_grid(bad)
        slots = (ShiftSlot(3, DAY, Decimal("8"), Decimal("10")),)
        short = replace(_inputs(), employees=(replace(_inputs().employees[0], min_hours_week=3),))
        short = replace(short, target_window=(DAY, DAY.fromordinal(DAY.toordinal() + 6)))
        model = build_model(short, slots=slots)
        self.assertIn("shift_rules", diagnose_infeasibility(model))

    # spec: F11.ac11
    def test_ac11_max_consecutive_days_is_enforced(self):
        employee = replace(_inputs().employees[0], min_hours_week=24, max_hours_week=40)
        days = tuple(DAY.fromordinal(DAY.toordinal() + offset) for offset in range(6))
        inputs = replace(_inputs(employees=(employee,)), target_window=(days[0], days[-1]))
        slots = tuple(ShiftSlot(3, day, Decimal("8"), Decimal("12")) for day in days)

        model = build_model(inputs, slots=slots)
        model.model.Add(sum(model.assign.values()) == len(slots))

        self.assertEqual(_solve(model), cp_model.INFEASIBLE)
        self.assertIn("shift_rules", diagnose_infeasibility(model))

    # spec: F11.ac11
    def test_ac11_minimum_rest_is_enforced(self):
        employee = replace(_inputs().employees[0], min_hours_week=8, max_hours_week=20)
        later_day = DAY.fromordinal(DAY.toordinal() + 1)
        inputs = replace(
            _inputs(employees=(employee,)),
            stores=(StoreInput(3, "Test", 2, 2, {}),),
            target_window=(DAY, later_day),
        )
        slots = (
            ShiftSlot(3, DAY, Decimal("18"), Decimal("22")),
            ShiftSlot(3, later_day, Decimal("6"), Decimal("10")),
        )

        model = build_model(inputs, slots=slots)
        model.model.Add(sum(model.assign.values()) == len(slots))

        self.assertEqual(_solve(model), cp_model.INFEASIBLE)
        self.assertIn("shift_rules", diagnose_infeasibility(model))

    # spec: F11.ac12
    def test_ac12_home_location(self):
        inputs = _inputs(employees=(replace(_inputs().employees[0], home_location_id=5),))
        slots = (ShiftSlot(3, DAY, Decimal("8"), Decimal("10")),)
        model = build_model(inputs, slots=slots)
        self.assertEqual(_solve(model), cp_model.INFEASIBLE)
        self.assertIn("home_location", diagnose_infeasibility(model))

    # spec: F11.ac13
    def test_ac13_diagnoses_assumption_core(self):
        model = build_model(_inputs(crew=3), slots=(ShiftSlot(3, DAY, Decimal("8"), Decimal("10")),))
        with self.assertRaisesRegex(Exception, "coverage_min_crew"):
            assert_feasible(model)

    # spec: F11.ac14
    def test_ac14_model_has_no_objective(self):
        model = build_model(_inputs(), slots=(ShiftSlot(3, DAY, Decimal("8"), Decimal("10")),))
        self.assertFalse(model.model.Proto().objective.vars)


DSN = os.environ.get("DATABASE_URL", "postgresql://coffeeshop:coffeeshop@localhost:5432/coffeeshop")
_SKIP = None
try:
    import psycopg

    with psycopg.connect(DSN, connect_timeout=3) as connection:
        connection.execute("SELECT 1")
except Exception as exc:  # pragma: no cover
    _SKIP = f"psycopg or database unavailable: {exc}"


class _Rollback(Exception):
    pass


@unittest.skipIf(_SKIP, _SKIP or "")
class DatabaseConstraintTests(unittest.TestCase):
    # spec: F11.ac8
    def test_ac8_real_inputs_build_with_compliance_adapter(self):
        from datetime import date

        from agents.demand_forecast.feature_build import build_feature_set
        from agents.demand_forecast.model import persist_forecasts, predict, train
        from agents.scheduler.inputs import assemble_solver_inputs
        from data_access.session import open_session_context

        version = "test-cs016b"
        # Nothing seeds the `forecasts` table — the ETL never writes it and forecasts are only
        # produced inside rollback-only tests. So this test creates its own demand rather than
        # skipping on an empty table, which would make it permanently inert.
        target = (date(2023, 6, 24), date(2023, 6, 25))
        try:
            with psycopg.connect(DSN) as conn, conn.transaction():
                open_session_context(conn, role="app_rw", tenant_id="00000000-0000-0000-0000-000000000001")
                build_feature_set(conn, feature_version=version)
                train(conn, model_version=version, feature_version=version,
                      window=(date(2023, 1, 1), date(2023, 6, 2)))
                forecasts = predict(conn, version, target_window=target)
                self.assertEqual(persist_forecasts(conn, forecasts), len(forecasts.records))

                inputs = assemble_solver_inputs(conn, target_window=target, model_version=version)
                self.assertGreater(len(inputs.demand.records), 0)
                self.assertGreater(len(inputs.employees), 0)

                # The real-data model builds and carries real decision variables.
                model = build_model(inputs, legality=_allow_all)
                self.assertGreater(len(model.assign), 0)
                self.assertEqual(model.excluded, {})

                # The live compliance adapter FAILS CLOSED on this window, and that is correct:
                # every enrolled employee's school_calendar_id is NYC-DOE-2026-27 (2026-09-10..
                # 2027-06-28) while all demand is 2023-H1, so there is no school_calendar_days row
                # for a 2023 shift and the engine refuses to guess a minor's school status.
                # Tracked as a roster/demand calendar-alignment gap; CS-016 cannot produce a
                # legality-evaluated schedule on the seeded data until the roster points at the
                # calendar covering the scheduling window.
                with self.assertRaises(ComplianceUnavailable) as caught:
                    build_model(inputs, legality=legality_from_compliance(DSN, inputs))
                self.assertIn("school_calendar_days", str(caught.exception))
                raise _Rollback()
        except _Rollback:
            pass


class LegalityPartitionTests(unittest.TestCase):
    """BUG-071: a rule is either decidable per assignment or enforced in the model."""

    # spec: F11.ac15 — only per-assignment-decidable metrics are pre-computed.
    def test_ac15_partition_excludes_schedule_crew_and_break_metrics(self):
        self.assertEqual(
            PER_ASSIGNMENT_METRICS,
            frozenset({"role_eligibility", "work_window", "hours_per_day", "span_hours", "rate"}),
        )
        # Schedule-scoped: depends on which other assignments are chosen (CP-SAT shift_rules).
        for metric in ("hours_per_week", "days_per_week", "rest_hours_between_shifts"):
            self.assertNotIn(metric, PER_ASSIGNMENT_METRICS)
        # Crew-scoped: depends on who else is on site (CP-SAT coverage_* groups).
        self.assertNotIn("certified_staff_on_site", PER_ASSIGNMENT_METRICS)
        # Break-dependent, and call-out/posting rules that belong to other flows.
        for metric in ("break_minutes", "hours_paid_min", "process", "premium_amount", "advance_notice_days"):
            self.assertNotIn(metric, PER_ASSIGNMENT_METRICS)


@unittest.skipIf(_SKIP, _SKIP or "")
class LegalityPartitionDatabaseTests(unittest.TestCase):
    # spec: F11.ac15 — a crew-scoped rule must never exclude a candidate assignment.
    def test_ac15_crew_rules_do_not_exclude_candidates(self):
        from datetime import date

        from agents.demand_forecast.feature_build import build_feature_set
        from agents.demand_forecast.inference import build_inference_rows
        from agents.demand_forecast.model import persist_forecasts, predict, train
        from agents.scheduler.inputs import assemble_solver_inputs
        from data_access.session import open_session_context

        version = "test-bug071"
        # A forward window, so the roster is valid for its own era (see BUG-069). One school day
        # is enough to exercise the partition, and keeps the per-candidate legality cost that
        # BUG-073 tracks off the CI critical path.
        target = (date(2026, 12, 7), date(2026, 12, 7))
        try:
            with psycopg.connect(DSN) as conn, conn.transaction():
                open_session_context(conn, role="app_rw", tenant_id="00000000-0000-0000-0000-000000000001")
                build_feature_set(conn, feature_version=version)
                train(conn, model_version=version, feature_version=version,
                      window=(date(2023, 1, 1), date(2023, 6, 2)))
                rows = build_inference_rows(conn, window=target, history_feature_version=version)
                persist_forecasts(conn, predict(conn, version, target_window=target, rows=rows))
                inputs = assemble_solver_inputs(conn, target_window=target, model_version=version)

                model = build_model(inputs, legality=legality_from_compliance(DSN, inputs))
                bound = {rule_id for verdict in model.excluded.values() for rule_id, _ in verdict.binding}
                # The defect: FPC-on-site blocked every candidate because the adapter claimed a
                # complete-but-empty crew. Crew coverage is a model constraint, never an exclusion.
                self.assertNotIn("NYC-FPC-COVERAGE", bound)
                # And the model must retain genuinely legal candidates to choose from.
                self.assertGreater(len(model.assign) - len(model.excluded), 0)
                # What does bind is real per-assignment minor law.
                self.assertTrue(bound, "expected minor-law exclusions on a school week")
                self.assertTrue(all(rule_id.startswith(("NY-MINOR", "FED-MINOR")) for rule_id in bound), bound)
                raise _Rollback()
        except _Rollback:
            pass


if __name__ == "__main__":
    unittest.main()
