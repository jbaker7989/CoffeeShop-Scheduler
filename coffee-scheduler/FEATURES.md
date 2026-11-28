# Coffee Shop Scheduler — Feature Backlog

Features yet to be built for the Predictive Smart Shift Scheduler (build target #4), ordered by development priority. Phase 1 is the multi-agent scheduling hub; Phase 2/3 features are listed at the end as design-only.

**Core principle:** the LLM never produces schedules or forecasts. A statistical/ML model forecasts, OR-Tools CP-SAT builds the schedule, and the LLM converses, routes tools, explains decisions and negotiates with staff.

**Legend**
- **Status:** `Blocked` · `Ready` · `In design` · `Not started`
- **Size:** `XS` (<0.5d) · `S` (~1d) · `M` (2–4d) · `L` (1–2wk) · `XL` (multi-week)
- **Priority:** `P0` (blocks everything downstream) · `P1` (core Phase 1) · `P2` (needed for a credible demo) · `P3` (hardening / later phase)

---

## Phase 1 — Data & foundation (unblocks the engine)

### F1. Weather data pipeline run & validation
- **Status:** Done (closed 2026-11-11 via CS-001)
- **Size:** S · **Priority:** P0
- **Description:** `data/weather_forecast.xlsx` exists with `Hourly_Sales_Weather` (9,231 rows), `Daily_Sales_Weather` (543 rows × 3 stores) and a WMO `Weather_Codes` lookup. The script that produced it lives at `data/build_weather_forecast.py` (57 lines; hits Open-Meteo's `archive-api` for historical reanalysis, not the forecast endpoint — the filename is a misnomer). All three sub-tasks closed: (a) `data/VALIDATION.md` documents the join sanity-check — PASS 5/5 across 36 sampled rows and the zero-fill / trading-hours invariants; (b) `ATTRIBUTION.md` records the Open-Meteo CC BY 4.0 license and the free-tier non-commercial-only constraint; (c) `requirements.txt` pins `pandas 3.1.1`, `openpyxl 3.1.5`, `requests 2.34.2`. CS-001 in `Backlog.md` is the closing story.
- **Expected outcome:** Verified weather×sales join covering all 3 stores; Open-Meteo license recorded so the data is never accidentally shipped commercially; script's Python deps pinned — all three satisfied.

### F2. Roster scheduling fields
- **Status:** Blocked (on roster extension) · Ready to start
- **Size:** M · **Priority:** P0
- **Description:** Extend `coffee_shop_staff.xlsx` (currently identity-only) with the fields the solver needs: roles, skill levels, home location, cross-location eligibility, key-holder status, cash authorisation, Food Protection Certificate holder, hourly rate, school enrolment, availability personas, and min/max hours per week. Minor occupation restrictions map to roster roles.
- **Expected outcome:** A roster the CP-SAT solver can actually schedule against; without this the solver has no inputs.
- **Regression acceptance — BUG-001:**
  1. **AC1 — complete allocation:** The generator derives each cohort's store quotas from the actual adult/minor partitions, and quota totals equal the partition totals.
  2. **AC2 — future-date coverage:** With `AS_OF` set so the roster has 33 adults and 7 minors, generation assigns every employee and writes all 15 `NEW_COLS`; it must not raise `KeyError`.
  3. **AC3 — infeasible roster:** If the adult partition cannot supply the per-store leadership/skill floor, generation stops before workbook mutation with a descriptive typed error that names the cohort and required minimum.

### F3. School calendar load
- **Status:** Not started · **Size:** S · **Priority:** P0
- **Description:** Load the NYC public schools 2026–27 calendar (school days, holidays, breaks) as effective-dated data. Every minor-labor rule (hours on school days vs. non-school days, nightly cutoffs) depends on it. Scope (years covered) is set by **F25** — load 2023–2028, not just 2026–27.
- **Expected outcome:** A calendar the compliance engine can query by date so minor constraints resolve correctly, with multi-year coverage.

### F4. Closing-hours decision & operating-hours config
- **Status:** Not started · **Size:** XS (decision) + S (config) · **Priority:** P0
- **Description:** Resolve the open business decision: sales stop ~8–9 PM at every store and Astoria opens at 7 AM. Either keep 10:30 PM close with minimum evening crew, or change operating hours. Encode the chosen hours per store (including the 45 min pre-open / post-close) in `coffee_shop_labor_standards.xlsx`.
- **Expected outcome:** Settled, machine-readable operating hours per store that bound the solver's time horizon.

### F5. Assumption calibration (throughput, multipliers, targets)
- **Status:** Ready (F1 done; weather join unblocked this)
- **Size:** M · **Priority:** P1
- **Description:** Calibrate the synthetic/assumed numbers using the `Daily_Sales_Weather` join from F1: throughput seconds per item for all 9 categories plus per-transaction register overhead (currently overstated — no basket ID); weather multipliers in `Demand_Adjustments` (`ADJ-RAIN`, `ADJ-COLD`, `ADJ-HEAT`, `ADJ-SNOW`) derived from the join; event multipliers need a separate ingest pipeline (F27); target utilisation (85%), forecast quantile (P70), labour % targets, and the overtime budget. Deliverable includes a back-test period (e.g. last 4 weeks of the 6-month sales window) and an acceptance band (e.g. ≤±10% MAPE per category-hour) so "calibrated" is measurable. Every accepted change must append to the `Calibration_Log` sheet in `coffee_shop_labor_standards.xlsx` (id, table, row_key, planned/observed/proposed values, sample period, approver).
- **Expected outcome:** Defensible demand and cost parameters with a logged history of every change — so forecast and solver outputs are realistic rather than arbitrary, and the trail is auditable.
- **Regression acceptance — BUG-003:**
  1. **AC1 — initial calibration:** A workbook whose throughput and demand-adjustment rows are uncalibrated may be calibrated once, marks the applicable rows `CALIBRATED`, and records its audit entries.
  2. **AC2 — repeat-run protection:** A second calibration run against that workbook exits with a descriptive already-calibrated error before mutation; throughput values, demand adjustments, and `Calibration_Log` remain unchanged.
  3. **AC3 — mixed-state protection:** If any applicable throughput or demand-adjustment row is already `CALIBRATED`, calibration refuses the mixed-state workbook before appending a log row or changing another row.

---

## Phase 1 — Agent design & contracts

### F6. AGENTS.md + SKILL.md authoring
- **Status:** Done (2026-11-26) — CS-010 completed; root and four agent guides, five reusable skills, and offline documentation-contract tests are in place · **Size:** M · **Priority:** P1
- **Description:** Author practical, repository-consistent instructions for the root project, the scheduler, labor-compliance, demand-forecast, and staff-comms agents, plus the reusable procedures for labor-rule evaluation, synthetic roster generation, sales/weather joins, standards calibration, and agent-card authoring. Keep canonical policies in one place and link to them from agent-specific guidance.
- **Acceptance criteria:**
  1. **AC1 (F6.ac1)** — The root `AGENTS.md` documents the actual stack and layer ownership, data/PII policy, required test commands, environment-gated tests, and fail-closed/security conventions that match repository configuration.
  2. **AC2 (F6.ac2)** — Each of the four runtime agent directories has an `AGENTS.md` with its owned responsibilities, explicit non-responsibilities, relevant inputs/outputs, and links to the applicable shared skills.
  3. **AC3 (F6.ac3)** — Each of the five named skills exists, provides actionable procedure and safety/validation steps, and does not invent or conflict with canonical contracts, business rules, or implementation behavior.
  4. **AC4 (F6.ac4)** — Relative file references introduced by the CS-010 guidance resolve from the document containing each reference, and required file inventories are checked by an offline automated test.
  5. **AC5 (F6.ac5)** — Runtime configuration and test instructions name real files/commands; no CS-010 document instructs an agent to commit secrets, real PII, or synthetic data as production truth.
  6. **AC6** — The changed-file traceability and review scripts inspect scheduler files when the project is nested within a Git checkout.
- **Edge / adversarial cases:** A referenced file is renamed or missing; an agent's role is unclear or overlaps another; a skill's behavior disagrees with the implementation; a test command silently omits gated integrations; a document suggests that synthetic fixtures are customer data; an example contains a real secret or person identifier; instructions claim a component exists when it is still scaffolded.
- **Cross-references:** `coffee-scheduler/AGENTS.md`; `agents/{scheduler,labor_compliance,demand_forecast,staff_comms}/AGENTS.md`; `skills/{labor-rule-evaluation,synthetic-roster-generation,sales-weather-join,standards-calibration,agent-card-authoring}/SKILL.md`; `test/SKILL.md`; F7; CS-001, CS-006, CS-010, CS-011, CS-013, CS-021.
- **Expected outcome:** People and agents can follow one verified set of project conventions and reusable procedures without inventing policy or duplicating domain rules.

### F7. Agent Cards (A2A contracts)
- **Status:** Implemented and validated on the CS-011/015 branch
- **Size:** M · **Priority:** P1
- **Description:** Publish authenticated A2A cards for the Scheduler, Demand Forecast, Labor Compliance and Staff Comms runtimes. Design cards cover Inventory, Prep and Loyalty. Payroll/HCM export is an n8n workflow under F16 and CS-083.
- **Expected outcome:** Every runtime boundary has a versioned, authenticated contract; one agent built in a second framework proves interoperability; the Payroll/HCM question is settled (runtime agent or n8n-only).

- **Acceptance criteria:**
  - **AC1 (F7.ac1)** — Each runtime agent publishes an A2A 0.3 JSON-RPC card with a callable versioned skill.
  - **AC2 (F7.ac2)** — Service requests without an authorized tenant, audience and operation are rejected before domain execution.
  - **AC3 (F7.ac3)** — Inventory, Prep and Loyalty are non-callable design cards and Payroll/HCM remains an F16 n8n export.
  - **AC4 (F7.ac4)** — A Scheduler client decodes a deterministic F8 forecast returned by the independent ADK worker.
  - **AC5 (F7.ac5)** — Staff Comms renders a versioned template preview without contacting a recipient.
  - **AC6 (F7.ac6)** — Labor Compliance evaluates a typed proposal against the verified tenant scope without writing audit records.
- **Edge / adversarial cases:** Wrong protocol or credential audience, malformed messages, extra authority fields, forged tenant scope, unknown templates, missing calendar or evaluation evidence.
- **Cross-references:** F8, F10, F12, F15, F16, F61, F78. CS-083 delivers reconciled payroll exports via n8n.

### F8. Demand Forecast output contract
- **Status:** ✅ Done (2026-11-21) · **Size:** S · **Priority:** P1
- **Description:** Define the forecast output schema: prediction by product category, per hour, per store, with prediction intervals (P70 and bounds). This is the interface the solver and explanation layer consume regardless of the underlying model. Category vocabulary is owned by **F29** — the contract must reference the versioned taxonomy, not invent its own.
- **Acceptance criteria:**
  - **F8.ac1** — Every forecast record's `product_category` is a member of the pinned taxonomy vocabulary; an unknown category is rejected and an empty vocabulary fails closed.
  - **F8.ac2** — A forecast set pins one `contract_version`, `taxonomy_version` and `model_version`; a record disagreeing with the set is rejected.
  - **F8.ac3** — Prediction interval holds `0 <= lower <= p70 <= upper`; missing or non-finite values are rejected.
  - **F8.ac4** — `forecast_hour` is timezone-aware on an exact clock hour; naive or sub-hourly values are rejected.
  - **F8.ac5** — `location_id` is a known store and the hour falls inside that store's open window on an open day; closed hours are omitted, never emitted as null.
  - **F8.ac6** — A forecast set holds at most one record per `(location_id, product_category, forecast_hour)`.
- **Expected outcome:** A stable contract that decouples model choice from downstream consumers and pins the category taxonomy.

---

## Phase 1 — Core engine (deterministic)

### F9. Demand forecast model
- **Status:** In progress (CS-014a complete 2026-11-21 — the feature dataset; model = CS-014b, backtest = CS-014c) · **Size:** L · **Priority:** P1
- **Description:** Choose, train and validate a statistical/ML forecast model on 6 months of 2023 sales joined with weather. Must handle the data limits openly: no annual seasonality (6 months), no basket ID, no evening sales history. Produce per-category hourly forecasts with intervals per F8; define a validation/backtest approach.
- **Acceptance criteria:** the umbrella F9 criteria are defined per sub-story in the SDDs: `docs/sdd-cs014a.md` owns `F9.ac1–ac7`, `docs/sdd-cs014b.md` owns `F9.ac8–ac13`, and `docs/sdd-cs014c.md` owns `F9.ac14–ac20`.
- **CS-014a (feature dataset, done):** `agents/demand_forecast/feature_build.py` builds a versioned, RLS-scoped `forecast_features` table (+ `feature_sets` registry, migration `0019`) from canonical Postgres — one 0-filled row per open store-hour × taxonomy category over Jan–Jun 2023, with calendar/weather/rolling features and honest `has_basket_id`/`has_evening_history`/`has_annual_seasonality` masks. Criteria `F9.ac1–ac7` in `docs/sdd-cs014a.md`.
- **CS-014a accuracy integrity (done):** `low_history` no longer sums the target across the reserved 28-day holdout, `revenue` is barred from any trainer, holiday reads are jurisdiction-scoped, and the rolling-mean semantics are pinned; the feature version is `forecast-features-v2`. A committed baseline in `reports/forecast_accuracy_baseline.json` plus `test/test_forecast_accuracy_baseline.py` make holdout accuracy a guarded property, so a feature or model change that degrades it fails. Criteria `F9.ac23–ac26` in `docs/sdd-cs014a.md`.
- **CS-014b (model, done):** `agents/demand_forecast/model.py` trains deterministic empirical-quantile and linear-quantile candidates, selects by validation pinball loss, persists the selected versioned artifact, and emits contract-valid interval forecasts. Criteria `F9.ac8–ac13` in `docs/sdd-cs014b.md`.
- **CS-014b reopen (acceptance metric):** adds a count-shrinkage candidate (`F9.ac21–ac22`) and the finding that the `≤10%` per-group MAPE gate is a metric mismatch for small-count demand; the memo `docs/decisions/CS-014b-acceptance-metric.md` recommends a WAPE plus pinball plus coverage gate for CS-014c to ratify.
- **CS-014c (backtest, done — gate passes):** the acceptance metric was ratified on 2026-11-23 (`docs/decisions/CS-014b-acceptance-metric.md`) and the gate re-specified as volume-weighted WAPE plus calibration, because per-group MAPE is arithmetically hostile to small-count demand. Overall WAPE 54.14% against a 60% band; worst volume-gated group 79.0% against an 85% band; calibration within 8pp of nominal. Groups below a 1000-unit volume floor are reported as diagnostics rather than gated, and the gate fails closed when no group clears the floor. `agents/demand_forecast/backtest.py` runs a strictly time-separated, rolling-origin backtest of the persisted CS-014b model on the final 28 days of the registered source window. It reports per-store/category/clock-hour MAPE, P70 hit rate and P10–P90 interval coverage, records zero-actual and unscorable groups, and gates the ≤10% F5 band without allowing a macro-average to conceal failures. F9.ac14–ac20 tests and the full CI-equivalent suite pass.
- **CS-014d (forward inference, done):** `forecast_features` is a training table whose target is `NOT NULL`, so a future week cannot be written to it without inventing demand. `agents/demand_forecast/inference.py` instead builds target-free inference rows in memory and `model.predict(..., rows=...)` scores them, which lets the scheduler forecast a forward week while the roster stays valid for its own era. Unknowables stay explicit: the previous open hour is null, the trailing same-hour mean and low-history flag come from the named training set's priors, and weather absent from `weather_hourly` stays null for the model to impute. Criteria `F9.ac27–ac29` in `docs/sdd-cs014d.md`.
- **Expected outcome:** A forecaster that drives staffing need per hour with honest uncertainty — the demand input to the solver.

### F10. Labor-compliance rule engine
- **Status:** Done (CS-013, 2026-11-12) · **Size:** L · **Priority:** P1
- **Description:** Build the engine that evaluates the effective-dated rules with strictest-wins resolution and exposes them as hard constraints to the solver and as explanations to the LLM. Covers meal breaks, spread-of-hours (Part 146), Fair Workweek premiums & clopening (per-employer flag), Earned Safe/Sick Time (no requirement for a sick employee to find cover), Food Protection Certificate on site, and minor hours/occupation limits computed from DOB on the shift date. **Sub-tasks the engine must own explicitly:**
  - **Rule_Eval_Log write path** — every evaluation inserts into Postgres `rule_eval_log` (`eval_id, schedule_id, shift_id, employee_id, rule_id, result, premium_cost_usd, evaluated_at, agent`). The xlsx sheet is not the write target. No silent passes.
  - **Waiver expiry evaluation** — for any rule marked `waivable_by_consent`, re-check `Consent_Waivers.valid_from` / `valid_to` against the shift date on every shift generation, not just at rule load. Example: WV-0001 (EMP-1033, NY-MINOR-1617-SCH-WINDOW) expires 2027-06-26.
  - **Coverage_Rules ↔ Labor_Rules reconciliation** — `Coverage_Rules.COV-FPC` says "LAW — see NYC-FPC-COVERAGE"; the engine must fail closed if a coverage rule and a legal rule ever disagree about whether a constraint is HARD vs SOFT.
- **Acceptance criteria:**
  1. **AC1** — A complete adult proposal with a sufficient break returns one result per applicable rule, all `PASS` or waived `PASS`, and `binding` is empty.
  2. **AC2** — An applicable hard violation returns `FAIL` with that `rule_id` in `binding` and in `reason`. The function does not change the shift.
  3. **AC3** — Two applicable rules on the same metric both appear in `results`; `binding` keeps only the stricter hard bound.
  4. **AC4** — A waiver whose `valid_to` is the shift date does not waive; the previous local date does.
  5. **AC5** — Age, school day, and spread use the tenant timezone and the shift date, not `date.today()` or the UTC date.
  6. **AC6** — An enrolled employee with no `school_calendar_days` row for the shift date raises; it does not pass.
  7. **AC7** — `week_complete=false`, or omitted crew when an on-site rule applies, raises; it does not pass.
  8. **AC8** — Fair Workweek rules do not apply when `fww_covered` is false, and do apply when it is true.
  9. **AC9** — A sick call-out cannot be satisfied by requiring the employee to find cover; the ESSTA rule id is in the reason.
  10. **AC10** — `persist=True` inserts one append-only row per applicable rule and raises if `shift_id` is not in `shifts`.
  11. **AC11** — Evaluation with no visible tenant, or as a role that bypasses RLS, raises. It does not return `PASS`.
  12. **AC12** — An unknown `(metric, operator, unit)` raises before any shift is judged legal.
  13. **AC13** — `reconcile_coverage` raises when a `LAW` coverage row cites a missing rule or a non-blocking legal rule.
  14. **AC14** — `claims_legal_accuracy` is false when any applied rule is not `VERIFIED`, and the illegal shift still fails.
  15. **AC15** — a `role_eligibility` rule is evaluated as a pipe-separated allow-list of permitted roles for its age band: a listed role passes, an unlisted role fails and binds, and a value the engine cannot read raises a configuration error rather than passing silently.
- **Edge / adversarial:** Waiver expiry is half-open. Age flips on the birthday. A 05:15 start must take a midway meal when the early-start rule's condition matches. Prose occupation text raises for a 14–15 year old instead of passing. Empty tenant context is an error, not an all-pass.
- **Cross-references:** `labor_rules`, `consent_waivers`, `school_calendar_days`, `employer_config`, `coverage_rules`, `rule_eval_log`. CS-004 calendar. CS-021 owns the VERIFIED gate. CS-016 consumes `binding` and does not recompute legality.
- **Expected outcome:** A single authoritative legality service that writes every decision to `rule_eval_log`, honours waiver expiry on the actual shift date, and never silently contradicts the coverage rules — the solver and LLM never compute legality themselves.

### F11. CP-SAT schedule solver
- **Status:** Blocked (on F2, F4, F9, F10) · **Size:** L · **Priority:** P1
- **CS-016a (input adapters, done):** Typed, read-only solver input assembly and cross-validation are specified in [docs/sdd-cs016a.md](docs/sdd-cs016a.md) (F11.ac1–ac7).
- **CS-016b (hard constraints, done):** Deterministic CP-SAT legality, coverage, capacity, shift, and location constraints are specified in [docs/sdd-cs016b.md](docs/sdd-cs016b.md) (F11.ac8–ac14).
- **CS-016c (soft objectives, done):** Integer-only labour, coverage, overtime, premium, and sales-prior objective terms are specified in [docs/sdd-cs016c.md](docs/sdd-cs016c.md) (F11.ac16–ac21).
- **CS-092 (build scaling, done):** Slot lookups are built once and the minimum-rest rule is encoded as one no-overlap constraint per employee over optional intervals, specified in [docs/sdd-cs092.md](docs/sdd-cs092.md) (F11.ac27-ac33).
- **CS-094 (open-hour coverage, done):** Open store-hours without any candidate slot now fail closed at model build time, while role shortages remain coverage-group infeasibility; specified in [docs/sdd-cs094.md](docs/sdd-cs094.md) (F11.ac34-ac40).
- **CS-093 (solver stopping rule, done):** A typed, validated `SolverStopPolicy` bounds the solve by a time budget, a post-solve accepted-gap publish guard records the CP-SAT status, best bound, relative gap and stopping reason on `SolveResult`, and no-incumbent / invalid-config / over-gap cases fail closed; specified in [docs/sdd-cs093.md](docs/sdd-cs093.md) (F11.ac41-ac50), with the real-scale default evidenced in [reports/cs093_solver_scale.md](reports/cs093_solver_scale.md).
- **CS-016d (solver runner, done):** `run_schedule` wires input assembly, model construction, objective installation, CS-093 solving, and pure shift decoding into a deterministic reviewable output with labour economics, premium rule ids, omitted-objective terms, and solver-quality evidence; specified in [docs/sdd-cs016d.md](docs/sdd-cs016d.md) (F11.ac51-ac59).
- **CS-088 (soft guaranteed hours, done):** Fully covered ISO weeks use a cents-priced, non-negative guarantee shortfall objective rather than a hard minimum; weekly maximums remain hard. Specified in [docs/sdd-cs088.md](docs/sdd-cs088.md) (F11.ac60-ac64).
- **CS-089 (one workweek, done):** ISO Monday-start workweeks now consistently scope weekly bounds, guarantees, overtime, and compliance proposal context; multi-week overtime is calculated per workweek. Specified in [docs/sdd-cs089.md](docs/sdd-cs089.md) (F11.ac65-ac69).
- **Description:** OR-Tools CP-SAT model emitting a legal, cost-aware weekly schedule. Hard constraints: legality and coverage. Soft objectives: labour % target, 85% utilisation, overtime budget, cost targets. Respects stations per store (LM/HK 4 stations/6 staff, Astoria 3/4) and fixed tasks. **The solver's input tables must be enumerated explicitly in the implementation so nothing is implicit:**
  - Forecast output (F8 / F9)
  - Roster (F2)
  - `Labor_Rules` evaluated per shift via F10
  - `Stations` (per-store station → role map)
  - `Hourly_Demand` (baseline per location × day-of-week × hour)
  - `Throughput_Standards` (seconds per unit by category)
  - `Coverage_Rules` (min crew, key-holder open/close, FPC, peak-skill, break relief)
  - `Fixed_Tasks` (open/close setup, deliveries, cold brew, pastry, inventory)
  - `Shift_Rules` (min/max length, 30-min increment, max 5 consec days, 10h rest, no split shifts, Opener/Mid/Closer templates)
  - `Cost_Targets` (labour % per store, overtime budget)
  - `School_Calendar` (F3) joined to roster school enrolment
- **Expected outcome:** A deterministic, legal, cost-aware weekly schedule whose inputs are an enumerated, reviewed list — the heart of the product.

---

## Phase 1 — Orchestration & conversation (LLM / LangGraph)

### F12. LangGraph state & schedule-generation graph
- **Status:** Implemented and validated on the CS-011/015 branch · **Size:** M · **Priority:** P1
- **Description:** Define LangGraph state and nodes for the schedule-generation flow: gather inputs → call forecast → call compliance → run solver → present result. LangChain wraps tools and prompt templates; the LLM routes between tools and never generates numbers.
- **Expected outcome:** A runnable orchestration that turns a request into a schedule end to end.

- **Acceptance criteria:**
  - **AC1 (F12.ac1)** — A valid request executes gather, forecast, compliance, solve and present in order and returns a versioned draft.
  - **AC2 (F12.ac2)** — A scheduling request rejects unsupported dates, windows and authority fields before domain work.
  - **AC3 (F12.ac3)** — The solver consumes the complete validated forecast for the requested model and calendar window.
  - **AC4 (F12.ac4)** — Compliance and solver inputs are scoped to the authenticated tenant even when domain identifiers overlap.
  - **AC5 (F12.ac5)** — A failed solve or unavailable compliance step produces a typed terminal error without a schedule.
  - **AC6 (F12.ac6)** — Transient forecast retries are bounded by a deadline and attempt limit.
  - **AC7 (F12.ac7)** — An authorized resumed request only reuses validated checkpoints from its own run.
  - **AC8 (F12.ac8)** — Intent routing cannot modify validated inputs or skip graph stages.
  - **AC9 (F12.ac9)** — Draft generation returns a safe projection without mutating forecasts, schedules or payroll.
- **Edge / adversarial cases:** Incomplete forecast grid, unsupported local hours, expired claim, duplicate claim, stale revision, unknown checkpoint schema, DB failure and unacceptable solver gap.
- **Cross-references:** F8, F9, F10, F11, F23, F30, F45, F60. CS-021 owns the statutory verification gate.

### F13. Schedule explanation layer
- **Status:** Done · **Size:** S · **Priority:** P2
- **Description:** Owners and managers can request a plain-language explanation of an assignment's compliance evidence. Explanations cite the engine's rule results and reasons for that shift.
- **Acceptance criteria**
  - **AC1 (F13.ac1)** — Every cited rule ID exists in the requested shift's evidence.
  - **AC2 (F13.ac2)** — Every number and verdict in the explanation is supported verbatim by its cited engine fact.
  - **AC3 (F13.ac3)** — The authenticated endpoint reads under the caller's tenant-scoped `app_ro` role and fails closed for missing or foreign shifts.
  - **AC4 (F13.ac4)** — A shift with no logged results gets live evidence from `judge()` or an insufficient-evidence response if no facts are available.
  - **AC5 (F13.ac5)** — Every response surfaces `claims_legal_accuracy=false` and flags rule verification as unconfirmed.
  - **AC6 (F13.ac6)** — A callable model stub can drive the explanation, and the evidence fallback works with no API key.
  - **AC7 (F13.ac7)** — The explanation layer uses the existing compliance engine and the pinned taxonomy vocabulary.
- **Edge / adversarial cases:** Fabricated IDs, numbers, verdicts, and legal claims trigger the evidence fallback. A missing API key, empty rule log, or shift with no usable compliance inputs produces a safe response.
- **Cross-references:** F10 and `rule_eval_log` for engine results, F29/CS-009 for taxonomy, F31/CS-031 for the weekly schedule, and F60/F61 for tenant isolation and authentication.
- **Expected outcome:** Managers can see the compliance evidence for a shift and the rules that produced it.

### F14. Call-out & swap workflow
- **Status:** Not started · **Size:** L · **Priority:** P2
- **Description:** Stateful LangGraph workflow for call-outs and swaps with long waits, loops and manager approval. Finds eligible, legal cover (skills, hours, minor limits, key-holder/FPC coverage) and **never requires a sick employee to find their own replacement** — this is the `NYC-ESSTA-NO-REPLACE` rule and must be cited by ID in the workflow's hard-stop branch so it is auditable. **Call-in pay premium**: `NY-CALLIN-HOSP` (paid the lesser of 3 hrs or scheduled shift on report-and-send-home) must be priced into the cost objective when a shift is cancelled after report time — the workflow surfaces the premium to payroll via F16. Both rules already exist in `Labor_Rules`; this feature wires them into behaviour.
- **Expected outcome:** Realistic day-of operations that respect ESSTA cover rights, price call-in pay, and produce a replacement candidate the manager can approve — the headline "smart" capability.

### F15. Staff Comms (SMS) + consent
- **Status:** Not started · **Size:** M · **Priority:** P2
- **Description:** Staff Comms agent for SMS negotiation: contact preferences, SMS consent tracking, message templates, inbound SMS handling. Exposed as an A2A service and wired to the call-out/swap graph.
- **Expected outcome:** Two-way staff communication that drives the call-out workflow.

---

## Phase 1 — Manager-facing UI

The Next.js scaffold at `coffee_shop/app/` exists but is stock `create-next-app` boilerplate — no scheduler routes, no API integration, no components. This section turns it into the manager-facing surface. F30 is the umbrella; F31–F35 are the individual screens.

### F30. Manager-facing web UI (umbrella)
- **Status:** ✅ Done (CS-030, 2026-11-16) — `coffee_shop/` wired to a FastAPI gateway (`api/`): same-origin `/api/*` rewrite, seeded demo login → HttpOnly JWT cookie, auth-gated manager shell, dashboard lists the tenant's stores via RLS. Integration pattern documented in `docs/architecture/manager-ui-integration.md`. · **Size:** M · **Priority:** P1
- **Description:** Claim ownership of `coffee_shop/app/` as the manager-facing surface for the scheduler. This feature owns the decisions the screens below share: Next.js → backend integration pattern (server actions vs API routes vs n8n-mediated), manager auth (single-tenant demo OK to start), the shared layout / navigation, and the convention for how each screen binds to the LangGraph state in F12. Each screen is a separate feature (F31–F35); this is the plumbing they all use.
- **Expected outcome:** A scaffolded Next.js app a manager can sign into, with shared navigation and a documented integration pattern the screens below can build on.
- **Operational acceptance — BUG-031:**
  1. **AC1** — Gateway lifespan startup and `/healthz` do not open the lazy PostgreSQL pool.
  2. **AC2** — When the gateway uses default database accessors, lifespan shutdown closes the pool, including after an exception; each app owns its pool and can allocate a fresh one on restart.

### F31. Weekly schedule view
- **Status:** ✅ Done (CS-031, 2026-11-27) — read-only weekly roster in the manager shell: tenant-timezone grid with station coverage and meal-break indicators, store/role/status/multi-week filters, and a shift→`rule_eval_log` panel. Backed by `agents/scheduler/persist.py` (solver output → `schedules`/`shifts`/`shift_assignments`/`rule_eval_log`), migration `0026` (`schedules.status` CHECK), and gateway `/schedule` + `/schedule/shift/{id}/rules` under `app_ro` RLS. · **Size:** M · **Priority:** P1
- **Description:** Managers and owners can read a store's published weekly roster, station coverage, and meal-break coverage. Selecting an assignment shows its recorded compliance results and reasons.
- **Acceptance criteria**
  - **AC1** — The verified owner or manager sees only the signed-in tenant's published roster for the requested location and week through an `app_ro` RLS session.
  - **AC2** — Shift dates, clock times, and dayparts use the tenant's IANA timezone, including shifts near midnight UTC.
  - **AC3** — The grid shows each assignment's employee, role, station, local start and end, and meal-break coverage, plus each station's staffing against its expected role.
  - **AC4** — Store, role, status, and week controls preserve tenant and week scope and show a graceful empty state when a week has no published roster.
  - **AC5** — Selecting a shift shows its recorded rule IDs, results, and reasons.
  - **AC6** — Superseded and soft-deleted schedules are hidden.
  - **AC7** — Schedule endpoints accept only read requests.
- **Edge cases:** Missing or forged credentials, routed-tenant mismatch, unknown location, empty week, uncovered station, and orphan or unassigned station IDs.
- **Cross-references:** CS-031, F10, F30, and the `schedules`, `shifts`, `shift_assignments`, `stations`, and `rule_eval_log` tables.
- **Expected outcome:** A manager can inspect who is scheduled, where they work, and which compliance rules were evaluated.

### F32. Call-out approval
- **Status:** Not started · **Size:** M · **Priority:** P2
- **Description:** UI surface for the F14 call-out/swap LangGraph workflow. Manager sees the sick employee's shift, the candidate cover the workflow proposed, the eligibility reasons (skills, hours, minor limits, key-holder/FPC), and approves or redirects. The workflow's hard-stop branches (e.g. `NYC-ESSTA-NO-REPLACE`) must be visible inline so the decision is auditable. Approving routes back to F16 to update payroll/HCM and to F10 to log the resolution. Two-way binding to LangGraph state — the screen is the human-in-the-loop step the workflow already waits on.
- **Expected outcome:** A manager can review and approve a replacement in one screen, with the legal reasons visible at decision time.

### F33. Forecast review
- **Status:** Not started · **Size:** M · **Priority:** P2
- **Description:** Chart overlay of P70 forecast vs. actuals by store × day × hour × category (per F8 contract). Used to (a) sanity-check the forecaster before trusting it, and (b) drive calibration cycles (F5). Back-test period selectable; click-through to per-hour detail with the weather join from F1 visible alongside. Read-only — calibration actions route through F5.
- **Expected outcome:** A manager can see "did the forecast match the day" and feed back into calibration without leaving the app.

### F34. Rules admin
- **Status:** Planned — not built · **Size:** M · **Priority:** P1 · Plan (reviewed & reconciled to the live schema 2026-11-13): [docs/plans/CS-034-rules-admin.md](./docs/plans/CS-034-rules-admin.md)
- **Description:** View / edit `Labor_Rules` rows with the F26 reconciliation check running live: any change that creates an assumption-vs-law conflict, marks an unverified rule as `LAW`, or sets coverage-rule effective dates that conflict with the underlying legal rule is blocked at submit. Surface the `Consent_Waivers` list with expiry warnings (per F10 waiver-expiry sub-task) so managers see WV-0001 expiring 2027-06-26 before it does. All edits route through F28 (rule-change rollout + `Rule_Eval_Log` audit row).
- **Acceptance criteria:**
  1. **AC1** — A manager views all `labor_rules` rows with `verification_status` badges (all three: `VERIFIED` / `VERIFY` / `ASSUMPTION`), `enforcement` (incl. `PAY_ONLY`), and F26 reconciliation flags.
  2. **AC2** — An unverified rule (`verification_status != 'VERIFIED'`) set to a blocking `enforcement` is blocked at submit with a single precedence-ordered message (plan §6; AC2/AC6 are one gate). Until CS-021 ships, the gate still fails closed.
  3. **AC3** — A coverage-rule effective-date conflict with the underlying legal rule is surfaced inline before save. **Requires** new migration `0002` adding `coverage_rules.validity` (no effective dates exist today); until it lands, AC3 is not implementable.
  4. **AC4** — The `consent_waivers` list shows expiry warnings with **half-open, tenant-local** semantics: WV-0001 (EMP-1033) is valid through 2027-06-25 and already expired on 2027-06-26.
  5. **AC5** — Every edit writes an F28 audit row to `rule_eval_log` with `agent='rules-admin-agent'` and before/after JSON.
  6. **AC6** — Setting a rule to blocking enforcement without a verified `source_url`/citation or a documented design-decision note is blocked (plan §6). The manager-supplied `source_url` fetch is **SSRF-guarded**.
  7. **AC7** — The reconciliation check runs on every submit and presents the conflicting rows; no silent accept.
  8. **AC8** — Exported CSV matches the Postgres source **value-for-value** (not just row count); embedded commas/newlines quoted correctly.
- **Edge / adversarial cases:**
  - `LAW` rule with no citation → blocked, not saved.
  - Coverage date range overlaps the legal rule (post-migration `0002`) → submit fails, conflict highlighted.
  - Cross-tenant `rule_id` → 404 (not 403; no existence disclosure, per F68).
  - Waiver WV-0001 expiry is today → **already expired** (half-open, tenant-local); banner "Expired on `<date>`"; rule re-evaluates without the waiver.
  - Manager-supplied `source_url` fetched server-side → **SSRF-guarded** (https-only, private/metadata ranges blocked after DNS, ≤3 s timeout, no cross-host redirects); a failed/internal URL never blocks the save.
  - Two managers edit the same rule → **optimistic concurrency** (`If-Match` etag) returns 409 on the stale edit; RLS/append-only alone do not prevent lost updates.
- **Expected outcome:** A manager can maintain the rules table without ever silently breaking the assumption-vs-law boundary.

### F35. Calibration & rule-eval log viewer
- **Status:** Not started · **Size:** S · **Priority:** P2
- **Description:** Read-only table view over `Calibration_Log` (F5) and `Rule_Eval_Log` (F10) with filter by date, rule, employee, store and result. Export to CSV for auditors. The write paths stay owned by F5 and F10 — this is the read surface only, so auditors and managers can answer "what did the engine decide, and why" without opening the workbook.
- **Expected outcome:** Auditors and managers can answer "what did the engine decide, and why" without running a query against the workbooks.

---

## Phase 1 — Analytics

Two personas, two umbrellas (F52, F56), each with three depth tiers: top-of-funnel KPIs (the executive signal), operational insights (where managers spend time), and deep analytics (cohort / forensic views). Every screen reads from the Postgres layer defined by F45 / F47, with materialised views for the heavy aggregates.

### F52. Store-manager analytics (umbrella)
- **Status:** Not started · **Size:** M · **Priority:** P2
- **Description:** Owns the store-manager analytics experience. Decisions: RBAC (a store manager sees only their store's data; a district manager can be added later as a third persona), the layout pattern (KPI cards across the top, operational insights below, drill-down on click), the shared date-range filter and store selector (single store, fixed), and the convention for "this store vs same-store prior period vs LY." Each depth tier is its own story below. Builds on F30 (Manager-facing UI umbrella). Reads from Postgres read-only role per F45; aggregates served from materialised views defined in F47.
- **Expected outcome:** A store manager opens the analytics tab and sees their store's signal in three tiers, drilled into by clicking any KPI.

### F53. Store-manager top-of-funnel KPIs
- **Status:** Not started · **Size:** S · **Priority:** P2
- **Description:** The dashboard a store manager looks at first thing. Cards across the top, each clickable into the operational view: today's revenue vs forecast (with delta %), today's labour cost %, shifts scheduled vs needed (coverage gaps from F11), compliance issue count (target: zero), overtime hours this week, forecast accuracy MAPE for the last 7 days (from F9), and items per labour hour. Each card surfaces the underlying rule or constraint ID if the value is off-target.
- **Expected outcome:** A store manager opens one screen and knows whether today is on track before their first coffee.

### F54. Store-manager operational insights
- **Status:** Not started · **Size:** M · **Priority:** P2
- **Description:** Where the manager spends most of their time: hourly heatmap (sales × daypart × day-of-week); coverage gaps by hour from F11; schedule compliance rate after F14; call-out frequency by employee with an attention flag for any employee above a threshold; sales per labour hour by daypart; throughput variance vs the F5 standards; minor rule utilisation (any minor at > 90 % of their weekly cap — surfaces F10 / F24); and the F10 `Rule_Eval_Log` filtered to this store, with the rule_id and reason shown inline.
- **Expected outcome:** A manager can move from "today is off" to "this is why" in one click, and from "this is why" to "this is the rule" in one more click.

### F55. Store-manager deep analytics
- **Status:** Not started · **Size:** M · **Priority:** P3
- **Description:** Forensic and cohort views: demand elasticity (rain → sales per category, derived from the F1 weather join); product mix shift over time (which categories growing, which shrinking); solver quality (feasibility rate, average solve time for this store); spread-of-hours incidents per employee per week (Part 146); ESSTA call-out clustering (any patterns in `NYC-ESSTA-NO-REPLACE` triggers); FPC coverage compliance (every shift had an FPC holder on site); manager override rate vs F11's solver recommendations.
- **Expected outcome:** The store manager has the same diagnostic depth a data analyst would have, surfaced as pre-built views rather than ad-hoc SQL.

### F56. C-Suite analytics (umbrella)
- **Status:** Not started · **Size:** M · **Priority:** P2
- **Description:** Owns the C-Suite analytics experience. Decisions: RBAC (C-Suite sees all stores; can drill into any store's manager view from F52–F55), the one-screen executive layout (signal-first, narrative annotations supported), the cross-store comparison pattern (sparklines, percentile bands, period-over-period deltas), and the export conventions (PDF for board decks, CSV for finance). Includes the manager-level rollup — a C-Suite user can drop into any store's analytics and see exactly what its manager sees, with the store's full history appended. Each depth tier is its own story below. Builds on F52 (so C-Suite inherits the store layout, not invents a new one).
- **Expected outcome:** A C-Suite user opens one screen and knows whether the company is on track across all stores, then drills into any store at any tier.

### F57. C-Suite top-of-funnel KPIs
- **Status:** Not started · **Size:** S · **Priority:** P2
- **Description:** One-screen executive summary. Cards: total company revenue (this week vs prior week vs LY), total labour cost % vs the F11 cost target, total transactions and items, forecast accuracy across all stores, zero-tolerance compliance counter (any `VERIFY` rule that touched a real shift this week — surfaces F19), overtime dollars across the company, schedule coverage rate, and a top-movers strip (stores up / down vs prior week). Each card clickable into the cross-store rollup. Narrative annotation supported — C-Suite can pin a one-line note to any anomaly for the next viewer.
- **Expected outcome:** The executive summary is one screen, all cards have a one-line reason if off-target, and the company-wide compliance posture is visible at a glance.

### F58. C-Suite cross-store rollup + manager view
- **Status:** Not started · **Size:** M · **Priority:** P2
- **Description:** Compare stores and drop into manager views: per-store revenue and labour % sparklines with percentile bands; store-vs-store KPI matrix (revenue / FT, items / labour-hour, overtime rate, call-out rate, solver feasibility rate); per-store compliance summary (drill-through to F35); per-store call-out frequency hotspots; per-store solver quality (which store has the most infeasible days); manager override rate rollup (which stores the system is fighting the most). Click any store → drops into F52 with that store's data, full history, and the C-Suite viewer's audit trail noted.
- **Expected outcome:** The C-Suite can compare any two stores side-by-side and drop into either store's manager view without losing context.

### F59. C-Suite deep analytics
- **Status:** Not started · **Size:** L · **Priority:** P3
- **Description:** Strategic views: cohort analysis (stores opened in similar conditions performing similarly now); cross-store weather elasticity (do all stores respond the same way to weather, per F1); forecast accuracy broken down by category × store × daypart (where is the model weakest); solver ROI (labour cost savings attributable to F11 vs a naive baseline schedule); compliance audit risk (count of any `VERIFY` rule that touched a real shift, rolled up by rule_id); coverage-of-coverage (how often each store ran at minimum crew vs full crew, by daypart); cohort retention (do employees at higher-utilisation stores stay longer).
- **Expected outcome:** Strategic questions ("are we spending the labour budget well?", "where is the model wrong?", "is the solver paying for itself?") have pre-built answers — no ad-hoc SQL required.

---

## Phase 1 — Platform, plumbing & hardening

### F16. n8n workflows
- **Status:** Not started · **Size:** M · **Priority:** P2
- **Description:** Build and export the n8n workflows: weekly forecast trigger, nightly POS sync, inbound SMS webhook, payroll/HCM export. n8n owns triggers and plumbing; stateful logic stays in LangGraph. Event-driven demand uplift (the `ADJ-EVENT-LOCAL` trigger in `Demand_Adjustments`) is a separate pipeline — see **F27**.
- **Expected outcome:** The system runs on schedule and integrates with POS, payroll and event feeds without manual steps.

### F17. Square Sandbox replay
- **Status:** Not started · **Size:** S · **Priority:** P3
- **Description:** Replay historical sales through Square Sandbox to simulate a live POS feed for demos and integration testing.
- **Expected outcome:** A realistic live-feed simulation without needing production POS data.

### F18. Observability across A2A
- **Status:** Not started · **Size:** M · **Priority:** P3
- **Description:** One trace ID propagated across all A2A calls, with authentication and timeouts on each boundary; basic tracing/metrics. Includes the rule-change watch-and-reload flow — see **F28** — so effective-dated rule rows actually take effect.
- **Expected outcome:** Debuggable, trustworthy inter-agent calls and auditable rule reloads — failures and rule changes are both attributable.

### F19. Compliance verification pass
- **Status:** Not started (21/31 already VERIFIED) · **Size:** M · **Priority:** P1 (gate before any "legally accurate" demo)
- **Description:** Close the 9 remaining `VERIFY` rows and the 1 `ASSUMPTION` row in `Labor_Rules` against primary sources (not memory). The 9 to verify: `NY-SPREAD-10H`, `NY-MEAL-EARLY-LATE-START`, `NY-MEAL-EVENING`, `NY-CALLIN-HOSP`, `FED-MINOR-1415-OCCUPATION`, `NYC-FWW-CHANGE-PREMIUM`, `NYC-FWW-CLOPEN`, `NYC-ESSTA-NO-REPLACE`, `NYC-FPC-COVERAGE`. The 1 assumption (`NY-TIPCREDIT-EXCLUDED`) needs either a source or a written rationale as a design decision. Per rule: open the cited statute/regulation, confirm the values (45-min midway, 20-min evening, 3-hr call-in floor, ESSTA cover wording, FPC coverage requirement), update `verification_status` and `verified_on`, and fix any `source_url` that doesn't resolve.
- **Expected outcome:** A rules table where every row is `VERIFIED` or a documented `ASSUMPTION` — safe to rely on for any demo claiming legal accuracy.

---

## Phase 2 / 3 — Design-only now (schema + Agent Card)

### F20. Prep / Waste agent (#3) — Phase 2
- **Status:** Design-only · **Size:** L (later) · **Priority:** P3
- **Description:** Agent Card and schema designed now, populated later. Forecast-driven prep and waste reduction.
- **Expected outcome:** A ready interface so Phase 2 build starts without redesign.

### F21. Inventory agent (#2) — Phase 2
- **Status:** Design-only · **Size:** L (later) · **Priority:** P3
- **Description:** Agent Card and schema designed now. Demand-aware inventory management.
- **Expected outcome:** Interface locked for Phase 2.

### F22. Loyalty agent (#5) — Phase 3
- **Status:** Design-only · **Size:** L (later) · **Priority:** P3
- **Description:** Forecast-aware Loyalty agent; Agent Card and identity/consent schema designed now, populated in Phase 3.
- **Expected outcome:** Identity/consent contract defined early so later build is additive.

---

## Phase 1 — Gaps surfaced by review

These were not in the original backlog but the data and Project Status already imply them. Add to the order above where they unblock downstream work.

### F23. Data directory location & git policy
- **Status:** Not started · **Size:** XS · **Priority:** P0
- **Description:** `coffee-scheduler/data/` is empty; the actual files (`coffee_shop_*.xlsx`, `weather_forecast.xlsx`, `coffee_shop_sales.xlsx`) live in `/Users/jbaker/Development/coffeeshop/data/`. The repo `.gitignore` only lists `.DS_Store`. Decide: symlink from `coffee-scheduler/data/` → `../data/`, move the files in, or update every doc to point at the real path. Then add an explicit gitignore policy (track synthetic workbooks, ignore any real PII) and commit.
- **Expected outcome:** One canonical data path that agents, scripts and humans all reference — no broken symlinks, no path drift.

### F24. Roster: school enrolment & multi-employer field
- **Status:** Not started (part of F2) · **Size:** XS · **Priority:** P1
- **Description:** F2 lists "school enrolment" but the solver's join needs it to live in a typed column (`is_enrolled`, `school_calendar_id`, `grade_band`). Also add `has_other_employer` (default False) so `NY-MINOR-ALL-MULTIEMPLOYER` can be evaluated. For the 3 roster employees aged 15 (`FED-MINOR-1415-OCCUPATION`), record the parent/school certificate on file via the existing `Consent_Waivers` table.
- **Expected outcome:** Roster columns the compliance engine can join to `School_Calendar` and `Labor_Rules` without ambiguity.

### F25. Multi-year holiday & school calendar scope
- **Status:** Not started (extends F3) · **Size:** S · **Priority:** P0
- **Description:** `Holiday_Calendar` covers only 2026 (11 rows). Sales data is 2023; forecast horizon runs ≥1 year ahead of any plan date. Load NYC public schools calendar and federal/NY holiday calendar for at least 2023–2028 so the solver has data for both back-tests (F5, F9) and forward planning. Mark holidays effective-dated so a calendar change doesn't silently re-classify past dates.
- **Expected outcome:** Calendar coverage matching the forecast horizon — no `IN_SESSION = None` rows in any schedule the solver emits.

### F26. Coverage_Rules ↔ Labor_Rules reconciliation check
- **Status:** Not started · **Size:** XS · **Priority:** P1
- **Description:** `Coverage_Rules` declares `COV-FPC` as `LAW (reference)` pointing at `NYC-FPC-COVERAGE` in `Labor_Rules`. Coverage_Rules otherwise holds business assumptions. Add a CI test or pre-commit check that flags any coverage row marked `LAW` whose underlying legal rule is not `VERIFIED` (gates on F19) and any coverage constraint whose effective dates conflict with the legal rule's effective dates.
- **Expected outcome:** A coverage rule can never silently contradict a legal rule — the assumption-vs-law boundary stays clean.

### F27. Event data ingest pipeline
- **Status:** Not started · **Size:** M · **Priority:** P2
- **Description:** `Demand_Adjustments.ADJ-EVENT-LOCAL` (+25% demand, +1 staff within 0.5 mi, ±2h/+1h window) needs an event feed that isn't in scope of F16 (which only lists forecast/POS/SMS/payroll triggers). Ingest a source (Ticketmaster / Songkick / per-venue feeds per store) into an `Event_Calendar` table effective-dated, then expose it to the forecaster and the solver as a multiplier override. Calibrate the +25% / +1 staff values against observed uplift after F1 weather join is in (use the same back-test window as F5).
- **Expected outcome:** Event-driven demand uplift that the forecaster and solver can apply without manual manager overrides.

### F28. Rule-change rollout & reload flow
- **Status:** Not started (part of F18) · **Size:** S · **Priority:** P2
- **Description:** `Labor_Rules` is effective-dated but no feature says how a new rule row gets picked up by a running solver. Build: (a) a versioned reload endpoint the solver calls per shift generation; (b) a diff log in `Rule_Eval_Log` (`agent = "labor-compliance-agent/reload"`) so rule changes are auditable; (c) an n8n trigger that watches the workbook for changes and notifies the agent. Without this, "effective_from = 2027-01-01" means nothing operationally.
- **Expected outcome:** Effective dating becomes behaviour, not a column — adding or retiring a rule row takes effect without redeploying.

### F29. Product→category taxonomy ownership
- **Status:** Not started (part of F8) · **Size:** XS · **Priority:** P1
- **Description:** Sales has `product_id` and `product_category`; `Throughput_Standards` maps 9 categories to seconds-per-unit; the forecaster must consume the same taxonomy. Add a `Product_Taxonomy` table (or a versioned mapping sheet) and pin it: the forecaster, throughput table, and explanation layer all read the same version. Without this, "Coffee" in the forecast and "Coffee" in throughput can silently drift.
- **Expected outcome:** One versioned category taxonomy that every downstream consumer reads — so calibration, forecast and explanation stay aligned.
- **Acceptance criteria (CS-009):**
  1. **AC1 — complete mapping:** `Product_Taxonomy` has exactly one row per distinct sales `product_id` (80), each with a non-null `product_detail`, `product_type` and `product_category`.
  2. **AC2 — category is a function of product:** no `product_id` maps to more than one `product_category` (the sales hierarchy is single-valued).
  3. **AC3 — no drift vs source:** the set of `product_category` values in `Product_Taxonomy` equals the set in `coffee_shop_sales.xlsx` — none missing, none invented.
  4. **AC4 — cross-consumer coverage (also BUG-002):** the taxonomy's category set equals the `Throughput_Standards` category set; the taxonomy validator names any category present in one consumer but absent from the other, and calibration refuses (does not score 0 labor) any sales category absent from `Throughput_Standards`.
  5. **AC5 — pinned version:** a single `taxonomy_version` is recorded in `Taxonomy_Meta` and stamped on every `Product_Taxonomy` row, so downstream consumers can reference an exact version.
  - **Edge / adversarial:** a sales row carrying a category absent from `Throughput_Standards` must make both the taxonomy validator **fail** and calibration **raise** — never silently pass or score it as zero labor.
  - **Cross-references:** `coffee_shop_sales.xlsx` (source hierarchy), `Throughput_Standards` (consumer), F8/CS-012 (forecast contract — future consumer of the pinned version), BUG-002.

---

## Phase 1 — Observability (OpTel)

OpenTelemetry is the chosen observability framework. F18 ("Observability across A2A") describes the requirement; this section implements it with OTel. Stack: OTel SDK in every agent, OTel Collector, and the LGTM backends (Tempo / Mimir / Loki) with Grafana for visualisation.

### F36. OpenTelemetry implementation (umbrella)
- **Status:** ✅ Done (CS-036, 2026-11-16) · **Size:** M · **Priority:** P2
- **Description:** Establish the shared OpenTelemetry SDK contract and reusable bootstrap for every agent, then prove W3C context propagation through a minimal two-service A2A HTTP demo. Collector configuration is F38; LGTM backends and Grafana are F39/F40.
- **Acceptance criteria:**
  1. **AC1 — pinned SDK requirements:** `otel/requirements.txt` pins exact versions of `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-grpc`, and `opentelemetry-semantic-conventions`.
  2. **AC2 — required resource schema:** `init_telemetry` creates a resource containing `service.name`, `service.version`, and `service.namespace=coffeeshop`.
  3. **AC3 — service identity required:** `init_telemetry` rejects a missing or blank `service_name`.
  4. **AC4 — deployment environment ownership:** `init_telemetry` does not set `deployment.environment` on the SDK resource.
  5. **AC5 — tenant location:** telemetry records `tenant_id` only as a span attribute, never as a resource attribute.
  6. **AC6 — PII prohibition:** telemetry attributes do not contain employee names or email addresses.
  7. **AC7 — head sampler:** the tracer provider uses `ParentBased(AlwaysOn)` sampling.
  8. **AC8 — propagation format:** initialization sets the global propagator to `tracecontext,baggage`.
  9. **AC9 — local OTLP transport:** the OTLP gRPC span exporter uses `OTEL_EXPORTER_OTLP_ENDPOINT` or `localhost:4317` with `insecure=True`.
  10. **AC10 — flush on shutdown:** `shutdown()` force-flushes tracing and metrics before provider shutdown.
  11. **AC11 — reusable bootstrap API:** `init_telemetry(service_name, service_version="0.1.0")` returns `(tracer, meter)`.
  12. **AC12 — injection helper:** `inject_headers(headers)` adds the current W3C trace context to the supplied header carrier.
  13. **AC13 — extraction helper:** `extract_context(headers)` preserves the incoming valid trace ID in its returned context.
  14. **AC14 — two-service trace:** the stdlib HTTP demo emits one CLIENT span and one SERVER span with the same trace ID.
  15. **AC15 — metric vocabulary:** the bootstrap defines `solver.solve_time_ms`, `rule_eval.count`, `forecast.quantile_p70`, `a2a.server.duration_ms`, and `a2a.calls` with their documented types, units, and attributes.
  16. **AC16 — collector trace verification:** with CS-038's Collector running, the demo verification reads Collector `debug` output and asserts the CLIENT and SERVER spans share a trace ID.
  17. **AC17 — collector metric verification:** with CS-038's Collector running, the demo verification observes `a2a.calls` or `a2a.server.duration_ms` in Collector `debug` metrics output.
  18. **AC18 — loopback-only plaintext (BUG-055):** `init_telemetry` builds both exporters with `insecure=True` and no TLS credentials only when the parsed OTLP endpoint host is an explicit loopback literal (`127.0.0.0/8`, `::1`, bracketed `[::1]`) or `localhost` (case-insensitive); it never resolves DNS to make that decision.
  19. **AC19 — remote endpoints require TLS (BUG-055):** a non-loopback endpoint builds both exporters with `insecure=False` and gRPC channel credentials from a readable CA bundle (`OTEL_EXPORTER_OTLP_CERTIFICATE`); a missing, unreadable, empty, or structurally invalid (non-PEM) CA raises a typed configuration error — naming no path or byte of the material — before either exporter constructor runs.
  20. **AC20 — mutual-TLS pair integrity (BUG-055):** the client certificate (`OTEL_EXPORTER_OTLP_CLIENT_CERTIFICATE`) and client key (`OTEL_EXPORTER_OTLP_CLIENT_KEY`) are mutually required; supplying exactly one raises before exporter construction, supplying both includes the client certificate and key bytes in the channel credentials, and structurally invalid client certificate or key material raises before any exporter is constructed.
  21. **AC21 — endpoint parsing fails closed (BUG-055):** a malformed endpoint (URL scheme, userinfo, path/query, missing port, out-of-range port, unbracketed IPv6, or surrounding whitespace) raises a typed configuration error before any exporter construction or DNS/network I/O.
  22. **AC22 — transport is part of initialization identity (BUG-055):** a repeated `init_telemetry` whose endpoint or TLS mode changed raises rather than reusing the process-global providers established for a different trust boundary.
- **Edge / adversarial:** an absent or blank service name fails closed; extraction of a valid incoming `traceparent` must not create a new trace ID; names/emails are rejected or scrubbed before export; short-lived processes must flush before exit; Docker-dependent Collector verification is gated and unit contracts do not require Docker; a hostname that merely *resolves* to loopback is treated as remote (no DNS trust decision); incomplete TLS material (missing CA, half an mTLS pair) and malformed endpoints fail closed before any exporter is constructed.
- **Cross-references:** [CS-036 plan](./docs/plans/CS-036-otel-foundation.md), F18 (cross-agent trace), F37 (per-agent instrumentation), F38/CS-038 (Collector `debug` verification), F68 AC4 (PII scrub).
- **Expected outcome:** A reusable, locally exportable telemetry bootstrap and demo prove one A2A trace ID plus RED metrics at the Collector; F39 later provides the LGTM visualisation.

### F37. OTel SDK instrumentation per agent
- **Status:** Not started · **Size:** M · **Priority:** P2
- **Description:** Each runtime agent (Scheduler, Demand Forecast, Labor Compliance, Staff Comms) and the CP-SAT solver emit OTel spans for: every A2A call (client + server), every tool invocation, every LangGraph node transition, every solver iteration, every rule evaluation, every SMS send / receive. Custom spans for `solver.solve_time_ms`, `rule_eval.count{rule_id,result}`, `forecast.quantile_p70`. Trace context propagated via W3C `traceparent` on every A2A boundary. Each agent's `AGENTS.md` lists the spans it emits.
- **Expected outcome:** Every agent boundary, tool call and internal step is a span — all sharing one `trace_id` across the call chain.

### F38. OTel Collector deployment
- **Status:** Done (CS-038, 2026-11-15) · **Size:** S · **Priority:** P2 · Plan: [docs/plans/CS-038-otel-collector.md](./docs/plans/CS-038-otel-collector.md)
- **Description:** Deploy the OTel Collector as a single Docker Compose service. Receivers: OTLP/gRPC + OTLP/HTTP on standard ports. Processors: tail-based sampling (keep 100% of errors, 10% of success), batch, resource attributes (`service.name`, `service.version`, `deployment.environment`). Exporters: Tempo (traces), Mimir via `prometheusremotewrite` (metrics), Loki (logs). Config versioned at `coffee-scheduler/otel/collector-config.yaml`.
- **Acceptance criteria:**
  1. **AC1** — `otel/collector-config.yaml` exposes OTLP receivers bound to `0.0.0.0:4317` (gRPC) and `0.0.0.0:4318` (HTTP).
  2. **AC2** — The traces `tail_sampling` processor keeps 100% of error traces (a `status_code`/`ERROR` policy) and samples the remaining traces at 10% (a `probabilistic` policy at `sampling_percentage: 10`).
  3. **AC3** — The traces pipeline processor order is `memory_limiter` first, `tail_sampling` before `batch`, and `batch` last.
  4. **AC4** — A `resource` processor upserts `deployment.environment`, and `service.name`, `service.version` and `deployment.environment` are the config's documented resource attributes.
  5. **AC5** — Every pipeline (traces, metrics, logs) includes the `debug` exporter; traces also export to Tempo via `otlp`, metrics to Mimir via `prometheusremotewrite`, and logs to Loki via `otlphttp`.
  6. **AC6** — Each backend exporter sets `retry_on_failure` so an unreachable backend is non-fatal (the Collector keeps ingesting to `debug`).
  7. **AC7** — The `health_check` extension binds `0.0.0.0:13133` and is listed in `service.extensions`.
  8. **AC8** — The `otel-collector` Compose service defines no `sh`/`bash`/`curl`/`wget` healthcheck (the image is distroless).
  9. **AC9** — The Compose service pins `otel/opentelemetry-collector-contrib` to an exact (non-`latest`) tag at least two weeks old.
  10. **AC10** — `otelcol-contrib validate` under the pinned image accepts the committed config and rejects a deliberately invalid config.
  11. **AC11** — In an isolated bring-up, an OTLP error trace pushed to the Collector is ingested and retained via the `debug` exporter (tail sampling keeps errors).
  12. **AC12 — loopback-only development host ports (BUG-055):** the development `docker-compose.observability.yml` publishes `4317`, `4318`, and `13133` bound to `127.0.0.1` only, while the in-container OTLP receivers stay on `0.0.0.0` for compose-network access.
  13. **AC13 — production TLS Collector configuration (BUG-055):** a separate production Collector configuration enables receiver TLS (`cert_file`+`key_file`) without selecting plaintext (`insecure: true`), enables client authentication only when a client CA (`client_ca_file`) is explicitly configured — and the pinned image validates it with server cert+key and **no** client CA, proving mTLS is optional — and commits no certificate or key material.
- **Edge / adversarial:** an invalid/absent config fails `validate` and the suite; the Collector comes up healthy with Tempo/Mimir/Loki absent; a shell/curl healthcheck on the distroless image is rejected; a development host port published on `0.0.0.0` is rejected; a production receiver TLS block missing its key (or selecting plaintext) is rejected.
- **Cross-references:** F39, F41, root `docker-compose.observability.yml`, and agent SDKs that set `service.name`/`service.version`.
- **Expected outcome:** A running Collector that receives from every agent and ships to all three backends, configured in version control.

### F39. LGTM backends (Tempo, Mimir, Loki)
- **Status:** Not started · **Size:** M · **Priority:** P2
- **Description:** Deploy Grafana Tempo (traces), Grafana Mimir (metrics) and Grafana Loki (logs) via Docker Compose for the demo; document the Kubernetes / managed-equivalent path for production. Tempo backed by MinIO locally, S3 in production. Mimir with a single replica and the standard ingester / store-gateway / querier split. Loki with filesystem store locally; S3-compatible store in production. One Compose file, one startup command.
- **Expected outcome:** Tempo, Mimir and Loki running, each receiving data from the Collector, with documented production upgrade paths.

### F40. Grafana dashboards
- **Status:** Not started · **Size:** S · **Priority:** P2
- **Description:** Pre-built Grafana dashboards provisioned via JSON in `coffee-scheduler/otel/dashboards/`: (a) A2A call graph (Tempo), (b) per-agent RED metrics (Mimir), (c) solver performance (Mimir custom metrics), (d) rule-eval counts grouped by `rule_id` and `result` (Mimir), (e) call-out workflow state-transition log (Loki), (f) cross-signal "trace drill-down" pane — takes a `trace_id` and shows spans, metrics and logs for that trace. Data sources for all three backends pre-wired in the Grafana provisioning. Per-story dashboards for F14 (call-out workflow) and F11 (solver).
- **Expected outcome:** A manager or engineer opens Grafana and sees the whole system's behaviour from one `trace_id`.

### F41. CI observability gate
- **Status:** Not started · **Size:** S · **Priority:** P2
- **Description:** Per-PR CI job that fails if any new external call (HTTP, A2A, DB, tool invocation) lacks an OTel span. Implemented as a span exporter in test mode — every test must exercise a code path that emits at least one span, asserted via the exporter. New agents or new tool wrappers without instrumentation fail this gate before they merge.
- **Expected outcome:** A PR cannot land code that bypasses observability.

---

## Phase 1 — Data platform (PostgreSQL)

Move the project off `.xlsx` as the source of truth onto PostgreSQL. The Excel files stay in git as historical fixtures and as a read-side export (F46). F47 is the schema design; F42–F46 are the platform that runs it.

### F42. PostgreSQL implementation (umbrella)
- **Status:** Done (CS-042, 2026-11-14) · **Size:** M · **Priority:** P0 · Platform doc: [docs/data-platform.md](./docs/data-platform.md)
- **Description:** Move the project off `.xlsx` as the source of truth onto PostgreSQL. This umbrella owns: the Postgres version pin, the connection-string convention (`.env.example`, never committed), the migration tool (Alembic), the role / user model (read-only agent role, read-write agent role, manager role), and the path from current workbooks to schema-versioned tables. **F23 (data directory decision) is settled here: Postgres is the canonical store.** The Excel files become fixtures and exports.
- **Expected outcome:** Every agent reads from and writes to Postgres; Excel is a derived artefact, not a source.

### F43. Postgres provisioning & migrations
- **Status:** In development · **Size:** S · **Priority:** P0
- **Description:** Postgres 16 and PgBouncer provide reproducible local infrastructure; Alembic migrations are reversible and the data plane has a tested recovery procedure.
- **Acceptance criteria:**
  1. **AC1** — `docker compose up` starts Postgres 16 and PgBouncer, and both become healthy.
  2. **AC2** — From a fresh checkout, `alembic upgrade head` succeeds over the direct composed Postgres connection and reaches revision `0001`.
  3. **AC3** — `upgrade → downgrade → upgrade` round-trips without leftover tables, roles, partitions, or policies and without second-upgrade conflicts.
  4. **AC4** — The application uses PgBouncer transaction pooling on port 6432, while transaction-local `SET ROLE` and `set_config('app.tenant_id', ..., true)` preserve RLS tenant isolation.
  5. **AC5** — `docs/postgres-backup.md` provides scheduled custom-format `pg_dump`, retention, and a scratch-database restore drill that verifies row counts and RLS.
- **Edge / adversarial:** an unset tenant setting matches no rows; migration teardown removes externally maintained child partitions through its parent; a role with grants has its grants removed before deletion.
- **Cross-references:** CS-043, F42 (canonical Postgres store), F44 (ETL), F47 (schema and migration `0001`).
- **Expected outcome:** A fresh checkout can compose the data plane, migrate it directly, use a pooled application connection safely, and restore it from a verified backup.

### F44. ETL: Excel → Postgres
- **Status:** Done (2026-11-12 via CS-044) · **Size:** M · **Priority:** P0
- **Description:** Idempotent ETL script (`scripts/etl_excel_to_postgres.py`) that reads each `coffee_shop_*.xlsx` and `weather_forecast.xlsx` and writes to the Postgres schema defined in F47. Uses `COPY` for the big tables (`transactions`, `weather_hourly`, `weather_daily`, `rule_eval_log`). Validates row counts after load and asserts known invariants (e.g. "every rule in `labor_rules` has a `rule_id`"; "every transaction has a valid `store_id`"). Reports per-table counts and any anomalies. Safe to re-run; uses upserts / `ON CONFLICT` everywhere. Tagged and runnable from CI on a schedule.
- **Expected outcome:** A single command migrates the current Excel data into Postgres with no manual steps and no data loss; the script is idempotent.
- **Acceptance criteria (CS-044):**
  1. **AC1 — complete public load:** A load for `maven-demo` imports every supported public source sheet into its F47 table and reports equal source and target counts for each table.
  2. **AC2 — idempotent snapshot:** A second successful load of identical workbooks has the same target counts and natural-key rows, with no duplicate transactions, weather rows, or audit entries.
  3. **AC3 — bulk-path parity:** `transactions`, `weather_hourly`, `weather_daily`, and `rule_eval_log` are staged through PostgreSQL `COPY`, then have the same source and target counts as the other tables.
  4. **AC4 — atomic fail-closed validation:** A missing `labor_rules.rule_id`, unknown transaction `store_id`, unmapped taxonomy category, or invalid typed value produces a descriptive error and leaves no partial target changes.
  5. **AC5 — PII isolation:** Default execution neither reads nor writes `employee_pii`; `--include-pii` loads its private workbook only when explicitly requested and available.
  - **Edge / adversarial:** empty optional log sheets load as zero rows; Excel blanks normalize to SQL NULL only for nullable columns; a source row removed from a fixture is removed from the selected tenant's source-backed snapshot; an unknown tenant, unmigrated database, missing source workbook, or malformed typed cell fails before mutation.
  - **Cross-references:** F43/CS-043 (migrated PostgreSQL service), F47/CS-047 (`docs/schema.md` and migration `0001`), F29/CS-009 (taxonomy), F2/CS-002–003 (roster), F5/CS-008 (calibration), F26 (coverage-rule reconciliation), and `migration/SKILL.md`.

### F45. Agent data-access layer
- **Status:** Done (CS-045, 2026-11-14) · **Size:** M · **Priority:** P1
- **Description:** SQLAlchemy models (or equivalent) for every table defined in F47. Repository pattern for read and write — agents call `repo.get_employee(...)`, `repo.append_rule_eval(...)`, never raw SQL. Connection pooling shared across agents. Read-only role for the Compliance agent, read-write role for the Solver and the Manager-facing UI (F34). LangGraph state checkpoints stored in Postgres per F12. All queries parameterised; no string concatenation. Integration tests cover the layer against a test database.
- **Expected outcome:** Agents access data through a typed, pooled, role-aware layer — no direct SQL in agent code.

### F46. Excel export (read-only export path)
- **Status:** Not started · **Size:** S · **Priority:** P2
- **Description:** Generator script that writes the same `.xlsx` shape (`coffee_shop_staff.xlsx`, `coffee_shop_labor_rules.xlsx`, `coffee_shop_labor_standards.xlsx`) from the Postgres tables. Uses the F29 product taxonomy to keep category names consistent. Runs on demand via n8n or the CLI. The exported Excel files are byte-different (timestamps, formula values) but row-identical to the source-of-truth Postgres content. Does not write back into Postgres.
- **Expected outcome:** Managers who want an Excel view can generate it; the workbook is no longer the source.

---

## Phase 1 — Data mapping & schema

The canonical schema document for the project. Lives at `coffee-scheduler/docs/schema.md` with the live source in Alembic migrations under `coffee-scheduler/db/`. Every table, every relationship, every index, documented.

### F47. Data mapping & schema design (ERD + PK / FK / indexes)
- **Status:** Not started · **Size:** M · **Priority:** P0
- **Description:** The canonical schema document. For every Postgres table: primary key, foreign keys (with `ON DELETE` behaviour), single-column and composite indexes, effective-dating strategy (`DATERANGE` columns + `EXCLUDE USING GIST` constraints where overlap is illegal), partitioning strategy (`PARTITION BY RANGE (date)` for `transactions`, `rule_eval_log`, `weather_hourly`), and JSONB columns where the schema is loose. Includes an ERD (Mermaid or dbdiagram.io source) showing all relationships. Tables covered at minimum: `employees`, `employer_config`, `labor_rules`, `school_calendar`, `holidays`, `consent_waivers`, `rule_eval_log`, `store_profiles`, `stations`, `throughput_standards`, `coverage_rules`, `fixed_tasks`, `shift_rules`, `cost_targets`, `demand_adjustments`, `calibration_log`, `transactions`, `weather_hourly`, `weather_daily`, `weather_codes`, `product_taxonomy` (F29), `events` (F50). Document cross-references F26 (Coverage_Rules ↔ Labor_Rules) and F10 (effective-dating sub-task).
- **Expected outcome:** A single document an engineer can read to understand every table, every relationship, every index — before writing a query. The Alembic migrations are generated from this schema, not the other way round.

---

## Phase 1 — Data ingestion

Pipelines that move external data into the Postgres schema from F47. Each is an idempotent n8n-driven job with OTel spans (F37) and Loki logs (F39). New ingestion pipelines follow the same shape.

### F48. Weather data ingestion pipeline
- **Status:** Not started (the script exists at `data/build_weather_forecast.py`) · **Size:** S · **Priority:** P1
- **Description:** Wrap `build_weather_forecast.py` in a scheduled n8n cron (weekly) that re-pulls Open-Meteo historical data and refreshes the `weather_hourly` and `weather_daily` tables. Idempotent — re-runs upsert by `(location_id, date, hour)`. Logs each run to Loki (F39) and emits a custom OTel span (F37). Fails loud (and pages via n8n) if any store has missing hours. Attribution per F1 written to `ATTRIBUTION.md` once.
- **Expected outcome:** A weekly cron that keeps the weather join current without manual intervention; failures visible in Grafana.

### F49. POS data ingestion
- **Status:** Not started · **Size:** M · **Priority:** P2
- **Description:** n8n workflow that pulls transaction data from Square Sandbox (F17) or a production POS and writes to the `transactions` table. Maps POS `product_id` to the F29 product taxonomy. Backfills historical data on first run, then incremental on a nightly cron. Logs to Loki; emits OTel spans per ingestion batch. Dead-letter queue for unmapped `product_id` values so the taxonomy gap is visible, not silently dropped.
- **Expected outcome:** Nightly POS sync from sandbox (or production) into Postgres with no manual steps and a visible dead-letter for taxonomy gaps.

### F50. Event data ingestion (F27 implementation)
- **Status:** Not started · **Size:** M · **Priority:** P2
- **Description:** Per-store event feed ingest from Ticketmaster / Songkick / per-venue feeds. Writes to an `events` table keyed by `(store_id, event_id, starts_at)`. Effective-dated so a cancelled event has `effective_to = cancellation_date`, not a delete. Exposes an `event_calendar` view that the forecaster (F9) and the solver (F11) consume. Reuses the F48 ingestion pattern (n8n cron + OTel spans + Loki logs). Implements the abstract pipeline described in F27.
- **Expected outcome:** Event-driven demand uplift is computed from live event data, not assumed.

### F51. Rule-change ingestion (F28 implementation)
- **Status:** Not started · **Size:** S · **Priority:** P2
- **Description:** n8n trigger that watches `labor_rules` for inserts, updates and `effective_to` changes (via Postgres `LISTEN/NOTIFY` or a polling trigger). On change: emits an OTel event, writes a `Rule_Eval_Log` row tagged `agent = "labor-compliance-agent/reload"`, and notifies the running solver to reload its rule snapshot. Idempotent — multiple changes in one window produce one notification, not N. Implements the rollout flow described in F28.
- **Expected outcome:** A rule row added today takes effect in the running solver within minutes, with an audit trail.

---

## Phase 1 — SaaS multi-tenancy

Convert the single-tenant deployment into a multi-tenant SaaS serving SMB coffee shops (1–10 stores per tenant). Shared schema, `tenant_id` on every domain table, Postgres Row-Level Security for isolation. Maven Roasters data becomes the demo / seed tenant. Architecture details: [`docs/architecture/saas-multi-tenancy.md`](./docs/architecture/saas-multi-tenancy.md).

### F60. Tenancy model & Row-Level Security
- **Status:** Not started · **Size:** M · **Priority:** P0
- **Description:** Add the `tenants` table (root); add `tenant_id` to every **tenant-scoped** F47 table (a named set of global reference tables, e.g. `weather_codes`, is exempt — their data is identical for all tenants); enable RLS on every tenant-scoped table **and the `tenants` root table itself** with a `tenant_isolation` policy that filters by the fail-closed expression `NULLIF(current_setting('app.tenant_id', true), '')::UUID`; add composite `(tenant_id, …)` indexes; reconfigure PgBouncer to transaction mode and set the role + `app.tenant_id` per request with `SET LOCAL`. Migration: backfill Maven data as the `maven-demo` tenant before enabling RLS so application code can be deployed alongside.
- **Expected outcome:** Every domain row — and the tenant registry itself — is tenant-scoped; cross-tenant reads/writes return zero rows (or are rejected) at the database level regardless of what the application does, and no tenant can enumerate another.
- **Acceptance criteria:**
  1. **AC1** — `tenants` table exists; every F47 **tenant-scoped** table has `tenant_id UUID NOT NULL REFERENCES tenants(id)`, and every prior index / global `UNIQUE` on those tables is recreated with a leading `tenant_id`. A **documented, enumerated set of global reference tables** (e.g. `weather_codes`) is explicitly exempt from `tenant_id`. *(BUG-023)*
  2. **AC2** — every tenant-scoped relation — each base table **and every partition, including the `*_default` catch-all partitions and every future partition** — has `ENABLE` **and** `FORCE ROW LEVEL SECURITY` with a `tenant_isolation` policy carrying both `USING` and `WITH CHECK`. The policy expression is `tenant_id = app_current_tenant()` — a `STABLE` helper (migration `0006`) that returns NULL for an unset, empty, **or malformed/non-UUID** context, so every bad context yields zero rows, **never** a `''::uuid` or `'<non-uuid>'::uuid` cast error. Every partition child (incl. `*_default`) carries the same `ENABLE`/`FORCE`/policy (migration `0007`); `db/partitions.py::ensure_partition_rls` keeps future partitions protected. *(BUG-021, BUG-022)*
  3. **AC3** — the app connects as a non-owner `app_rw` role without `BYPASSRLS`; only the migration/ETL role bypasses RLS.
  4. **AC4** — the **`tenants` root table is itself RLS-protected**: an `app_rw`/`app_ro` session scoped to tenant A sees exactly its own `tenants` row and cannot read tenant B's `slug`/`name`/`timezone`/`jurisdiction` (RLS `id = NULLIF(current_setting('app.tenant_id', true), '')::uuid`, or app roles have no direct SELECT and read their tenant only through a scoped view). *(BUG-020)*
  5. **AC5** — **global reference tables are read-only** to `app_rw`/`app_ro` (SELECT only); their writes are owned solely by the migration/ETL/global-admin path, so one tenant's session can never mutate shared reference data. *(BUG-023, BUG-009)*
  6. **AC6** — tenant context is **fail-closed for every bad input**: unset, empty, **and malformed/non-UUID** `app.tenant_id` all yield **zero rows (or a typed, caught error) — never a raw cast 500**. The session-context middleware validates/normalizes `app.tenant_id` before the first statement. *(BUG-022)*
  7. **AC7** — context is applied **transaction-locally under pooling**: the middleware opens an explicit transaction, **asserts the connection is not in autocommit**, and issues `SET LOCAL ROLE app_rw` **and** `SET LOCAL app.tenant_id = …` together before the first statement; neither the role nor the tenant GUC leaks to the next pooled client. With no context set, every query returns zero rows. *(BUG-025, ties BUG-013/BUG-017)*
  - **Edge / adversarial:** a connection as the table owner is still denied (proves `FORCE`); an INSERT/UPDATE carrying another tenant's `tenant_id` is rejected by `WITH CHECK`; backfill is batched, stamps the cutover `tenant_id` via a temporary `DEFAULT`/`BEFORE INSERT` trigger so concurrently-written rows never get a `NULL tenant_id` during the deploy-alongside window, then `VALIDATE … NOT NULL`. Index builds on the big partitioned tables use **per-partition `CREATE INDEX CONCURRENTLY` + `ATTACH`** (or parent `ONLY` + concurrent children) — **not** `CREATE INDEX CONCURRENTLY` on the partitioned parent, which Postgres rejects. *(BUG-024)* Verified in F68.
  - **Cross-refs:** PLAN §2 + §7 + §15.1; architecture §2 + §14.1; defect stories BUG-020…BUG-025.

### F61. Auth + per-tenant RBAC
- **Status:** ✅ Done (2026-11-17) — CS-061a core auth, CS-061b hardening, and CS-061c invitations/audit are all complete (Argon2id identities, RS256/JWKS, rotating refresh, durable revocation/reset/throttle/MFA, gateway/UI cutover; `tenant_invitations` invite→accept + append-only `audit_log` via migration `0012`). Multi-tenant membership split (`users`/`tenant_memberships`) and real invite-email delivery are deferred follow-ups; review filed BUG-041..044. · **Size:** M · **Priority:** P0
- **Description:** `tenant_users` table (per-tenant users with `owner` / `manager` / `staff` / `read_only` roles); `saas_admins` table for cross-tenant admins with MFA; argon2id password hashing; email + password login (no SSO in MVP); JWT issuance with `tenant_id`, `user_id`, `role`, `is_saas_admin` claims; impersonation flow with audit logging. Per-tenant email uniqueness — the same email can register in two tenants (common for SMB owners).
- **Expected outcome:** Every authenticated request carries a tenant context; SaaS admins can act on any tenant with full audit trail.
- **Acceptance criteria:**
  1. **AC1** — `tenant_users` (roles `owner`/`manager`/`staff`/`read_only`, per-tenant email uniqueness) and `saas_admins` (MFA) exist; passwords hashed with argon2id.
  2. **AC2** — login issues an asymmetrically-signed JWT (RS256/ES256; `alg=none` rejected; `iss`/`aud`/`exp` validated) with `tenant_id`/`user_id`/`role`/`is_saas_admin`; short access token + rotating refresh token with reuse detection.
  3. **AC3** — suspension/deactivation takes effect within one access-token TTL: middleware re-checks tenant `status` + user `is_active` **per request**, backed by a revocation list.
  4. **AC4** — password reset (time-boxed single-use token), login rate-limiting / lockout, and optional TOTP MFA for `owner`/`manager` all exist.
  5. **AC5** — user **invitations** (`tenant_invitations`) create members by email; impersonation records **both** `impersonated_by` and the target in an **append-only** `audit_log`.
  - **Edge / adversarial:** a JWT issued before suspension is rejected on the next request; a deactivated user's live sessions are revoked; a tampered/expired refresh token is refused.
  - **Cross-refs:** PLAN §3 + §15.2–15.4; architecture §3 + §14.2–14.4.

### F62. Tenant onboarding
- **Status:** ✅ Done (2026-11-19) — CS-062 adds atomic signup/owner creation, digest-only verification URLs,
  usable 14-day trial authorization + idempotent suspension job, verified-owner provisioning, and a global
  reviewed NY rule-template registry. Provisioning is idempotent and rejects unverified jurisdictions rather
  than defaulting to NY. Wizard UI, real email, billing exemption, solver, and full F19 remain deferred. · **Size:** M · **Priority:** P0
- **Description:** Signup endpoint (creates tenant + owner in one transaction); email verification (blocks solver until verified); 14-day trial period with nightly suspension job; first-run wizard (add first store, add first employee, set hours, pick plan); provisioning transaction that seeds jurisdiction-specific `labor_rules` and default `labor_standards`. Idempotent provisioning so re-running on a populated tenant is a no-op.
- **Expected outcome:** A new SMB owner can sign up, verify email, complete the wizard, and have a working tenant in under 10 minutes.
- **Acceptance criteria:**
  1. **AC1** — signup creates tenant + owner in **one transaction** (both commit or both roll back); slug validated against format and the **reserved-slug denylist**; duplicate slug → 409.
  2. **AC2** — email verification blocks solver invocation until verified; unverified owners can sign in but not schedule.
  3. **AC3** — 14-day trial (`status='trial'`); a nightly job suspends expired, unconverted trials; suspension blocks all API calls except `/billing/*`.
  4. **AC4** — provisioning seeds jurisdiction-specific `labor_rules` + default `labor_standards` and is **idempotent** (re-run on a populated tenant is a no-op).
  - **Edge / adversarial:** a non-NY jurisdiction with no verified rule set is **refused** at provisioning (see F19 dependency), not seeded with NY rules by default.
  - **Cross-refs:** PLAN §15.13; architecture §4 + §14.10 + §14.13.

### F63. Per-tenant configuration
- **Status:** Not started · **Size:** M · **Priority:** P1
- **Description:** Per-tenant settings: jurisdiction, operating hours (per store), coverage rules, cost targets, demand adjustments, SMS provider config (Twilio subaccount or shared pool), weather source override, branding (logo, primary color). Fast-changing per-tenant flags live in `tenants.settings` JSONB; structured config lives in dedicated tables (which get the same RLS treatment).
- **Expected outcome:** Each tenant's behaviour is driven by their own configuration, not a shared global default.
- **Acceptance criteria:**
  1. **AC1** — structured config (jurisdiction, per-store hours, coverage rules, cost targets, demand adjustments) lives in dedicated RLS-protected tables; only non-secret fast-changing flags live in `tenants.settings` JSONB.
  2. **AC2** — per-tenant **credentials** (Twilio, Stripe, paid weather) live in an encrypted `tenant_secrets` table, **never** plaintext JSONB; decrypted only in the path that needs them.
  3. **AC3** — the solver/forecaster/rule-engine read the current tenant's config; changing one tenant's config never affects another.
  - **Edge / adversarial:** a `SELECT *` on `tenants` or the admin UI never exposes a secret value.
  - **Cross-refs:** PLAN §4 + §15.1; architecture §5 (secrets note) + §14.1.

### F64. SaaS admin surface
- **Status:** Not started · **Size:** L · **Priority:** P1
- **Description:** A separate UI at `admin.coffeescheduler.com` (or `/admin` behind a separate auth flow): tenant list with search / filter / pagination; tenant detail with usage, last login, plan, payment status; suspend / activate / churn actions; impersonate (audit-logged); plan management (calls Stripe API); aggregate usage dashboard (MRR, active tenants, total solver runs, total SMS); audit log of every admin action. Separate React app, separate auth flow, separate deploy from the manager / C-Suite UI.
- **Expected outcome:** A SaaS admin can find, suspend, impersonate, and re-plan any tenant without leaving the admin UI.
- **Acceptance criteria:**
  1. **AC1** — tenant list (search by name/slug/email, filter by plan/status, paginated) and tenant detail (usage, last login, plan, payment status).
  2. **AC2** — suspend / activate / churn set `tenants.status` and take effect on the next tenant request; actions are append-only audit-logged (admin, target, timestamp, IP).
  3. **AC3** — impersonation issues a capped-duration token recording `impersonated_by`; every impersonated action is logged.
  4. **AC4** — the admin app is a **separate** deploy / auth flow from the manager / C-Suite UI; a SaaS admin cannot accidentally land in a tenant's view, and a tenant user cannot reach `/admin`.
  - **Cross-refs:** PLAN §8 (SaaS admin) + §15.3; architecture §6 + §14.3.

### F65. Billing & plan tiers
- **Status:** Not started · **Size:** L · **Priority:** P1
- **Description:** Stripe integration — `Customer` per tenant, `Subscription` per tenant, webhooks for `customer.subscription.updated`, `invoice.paid`, `invoice.payment_failed`. Plans: Starter (1 store, 10 employees, free), Pro ($99/mo per location, up to 10 stores), Enterprise (custom). Payment failure → 7-day grace period → suspension. Plan tier limits enforced at API gateway.
- **Expected outcome:** Tenants can sign up to a paid plan, be billed monthly, and have subscription state changes propagate to the application automatically.
- **Acceptance criteria:**
  1. **AC1** — one Stripe `Customer` + one active `Subscription` per tenant; the three plan tiers exist with their documented limits.
  2. **AC2** — the webhook handler **verifies Stripe signatures**, is idempotent (dedupes retried/out-of-order events via an event log), and maps `subscription.updated`/`invoice.paid`/`invoice.payment_failed` to `tenants.plan`/`status`.
  3. **AC3** — payment failure → 7-day grace → suspension; **downgrade is enforced** (a plan whose limits the tenant currently exceeds is blocked or requires reduction first).
  4. **AC4** — proration, tax (Stripe Tax/VAT), and dunning emails are handled (or explicitly deferred with a tracked follow-up).
  - **Cross-refs:** PLAN §8 (billing) + §12 + §15.11; architecture §7 + §14.11.

### F66. Usage tracking & quotas
- **Status:** Not started · **Size:** M · **Priority:** P1
- **Description:** Per-tenant metering aggregated nightly from `rule_eval_log` (solver runs), OTel metrics (SMS sent), and Postgres size queries (storage). Stored in `tenant_usage` table; quotas enforced at API gateway with 429 responses carrying quota context. Plan-tier limits (e.g. Starter = 50 solver runs / mo) checked on every API call.
- **Expected outcome:** A tenant exceeding their plan's quota gets a clear 429 with the relevant numbers; no over-usage billing surprises.
- **Acceptance criteria:**
  1. **AC1** — per-tenant metered counters (solver runs, forecast calls, SMS, storage, API calls) incremented from **real events** (not a `rule_eval_log` proxy), rolled up per period.
  2. **AC2** — plan-tier limits enforced at the gateway; over-quota returns **429** with `Retry-After` and quota context identifying the tenant.
  3. **AC3** — DB-level noisy-neighbor guards (`statement_timeout`, `work_mem`, connection limits per role; fair-share solver queue) stop one tenant exhausting the shared pool/CPU.
  - **Cross-refs:** PLAN §8 (usage) + §15.7; architecture §7.3 + §14.7.

### F67. Tenant-scoped observability
- **Status:** Not started · **Size:** M · **Priority:** P1
- **Description:** Every OTel span gets `tenant.id` and `tenant.slug` resource attributes (extends F37). Grafana dashboards gain a `tenant` template variable. Loki log queries scope by `tenant.id`. Per-tenant alerting (e.g. "tenant X's solver has been infeasible for 3 days") without per-tenant dashboards.
- **Expected outcome:** A support engineer can debug a single tenant's behaviour without seeing other tenants' data; a SaaS admin sees cross-tenant aggregates.
- **Acceptance criteria:**
  1. **AC1** — every span/metric/log carries `tenant.id` + `tenant.slug` as **resource attributes** (not high-cardinality metric labels); Grafana gains a `tenant` template variable; Loki queries scope by `tenant.id`.
  2. **AC2** — per-tenant alerting works without per-tenant dashboards (e.g. "tenant X solver infeasible 3 days").
  3. **AC3** — a span/log **PII-scrubbing** processor strips SSN/address/DOB, raw SMS bodies, and LLM prompt/response payloads with employee PII before export.
  - **Cross-refs:** PLAN §8 (observability) + §15.5; architecture §8 + §14.5; extends F37.

### F68. Multi-tenant isolation tests
- **Status:** ✅ Done — **CS-068a (DB-level gate)** (`test/test_isolation_gate.py`: cross-tenant R/W/U/D denial, FORCE catalog proof on every table + partition, `WITH CHECK` rejection, pooled-connection context-bleed, fail-closed empty/malformed context, self-maintaining coverage meta-test, `tenants`-enumeration tripwire) and **CS-068b (HTTP/auth gate)** (`test/test_cs068b_http_isolation.py`: self-seeded rollback-only tenant pair, 401/403/404 discipline, genuine-signature JWT attacks, route/principal binding, suspension recheck, refresh replay, cache/PII scrub, and spec-tag meta-test) are green. AC1–AC5 are met except the deliberately deferred impersonation flow and `/billing` suspension exemption. · **Size:** M · **Priority:** P0 (gate before any multi-tenant deploy)
- **Description:** Automated cross-tenant attack scenarios per the SDD test classes: a user from tenant A receives 404 (not 403, to avoid existence disclosure) when accessing tenant B's resources; SQL injection attempts with `tenant_id = '<tenant_b_uuid>'` return zero rows under RLS; tenant tokens cannot self-escalate to SaaS admin; suspended tenants get 403 across protected endpoints. The full impersonation flow and `impersonated_by` audit trail belong to CS-064b. Each test tagged `spec: F68.ac#` for CI traceability.
- **Expected outcome:** The CI suite catches any cross-tenant data leak before merge; isolation is regression-proof.
- **Acceptance criteria:**
  1. **AC1** — cross-tenant read/insert/update/delete attempts against every domain table return zero rows / are rejected under RLS; a table-owner connection is still denied (proves `FORCE`).
  2. **AC2** — a `WITH CHECK` violation (write with another tenant's `tenant_id`) is rejected; a request with no JWT → 401; a suspended tenant → 403; cross-tenant resource access → 404 (no existence disclosure).
  3. **AC3** — a pooled-connection **context-bleed** test proves tenant A's `SET LOCAL` never survives into tenant B's reuse of the same physical connection.
  4. **AC4** — JWT `alg=none`/tamper/expired-refresh rejected; impersonation cannot escalate beyond the target tenant; any shared cache is tenant-keyed; emitted spans/logs pass the PII-scrubbing assertion.
  5. **AC5** — every test tagged `spec: F68.ac#`; the suite is a **CI gate** before any multi-tenant deploy.
  - **Cross-refs:** PLAN §9 + §15.14; architecture §11 + §14.14.

### F69. Data export / delete (GDPR)
- **Status:** ◑ Partial — CS-087 establishes the normal-operation soft-delete lifecycle; audited retention and erasure remain blocked · **Size:** M · **Priority:** P2
- **Description:** Per-tenant data export — JSON dump of every row owned by the tenant, packaged and delivered to the requesting user. Per-tenant hard delete — removes every row across every domain table in a single transaction; audit-logged; tenant row marked `churned` rather than deleted (so historical invoices remain). Right-to-be-forgotten flow for individual users.
- **Expected outcome:** EU customers can request data export / deletion per GDPR Article 15 and Article 17; the SaaS can fulfil both within statutory windows.
- **Acceptance criteria:**
  1. **AC1** — per-tenant export packages every row the tenant owns (signed, time-limited URL); completeness verified against row counts.
  2. **AC2** — tenant hard-delete runs a defined timeline: soft-delete (`churned`) → retention grace → hard delete across every domain table → **backup purge**; audit-logged; a reactivation window exists.
  3. **AC3** — **employee-level** right-to-be-forgotten erases a single employee's PII (not just whole-tenant), consistent with the single-tenant PII model.
  4. **AC4** — the **per-tenant restore** path is documented (shared-schema PITR is global → restore via export/reimport); backups encrypted.
  - **Cross-refs:** PLAN §8 (GDPR) + §15.12; architecture §9 + §14.12.

### F70. Tenant routing
- **Status:** ✅ Done (2026-11-19) — strict edge resolver (`api/routing.py`): subdomain (prod) / `/t/<slug>/` (dev) resolution, reserved-slug denylist, trusted-proxy client-IP boundary (resolves BUG-045), route↔principal 403 binding before RLS, origin-CSRF, safe 308 aliases; unknown/suspended/churned/lookup → one cache-safe 404; `resolve_active_slug` routes active+trial. 16 CS-070 tests + BUG-045 regression green. Routing is opt-in via `TENANT_ROUTING_ENABLED` so the single-tenant demo is preserved (BUG-056). Wildcard TLS/CORS-engine/cache-partitioning deferred. · **Size:** S · **Priority:** P0
- **Description:** Resolve tenant from request — subdomain (`acme.coffeescheduler.com`) for production, path-prefix (`/t/acme/`) for dev. Strict raw-authority parsing, reserved-slug rejection, trusted forwarding/client-IP extraction, route↔principal binding, origin checking, and safe alias redirects all happen before endpoint/RLS work. Unknown, suspended, churned, and lookup failure outcomes share a cache-safe 404. Wildcard TLS issuance and edge HTTP→HTTPS proof are deployment evidence, not application behavior in this slice.
- **Expected outcome:** Every request lands in the right tenant context before any data access; routing failures are loud and fast.
- **Acceptance criteria:**
  1. **AC1** — resolver extracts the tenant from subdomain (prod) or path-prefix (dev), looks up `tenant_id`, and sets the request context before any data access; unknown/suspended tenant → 404.
  2. **AC2** — the **reserved-slug denylist** (`admin`, `www`, `api`, `app`, `auth`, `billing`, `status`, …) is enforced so no tenant collides with the admin/API surface.
  3. **AC3** — wildcard TLS for `*.coffeescheduler.com`; **CORS** locked to tenant origins; **CSRF** protection and secure cookie flags (`HttpOnly`/`Secure`/`SameSite`) on the session path.
  - **Edge / adversarial:** enterprise **custom domains** (CNAME + per-domain TLS) are noted as a later feature beyond this `S` scope.
  - **Cross-refs:** PLAN §3 + §15.10; architecture §14.10.

---

## Phase 2 — Employee experience, actuals & workforce ops

This phase closes the employee side of the loop (the manager/exec surfaces F30–F35 / F52–F59 have no
employee-facing counterpart) and the **actuals** loop (nothing today captures what actually happened).
The core principle is unchanged: the LLM only converses/routes/explains, ML forecasts, OR-Tools schedules.
These add new **inputs** (availability, actuals, credentials), new **solver objectives** (fairness), and
new **surfaces** (employee app, publish gate) — never LLM-authored schedules.

### F71. Employee self-service surface (umbrella)
- **Status:** Not started · **Size:** L · **Priority:** P1
- **Description:** The employee-facing counterpart to the manager UI (F30). A mobile-first web surface where
  staff see their published schedule, set availability, request time off (F72), clock in/out (F73), and
  claim/offer shifts (F75). Authenticated via F61 with an `employee` role (not `manager`), RLS-scoped to the
  signed-in employee: they see only their own rows plus open shifts at location(s) they are eligible for.
  No new agent authority — this is a read/write surface over the same Postgres and the existing F14 workflow.
- **Acceptance criteria:**
  1. **AC1** — An authenticated employee sees only their own shifts, punches, availability, requests and
     credentials; a request for another employee's data returns 404 (no existence disclosure), enforced by
     RLS plus an `employee_id = current principal` scope.
  2. **AC2** — The surface is read/write only through the F72–F75 service layers; it never writes
     `labor_rules`, schedules, or another employee's data.
  3. **AC3** — Works at phone width; a published schedule renders offline-cached (last-fetched) when the
     network drops, with a clear "last updated" stamp.
- **Edge / adversarial:** an employee crafting a request with another `employee_id` or another `tenant_id`
  gets 404; a suspended/terminated employee loses access immediately (F61).
- **Cross-references:** F30 (manager UI pattern), F61 (auth/RBAC), F60 (RLS), F14 (swap workflow), F72–F75.
- **Expected outcome:** Staff self-serve the things that today only exist as assumptions or SMS, on their phone.

### F72. Availability & time-off / PTO intake
- **Status:** Not started · **Size:** M · **Priority:** P1
- **Description:** Structured, effective-dated employee availability plus a time-off/leave request workflow.
  Replaces the static `availability_persona` assumption with real data the solver (F11) and legality engine
  (F10, minor hours) consume. New tables: `employee_availability` (recurring weekly pattern + dated
  exceptions, `DATERANGE`/time windows, RLS by employee), `time_off_requests` (requested → approved/denied
  state machine, manager-approved, blocks the solver when approved).
- **Acceptance criteria:**
  1. **AC1** — An approved availability window or time-off becomes a **hard** unavailable constraint the
     solver honors; an employee is never scheduled outside their stated availability without an explicit
     manager override that is logged.
  2. **AC2** — Availability is **effective-dated**; the solver reads the window in force for the shift date
     (tenant-local), not "today's" availability (mirrors the F10 as-of discipline).
  3. **AC3** — A time-off request has an auditable approval trail (who/when); approval before the schedule
     is generated removes the employee from that day; a request after publish routes through F74 re-publish.
  4. **AC4** — Overlapping/contradictory availability rows are rejected at write (GiST exclusion), not
     silently merged.
- **Edge / adversarial:** a minor's availability interacts with school-day limits (F10) — the stricter of
  availability and legality wins; a time-off request spanning an already-published shift surfaces the
  conflict rather than silently dropping the shift.
- **Cross-references:** F11 (solver constraints), F10 (minor hours), F24 (roster fields), F74 (re-publish).
- **Expected outcome:** The solver schedules against real availability and approved leave, not a persona.

### F73. Time & attendance (actuals & no-show)
- **Status:** Not started · **Size:** M · **Priority:** P1
- **Description:** Capture **actual** clock-in/out against scheduled shifts — the system has no source of
  truth for what actually happened today. New `time_entries` (punch in/out, break punches, tied to
  `shift_id`, RLS by tenant/employee). Computes actual vs scheduled hours, late/early-out, and **no-show**
  detection. No-show auto-triggers the F14 cover workflow; actuals feed payroll (F78) and forecast feedback
  (F80). The manager approves/edits exceptions (missed punch) with an audit trail.
- **Acceptance criteria:**
  1. **AC1** — A punch is recorded against the correct `shift_id` with tenant-local timestamps; actual
     hours are computed half-open and DST-correct (reuse the F10 time helpers, not naive wall-clock).
  2. **AC2** — A shift with no clock-in by a configurable grace window past start is flagged **no-show** and
     emits the F14 cover trigger exactly once (idempotent).
  3. **AC3** — Actual-vs-scheduled variance is queryable per employee/day/store and exported to payroll (F78).
  4. **AC4** — A missing/edited punch requires a manager correction with reason; the correction is audited
     and never silently overwrites the raw punch.
- **Edge / adversarial:** a punch outside any scheduled shift (unscheduled work) is captured and flagged,
  not dropped; a shift crossing midnight attributes hours to the correct business day; duplicate punches are
  idempotent.
- **Cross-references:** F10 (time helpers), F14 (no-show cover), F78 (payroll), F80 (forecast feedback), F11.
- **Expected outcome:** A reliable "actual labor" record closes the payroll and forecast-accuracy loops.

### F74. Schedule review, edit & publish workflow
- **Status:** Not started · **Size:** M · **Priority:** P1
- **Description:** The human-in-the-loop gate on the solver output (F11/F12) — today F31 only *views* a
  schedule and there is no way to edit or publish it. A manager reviews the generated week, hand-edits
  assignments, and **every edit is re-validated live against F10 (legality) and coverage (F26)** before it
  can be saved; then approves and **publishes**, which notifies staff (F15/F71) and locks the version.
  Draft → published state machine with a republish diff; the LLM explains changes (F13) but never edits.
- **Acceptance criteria:**
  1. **AC1** — A manual assignment edit is rejected if it fails F10 legality or coverage, with the specific
     rule/constraint cited inline (reuses the F34/F10 evaluation path); the schedule is not saved invalid.
  2. **AC2** — Publishing transitions the schedule `draft → published`, writes an immutable version, and
     notifies affected employees once (idempotent); edits after publish create a new version with a diff.
  3. **AC3** — A published schedule is the one employees see (F71); a draft is never visible to staff.
  4. **AC4** — Every publish/edit is audited (who/when/what) in the rule-eval/audit log.
- **Edge / adversarial:** two managers editing the same week → optimistic concurrency (version/etag) blocks
  the stale save; publishing a week with an unresolved hard violation is refused, not warned.
- **Cross-references:** F11/F12 (solver output), F10/F26/F34 (live validation), F13 (explanation), F15/F71
  (notify), F31 (view).
- **Expected outcome:** Managers can trust, adjust, and ship the generated schedule without breaking legality.

### F75. Open-shift & shift-swap marketplace
- **Status:** Not started · **Size:** M · **Priority:** P2
- **Description:** Extends the F14 call-out/swap workflow into a marketplace. Managers (or the solver, for
  unfilled demand) post **open shifts**; eligible employees claim them (first-come or manager-approved).
  Employees offer/claim **swaps** with each other. Every claim/swap passes F10 legality + coverage (skills,
  minor limits, rest, FPC/key-holder) **before** confirmation and is audited; an ineligible claim is
  refused with the reason.
- **Acceptance criteria:**
  1. **AC1** — An open shift is claimable only by an employee who passes F10 legality and coverage for that
     specific shift; an ineligible claim is refused with the citing rule, never silently accepted.
  2. **AC2** — A claim/swap is atomic: two employees claiming the same open shift → exactly one wins; the
     loser sees "already taken" (no double-book).
  3. **AC3** — Manager-approval mode holds the claim pending; first-come mode confirms immediately; both
     write an audit row and notify (F15/F71).
  4. **AC4** — Confirmed claims/swaps route through F74 as a published-schedule change (versioned).
- **Edge / adversarial:** a claim that would push the employee into overtime/clopening surfaces the premium
  (F10) before confirm; a swap that is legal for one party but not the other is refused for both.
- **Cross-references:** F14 (swap workflow), F10 (legality), F74 (publish), F11 (open demand), F71.
- **Expected outcome:** Gaps fill themselves — staff pick up and swap shifts within the legal guardrails.

### F76. Fair & equitable scheduling objectives
- **Status:** Not started · **Size:** M · **Priority:** P2
- **Description:** Add fairness terms to the CP-SAT objective (F11), which today optimizes cost + legality
  only. Equitable distribution of desirable/undesirable shifts (opens/closes/weekends), clopening-load
  balancing, preference-satisfaction score (from F72 availability/preferences), hours-equity against
  contracted min/max, and optional seniority weighting. Weights are per-tenant config (F63) and the chosen
  trade-off is **explainable** (F13 cites why one person got the close over another).
- **Acceptance criteria:**
  1. **AC1** — Fairness is a **soft** objective: it never overrides a hard legality (F10) or coverage
     constraint; a legal, covered schedule is always producible regardless of weights.
  2. **AC2** — Over a configurable window, undesirable-shift counts per eligible employee converge within a
     tenant-set tolerance; the metric is reported.
  3. **AC3** — Weights are per-tenant and changing them is reflected deterministically (same inputs + weights
     → same schedule); defaults are documented.
  4. **AC4** — The explanation layer (F13) can state the fairness trade-off for any assignment.
- **Edge / adversarial:** fairness vs cost tension is bounded (a documented cap) so fairness cannot explode
  labor cost; a tiny team where equity is infeasible degrades gracefully with a surfaced note.
- **Cross-references:** F11 (solver), F72 (preferences), F63 (per-tenant config), F13 (explanation).
- **Expected outcome:** Schedules are not just legal and cheap but demonstrably fair — a retention lever.

### F77. Certification & work-permit expiry tracking
- **Status:** Not started · **Size:** S · **Priority:** P2
- **Description:** Today FPC exists only as a *coverage constraint* (a holder must be on site); nothing
  tracks when an **employee's** credential lapses. New `employee_credentials` (type: FPC, minor work permit,
  alcohol, etc., with `DATERANGE` validity and a document reference). Warns before expiry; an **expired**
  credential removes the employee from constraints that require it (FPC coverage, minor eligibility) —
  **fail-closed**, mirroring the F10/CS-013b waiver-expiry semantics (half-open, tenant-local).
- **Acceptance criteria:**
  1. **AC1** — On the shift date, an employee whose required credential is expired (half-open: valid through
     the day before `valid_to`) does not satisfy the constraint that needs it; the engine fails closed.
  2. **AC2** — Credentials warn at configurable thresholds (e.g. 30/7 days) in the manager and employee
     surfaces, reusing the waiver-expiry warning tiers.
  3. **AC3** — A minor's work-permit expiry interacts with F10 minor rules; an expired permit blocks
     minor-restricted scheduling, not just warns.
- **Edge / adversarial:** a credential expiring mid-week flips eligibility on the exact boundary date (test
  at a future date, not today); a missing credential is treated as absent (fail-closed), never assumed valid.
- **Cross-references:** F10/CS-013b (waiver-expiry pattern), F26 (FPC coverage), F11 (solver eligibility).
- **Expected outcome:** Expiring certs and permits are caught before they become a compliance violation.

### F78. Payroll / HCM export contract
- **Status:** Not started · **Size:** M · **Priority:** P2
- **Description:** A first-class, versioned export of a pay period through the F16 n8n workflow selected in CS-011.
  Combines **actual** hours (F73), scheduled hours, and computed premiums/OT/spread/call-in (F10)
  per employee/day into an idempotent, reconcilable, auditable export to the payroll target. The compliance engine already prices the premiums; this feature delivers them with real
  hours behind them.
- **Acceptance criteria:**
  1. **AC1** — The export reconciles: Σ exported hours == Σ approved `time_entries` (F73) for the period;
     any mismatch fails the export, not silently ships.
  2. **AC2** — Each premium line cites the `Labor_Rules` rule_id that produced it (traceable to F10), and
     the export is idempotent per (tenant, period) — re-running produces the same artifact.
  3. **AC3** — An unapproved punch or an open pay period cannot be exported (fail-closed); the export records
     who ran it and when.
- **Edge / adversarial:** a mid-period rule change (F28) is attributed to the correct date range, not applied
  retroactively; a corrected punch after export forces a versioned re-export with a diff.
- **Cross-references:** F73 (actuals), F10 (premiums), F7 (agent-card question), F16 (n8n export), F28.
- **Expected outcome:** Legal pay — premiums, OT, spread — flows to payroll with auditable, reconciled hours.

### F79. Employee PII security, encryption & retention
- **Status:** Not started · **Size:** M · **Priority:** P2 (→ P1 before any real employee PII is loaded)
- **Description:** Dedicated protection for `employee_pii` (SSN, address). Today the table is restricted to
  `secrets_rw`; this feature adds encryption at rest (column/envelope encryption, decrypted only in the path
  that needs it — mirroring `tenant_secrets`), field-level **read auditing**, masked display by default,
  and configurable retention/purge. Complements F63 (tenant credentials) and F69 (GDPR export/delete) but
  for employee PII specifically.
- **Acceptance criteria:**
  1. **AC1** — SSN/address are encrypted at rest; a `SELECT *` by `app_rw`/`app_ro` never returns plaintext
     PII, and a dump without the key yields ciphertext.
  2. **AC2** — Every read of a PII field is audited (who/when/which field/which employee); masked display is
     the default and unmasking is an explicit, audited action.
  3. **AC3** — Retention policy purges or anonymizes PII on the configured schedule and on an F69 delete
     request; the purge is verifiable.
- **Edge / adversarial:** a misconfigured role cannot widen PII access without appearing in the audit; a
  backup/restore (F43) round-trips ciphertext, never plaintext.
- **Cross-references:** F63 (tenant secrets pattern), F69 (GDPR), F43 (backup), migration `0001` employee_pii.
- **Expected outcome:** Employee SSNs and addresses are encrypted, access-audited, masked, and time-bounded.

### F80. Forecast MLOps — accuracy, drift, retraining & registry
- **Status:** Not started · **Size:** L · **Priority:** P2
- **Description:** Operationalize the forecast model (F9), which today has a one-off back-test (F5) but no
  continuous lifecycle. Adds predicted-vs-actual accuracy tracking (using F73 actuals and real sales), drift
  detection, scheduled/triggered **retraining** gated by the F5 MAPE acceptance band, a per-tenant **model
  registry** (versioned artifacts, promotion/rollback), and OTel metrics (F37/F40). A model never
  auto-promotes unless it beats the incumbent on the held-out band.
- **Acceptance criteria:**
  1. **AC1** — Forecast accuracy (MAPE per category-hour) is computed continuously against actuals and
     tracked over time; a breach of the F5 band raises an alert (F40).
  2. **AC2** — Retraining is reproducible and **gated**: a candidate is promoted only if it beats the
     incumbent on the held-out window; otherwise the incumbent stays and the attempt is logged.
  3. **AC3** — Every served forecast records its model version (registry id); rollback to a prior version is
     one action and is audited.
  4. **AC4** — Drift (input or prediction) is detected and surfaced before it silently degrades schedules.
- **Edge / adversarial:** a retrain on a thin/biased recent window (e.g. a holiday) cannot silently replace
  a good model (the gate blocks it); a tenant with little data falls back to the shared/seed model, flagged.
- **Cross-references:** F9 (model), F5 (MAPE band), F73 (actuals), F37/F40 (OTel/dashboards), F63 (per-tenant).
- **Expected outcome:** The forecaster improves safely over time instead of silently drifting.

## Known limitations (state openly, not features)
- Only 6 months of sales history, all from 2023.
- No basket ID in the sales data (register overhead overstated).
- No sales history for evening hours (last transaction 20:00–20:59 at every store).
- Open-Meteo API is non-commercial only (CC BY 4.0; attribution required — see F1).
- Staff, labour standards and adjustments are synthetic or assumed.
- Roster, labour rules and labour standards are committed to the repo as synthetic data (see F23 for git policy).
