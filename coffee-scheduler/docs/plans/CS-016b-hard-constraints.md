# CS-016b — Hard constraints: legality + coverage (Plan)

## Goal and verified starting point

CS-016b builds the **CP-SAT model and its must-hold constraints** from the validated
`SolverInputs` that CS-016a produces. It introduces **no objective function** (CS-016c) and **no
runner/output object** (CS-016d): the deliverable is a model whose feasible solutions are exactly
the legal, adequately-covered schedules, plus an infeasibility diagnosis that names the binding
constraint instead of a bare "infeasible".

Verified on `origin/main` (`aad55f2`):

- **CS-016a is merged.** `agents/scheduler/inputs.py` exposes frozen `SolverInputs` with
  `stores` (each `StoreInput.open_windows: dict[int, StoreOpenWindow]`, `max_staff_on_floor`,
  `stations`), `stations`, `fixed_tasks`, `coverage_rules` (`location_id is None` = all stores),
  `shift_rules`, `cost_targets`, `throughput` + `per_transaction_overhead`, `employees`
  (`EmployeeInput`: role, skill_level, home_location_id, cross_location_eligible, is_key_holder,
  cash_authorised, food_protection_cert, min/max_hours_week, dob, is_enrolled, school_calendar_id),
  `school_calendars`, `demand` (a contract-valid `ForecastSet`), `target_window`,
  `allowed_categories`, `allowed_locations`, `demand_gaps`. Reuse it; do not re-read the DB.
- **CS-013 owns legality.** `compliance/engine.py` exposes `Proposal` (tenant_id, location_id,
  employee_id, role, start_at, end_at, breaks, week_start, week_shifts, crew, …),
  `load_snapshot(proposal, *, dsn=...)`, `judge(snapshot, proposal) -> Evaluation`, and
  `Evaluation(results, binding, claims_legal_accuracy)` with
  `RuleResult(rule_id, result, premium_cost_usd, reason)`. `result` is one of
  `PASS`/`FAIL`/`PREMIUM`/`WARN`.
- **ortools is new.** `ortools==9.15.6755` is the only release with a Python 3.14 wheel, and CP-SAT
  is verified working on this interpreter.

## The one architectural decision

CP-SAT cannot call a Python predicate during search, yet the spec requires
*"legality via CS-013 per-shift evaluation (no legality computed in the solver)"*. Therefore:

> **Pre-compute legality, then encode it.** For every candidate `(employee, location, day, slot)`
> assignment, build a `Proposal` and ask `compliance.judge` once. An assignment whose
> `Evaluation.binding` is non-empty is **forbidden** in the model (its variable is fixed to 0);
> `PREMIUM` results are legal and are carried as metadata for CS-016c to price. The solver never
> re-derives legality, and every exclusion is traceable to a `rule_id` + `reason`.

Legality evaluation is therefore a **pure, injectable** step: the model builder takes a
`legality` callable so the CP-SAT encoding is unit-testable offline with a stub, with no DB and no
compliance snapshot.

## Deliverables (all under `coffee-scheduler/`)

### 1. `requirements.txt`
Add `ortools==9.15.6755` with a comment noting: CS-016b/F11 needs CP-SAT; it is the only release
with a cp314 wheel; and its declared `protobuf>=6.33.1` conflicts with the deliberately pinned
`opentelemetry-proto==1.29.0` (`protobuf<6.0`). The clash is **unsatisfiable on Python 3.14** and
is **metadata-only** — CP-SAT solves to OPTIMAL under protobuf 5.29.6 and the OTLP exporter imports
under 6.33.6, both verified; CI installs `requirements.txt` then `otel/requirements.txt` (so
protobuf settles at 5.29.6) and does not run `pip check`. Upgrading the OTel line is a separate
follow-up, not this story.

### 2. `agents/scheduler/shifts.py` — the slot grid (pure)
- `ShiftSlot` (frozen): `location_id, day, start_hour, end_hour` derived from
  `StoreOpenWindow` and the `shift_rules` min/max length + 30-minute increment rule.
- `build_slot_grid(inputs) -> tuple[ShiftSlot, ...]`: candidate slots per store-day, honouring
  open hours (half-open `open_hour <= h < close_hour`), min/max shift length, the 30-minute
  increment, and **no split shifts** (one contiguous span per employee-day).
- `parse_rule_value(shift_rules, rule_id)` helpers that read the typed `value`/`unit` strings from
  `ShiftRuleInput` — fail closed (`SolverModelError`) on an unparseable or missing rule rather
  than substituting a default.

### 3. `agents/scheduler/constraints.py` — the CP-SAT model
- `SolverModelError(Exception)` base; `InfeasibleModelError(SolverModelError)` carrying the
  binding constraint names.
- `LegalityVerdict` (frozen): `allowed: bool`, `binding: tuple[tuple[str, str], ...]`
  (`rule_id`, `reason`), `premiums: tuple[tuple[str, Decimal], ...]`.
