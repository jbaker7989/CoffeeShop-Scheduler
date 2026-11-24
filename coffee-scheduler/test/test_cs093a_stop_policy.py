"""CS-093a — stop-policy and result contract (F11.ac41-ac43)."""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.scheduler.solve import (  # noqa: E402
    MAX_ALLOWED_GAP,
    SolvePolicyError,
    SolveResult,
    SolverStopPolicy,
    production_stop_policy,
    relative_gap,
)


class StopPolicyContractTests(unittest.TestCase):
    # spec: F11.ac41
    def test_ac41_policy_validates_time_and_gap(self):
        # A valid policy survives construction and is immutable.
        policy = SolverStopPolicy(max_time_seconds=5.0, max_relative_gap=0.02)
        self.assertEqual(policy.max_time_seconds, 5.0)
        self.assertEqual(policy.max_relative_gap, 0.02)
        with self.assertRaises(AttributeError):
            policy.max_time_seconds = 1.0  # type: ignore[misc]

        bad_time = [0, -1, 0.0, -0.5, float("inf"), float("nan"), "5", None, True]
        for value in bad_time:
            with self.subTest(time=value), self.assertRaises(SolvePolicyError):
                SolverStopPolicy(max_time_seconds=value, max_relative_gap=0.02)

        bad_gap = [-0.01, float("inf"), float("nan"), "0.1", None, True,
                   MAX_ALLOWED_GAP + 0.0001]
        for value in bad_gap:
            with self.subTest(gap=value), self.assertRaises(SolvePolicyError):
                SolverStopPolicy(max_time_seconds=5.0, max_relative_gap=value)

        # The zero gap (demand proven optimality) and the ceiling are both accepted.
        self.assertEqual(SolverStopPolicy(5.0, 0.0).max_relative_gap, 0.0)
        self.assertEqual(SolverStopPolicy(5.0, MAX_ALLOWED_GAP).max_relative_gap, MAX_ALLOWED_GAP)

    # spec: F11.ac42
    def test_ac42_default_is_explicit_and_overridable_and_applied(self):
        default = production_stop_policy()
        self.assertIsInstance(default, SolverStopPolicy)
        self.assertGreater(default.max_time_seconds, 0.0)
        self.assertTrue(math.isfinite(default.max_time_seconds))
        self.assertGreaterEqual(default.max_relative_gap, 0.0)
        self.assertLessEqual(default.max_relative_gap, MAX_ALLOWED_GAP)

        # Environment overrides the default, and an unparseable value fails closed.
        over = production_stop_policy(env={"SCHEDULER_SOLVE_MAX_SECONDS": "7.5",
                                           "SCHEDULER_SOLVE_MAX_GAP": "0.05"})
        self.assertEqual(over.max_time_seconds, 7.5)
        self.assertEqual(over.max_relative_gap, 0.05)
        with self.assertRaises(SolvePolicyError):
            production_stop_policy(env={"SCHEDULER_SOLVE_MAX_SECONDS": "not-a-number"})
        with self.assertRaises(SolvePolicyError):
            production_stop_policy(env={"SCHEDULER_SOLVE_MAX_GAP": "-1"})

        # The time budget is the CP-SAT-facing limit and is handed over verbatim. The accepted
        # gap is a post-solve publish guard, not CP-SAT's relative_gap_limit (see the SDD).
        from ortools.sat.python import cp_model

        solver = cp_model.CpSolver()
        SolverStopPolicy(7.5, 0.05).apply(solver)
        self.assertEqual(solver.parameters.max_time_in_seconds, 7.5)

    # spec: F11.ac43
    def test_ac43_result_serialization_is_not_misleading(self):
        policy = SolverStopPolicy(5.0, 0.02)
        result = SolveResult(
            assignments=((1, 0), (2, 3)),
            objective=38300,
            cp_sat_status="FEASIBLE",
            best_objective_bound=100.0,
            relative_gap=relative_gap(38300, 100.0),
            stopping_reason="time_limit",
            elapsed_seconds=2.5,
            policy=policy,
        )
        data = result.to_dict()
        for key in ("cp_sat_status", "objective", "best_objective_bound", "relative_gap",
                    "stopping_reason", "elapsed_seconds", "policy"):
            self.assertIn(key, data)
        self.assertEqual(data["cp_sat_status"], "FEASIBLE")
        self.assertEqual(data["objective"], 38300)
        self.assertTrue(math.isfinite(data["relative_gap"]))
        self.assertEqual(data["policy"], {"max_time_seconds": 5.0, "max_relative_gap": 0.02})
        # A feasible result never advertises optimality.
        self.assertFalse(result.optimal)
        self.assertNotEqual(data["stopping_reason"], "optimal")

    # spec: F11.ac43 (gap arithmetic + zero-objective convention)
    def test_ac43_gap_arithmetic_is_finite_and_zero_at_optimum(self):
        self.assertEqual(relative_gap(0.0, 0.0), 0.0)
        self.assertEqual(relative_gap(100.0, 100.0), 0.0)
        # Denominator floored at 1.0, so a near-zero objective stays finite.
        self.assertEqual(relative_gap(0.0, -1.0), 1.0)
        self.assertAlmostEqual(relative_gap(200.0, 100.0), 0.5)
        for a, b in ((1e9, 0.0), (5.0, 4.0), (0.0, 0.0)):
            self.assertTrue(math.isfinite(relative_gap(a, b)))


if __name__ == "__main__":
    unittest.main()
