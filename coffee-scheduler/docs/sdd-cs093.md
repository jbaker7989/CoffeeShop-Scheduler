# CS-093 solver stopping rule

Bounded, explainable CP-SAT solving as the production contract. A feasible schedule returned
after an explicit limit is **best found**, never silently represented as optimal. The stopping
policy is configuration; the result carries the solver status, bound, gap and stopping reason;
and CI tests cannot spend minutes attempting an optimality proof.

## Public surface

`agents/scheduler/solve.py`:

- `SolverStopPolicy(max_time_seconds, max_relative_gap)` — frozen, validated at construction.
- `production_stop_policy()` — the explicitly named default, env-overridable, loaded from the
  CS-093 measurement in `reports/cs093_solver_scale.md`.
- `SolveResult` — assignments, objective, `cp_sat_status`, `best_objective_bound`,
  `relative_gap`, `stopping_reason`, `elapsed_seconds`, and the `policy` actually used; with an
  `optimal` property and a `to_dict()` serialization.
- `solve_schedule(model, *, policy=None)` — the single public solve entry point.
- Typed failures `SolvePolicyError`, `NoIncumbentError`, `UnacceptableGapError`; `INFEASIBLE`
  preserves `constraints.InfeasibleModelError` and its assumption-core diagnostics.

## Acceptance criteria

**F11.ac34** (CS-093a): `SolverStopPolicy` refuses a non-positive, non-finite or non-numeric time
limit, and a negative, non-finite, non-numeric or over-cap (> 1.0 ratio) gap, before any solve.

**F11.ac35** (CS-093a): `production_stop_policy()` is an explicit, valid policy; `SCHEDULER_SOLVE_MAX_SECONDS`
and `SCHEDULER_SOLVE_MAX_GAP` override it and fail closed on an unparseable value; the policy's
limits are written to `CpSolver.parameters.max_time_in_seconds` and `relative_gap_limit`.

**F11.ac36** (CS-093a): `SolveResult.to_dict()` exposes the policy, the CP-SAT status name, the
integer objective, the best bound, a finite relative gap, the elapsed seconds and a stopping
reason that reads `optimal` only when CP-SAT returned `OPTIMAL`.

**F11.ac37** (CS-093b): under a short time cap a feasible-but-unproven incumbent is returned as
best found — `optimal` is `False`, the reason is not `optimal`, the objective is the known
incumbent, and the result is within the accepted gap.

**F11.ac38** (CS-093b): an `OPTIMAL` solve reports a zero relative gap and reason `optimal`.

**F11.ac39** (CS-093b): `UNKNOWN` or `MODEL_INVALID` with no incumbent raises `NoIncumbentError`
carrying the status, never an empty schedule; `INFEASIBLE` still raises `InfeasibleModelError`
with its binding groups.

**F11.ac40** (CS-093b): a feasible incumbent whose gap exceeds the allowed gap raises
`UnacceptableGapError` rather than being published.

**F11.ac41** (CS-093c): the CS-016 hard-constraint and objective fixtures solved through
`solve_schedule` under a capped policy keep their assignment legality and objective value; the
assertion is on the objective, never on an `OPTIMAL` status.

**F11.ac42** (CS-093c): every capped solve in the suite terminates within an explicit wall-clock
guard, so CI stays bounded.

**F11.ac43** (CS-093c): the real-scale measurement command records model build time, solve time,
incumbent objective, bound, gap, status, elapsed and the selected default to a checked-in report,
and runs only opt-in so CI never attempts the proof.

| AC | Test |
| --- | --- |
| F11.ac34 | `test_cs093a_stop_policy.py::test_ac34_policy_validates_time_and_gap` |
| F11.ac35 | `test_cs093a_stop_policy.py::test_ac35_default_is_explicit_and_overridable_and_applied` |
| F11.ac36 | `test_cs093a_stop_policy.py::test_ac36_result_serialization_is_not_misleading` |
| F11.ac37 | `test_cs093b_bounded_semantics.py::test_ac37_feasible_incumbent_is_best_found` |
| F11.ac38 | `test_cs093b_bounded_semantics.py::test_ac38_optimal_is_zero_gap` |
| F11.ac39 | `test_cs093b_bounded_semantics.py::test_ac39_no_incumbent_and_infeasible_fail_closed` |
| F11.ac40 | `test_cs093b_bounded_semantics.py::test_ac40_incumbent_outside_gap_is_rejected` |
| F11.ac41 | `test_cs093c_regression_guard.py::test_ac41_capped_solve_preserves_objective` |
| F11.ac42 | `test_cs093c_regression_guard.py::test_ac42_capped_solve_terminates_within_guard` |
| F11.ac43 | `test_cs093c_regression_guard.py::test_ac43_measurement_command_writes_report` |

## Gap arithmetic and the zero-objective convention

For a minimization the incumbent objective is at least the best bound, so the signed numerator
`objective - bound` is non-negative except for float noise near the optimum. The denominator is
`max(abs(objective), 1.0)`: the magnitude of the incumbent, floored at one cost cent so a
zero/near-zero objective yields a finite gap equal to the absolute difference rather than a
division by zero. A proven `OPTIMAL` therefore reports gap `0.0`. The gap is left signed (never
clamped) so a bound that floats a hair above the incumbent is visible rather than hidden.

## Stopping reason, without guessing

OR-Tools does not report which limit stopped the search, so the reason is derived from evidence,
not guessed:

- `OPTIMAL` → `optimal`.
- `FEASIBLE`, gap satisfied, time budget not yet reached → `gap_limit`.
- `FEASIBLE`, time budget reached, gap not satisfied → the solve is rejected (`UnacceptableGapError`).
- `FEASIBLE`, both the gap satisfied and the time budget reached → `limit_or_unknown` (ambiguous).
- `FEASIBLE`, neither (a solver-internal stop) → `limit_or_unknown`.

An accepted `FEASIBLE` is only ever published as best found; `optimal` is reserved for a true
`OPTIMAL` status.

## What this story does not change

Every CS-016a/b/c hard constraint, objective coefficient, deterministic input ordering and
infeasibility assumption-core is preserved. The solver still runs single-threaded with
`random_seed = 0`; the stopping policy only bounds how long the proof is pursued and records the
result honestly.
