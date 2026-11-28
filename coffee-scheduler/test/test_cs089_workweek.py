"""Offline checks for CS-089's authoritative ISO workweek."""

from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ortools.sat.python import cp_model  # noqa: E402

import agents.scheduler.constraints as constraints  # noqa: E402
from agents.scheduler.constraints import LegalityVerdict, build_model, diagnose_infeasibility  # noqa: E402
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
from agents.scheduler.shifts import ShiftSlot, workweek, workweek_days  # noqa: E402
from compliance.engine import Employee, Evaluation, Snapshot  # noqa: E402
from contracts.forecast import ForecastSet  # noqa: E402

MONDAY = date(2026, 12, 7)


def _employee(maximum: int = 100) -> EmployeeInput:
    return EmployeeInput(1, "Barista", 3, 3, False, True, True, True, Decimal("10"), "open", 0, maximum,
                         date(1990, 1, 1), False, None, "", False)


def _inputs(start: date, end: date, *, maximum: int = 100) -> SolverInputs:
    rules = tuple(CoverageRuleInput(name, None, name, "0", "HEADCOUNT", "coverage") for name in
                  ("minimum crew", "key holder", "fpc", "peak skill", "break relief"))
    return SolverInputs(
        (StoreInput(3, "Test", 9, 9, {}),), (), (), rules +
        (CoverageRuleInput("COV-UTIL-TARGET", None, "utilisation", "1", "PERCENT", "coverage"),),
        (ShiftRuleInput("SR-MIN-LEN", "min", "2", "HOURS", "shift"),
         ShiftRuleInput("SR-MAX-LEN", "max", "8", "HOURS", "shift"),
         ShiftRuleInput("SR-MAX-CONSEC", "consecutive", "7", "DAYS", "shift"),
         ShiftRuleInput("SR-MIN-REST", "rest", "10", "HOURS", "shift")),
        (CostTargetInput(3, Decimal("0.30"), Decimal("500"), Decimal("1000"), Decimal("1000")),),
        (ThroughputInput("Coffee", "Barista", Decimal("3600"), Decimal("1")),), None, (_employee(maximum),), (),
        ForecastSet("v", "tax", "test", datetime(2026, 12, 1, tzinfo=ZoneInfo("America/New_York")), ()),
        (start, end), "tax", frozenset(), frozenset({3}), frozenset(),
    )


def _solve(model):
    solver = cp_model.CpSolver()
    solver.parameters.random_seed = 0
    solver.parameters.num_search_workers = 1
    solver.parameters.max_time_in_seconds = 5
    return solver, solver.Solve(model.model)


def _snapshot() -> Snapshot:
    return Snapshot("tenant", "America/New_York", (), {}, Employee(1, date(1990, 1, 1), Decimal("10"), False, None, True),
                    (), (), {}, {}, None)


class WorkweekTests(unittest.TestCase):
    # spec: F11.ac65
    def test_ac65_helper_uses_iso_monday_boundaries(self):
        self.assertEqual(workweek(MONDAY), (2026, 50))
        self.assertEqual(workweek(MONDAY - timedelta(days=1)), (2026, 49))
        self.assertEqual(workweek_days((MONDAY - timedelta(days=1), MONDAY)), {(2026, 49): (MONDAY - timedelta(days=1),),
                                                                                (2026, 50): (MONDAY,)})

    # spec: F11.ac66
    def test_ac66_overtime_is_per_week_not_window(self):
        days = tuple(MONDAY + timedelta(days=offset) for offset in range(12) if offset % 7 < 5)
        inputs = _inputs(days[0], days[-1])
        slots = tuple(ShiftSlot(3, day, Decimal("8"), Decimal("14")) for day in days)
        model = build_model(inputs, slots=slots, legality=lambda *_: LegalityVerdict(True))
        model.model.Add(sum(model.assign.values()) == len(slots))
        terms = build_objective(model, inputs, weights=ObjectiveWeights(0, 0, 1, 0, 0, 0))
        solver, status = _solve(model)
        self.assertIn(status, (cp_model.OPTIMAL, cp_model.FEASIBLE))
        self.assertEqual(solver.Value(terms.overtime_slack[1]), 0)

    # spec: F11.ac67
    def test_ac67_non_monday_window_spans_two_iso_weeks(self):
        start = MONDAY + timedelta(days=2)
        days = tuple(start + timedelta(days=offset) for offset in range(7))
        inputs = _inputs(days[0], days[-1], maximum=6)
        slots = tuple(ShiftSlot(3, day, Decimal("8"), Decimal("10")) for day in days)
        model = build_model(inputs, slots=slots, legality=lambda *_: LegalityVerdict(True))
        self.assertEqual(model.full_weeks, ())
        model.model.Add(sum(model.assign[(1, index)] for index in range(5)) == 5)
        self.assertIn("shift_rules", diagnose_infeasibility(model))

    # spec: F11.ac68
    def test_ac68_compliance_receives_slot_workweek_monday(self):
        inputs = _inputs(MONDAY + timedelta(days=2), MONDAY + timedelta(days=2))
        captured = []

        def judge(_snapshot, proposal):
            captured.append(proposal.week_start)
            return Evaluation((), (), True)

        with patch.object(constraints, "load_snapshot", return_value=_snapshot()), patch.object(constraints, "judge", side_effect=judge):
            verdict = constraints.legality_from_compliance("dsn", inputs)(
                inputs.employees[0], ShiftSlot(3, MONDAY + timedelta(days=2), Decimal("8"), Decimal("10"))
            )
        self.assertTrue(verdict.allowed)
        self.assertEqual(captured, [MONDAY])

    # spec: F11.ac69
    def test_ac69_monday_sunday_overtime_is_unchanged(self):
        days = tuple(MONDAY + timedelta(days=offset) for offset in range(7))
        inputs = _inputs(days[0], days[-1])
        slots = tuple(ShiftSlot(3, day, Decimal("8"), Decimal("14")) for day in days)
        model = build_model(inputs, slots=slots, legality=lambda *_: LegalityVerdict(True))
        model.model.Add(sum(model.assign.values()) == 7)
        terms = build_objective(model, inputs, weights=ObjectiveWeights(0, 0, 1, 0, 0, 0))
        solver, status = _solve(model)
        self.assertIn(status, (cp_model.OPTIMAL, cp_model.FEASIBLE))
        self.assertEqual(solver.Value(terms.overtime_slack[1]), 4)


if __name__ == "__main__":
    unittest.main()
