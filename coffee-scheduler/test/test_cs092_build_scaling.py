"""Offline equivalence and indexing checks for CS-092."""

from __future__ import annotations

import hashlib
import sys
import unittest
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ortools.sat.python import cp_model  # noqa: E402

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
from agents.scheduler.objective import build_objective  # noqa: E402
from agents.scheduler.shifts import (  # noqa: E402
    ShiftSlot,
    SolverModelError,
    build_slot_grid,
    build_slot_index,
)
from contracts.forecast import ForecastRecord, ForecastSet, StoreOpenWindow  # noqa: E402

DAY = date(2026, 12, 7)


def _allow(*_args):
    return LegalityVerdict(True)


def _inputs(days: int = 2, employees: int = 3, rest: str = "10") -> SolverInputs:
    dates = tuple(DAY + timedelta(days=offset) for offset in range(days))
    windows = {weekday: StoreOpenWindow(8, 18, frozenset({weekday})) for weekday in range(5)}
    people = tuple(EmployeeInput(number, "Barista", 3, 3, False, True, True, True, Decimal(10 + number), "open", 0,
                                 40, date(1990, 1, 1), False, None, "", False) for number in range(1, employees + 1))
    forecast = ForecastSet("v", "tax", "test", datetime(2026, 12, 1, tzinfo=ZoneInfo("America/New_York")),
                           (ForecastRecord(3, "Coffee", datetime(2026, 12, 7, 9, tzinfo=ZoneInfo("America/New_York")),
                                           Decimal(2), Decimal(2), Decimal(2), "test", "tax"),))
    rules = tuple(CoverageRuleInput(rule, None, rule, "1", "HEADCOUNT", "coverage") for rule in
                  ("minimum crew", "key holder", "fpc", "peak skill", "break relief"))
    return SolverInputs((StoreInput(3, "Test", 2, 2, windows),), (), (), rules +
                        (CoverageRuleInput("COV-UTIL-TARGET", None, "utilisation", "1", "PERCENT", "coverage"),),
                        (ShiftRuleInput("SR-MIN-LEN", "min", "4", "HOURS", "shift"),
                         ShiftRuleInput("SR-MAX-LEN", "max", "8", "HOURS", "shift"),
                         ShiftRuleInput("SR-MAX-CONSEC", "consecutive", "5", "DAYS", "shift"),
                         ShiftRuleInput("SR-MIN-REST", "rest", rest, "HOURS", "shift")),
                        (CostTargetInput(3, Decimal("0.30"), Decimal(500), Decimal(1000), Decimal(1000)),),
                        (ThroughputInput("Coffee", "Barista", Decimal(3600), Decimal(1)),), None, people, (), forecast,
                        (dates[0], dates[-1]), "tax", frozenset({"Coffee"}), frozenset({3}), frozenset())


def _solve(model, *, limit: float | None = None):
    solver = cp_model.CpSolver()
    solver.parameters.random_seed = 1
    solver.parameters.num_search_workers = 1
    if limit is not None:
        solver.parameters.max_time_in_seconds = limit
    return solver, solver.Solve(model.model)


def _tiny_inputs() -> SolverInputs:
    """Small enough to enumerate every feasible solution: 12 slots, 2 employees.

    Coverage minima are zero so the feasible set is shaped purely by the shift rules, which is
    what the encoding change touches.
    """
    people = tuple(EmployeeInput(number, "Barista", 3, 3, False, True, True, True, Decimal(10 + number), "open", 0,
                                 40, date(1990, 1, 1), False, None, "", False) for number in (1, 2))
    forecast = ForecastSet("v", "tax", "test", datetime(2026, 12, 1, tzinfo=ZoneInfo("America/New_York")),
                           (ForecastRecord(3, "Coffee", datetime(2026, 12, 7, 9, tzinfo=ZoneInfo("America/New_York")),
                                           Decimal(1), Decimal(1), Decimal(1), "test", "tax"),))
    rules = tuple(CoverageRuleInput(rule, None, rule, "0", "HEADCOUNT", "coverage") for rule in
                  ("minimum crew", "key holder", "fpc", "peak skill", "break relief"))
    windows = {weekday: StoreOpenWindow(8, 13, frozenset(range(7))) for weekday in range(7)}
    return SolverInputs((StoreInput(3, "Tiny", 9, 9, windows),), (), (), rules +
                        (CoverageRuleInput("COV-UTIL-TARGET", None, "utilisation", "1", "PERCENT", "coverage"),),
                        (ShiftRuleInput("SR-MIN-LEN", "min", "4", "HOURS", "shift"),
                         ShiftRuleInput("SR-MAX-LEN", "max", "5", "HOURS", "shift"),
                         ShiftRuleInput("SR-MAX-CONSEC", "consecutive", "5", "DAYS", "shift"),
                         ShiftRuleInput("SR-MIN-REST", "rest", "10", "HOURS", "shift")),
                        (CostTargetInput(3, Decimal("0.30"), Decimal(500), Decimal(1000), Decimal(1000)),),
                        (ThroughputInput("Coffee", "Barista", Decimal(3600), Decimal(1)),), None, people, (), forecast,
                        (DAY, DAY + timedelta(days=1)), "tax", frozenset({"Coffee"}), frozenset({3}), frozenset())


