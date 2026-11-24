"""CS-093b — bounded feasible and no-incumbent semantics (F11.ac37-ac40)."""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cs093_support import allow_all, real_shape_inputs, tiny_optimal_inputs  # noqa: E402
from ortools.sat.python import cp_model  # noqa: E402

from agents.scheduler.constraints import InfeasibleModelError, build_model  # noqa: E402
from agents.scheduler.objective import build_objective  # noqa: E402
from agents.scheduler.solve import (  # noqa: E402
    NoIncumbentError,
    SolverStopPolicy,
    UnacceptableGapError,
    classify_outcome,
    solve_schedule,
)

# The three-day / four-employee incumbent, per CS-092: reached in well under a second, never
# proven optimal inside 150s. Every solve here is capped so CI stays bounded.
INCUMBENT = 38300


class BoundedSemanticsTests(unittest.TestCase):
    # spec: F11.ac37
    def test_ac37_feasible_incumbent_is_best_found(self):
        # The hard CS-092 fixture reaches its incumbent fast but cannot be proven optimal. Under a
        # short time cap the search is time-stopped: a FEASIBLE incumbent, reported as best found
        # within the accepted gap, never as optimal.
        inputs = real_shape_inputs(3, 4)
        model = build_model(inputs, legality=allow_all)
        build_objective(model, inputs)
        started = time.monotonic()
        result = solve_schedule(model, policy=SolverStopPolicy(max_time_seconds=3.0, max_relative_gap=0.9))
        self.assertLess(time.monotonic() - started, 30.0)
        self.assertEqual(result.cp_sat_status, "FEASIBLE")
        self.assertFalse(result.optimal)
        self.assertNotEqual(result.stopping_reason, "optimal")
        self.assertIn(result.stopping_reason, ("time_limit", "limit_or_unknown"))
        self.assertEqual(result.objective, INCUMBENT)
        self.assertGreater(result.relative_gap, 0.0)
        self.assertLessEqual(result.relative_gap, 0.9)
        self.assertTrue(result.assignments)  # a real schedule, not an empty one

    # spec: F11.ac38
    def test_ac38_optimal_is_zero_gap(self):
        inputs = tiny_optimal_inputs()
        model = build_model(inputs, legality=allow_all)
        build_objective(model, inputs)
        result = solve_schedule(model, policy=SolverStopPolicy(max_time_seconds=10.0, max_relative_gap=0.0))
        self.assertEqual(result.cp_sat_status, "OPTIMAL")
        self.assertTrue(result.optimal)
        self.assertEqual(result.stopping_reason, "optimal")
        self.assertEqual(result.relative_gap, 0.0)

    # spec: F11.ac39
    def test_ac39_no_incumbent_and_infeasible_fail_closed(self):
        # UNKNOWN / MODEL_INVALID with no incumbent is a typed failure carrying the status,
        # tested at the classification boundary so it does not depend on solver timing.
        for status in (cp_model.UNKNOWN, cp_model.MODEL_INVALID):
            with self.subTest(status=status), self.assertRaises(NoIncumbentError) as caught:
                classify_outcome(
                    status=status, has_incumbent=False, objective=0, bound=0.0,
                    wall_time=0.0, policy=SolverStopPolicy(1.0, 0.02),
                )
            self.assertIn(cp_model.CpSolver().StatusName(status), str(caught.exception))

        # An infeasible model still raises InfeasibleModelError with its binding groups, not a
        # stopping-rule failure.
        inputs = real_shape_inputs(1, 1)  # one employee cannot meet the key-holder/fpc minima
        model = build_model(inputs, legality=allow_all)
        build_objective(model, inputs)
        with self.assertRaises(InfeasibleModelError) as caught:
            solve_schedule(model, policy=SolverStopPolicy(max_time_seconds=10.0, max_relative_gap=1.0))
        self.assertTrue(caught.exception.groups)

    # spec: F11.ac40
    def test_ac40_incumbent_outside_gap_is_rejected(self):
        inputs = real_shape_inputs(3, 4)
        model = build_model(inputs, legality=allow_all)
        build_objective(model, inputs)
        started = time.monotonic()
        with self.assertRaises(UnacceptableGapError) as caught:
            # Demanding a zero gap under a short cap: CP-SAT returns FEASIBLE with a positive
            # gap it cannot close, so the incumbent is rejected rather than published as optimal.
            solve_schedule(model, policy=SolverStopPolicy(max_time_seconds=3.0, max_relative_gap=0.0))
        self.assertLess(time.monotonic() - started, 30.0)
        self.assertGreater(caught.exception.relative_gap, 0.0)
        self.assertEqual(caught.exception.cp_sat_status, "FEASIBLE")


if __name__ == "__main__":
    unittest.main()
