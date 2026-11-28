"""Offline checks for CS-088 soft guaranteed weekly hours."""

from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ortools.sat.python import cp_model  # noqa: E402

from agents.scheduler.constraints import LegalityVerdict, SolverModelError, build_model, diagnose_infeasibility  # noqa: E402
from agents.scheduler.inputs import (  # noqa: E402
    CostTargetInput,
    CoverageRuleInput,
    EmployeeInput,
    ShiftRuleInput,
    SolverInputs,
    StoreInput,
    ThroughputInput,
)
from agents.scheduler.objective import ObjectiveWeights, build_objective  # noqa: E402
from agents.scheduler.shifts import ShiftSlot  # noqa: E402
from contracts.forecast import ForecastSet  # noqa: E402

MONDAY = date(2026, 12, 7)


def _inputs(employee: EmployeeInput, start: date, end: date) -> SolverInputs:
    rules = tuple(CoverageRuleInput(name, None, name, "0", "HEADCOUNT", "coverage") for name in
                  ("minimum crew", "COV-KEYHOLDER-OPEN", "COV-KEYHOLDER-CLOSE", "fpc", "peak skill", "break relief"))
    return SolverInputs(
        (StoreInput(3, "Test", 9, 9, {}),), (), (), rules +
        (CoverageRuleInput("COV-UTIL-TARGET", None, "utilisation", "1", "PERCENT", "coverage"),),
        (ShiftRuleInput("SR-MIN-LEN", "min", "2", "HOURS", "shift"),
         ShiftRuleInput("SR-MAX-LEN", "max", "8", "HOURS", "shift"),
         ShiftRuleInput("SR-MAX-CONSEC", "consecutive", "7", "DAYS", "shift"),
         ShiftRuleInput("SR-MIN-REST", "rest", "10", "HOURS", "shift")),
        (CostTargetInput(3, Decimal("0.30"), Decimal("500"), Decimal("1000"), Decimal("1000")),),
        (ThroughputInput("Coffee", "Barista", Decimal("3600"), Decimal("1")),), None, (employee,), (),
        ForecastSet("v", "tax", "test", datetime(2026, 12, 1, tzinfo=ZoneInfo("America/New_York")), ()),
        (start, end), "tax", frozenset(), frozenset({3}), frozenset(),
    )


def _employee(*, minimum: int, maximum: int = 40) -> EmployeeInput:
    return EmployeeInput(1, "Barista", 3, 3, False, True, True, True, Decimal("10"), "open", minimum, maximum,
                         date(1990, 1, 1), False, None, "", False)


def _slots(days: tuple[date, ...]) -> tuple[ShiftSlot, ...]:
    return tuple(ShiftSlot(3, day, Decimal("8"), Decimal("10")) for day in days)


def _solve(model):
    solver = cp_model.CpSolver()
    solver.parameters.random_seed = 0
    solver.parameters.num_search_workers = 1
    solver.parameters.max_time_in_seconds = 5
    return solver, solver.Solve(model.model)


class GuaranteedHoursTests(unittest.TestCase):
    # spec: F11.ac60
    def test_ac60_minor_shortfall_is_feasible_with_penalty(self):
        days = tuple(MONDAY + timedelta(days=offset) for offset in range(7))
        employee = _employee(minimum=16)
        model = build_model(_inputs(employee, days[0], days[-1]), slots=_slots(days[:3]), legality=lambda *_: LegalityVerdict(True))
        terms = build_objective(model, _inputs(employee, days[0], days[-1]),
                                weights=ObjectiveWeights(0, 0, 0, 0, 0, 2))
        solver, status = _solve(model)
        self.assertIn(status, (cp_model.OPTIMAL, cp_model.FEASIBLE))
        self.assertEqual(solver.Value(terms.guaranteed_hours_slack[(1, (2026, 50))]), 20)

    # spec: F11.ac61
    def test_ac61_higher_weight_books_more_guaranteed_hours(self):
        days = tuple(MONDAY + timedelta(days=offset) for offset in range(7))
        employee = _employee(minimum=8)
        inputs = _inputs(employee, days[0], days[-1])
        slots = _slots(days)
        low = build_model(inputs, slots=slots, legality=lambda *_: LegalityVerdict(True))
        build_objective(low, inputs, weights=ObjectiveWeights(1, 0, 0, 0, 0, 0))
        low_solver, low_status = _solve(low)
        high = build_model(inputs, slots=slots, legality=lambda *_: LegalityVerdict(True))
        build_objective(high, inputs, weights=ObjectiveWeights(1, 0, 0, 0, 0, 100))
        high_solver, high_status = _solve(high)
        self.assertIn(low_status, (cp_model.OPTIMAL, cp_model.FEASIBLE))
        self.assertIn(high_status, (cp_model.OPTIMAL, cp_model.FEASIBLE))
        self.assertLess(sum(low_solver.Value(var) for var in low.assign.values()), sum(high_solver.Value(var) for var in high.assign.values()))

    # spec: F11.ac62
    def test_ac62_zero_and_partial_minimums_create_no_slack(self):
        days = tuple(MONDAY + timedelta(days=offset) for offset in range(7))
        zero = _employee(minimum=0)
        zero_model = build_model(_inputs(zero, days[0], days[-1]), slots=_slots(days), legality=lambda *_: LegalityVerdict(True))
        zero_terms = build_objective(zero_model, _inputs(zero, days[0], days[-1]))
        self.assertEqual(zero_terms.guaranteed_hours_slack, {})
        partial = build_model(_inputs(_employee(minimum=8), days[1], days[-1]), slots=_slots(days[1:]), legality=lambda *_: LegalityVerdict(True))
        partial_terms = build_objective(partial, _inputs(_employee(minimum=8), days[1], days[-1]))
        self.assertEqual(partial.full_weeks, ())
        self.assertEqual(partial.skipped_minimums, ((1, (2026, 50)),))
        self.assertEqual(partial_terms.guaranteed_hours_slack, {})

    # spec: F11.ac63
    def test_ac63_invalid_minimum_and_hard_maximum_remain_hard(self):
        with self.assertRaisesRegex(SolverModelError, "min_hours_week 41 exceeds max_hours_week 40"):
            build_model(_inputs(_employee(minimum=41), MONDAY, MONDAY), slots=_slots((MONDAY,)))
        days = tuple(MONDAY + timedelta(days=offset) for offset in range(3))
        maximum = _employee(minimum=0, maximum=4)
        model = build_model(_inputs(maximum, days[0], days[-1]), slots=_slots(days), legality=lambda *_: LegalityVerdict(True))
        model.model.Add(sum(model.assign.values()) == 3)
        self.assertIn("shift_rules", diagnose_infeasibility(model))

    # spec: F11.ac64
    def test_ac64_fully_met_guarantee_has_zero_shortfall(self):
        days = tuple(MONDAY + timedelta(days=offset) for offset in range(7))
        employee = _employee(minimum=6)
        inputs = _inputs(employee, days[0], days[-1])
        model = build_model(inputs, slots=_slots(days[:3]), legality=lambda *_: LegalityVerdict(True))
        model.model.Add(sum(model.assign.values()) == 3)
        terms = build_objective(model, inputs)
        solver, status = _solve(model)
        self.assertIn(status, (cp_model.OPTIMAL, cp_model.FEASIBLE))
        self.assertEqual(solver.Value(terms.guaranteed_hours_slack[(1, (2026, 50))]), 0)


if __name__ == "__main__":
    unittest.main()
