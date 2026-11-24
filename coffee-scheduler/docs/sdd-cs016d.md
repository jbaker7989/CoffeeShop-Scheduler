# CS-016d — Solver runner and reviewable schedule output

`agents.scheduler.runner` joins typed input assembly, hard constraints, the installed objective,
CS-093's bounded solve, and pure schedule decoding. It publishes economics and the complete
`SolveResult` evidence, without recreating internal penalty slacks or writing compliance logs.

## Acceptance criteria

**F11.ac51**: The pure decoder turns each chosen `(employee_id, slot_index)` into the matching
employee and shift fields and orders shifts by store, day, start, and employee.

**F11.ac52**: Decoded labour cents use the objective's `_cents(hours * hourly_rate)` arithmetic;
the economics labour total reconciles to the decoded shifts.

**F11.ac53**: A feasible empty schedule is published as an empty schedule with zero economics,
not as an error.

**F11.ac54**: Premium cents and the sorted source rule ids attach to each chosen shift.

**F11.ac55**: Identical inputs yield deterministic decoded shifts and JSON-ready output.

**F11.ac56**: A chosen employee missing from `SolverInputs` fails closed with `RunnerError`.

**F11.ac57**: The runner preserves `InfeasibleModelError`, `NoIncumbentError`, and
`UnacceptableGapError` unchanged; it never converts a solve failure into an empty schedule.

**F11.ac58**: Objective omissions are carried onto `ScheduleOutput` and its objective equals the
attached solve result's objective.

**F11.ac59**: A rollback-only, DB-gated forward-day run emits a non-empty schedule whose labour
economics reconcile and whose shifts fall on the requested day.

## Traceability

| Acceptance criterion | Test |
| --- | --- |
| F11.ac51 | `RunnerTests.test_ac51_happy_path_decodes_chosen_assignments_in_schedule_order` |
| F11.ac52 | `RunnerTests.test_ac52_labour_cost_reconciles_to_objective_rounding_basis` |
| F11.ac53 | `RunnerTests.test_ac53_empty_feasible_schedule_has_zero_economics` |
| F11.ac54 | `RunnerTests.test_ac54_premium_money_and_rule_ids_attach_to_shift` |
| F11.ac55 | `RunnerTests.test_ac55_decode_and_json_output_are_deterministic` |
| F11.ac56 | `RunnerTests.test_ac56_phantom_employee_fails_closed` |
| F11.ac57 | `RunnerTests.test_ac57_run_schedule_propagates_infeasible_no_incumbent_and_over_gap` |
| F11.ac58 | `RunnerTests.test_ac58_omitted_objective_terms_pass_through_runner_output` |
| F11.ac59 | `DatabaseRunnerTests.test_ac59_one_forward_day_runs_and_reconciles` |
