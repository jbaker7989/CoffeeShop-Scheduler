"""Offline regression checks for CS-094 open-store-hour coverage."""

from __future__ import annotations

import sys
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ortools.sat.python import cp_model  # noqa: E402

from agents.scheduler.constraints import LegalityVerdict, build_model, diagnose_infeasibility  # noqa: E402
from agents.scheduler.inputs import CoverageRuleInput, EmployeeInput, ShiftRuleInput, SolverInputs, StoreInput  # noqa: E402
from agents.scheduler.shifts import ShiftSlot, SolverModelError  # noqa: E402
from contracts.forecast import ForecastSet, StoreOpenWindow  # noqa: E402

DAY = date(2026, 11, 23)
STORE_ID = 3


def _allow(*_args) -> LegalityVerdict:
    return LegalityVerdict(True)


def _inputs(
    *,
    opening: int = 8,
    closing: int = 10,
    weekdays: frozenset[int] = frozenset({0}),
    minimum_shift: str = "4",
    crew: int = 0,
    key_holder: int = 0,
    fpc: int = 0,
    peak_skill: int = 0,
    break_relief: int | None = None,
    employees: tuple[EmployeeInput, ...] | None = None,
) -> SolverInputs:
    people = employees or (
        EmployeeInput(1, "Barista", 3, STORE_ID, False, True, True, True, Decimal("16"), "open", 0, 40,
                      date(1990, 1, 1), False, None, "", False),
        EmployeeInput(2, "Barista", 3, STORE_ID, False, True, True, True, Decimal("17"), "open", 0, 40,
                      date(1990, 1, 1), False, None, "", False),
    )
    break_rule = CoverageRuleInput("COV-BREAK-RELIEF", None, "break relief", "", "FLAG", "coverage")
    if break_relief is not None:
        break_rule = CoverageRuleInput(
            "COV-BREAK-RELIEF", None, "break relief", str(break_relief), "HEADCOUNT", "coverage"
        )
    rules = (
        CoverageRuleInput("COV-MIN-CREW", None, "minimum crew", str(crew), "HEADCOUNT", "coverage"),
        CoverageRuleInput("COV-KEY-HOLDER", None, "key holder", str(key_holder), "HEADCOUNT", "coverage"),
        CoverageRuleInput("COV-FPC", None, "fpc", str(fpc), "HEADCOUNT", "coverage"),
        CoverageRuleInput("COV-PEAK-SKILL", None, "peak skill", str(peak_skill), "HEADCOUNT", "coverage"),
        break_rule,
    )
    demand = ForecastSet("forecast-output-v1", "tax", "test", datetime(2026, 11, 1, tzinfo=ZoneInfo("America/New_York")), ())
    return SolverInputs(
        (StoreInput(STORE_ID, "Test", 4, 4, {0: StoreOpenWindow(opening, closing, weekdays)}),), (), (), rules,
        (
            ShiftRuleInput("SR-MIN-LEN", "minimum shift", minimum_shift, "HOURS", "shift"),
            ShiftRuleInput("SR-MAX-LEN", "maximum shift", "8", "HOURS", "shift"),
            ShiftRuleInput("SR-MAX-CONSEC", "maximum consecutive", "5", "DAYS", "shift"),
            ShiftRuleInput("SR-MIN-REST", "minimum rest", "10", "HOURS", "shift"),
        ),
        (), (), None, people, (), demand, (DAY, DAY), "tax", frozenset({"Coffee"}), frozenset({STORE_ID}), frozenset(),
    )


def _solve(model) -> int:
    solver = cp_model.CpSolver()
    solver.parameters.random_seed = 0
    solver.parameters.num_search_workers = 1
    return solver.Solve(model.model)


class OpenHourCoverageTests(unittest.TestCase):
    # spec: F11.ac34
    def test_ac34_whole_day_empty_grid_raises_for_minimum_crew(self):
        with self.assertRaisesRegex(SolverModelError, r"store 3 / day 2026-11-23 / hour 8 / rule COV-MIN-CREW"):
            build_model(_inputs(crew=2), legality=_allow)

    # spec: F11.ac35
    def test_ac35_partial_day_gap_in_caller_slots_raises(self):
        slots = (ShiftSlot(STORE_ID, DAY, Decimal("8"), Decimal("9")),)
        with self.assertRaisesRegex(SolverModelError, r"hour 9 / rule COV-MIN-CREW"):
            build_model(_inputs(minimum_shift="1", crew=1), slots=slots, legality=_allow)

    # spec: F11.ac36
    def test_ac36_open_weekday_mismatch_is_closed_for_coverage(self):
        model = build_model(_inputs(crew=2, weekdays=frozenset({1})), legality=_allow)
        self.assertEqual(model.slots, ())

    # spec: F11.ac37
    def test_ac37_degenerate_open_window_raises(self):
        with self.assertRaisesRegex(SolverModelError, r"store 3 weekday 0.*close_hour 8.*open_hour 8"):
            build_model(_inputs(opening=8, closing=8, crew=1), legality=_allow)

    # spec: F11.ac38
    def test_ac38_sub_hour_slot_does_not_cover_an_integer_hour(self):
        slots = (ShiftSlot(STORE_ID, DAY, Decimal("8"), Decimal("8.5")),)
        with self.assertRaisesRegex(SolverModelError, r"hour 8 / rule COV-MIN-CREW"):
            build_model(_inputs(closing=9, minimum_shift="0.5", crew=1), slots=slots, legality=_allow)

    # spec: F11.ac39
    def test_ac39_role_shortfall_remains_coverage_group_infeasibility(self):
        employees = (
            EmployeeInput(1, "Barista", 3, STORE_ID, False, False, True, True, Decimal("16"), "open", 0, 40,
                          date(1990, 1, 1), False, None, "", False),
        )
        model = build_model(
            _inputs(key_holder=1, employees=employees),
            slots=(ShiftSlot(STORE_ID, DAY, Decimal("8"), Decimal("10")),),
            legality=_allow,
        )
        self.assertEqual(_solve(model), cp_model.INFEASIBLE)
        self.assertIn("coverage_key_holder", diagnose_infeasibility(model))

    # spec: F11.ac40
    def test_ac40_fully_coverable_four_hour_window_builds(self):
        model = build_model(_inputs(opening=6, closing=21, crew=1), legality=_allow)
        self.assertTrue(model.slots)


if __name__ == "__main__":
    unittest.main()