- `build_model(inputs, *, slots=None, legality=None) -> SchedulingModel` where `SchedulingModel`
  (frozen) holds the `cp_model.CpModel`, the `assign` variable map
  `{(employee_id, slot_index): BoolVar}`, the per-group **assumption literals**, and the
  `excluded` map `{(employee_id, slot_index): LegalityVerdict}`.
- **Hard constraints**, each behind its own named assumption literal so infeasibility is
  attributable:
  - `legality` — assignments with binding rules fixed to 0.
  - `coverage_min_crew` — per store-hour minimum crew from `coverage_rules`.
  - `coverage_key_holder` — a key-holder present at open and at close.
  - `coverage_fpc` — at least one `food_protection_cert` holder on site whenever open.
  - `coverage_peak_skill` — the required count of `skill_level == 3` staff at peak hours.
  - `coverage_break_relief` — crew floor maintained across each break window.
  - `station_capacity` — concurrent assignments per store-hour ≤ `stores.stations`, and headcount
    ≤ `max_staff_on_floor`.
  - `shift_rules` — min/max shift length, 30-minute increments, **max 5 consecutive days**,
    **10 hours rest** between an employee's consecutive shifts, no split shifts, and
    `min_hours_week <= assigned hours <= max_hours_week`.
  - `home_location` — an employee may only work outside `home_location_id` when
    `cross_location_eligible`.
- `diagnose_infeasibility(model) -> tuple[str, ...]`: on `INFEASIBLE`, return the constraint-group
  names from `solver.SufficientAssumptionsForInfeasibility()` mapped back to readable labels —
  never a bare "infeasible". `assert_feasible(...)` raises `InfeasibleModelError` carrying them.
- A `legality_from_compliance(conn_or_dsn, inputs)` adapter that wraps `load_snapshot`/`judge`
  into the injectable `legality` callable, memoised per `(employee, location, weekday, span)` so
  the per-slot evaluation stays affordable.

### 4. `test/test_cs016b_constraints.py`
`unittest`, ends with `unittest.main()`, passes under both `unittest discover` and `pytest`; every
test tagged `# spec: F11.acN`.
- **Offline** (no DB, no compliance): a tiny 1-store/2-employee fixture with a **stub legality**
  callable. Assert each constraint group independently: a satisfying assignment solves to
  `OPTIMAL`/`FEASIBLE`; violating each constraint makes the model `INFEASIBLE` **and** the
  diagnosis names that group; a legality-forbidden assignment is excluded with its `rule_id`
  surfaced; an unparseable shift rule fails closed.
- **DB-gated** (skip-gate + rollback-only, pattern from `test/test_cs016a_inputs.py`): assemble
  real `SolverInputs`, build the model with `legality_from_compliance`, and assert it builds and
  that every exclusion carries a real `rule_id`. Keep it small (one store, a few days) so it is fast.

### 5. `docs/sdd-cs016b.md`
ACs **F11.ac8–ac14**, ids written as `**F11.ac8**:` — the traceability gate's regex is
`\*\*(F\d+\.ac\d+)\*\*`, so the **colon must sit outside the bold**:
`ac8` legality is pre-computed via CS-013 and encoded, never recomputed in the solver;
`ac9` coverage (min crew, key-holder open/close, FPC, peak-skill, break relief);
`ac10` station capacity and max staff on floor; `ac11` shift rules (length, increments, 5
consecutive days, 10h rest, no split shifts, weekly hour bounds); `ac12` home-location /
cross-location eligibility; `ac13` infeasibility names the binding constraint group(s);
`ac14` the model carries no objective (CS-016c owns it) and the encoding is deterministic.

### 6. `FEATURES.md` (F11) + `Backlog.md` (CS-016b)
A short `- **CS-016b (hard constraints, done):** …` bullet pointing at `docs/sdd-cs016b.md`
(F11.ac8–ac14). Do **not** change F11's status. No hedge words (`etc./TBD/TODO/and so on/and more`).
Set CS-016b's Backlog status to `✅ Done (2026-11-23)` with a one-paragraph summary and leave every
other entry untouched.

## Roles

**Codex** develops the code **and** the tests. **Claude** executes the three test categories and
independently verifies the diff (D1–D9 patterns, determinism, fail-closed behaviour) before
commit. Commit test-first, then the implementation.

## The three test categories

1. **Unit (offline)** under both `python3 -m unittest discover -s test` and `pytest`.
2. **DB-gated** rollback-only against live Postgres (compose up, `alembic upgrade head`, reseed
   ETL + rule templates).
3. **Static gates + round-trip** — `ruff check --config ruff.toml` clean on new files;
   spec-traceability, test-first, spec-lint, review-checklist (`--base origin/main`); migration
   round-trip green. **Note: the round-trip test wipes seed data — reseed after it.**

## Out of scope

The objective function and cost weights (CS-016c), the solver runner / schedule output object and
tuning (CS-016d), any DB migration, any change to `compliance/engine.py` or CS-016a's adapters,
and upgrading the OTel dependency line.
