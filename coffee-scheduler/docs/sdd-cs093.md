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
time budget is written to `CpSolver.parameters.max_time_in_seconds` (the accepted gap is a
post-solve publish guard, not a CP-SAT limit — see the finding below).

**F11.ac36** (CS-093a): `SolveResult.to_dict()` exposes the policy, the CP-SAT status name, the
integer objective, the best bound, a finite relative gap, the elapsed seconds and a stopping
reason that reads `optimal` only when CP-SAT returned `OPTIMAL`.

**F11.ac37** (CS-093b): under a short time cap the hard fixture is time-stopped — a `FEASIBLE`
incumbent returned as best found, `optimal` `False`, reason `time_limit`/`limit_or_unknown`, a
non-zero gap within the accepted guard, and a real schedule rather than an empty one.

**F11.ac38** (CS-093b): a solve that proves optimality reports status `OPTIMAL`, a zero relative
gap and reason `optimal`.

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

## Finding: CP-SAT `OPTIMAL` is not proof when a gap limit is set, and the decoupled guard

Measured on ortools 9.15: `CpSolver` returns status **`OPTIMAL` whenever its `relative_gap_limit`
is merely satisfied**, not only when it proves optimality. With `relative_gap_limit = 1.0` the
three-day fixture returns `OPTIMAL` in 0.03s over an incumbent of 127500 (true optimum 38300) with
a bound of 3000. So the status name alone is unsafe, and a loose accepted gap wired into
`relative_gap_limit` makes CP-SAT **stop early at a materially worse schedule** (gap limit 0.85 →
objective 54150 instead of 38300). A tight gap wired there has the opposite failure: the real
full-week solve reaches a gap of only ~0.85 at 30s (a weak linear bound, not a bad schedule), so
every real week would be rejected.

The plan's step 2 ("pass the policy to `relative_gap_limit`") is therefore replaced, on evidence,
by a **decoupled** design:

- CP-SAT is given the time budget and `relative_gap_limit = 0.0`, so the search pursues optimality
  for the whole budget and status `OPTIMAL` means genuinely proven (gap zero);
- the accepted gap is enforced **after** the solve as a publish guard: an incumbent within it is
  published as best found, one outside it raises `UnacceptableGapError` and is never published.

This keeps solution quality (no early stop at a worse incumbent), keeps `OPTIMAL` honest, and
still fails closed. `relative_gap` is always the computed `(objective - bound) / max(|objective|,1)`,
never CP-SAT's status.

## Stopping reason, without guessing

OR-Tools does not report which limit stopped the search, so the reason is derived from the
computed gap and the time evidence, never from the status name alone:

- gap zero and status `OPTIMAL` (proven) → `optimal`.
- status `OPTIMAL` with a non-zero gap (defensive; cannot arise while `relative_gap_limit = 0`) →
  `gap_limit`.
- `FEASIBLE` within the accepted gap, time budget reached → `time_limit`.
- `FEASIBLE` within the accepted gap, stopped earlier for a solver-internal reason → `limit_or_unknown`.
- any incumbent outside the accepted gap → rejected (`UnacceptableGapError`).
- no incumbent (`UNKNOWN`/`MODEL_INVALID`) → `NoIncumbentError`.

An accepted `FEASIBLE` is only ever published as best found; `optimal` is reserved for a proven
zero-gap `OPTIMAL`.

## What this story does not change

Every CS-016a/b/c hard constraint, objective coefficient, deterministic input ordering and
infeasibility assumption-core is preserved. The solver still runs single-threaded with
`random_seed = 0`; the stopping policy only bounds how long the proof is pursued and records the
result honestly.
