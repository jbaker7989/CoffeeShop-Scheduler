# CS-093 — Solver stopping rule: development plan

## Outcome

Make bounded, explainable CP-SAT solving the production contract.  A feasible schedule
returned after an explicit limit is **best found**, never silently represented as optimal.
The stopping policy is configuration, the result carries the solver status/bound/gap/reason,
and CI tests cannot spend minutes attempting an optimality proof.

## Scope and invariants

- Preserve every CS-016a/b/c hard constraint, objective coefficient, deterministic input
  ordering, and infeasibility-assumption-core behaviour.
- Do not claim a feasible result when CP-SAT reports `UNKNOWN` with no solution, nor weaken
  an infeasibility result because a time limit was reached.
- Treat a zero/near-zero objective carefully: calculate a signed, finite percentage gap using
  CP-SAT's objective and best bound; document the denominator convention and use an absolute
  tolerance for zero.
- A `FEASIBLE` result is publishable only when it is within the configured allowed gap; a
  timeout that finds no solution is a distinct failure. `OPTIMAL` is always acceptable and
  reports a zero gap/reason `optimal`.
- Configuration must be validated before solving: positive finite time limit, non-negative
  finite gap, and a bounded upper gap (for example <= 1.0 as a ratio). Invalid configuration
  must fail before model construction/solve rather than silently falling back.

## Design

1. Locate the public scheduler solve entry point and all callers/tests. Keep `build_model`
   pure; introduce a typed immutable `SolverStopPolicy` at the solve boundary with
   `max_time_seconds` and `max_relative_gap` (ratio, not percent), plus an explicitly named
   production default loaded from scheduler configuration/environment.
2. Pass the policy to `cp_model.CpSolver.parameters.max_time_in_seconds` and
   `relative_gap_limit`. Set only deterministic/safe parameters already established by the
   project; do not introduce non-deterministic worker parallelism without a documented reason.
3. Add a typed solve result that includes assignments/objective plus `cp_sat_status`,
   `best_objective_bound`, `relative_gap`, `stopping_reason`, elapsed seconds, and the policy
   actually used. Keep the existing external result shape compatible or migrate every caller
   atomically.
4. Classify statuses centrally:
   - `OPTIMAL` → accepted, `optimal`;
   - `FEASIBLE` within the gap → accepted, `gap_limit` or `time_limit` based on observed limit;
   - `FEASIBLE` outside gap → explicit non-accepted bounded-solve error/result;
   - `INFEASIBLE` → preserve `InfeasibleModelError` and assumption-core diagnostics;
   - `MODEL_INVALID`/`UNKNOWN` with no incumbent → explicit solve failure carrying status.
   Avoid guessing the actual stop cause if OR-Tools does not expose it; expose status plus
   `time_limit_reached`/`gap_satisfied` evidence and label ambiguous cases `limit_or_unknown`.
5. Thread policy through the API/agent layer and any CLI/demo entry point. Emit status, bound,
   gap, and elapsed time in structured logs/response metadata; never render `optimal` in an
   explanation unless CP-SAT returned `OPTIMAL`.
6. Establish production values from a measurement script using the real three-store, full-week
   input. Record hardware/runtime, model build time, solve time, incumbent objective, bound,
   gap, status, and policy in a checked-in report. The default budget is selected from that
   evidence, not from the small four-employee fixture.

## Test stories (failing first)

### CS-093a — stop-policy and result contract

- Unit-test invalid/missing/non-finite/negative time and gap values.
- Test the default policy is explicit and that an override is passed to CP-SAT.
- Test result serialization exposes policy, status, objective, bound, finite gap, elapsed time,
  and a non-misleading reason.

### CS-093b — bounded feasible and no-incumbent semantics

- Use a deterministic small model with a short cap to prove a feasible incumbent is returned
  as `best found` rather than `optimal` when CP-SAT is not optimal.
- Test `OPTIMAL` maps to zero gap/`optimal`.
- Test `UNKNOWN`/timeout without an incumbent returns a typed failure, never an empty schedule.
- Test a feasible incumbent exceeding the allowed gap is rejected rather than published.

### CS-093c — solver regression and performance guard

- Re-run CS-016 hard-constraint/objective fixtures under capped policies; assert assignment
  legality and objective values, not an `OPTIMAL` solver status.
- Keep each test under a small, explicit cap and prove it terminates within a wall-clock guard.
- Add the real-scale measurement command/report as a non-CI benchmark or an opt-in test so CI
  remains bounded while the default has an auditable basis.

## Delivery sequence

1. Claude writes CS-093a/b/c red tests and an SDD/contract note.
2. Claude implements the typed policy/result/status classifier and caller wiring.
3. Claude runs targeted scheduler tests plus the project suite and records the measured full-week
   benchmark.
4. Codex independently reviews the diff for status misrepresentation, gap arithmetic, invalid
   configuration fallbacks, lost infeasibility diagnostics, and test caps; Codex files BUG
   stories for every confirmed issue.
5. Codex executes the agreed test matrix. Only after all required tests pass does Codex commit
   the integrated units and push the branch.

## Acceptance evidence

- Red-first tests for all three test stories become green.
- A full-week measurement report names the selected default and evidence.
- Existing CS-016 solver, constraint, forecast-input, and API tests remain green.
- No result says `optimal` unless CP-SAT status is `OPTIMAL`; bounded feasible results expose
  their gap/bound and reason; no-incumbent and invalid-policy cases fail closed.
