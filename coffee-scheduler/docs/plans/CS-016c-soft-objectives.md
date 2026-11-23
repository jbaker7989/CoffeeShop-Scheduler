# CS-016c — Soft objectives & cost function (Plan)

## Goal and verified starting point

CS-016b produces a model whose feasible solutions are the legal, adequately-covered schedules but
carries **no objective**: every feasible schedule is equally good. CS-016c adds the weighted
objective the solver minimises, with tunable weights and a documented default weighting. It adds
**no new hard constraint** and **no runner** (CS-016d).

Verified on `origin/main` (`6119673`):

- `agents/scheduler/constraints.py` exposes `SchedulingModel(model, assign, assumptions, excluded)`
  where `assign` is `{(employee_id, slot_index): BoolVar}`, plus `build_model`,
  `diagnose_infeasibility`, `PER_ASSIGNMENT_METRICS` and `legality_from_compliance`.
- `agents/scheduler/shifts.py` exposes `ShiftSlot(location_id, day, start_hour, end_hour)` with a
  `.hours` property, `build_slot_grid`, and the canonical `SR-*` rule-id constants.
- `agents/scheduler/inputs.py` supplies `CostTargetInput(location_id, target_labor_pct_of_sales,
  weekly_overtime_budget_usd, understaffing_penalty_weight, premium_penalty_weight)`,
  `ThroughputInput(product_category, prep_role, seconds_per_unit, units_per_labor_hour)` plus
  `per_transaction_overhead`, `EmployeeInput.hourly_rate`, and `demand` as a contract `ForecastSet`.
- Coverage rules carry `COV-UTIL-TARGET` `0.85 PERCENT` and `COV-FORECAST-QUANTILE` `0.7`.

## Three gaps to close first (they are the reason this plan exists)

1. **`SchedulingModel` does not expose the slot grid.** `assign` is keyed by `slot_index`, so the
   objective cannot turn an assignment into hours, a store or a day. Re-deriving the grid and
   hoping the indices line up is exactly the kind of implicit coupling CS-016a was built to avoid.
   **Add `slots: tuple[ShiftSlot, ...]` to `SchedulingModel`** and populate it in `build_model`.
2. **Premiums are computed and then thrown away.** `LegalityVerdict.premiums` is produced for every
   candidate, but `SchedulingModel.excluded` keeps only *blocked* assignments, so the priced
   `PREMIUM` verdicts for *legal* assignments are lost — and pricing premiums into the objective is
   explicitly in CS-016c's scope. **Add `premiums: dict[tuple[int, int], Decimal]`** to
   `SchedulingModel`, carrying the per-candidate premium total for allowed assignments.
3. **There is no sales forecast, so "labour % of sales" cannot be computed directly.** The CS-012
   contract carries demand in **items** only; `forecast_features.revenue` is training-only and
   CS-014d deliberately excludes it from inference. Therefore derive a **revenue-per-item prior**
   per `(location, category)` from the named history feature set — the same documented-prior
   pattern CS-014d uses — and record its provenance. If no prior is available the term is
   **omitted and the omission is recorded on the returned object**, never silently treated as zero.

## Design decisions

- **Integers only.** CP-SAT needs integer coefficients: money in **cents**, time in **half-hour
  units** (matching `int(slot.hours * 2)` in CS-016b). Every conversion is explicit and rounded
  once, at the boundary; no float money reaches the model.
- **Terms are separable and individually inspectable.** `build_objective` returns a frozen
  `ObjectiveTerms` carrying each term's CP-SAT expression *and* its integer weight, so a test can
  assert one term's behaviour without the others, and CS-016d can report an objective breakdown
  (which it needs for explainability).
- **Weights are data, not literals.** `ObjectiveWeights` is a frozen dataclass with documented
  defaults; per-store penalty weights come from `cost_targets`. The defaults and their trade-offs
  are documented in the SDD, not buried in code.
- **Soft means soft.** No term may make a feasible schedule infeasible. Shortfalls and overtime are
  modelled as non-negative slack variables that are *penalised*, never forbidden.

## Deliverables (all under `coffee-scheduler/`)

### 1. `agents/scheduler/constraints.py` (small extensions only)
Add `slots` and `premiums` to `SchedulingModel` as above. No change to any constraint.

