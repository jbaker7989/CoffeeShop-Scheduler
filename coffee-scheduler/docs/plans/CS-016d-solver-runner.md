# CS-016d — Solver runner & reviewable schedule output (Plan)

## What is already done, and what this story is

**CS-093 is complete** (`agents/scheduler/solve.py`): `solve_schedule(model, *, policy)` runs the
built, objective-bearing model to a time budget, enforces the accepted gap as a post-solve publish
guard, and returns a `SolveResult` (`assignments`, integer `objective`, `cp_sat_status`,
`best_objective_bound`, `relative_gap`, `stopping_reason`, `elapsed_seconds`, `policy`, `optimal`).
It raises `NoIncumbentError`, `UnacceptableGapError`, or `InfeasibleModelError`. **This plan does
not touch `solve.py`.** The two CS-016d bullets CS-093 already satisfies — solver time limit,
search params, determinism (seed 0, single worker) and the performance measurement — are consumed,
not rebuilt.

CS-016d is the **end-to-end runner and the reviewable output object**: it ties CS-016a (assemble
inputs) → CS-016b (`build_model`) → CS-016c (`build_objective`) → CS-093 (`solve_schedule`), then
**decodes the raw `(employee_id, slot_index)` assignments into a structured schedule** that
CS-015/CS-017 can consume, with the labour economics and the solver-quality evidence attached.

## Deliverables (all under `coffee-scheduler/`)

### 1. `agents/scheduler/runner.py` (new)

```python
@dataclass(frozen=True, slots=True)
class ScheduledShift:
    employee_id: int
    role: str
    location_id: int
    day: date
    start_hour: Decimal          # matches ShiftSlot.start_hour (half-hour multiples)
    end_hour: Decimal
    hours: Decimal
    hourly_rate: Decimal
    labour_cost_cents: int        # _cents(hours * hourly_rate) — reused from objective.py
    premium_cents: int            # _cents of model.premiums.get((emp, idx), 0)
    premium_rule_ids: tuple[str, ...]   # from re-evaluating legality on this chosen shift

@dataclass(frozen=True, slots=True)
class ScheduleEconomics:
    labour_cost_cents: int        # sum over shifts
    premium_cents: int
    paid_half_hours: int
    headcount: int                # distinct employees scheduled
    labour_cents_by_store: dict[int, int]

@dataclass(frozen=True, slots=True)
class ScheduleOutput:
    target_window: tuple[date, date]
    model_version: str
    shifts: tuple[ScheduledShift, ...]          # sorted (location_id, day, start_hour, employee_id)
    economics: ScheduleEconomics
    solve: SolveResult                          # the CS-093 quality evidence, carried whole
    objective: int                              # == solve.objective
    omitted_terms: tuple[str, ...]              # from ObjectiveTerms.omitted (honest gaps)
    def to_dict(self) -> dict[str, Any]: ...    # JSON-ready for CS-015/CS-017 and A2A
```

`run_schedule(conn, *, target_window, model_version, policy=None, legality=None) -> ScheduleOutput`:

1. `inputs = assemble_solver_inputs(conn, target_window=target_window, model_version=model_version)`.
2. `legality = legality or legality_from_compliance(conn, inputs)`.
3. `model = build_model(inputs, legality=legality)`.
4. `terms = build_objective(model, inputs)` (installs the objective on `model.model`).
5. `result = solve_schedule(model, policy=policy)` — CS-093 owns the bound and determinism.
6. `shifts = decode_shifts(model, inputs, result, legality)` — the core new logic.
7. Aggregate `ScheduleEconomics`; return `ScheduleOutput` with `objective=result.objective` and
   `omitted_terms=terms.omitted`.

`decode_shifts(model, inputs, result, legality)` is a **pure** function (no DB, no solver) so it is
exhaustively testable offline:
- Index employees by id once (`{e.employee_id: e}`); a chosen id absent from inputs raises
  `RunnerError` naming it (defensive — `build_model` guarantees it, but the decode must not emit a
  shift for a phantom employee).
- For each `(employee_id, slot_index)` in `result.assignments`: resolve the `ShiftSlot`, compute
  `labour_cost_cents = _cents(slot.hours * employee.hourly_rate)` (import `_cents` from
  `objective.py` — never re-implement cents rounding), `premium_cents = _cents(premiums.get(...))`,
  and `premium_rule_ids` by calling `legality(employee, slot).premiums` (the legality callable is
  already memoised, so this is free; it yields the rule ids behind the premium the objective priced).
- Sort deterministically by `(location_id, day, start_hour, employee_id)`.