class _Collect(cp_model.CpSolverSolutionCallback):
    def __init__(self, keyed):
        super().__init__()
        self._keyed = keyed
        self.found: set = set()

    def on_solution_callback(self):
        self.found.add(tuple(sorted(key for key, var in self._keyed if self.Value(var))))


def _feasible_set(model) -> set:
    """Every feasible solution, keyed on slot IDENTITY so a reordering cannot look like a change."""
    for literal in model.assumptions.values():
        model.model.Add(literal == 1)
    keyed = [((employee, (slot.location_id, slot.day.isoformat(), str(slot.start_hour), str(slot.end_hour))), var)
             for (employee, slot_index), var in model.assign.items()
             for slot in (model.slots[slot_index],)]
    solver = cp_model.CpSolver()
    solver.parameters.enumerate_all_solutions = True
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 1
    collector = _Collect(keyed)
    solver.Solve(model.model, collector)
    return collector.found


class BuildScalingTests(unittest.TestCase):
    # spec: F11.ac27
    def test_ac27_index_matches_naive_rescan(self):
        inputs = _inputs()
        slots = build_slot_grid(inputs)
        index = build_slot_index(slots, calendar_days=(DAY, DAY + timedelta(days=1)), rest=Decimal(10))
        for slot_index, slot in enumerate(slots):
            self.assertIn(slot_index, index.by_day[slot.day])
            year, week, _ = slot.day.isocalendar()
            self.assertIn(slot_index, index.by_week[(year, week)])
            for hour in range(24):
                expected = tuple(i for i, candidate in enumerate(slots) if candidate.location_id == slot.location_id
                                 and candidate.day == slot.day and candidate.start_hour <= hour
                                 and candidate.end_hour >= hour + 1)
                self.assertEqual(index.by_store_day_hour.get((slot.location_id, slot.day, hour), ()), expected)

    # spec: F11.ac28
    def test_ac28_feasible_set_is_unchanged(self):
        """The real equivalence proof: the whole feasible set, not one chosen optimum.

        Comparing a single chosen solution does NOT prove equivalence, because the objective has
        ties and re-encoding the rest rule changes which tied optimum CP-SAT reaches first. The
        count and digest below were measured on the pairwise encoding at commit ba2a162.
        """
        found = _feasible_set(build_model(_tiny_inputs(), legality=_allow))
        self.assertEqual(len(found), 2401)
        self.assertEqual(hashlib.sha256(repr(sorted(found)).encode()).hexdigest(),
                         "ebb4fb94e397f9bac5b6f86c702876fc563c7d1024b3da8a5c912f31e7eaabf0")

    # spec: F11.ac33
    def test_ac28_optimum_and_constraint_reduction(self):
        """Same optimum, far fewer constraints.

        These one-store fixtures shrink by about 9.5x (19,582 to 2,061 and 39,094 to 4,065). The
        bound asserted is 5x because the reduction grows with the window: the three-store grid
        goes from roughly 15.9M constraints to 288k over three days, a factor of about 55.

        The solve is capped and the status may be FEASIBLE rather than OPTIMAL. Both encodings
        reach the objective below in under a second but neither can PROVE optimality on the
        three-day fixture inside 150s, so demanding OPTIMAL here would assert a pre-existing
        solve-time limitation that CS-092 does not address, and would cost CI six minutes. The
        objective value is the invariant that matters, and it is identical in both encodings.
        """
        for days, people, objective, old_constraints in ((2, 3, 26900, 19582), (3, 4, 38300, 39094)):
            inputs = _inputs(days, people)
            model = build_model(inputs, legality=_allow)
            build_objective(model, inputs)
            solver, status = _solve(model, limit=20)
            self.assertIn(status, (cp_model.OPTIMAL, cp_model.FEASIBLE))
            self.assertEqual(int(solver.ObjectiveValue()), objective)
            self.assertLessEqual(len(model.model.Proto().constraints), old_constraints // 5)

    # spec: F11.ac29
    def test_ac29_rest_assumption_reification_and_dst_elapsed_time(self):
        """A gap that is legal by the wall clock and illegal in real elapsed time.

        US DST begins 2027-03-14. 22:00 Saturday to 08:30 Sunday reads as 10.5 wall-clock hours,
        which would satisfy a 10-hour rest, but only 9.5 hours actually elapse. A wall-clock
        implementation returns OPTIMAL here, so this fixture discriminates; a fixture whose gap
        violates the rule under both readings would not.
        """
        march = date(2027, 3, 13)
        inputs = replace(_inputs(2, 1), target_window=(march, march + timedelta(days=1)))
        slots = (ShiftSlot(3, march, Decimal("18"), Decimal("22")),
                 ShiftSlot(3, march + timedelta(days=1), Decimal("8.5"), Decimal("12.5")))
        index = build_slot_index(slots, calendar_days=(march, march + timedelta(days=1)), rest=Decimal(10))
        elapsed_gap = index.minutes_from_base[1] - (index.minutes_from_base[0] + index.duration_minutes[0])
        self.assertEqual(elapsed_gap, 570)  # 9.5h of real time
        wall_gap = (datetime.combine(slots[1].day, time(8, 30)) - datetime.combine(slots[0].day, time(22, 0)))
        self.assertEqual(wall_gap // timedelta(minutes=1), 630)  # 10.5h on the clock face
        model = build_model(inputs, slots=slots, legality=_allow)
        model.model.Add(sum(model.assign.values()) == 2)
        self.assertEqual(_solve(model)[1], cp_model.INFEASIBLE)
        self.assertIn("shift_rules", diagnose_infeasibility(model))
        model = build_model(inputs, slots=slots, legality=_allow)
        model.model.ClearAssumptions()
        for name, literal in model.assumptions.items():
            if name != "shift_rules":
                model.model.AddAssumption(literal)
        model.model.Add(sum(model.assign.values()) == 2)
        self.assertEqual(_solve(model)[1], cp_model.OPTIMAL)

    # spec: F11.ac30
    def test_ac30_identical_bounds_and_single_slot(self):
        inputs = _inputs(1, 1)
        inputs = replace(
            inputs,
            stores=(replace(inputs.stores[0], open_windows={0: StoreOpenWindow(8, 12, frozenset({0}))}),),
        )
        slots = (ShiftSlot(3, DAY, Decimal("8"), Decimal("12")), ShiftSlot(3, DAY, Decimal("8"), Decimal("12")))
        model = build_model(inputs, slots=slots, legality=_allow)
        model.model.Add(sum(model.assign.values()) == 2)
        self.assertEqual(_solve(model)[1], cp_model.INFEASIBLE)
        self.assertTrue(build_model(inputs, slots=slots[:1], legality=_allow).assign)

    # spec: F11.ac31
    def test_ac31_zero_rest_is_refused_and_empty_bucket_is_empty(self):
        """A zero minimum rest stays refused.

        The interval encoding would tolerate it (intervals would shrink to the slot itself), but
        accepting it would mean a tenant could switch off rest protection silently, so the
        pre-existing fail-closed check on `shift_rules` values is kept.
        """
        with self.assertRaises(SolverModelError):
            build_model(_inputs(1, 1, "0"), legality=_allow)
        slots = (ShiftSlot(3, DAY, Decimal("8"), Decimal("12")), ShiftSlot(3, DAY, Decimal("12"), Decimal("16")))
        inputs = _inputs(1, 1)
        inputs = replace(
            inputs,
            stores=(replace(inputs.stores[0], open_windows={0: StoreOpenWindow(8, 16, frozenset({0}))}),),
        )
        model = build_model(inputs, slots=slots, legality=_allow)
        self.assertEqual(model.index.by_store_day_hour.get((3, DAY, 7), ()), ())
        model.model.Add(sum(model.assign.values()) == 2)
        self.assertEqual(_solve(model)[1], cp_model.INFEASIBLE)  # one shift per day remains binding

    # spec: F11.ac32
    def test_ac32_builds_are_proto_deterministic(self):
        inputs = _inputs()
        self.assertEqual(str(build_model(inputs, legality=_allow).model.Proto()),
                         str(build_model(inputs, legality=_allow).model.Proto()))


if __name__ == "__main__":
    unittest.main()