### 2. `agents/scheduler/objective.py`
- `ObjectiveError(Exception)`.
- `ObjectiveWeights` (frozen): `labour_cost`, `understaffing`, `overtime`, `premium`,
  `labour_pct_breach` — documented integer defaults.
- `required_labour_hours(demand, throughput, *, utilisation_target)` — **pure**: converts the
  forecast's p70 items for a `(location, hour)` into required labour hours via
  `seconds_per_unit`, divided by the utilisation target so 85% utilisation is the planning basis.
  Unit-testable with plain inputs and no model.
- `revenue_priors(conn, history_feature_version)` — per `(location, category)` average
  revenue-per-item from the training feature set, for the labour-% term; returns `{}` when absent.
- `build_objective(model, inputs, *, weights=ObjectiveWeights(), revenue_priors=None) -> ObjectiveTerms`
  building, then minimising, the weighted sum of:
  - **labour cost** — `assign × int(slot.hours × hourly_rate × 100)` cents;
  - **understaffing** — per open `(store, hour)`, a non-negative slack for
    `required_staff − scheduled_staff`, weighted by that store's `understaffing_penalty_weight`;
  - **overtime** — per employee, a non-negative slack for hours beyond the weekly overtime
    threshold, priced against `weekly_overtime_budget_usd`;
  - **premiums** — `assign × premium_cents` from `SchedulingModel.premiums`, weighted by that
    store's `premium_penalty_weight`;
  - **labour-% breach** — a non-negative slack for labour cost above
    `target_labor_pct_of_sales × forecast sales`, where forecast sales come from the revenue
    prior; omitted and recorded when no prior exists.
- `ObjectiveTerms` (frozen): each term's expression, its weight, the slack variables, and
  `omitted: tuple[str, ...]` naming any term that could not be built and why.

### 3. `test/test_cs016c_objective.py`
`unittest`, ends with `unittest.main()`, passes under both runners, every test tagged `# spec: F11.acN`.
- **Offline, on a small synthetic feasible fixture** (the CS-016b fixture pattern plus a stub
  legality): each term is asserted independently — a cheaper-but-legal schedule is preferred; an
  understaffed hour costs more than a staffed one; overtime beyond the threshold is penalised;
  a premium-bearing assignment is dispreferred at equal cost; raising one weight changes the
  chosen solution in the documented direction; and **no term makes a feasible model infeasible**.
- Determinism: fixed seed and `num_search_workers=1`; the same inputs give the same objective value.
- **DB-gated** (skip-gate + rollback-only): build real inputs for **one forward school day**
  (2026-12-07, per BUG-069) and assert the objective builds, is integral, and records any omitted
  term. **Keep it to one day** — BUG-073 makes per-candidate legality expensive and CI's Test suite
  is already ~14 minutes.

### 4. `docs/sdd-cs016c.md`
ACs **F11.ac16–ac21**, ids written `**F11.ac16**:` with the **colon outside the bold** (the
traceability gate regex is `\*\*(F\d+\.ac\d+)\*\*`). Document the default weights and their
trade-offs in a table, and state plainly that the labour-% term rests on a derived revenue prior.

### 5. `FEATURES.md` (F11) + `Backlog.md` (CS-016c)
Short `- **CS-016c (soft objectives, done):** …` bullet; do **not** change F11's status; no hedge
words. Set CS-016c's status to `✅ Done (2026-11-23)` with a one-paragraph summary and leave every
other entry untouched.

## What this plan will NOT claim

CS-016c is verified on **small synthetic feasible models**, not on a real full week, because
**BUG-072** (weekly hour bounds applied to any window, so a sub-week window is infeasible by
construction) and **BUG-073** (a compliance snapshot per candidate span, so a full week does not
finish) make a real full-week optimisation unobtainable today. The DB-gated test therefore asserts
the objective *builds and is well-formed* on real inputs, not that it optimises a real week. Any
claim of a cost-optimised weekly schedule belongs to CS-016d, after those two bugs.

## Roles

**Codex** writes the code **and** the tests. **Claude** executes the three test categories and
verifies independently before commit. Commit test-first, then the implementation.

## Out of scope

The solver runner, output object and tuning (CS-016d); any new hard constraint; BUG-072 and
BUG-073; call-out re-plan premiums beyond pricing what CS-013 already returns (CS-018).
