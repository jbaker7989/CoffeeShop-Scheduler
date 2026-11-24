"""CS-093c — solver regression and bounded-CI performance guard (F11.ac48-ac50)."""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import measure_solver_scale  # noqa: E402
from cs093_support import allow_all, real_shape_inputs  # noqa: E402

from agents.scheduler.constraints import build_model  # noqa: E402
from agents.scheduler.objective import build_objective  # noqa: E402
from agents.scheduler.solve import SolverStopPolicy, solve_schedule  # noqa: E402

# Capped policy for every regression solve. Generous publish guard so a time-stopped incumbent is
# accepted as best found; short time so CI cannot spend minutes proving optimality.
CAPPED = SolverStopPolicy(max_time_seconds=5.0, max_relative_gap=0.9)
# Known CS-092 optima for the shared fixture: asserted on the objective, never on OPTIMAL status.
# The 2-day fixture proves optimal in under a second; the 3-day one reaches the same incumbent
# but cannot be proven, so it is accepted as best found under the publish guard.
KNOWN_OBJECTIVE = {(2, 3): 26900, (3, 4): 38300}


class RegressionGuardTests(unittest.TestCase):
    # spec: F11.ac48
    def test_ac48_capped_solve_preserves_objective(self):
        for (days, people), objective in KNOWN_OBJECTIVE.items():
            with self.subTest(days=days, people=people):
                inputs = real_shape_inputs(days, people)
                model = build_model(inputs, legality=allow_all)
                build_objective(model, inputs)
                result = solve_schedule(model, policy=CAPPED)
                # The invariant is the objective value; status may be FEASIBLE or OPTIMAL.
                self.assertEqual(result.objective, objective)
                self.assertIn(result.cp_sat_status, ("OPTIMAL", "FEASIBLE"))
                # Each returned assignment is a legal (employee, slot) pair in the model.
                self.assertTrue(result.assignments)
                for key in result.assignments:
                    self.assertIn(key, model.assign)

    # spec: F11.ac49
    def test_ac49_capped_solve_terminates_within_guard(self):
        inputs = real_shape_inputs(3, 4)
        model = build_model(inputs, legality=allow_all)
        build_objective(model, inputs)
        started = time.monotonic()
        result = solve_schedule(model, policy=SolverStopPolicy(max_time_seconds=2.0, max_relative_gap=1.0))
        elapsed = time.monotonic() - started
        # The 2s cap must actually bound the wall clock; allow generous slack for CI jitter.
        self.assertLess(elapsed, 20.0)
        self.assertLessEqual(result.elapsed_seconds, elapsed + 0.5)

    # spec: F11.ac50
    def test_ac50_measurement_command_writes_report(self):
        # A small capped measurement proves the command records the documented evidence; the
        # real-scale default is established by running the script manually (see the report).
        inputs = real_shape_inputs(2, 3)
        measurement = measure_solver_scale.measure(
            inputs, policy=CAPPED, legality=allow_all, label="2-day/3-employee smoke",
        )
        for key in ("label", "build_seconds", "solve_seconds", "objective",
                    "best_objective_bound", "relative_gap", "cp_sat_status", "stopping_reason"):
            self.assertIn(key, measurement)
        self.assertEqual(measurement["objective"], 26900)

        report = measure_solver_scale.render_report(
            [measurement],
            policy=CAPPED,
            runtime="unittest smoke, offline synthetic fixture",
        )
        self.assertIn("## Measurements", report)
        self.assertIn("26900", report)
        self.assertIn("relative_gap", report.lower())
        # The report names the policy it measured so the default has an auditable basis.
        self.assertIn("5.0", report)


if __name__ == "__main__":
    unittest.main()
