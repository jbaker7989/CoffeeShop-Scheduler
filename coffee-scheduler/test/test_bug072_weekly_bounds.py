"""Offline regression coverage for BUG-072 ISO-week hour bounds."""

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

from agents.scheduler.constraints import SolverModelError, build_model, diagnose_infeasibility  # noqa: E402
from agents.scheduler.inputs import CoverageRuleInput, EmployeeInput, ShiftRuleInput, SolverInputs, StoreInput  # noqa: E402
from agents.scheduler.shifts import ShiftSlot  # noqa: E402
from contracts.forecast import ForecastRecord, ForecastSet  # noqa: E402

MONDAY = date(2026, 12, 7)


def _inputs(employee: EmployeeInput, start: date, end: date) -> SolverInputs:
    demand = ForecastSet(
        "forecast-output-v1", "TAX-2023H1-v1", "test", datetime(2026, 12, 1, tzinfo=ZoneInfo("America/New_York")),
        (ForecastRecord(3, "Coffee", datetime(2026, 12, 7, 8, tzinfo=ZoneInfo("America/New_York")), Decimal("1"),
                        Decimal("1"), Decimal("1"), "test", "TAX-2023H1-v1"),),
    )
    return SolverInputs(
        (StoreInput(3, "Test", 2, 2, {}),), (), (),
        (
            CoverageRuleInput("minimum crew", None, "minimum crew", "1", "HEADCOUNT", "coverage"),
            CoverageRuleInput("key holder", None, "key holder", "1", "HEADCOUNT", "coverage"),
            CoverageRuleInput("fpc", None, "fpc", "1", "HEADCOUNT", "coverage"),
            CoverageRuleInput("peak skill", None, "peak skill", "1", "HEADCOUNT", "peak"),
            CoverageRuleInput("break relief", None, "break relief", "1", "HEADCOUNT", "coverage"),
        ),
        (
            ShiftRuleInput("SR-MIN-LEN", "min shift", "2", "HOURS", "shift"),
            ShiftRuleInput("SR-MAX-LEN", "max shift", "8", "HOURS", "shift"),
            ShiftRuleInput("SR-MAX-CONSEC", "max consecutive", "5", "DAYS", "shift"),
            ShiftRuleInput("SR-MIN-REST", "rest", "10", "HOURS", "shift"),
        ),
        (), (), None, (employee,), (), demand, (start, end), "TAX-2023H1-v1", frozenset({"Coffee"}),
        frozenset({3}), frozenset(),
    )


def _employee(*, minimum: int, maximum: int) -> EmployeeInput:
    return EmployeeInput(1, "Barista", 3, 3, False, True, True, True, Decimal("16"), "open", minimum, maximum,
                         date(1990, 1, 1), False, None, "", False)


def _slots(days: tuple[date, ...]) -> tuple[ShiftSlot, ...]:
    return tuple(ShiftSlot(3, day, Decimal("8"), Decimal("16")) for day in days)


def _solve(model) -> int:
    solver = cp_model.CpSolver()
    solver.parameters.random_seed = 0
    solver.parameters.num_search_workers = 1
    return solver.Solve(model.model)


class WeeklyBoundsTests(unittest.TestCase):
    # spec: F11.ac23
    def test_ac23_subweek_skips_minimum_and_keeps_full_maximum(self):
        days = (MONDAY, MONDAY + timedelta(days=1))
        model = build_model(_inputs(_employee(minimum=32, maximum=40), days[0], days[-1]), slots=_slots(days))
        self.assertEqual(model.skipped_minimums, ((1, (2026, 50)),))
        self.assertEqual(_solve(model), cp_model.OPTIMAL)

    # spec: F11.ac23
    def test_ac23_fourteen_days_are_not_capped_at_one_week_maximum(self):
        days = tuple(MONDAY + timedelta(days=offset) for offset in range(14))
        workdays = days[:5] + days[7:12]
        model = build_model(_inputs(_employee(minimum=0, maximum=40), days[0], days[-1]), slots=_slots(workdays))
        model.model.Add(sum(model.assign.values()) == 10)
        self.assertEqual(_solve(model), cp_model.OPTIMAL)

    # spec: F11.ac24
    def test_ac24_monday_aligned_week_applies_minimum_once(self):
        days = tuple(MONDAY + timedelta(days=offset) for offset in range(7))
        model = build_model(_inputs(_employee(minimum=32, maximum=40), days[0], days[-1]), slots=_slots(days[:3]))
        self.assertEqual(model.skipped_minimums, ())
        self.assertIn("shift_rules", diagnose_infeasibility(model))

    # spec: F11.ac24
    def test_ac24_unaligned_week_skips_both_partial_iso_weeks(self):
        start = MONDAY + timedelta(days=1)
        days = tuple(start + timedelta(days=offset) for offset in range(7))
        model = build_model(_inputs(_employee(minimum=32, maximum=40), days[0], days[-1]), slots=_slots(days[:3]))
        self.assertEqual(model.skipped_minimums, ((1, (2026, 50)), (1, (2026, 51))))
        self.assertEqual(_solve(model), cp_model.OPTIMAL)

    # spec: F11.ac25
    def test_ac25_zero_minimum_adds_no_skip_or_minimum_constraint(self):
        days = (MONDAY, MONDAY + timedelta(days=1))
        model = build_model(_inputs(_employee(minimum=0, maximum=8), days[0], days[-1]), slots=_slots(days))
        self.assertEqual(model.skipped_minimums, ())
        model.model.Add(sum(model.assign.values()) == 2)
        self.assertIn("shift_rules", diagnose_infeasibility(model))

    # spec: F11.ac26
    def test_ac26_contradictory_weekly_bounds_name_employee(self):
        with self.assertRaisesRegex(SolverModelError, "employee 1.*min_hours_week 41 exceeds max_hours_week 40"):
            build_model(_inputs(_employee(minimum=41, maximum=40), MONDAY, MONDAY), slots=_slots((MONDAY,)))


class SlotWindowGuardTests(unittest.TestCase):
    # spec: F11.ac26 — a slot dated outside the target window escapes every hour bound.
    def test_ac26_slots_outside_target_window_are_refused(self):
        days = tuple(date(2026, 12, 7) + timedelta(days=offset) for offset in range(7))
        inputs = _inputs(_employee(minimum=0, maximum=40), days[0], days[-1])
        stray = ShiftSlot(3, days[-1] + timedelta(days=30), Decimal("8"), Decimal("16"))
        # The per-day, consecutive-day and per-week constraints all key off the target window, so
        # a stray slot would sit in none of them and be unconstrained by any hour limit.
        with self.assertRaises(SolverModelError) as caught:
            build_model(inputs, slots=(*_slots(days), stray))
        self.assertIn("outside target window", str(caught.exception))
        # The in-window grid still builds, so the guard rejects only the genuinely stray slot.
        self.assertTrue(build_model(inputs, slots=_slots(days)).assign)


if __name__ == "__main__":
    unittest.main()