### 2. `test/test_cs016d_runner.py`
### 3. `docs/sdd-cs016d.md` — ACs **F11.ac51** onward (ac50 is the current top), colon OUTSIDE the
bold (`**F11.ac51**:`), with the AC→test table.
### 4. `FEATURES.md` — a `- **CS-016d (solver runner, done):** …` bullet under `### F11`.
### 5. `Backlog.md` — CS-016d to `✅ Done`, and **correct its stale status line** (it currently
reads "Blocked (on CS-016b, CS-016c)"; both are merged, and CS-093 is done).

## The reconciliation invariant (what makes the decode provably correct)

CS-016c builds the `labour_cost` objective term as exactly `Σ_chosen _cents(slot.hours × rate)`
(`objective.py:172`). The decode computes the same per shift. So:

> **`economics.labour_cost_cents == Σ shift.labour_cost_cents`**, and this equals the model's
> `labour_cost` term — the decode's economics is the *same* arithmetic the objective used, not a
> parallel re-derivation.

A test fixes a known-optimal tiny schedule and asserts the decoded labour cost equals a
hand-computed value, pinning the decode to the objective's own cost basis.

## Scope boundary: economics, not penalty itemisation

The objective has five terms (labour, understaffing, overtime, premium, labour-%-breach). Only
**labour** and **premium** are real money and are robustly recomputable from the chosen shifts; the
other three are **penalty slacks** whose exact values live inside the CP-SAT solver that
`solve_schedule` deliberately does not expose. CS-016d therefore reports the **labour-economics
decomposition** (what a manager reviews: cost, premium, hours, per-store) plus the **solver-quality
evidence** from `SolveResult` (objective total, gap, stopping reason, `optimal`). It does **not**
itemise the three penalty terms, because doing so would either re-implement CS-016c's slack logic in
a second place (divergence risk) or require modifying CS-093's reviewed `solve.py`. If a
penalty-term breakdown is later wanted for explainability, it is a clean follow-up that extends the
solve layer; this plan records that boundary rather than guessing the penalties.

Likewise, **persisting** to `Rule_Eval_Log` is CS-013a's write path, out of scope here. CS-016d
*attaches* the premium rule ids it can derive per shift; it does not write the log.

## Edge cases the tests must cover

- **Happy path, synthetic**: a tiny feasible model solved through `solve_schedule`; decoded shifts
  match the chosen assignments one-for-one in employee, store, day, start/end, hours; sorted order
  is deterministic.
- **Reconciliation**: decoded `labour_cost_cents` equals the hand-computed `Σ _cents(hours×rate)`.
- **Empty but feasible**: a model with no coverage minimum whose cost-minimising optimum schedules
  nobody → `shifts == ()`, all economics zero, `headcount == 0`. Must not raise.
- **Premium attached**: a stubbed legality returning a PREMIUM verdict → the chosen shift carries
  `premium_cents > 0` and the originating `premium_rule_ids`.
- **Determinism**: two `decode_shifts` / two full runs on the same inputs produce byte-identical
  `to_dict()`.
- **Error propagation**: an infeasible model → `run_schedule` propagates `InfeasibleModelError`
  (with its assumption-core groups); a no-incumbent / over-gap outcome propagates
  `NoIncumbentError` / `UnacceptableGapError` unchanged. The runner never converts a solve failure
  into an empty schedule.
- **Phantom employee**: a `SolveResult` naming an employee id absent from inputs → `RunnerError`.
- **Omitted terms passthrough**: inputs with no revenue prior → `omitted_terms` is non-empty and
  surfaced on the output (honest about what the objective could not price).
- **One DB-gated end-to-end** (single forward day 2026-12-07, rollback-only): `run_schedule`
  against seeded Postgres yields a non-empty schedule whose every shift falls inside the store's
  open window and shift-length bounds, whose `objective == solve.objective`, and whose
  `economics.labour_cost_cents == Σ shift.labour_cost_cents`.

## Verification (Claude)

Offline tests under **both** runners; `ruff` clean on changed files; the full suite green against
live Postgres; the four CI gate scripts; the migration round-trip (reseed after). Keep every solve
in a test capped (CS-093's pattern: `SolverStopPolicy(max_time_seconds≈5, …)`) and assert the
decoded schedule and economics, never an `OPTIMAL` status. The DB-gated test stays one forward day.

## Out of scope

Modifying `solve.py` or CS-093's policy/defaults; itemising the three penalty terms; strengthening
the optimality bound (that is [[CS-097]]); writing `Rule_Eval_Log` (CS-013a); the LangGraph wiring
that consumes `ScheduleOutput` (CS-015) and the explanation layer (CS-017); any change to a
constraint or objective coefficient.
