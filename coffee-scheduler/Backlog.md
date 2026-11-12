# Coffee Shop Scheduler — Backlog

Stories derived from [`FEATURES.md`](./FEATURES.md). Each story links back to its feature (F#) and carries: **Priority**, **Size**, **Status**, **Description**, **Detailed specifications** (the story's acceptance criteria), **Developer**, **Started** (datetime work began), and **Duration** (development time — estimate until work starts, actual once done). The feature catalogue owns the product-level expected outcome; this backlog owns the implementable, verifiable work. Every feature must have at least one linked story.

Backlog created: 2026-11-10.

**Conventions**
- **Priority:** `P0` (blocks everything) · `P1` (core Phase 1) · `P2` (credible demo) · `P3` (hardening / later phase) — inherited from the linked feature.
- **Size:** `XS` (<0.5d) · `S` (~1d) · `M` (2–4d) · `L` (1–2wk) · `XL` (multi-week).
- **Duration:** when a story is not started, this is the planned estimate from Size (XS≈0.5d, S≈1d, M≈3d, L≈8d, XL≈15d). Replace with actuals as work completes.
- **Developer / Started:** `Unassigned` / `—` until a person picks up the story. Fill these in on assignment.

Stories are grouped by priority tier and ordered by dependency within each tier.

---

## P0 — Blockers (nothing downstream runs without these)

### CS-001 — Validate weather×sales join & record Open-Meteo license
- **Feature:** [F1](./FEATURES.md) · **Priority:** P0 · **Size:** S
- **Status:** ✅ Done (2026-11-11) — all 5 validation checks PASS
- **Developer:** Unassigned · **Started:** 2026-11-11 · **Duration:** ~0.5d (actual)
- **Description:** The weather pipeline is built (`data/build_weather_forecast.py`) and `data/weather_forecast.xlsx` exists. Close the two remaining items: spot-check the join and record the data license so this is never shipped commercially by accident.
- **Detailed specifications:**
  - Spot-check ≥20 random `Hourly_Sales_Weather` rows (9,231 total) against the source sales file — store, date, hour and sales value must match.
  - Confirm `Daily_Sales_Weather` has 543 rows × 3 stores with no date gaps across the sales window.
  - Verify `Weather_Codes` lookup is complete for every WMO code present in the data.
  - Write `ATTRIBUTION.md`: Open-Meteo, CC BY 4.0, **non-commercial only**, with the archive-api note (filename "forecast" is a misnomer — it uses historical reanalysis).
  - Hand the dependency list (`pandas`, `openpyxl`, `requests`) to CS-010 (F6) for pinning.
- **Delivered:**
  - `data/validate_weather_forecast.py` — re-runnable, read-only validator (checks A–E).
  - `VALIDATION.md` — PASS report (36 join rows checked, 543 daily rows continuous, 0 orphan codes, 0 out-of-window sales).
  - `ATTRIBUTION.md` — Open-Meteo CC BY 4.0 / non-commercial, archive-reanalysis note.
  - `requirements.txt` — pinned `pandas==3.1.1`, `openpyxl==3.1.5`, `requests==2.34.2` (CS-010 dep handoff now has a starting point).
  - Note: the validator lives in the `data/` dir next to the build script; **CS-006** (done) re-rooted the repo so both scripts are now under version control.

### CS-002 — Add roster scheduling fields
- **Feature:** [F2](./FEATURES.md) · **Priority:** P0 · **Size:** M
- **Status:** Ready to start
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** Extend `coffee_shop_staff.xlsx` from identity-only to a schedulable roster.
- **Detailed specifications:**
  - Add typed columns: `role`, `skill_level`, `home_location_id`, `cross_location_eligible`, `is_key_holder`, `cash_authorised`, `fpc_holder`, `hourly_rate`, `enrolled_in_school`, `availability_persona`, `min_hours_week`, `max_hours_week`.
  - Map minor occupation restrictions to the `role` vocabulary (align with `FED-MINOR-1415-OCCUPATION`).
  - `location_id = store_id`; home location must be one of the 3 stores.
  - Validate: every active employee has role, home location, rate, and min/max hours; no min > max.
  - Output consumed directly by the solver (CS-016).

### CS-003 — Roster school-enrolment & multi-employer typed fields
- **Feature:** [F24](./FEATURES.md) (part of F2) · **Priority:** P1 · **Size:** XS
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~0.5d (est.)
- **Description:** Make school enrolment a typed join key and add the multi-employer flag, so compliance rules evaluate cleanly.
- **Detailed specifications:**
  - Replace free-text "school enrolment" with `is_enrolled` (bool), `school_calendar_id` (FK to School_Calendar), `grade_band`.
  - Add `has_other_employer` (default `False`) to support `NY-MINOR-ALL-MULTIEMPLOYER`.
  - For the 3 employees aged 15 (`FED-MINOR-1415-OCCUPATION`), record parent/school certificate on file via the existing `Consent_Waivers` table.
  - Depends on CS-002; blocks CS-016.

### CS-004 — Load NYC school calendar (multi-year)
- **Feature:** [F3](./FEATURES.md) + [F25](./FEATURES.md) · **Priority:** P0 · **Size:** S
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~1d (est.)
- **Description:** Load NYC public schools + federal/NY holiday calendars as effective-dated data covering the full forecast/back-test horizon.
- **Detailed specifications:**
  - Load school days, holidays and breaks for **2023–2028** (back-tests use 2023; forward planning runs ≥1 yr ahead).
  - Each row effective-dated so a later edit never silently re-classifies a past date.
  - Expose an `IN_SESSION` lookup by date; no `None` rows allowed for any date the solver can emit.
  - Replace the 2026-only `Holiday_Calendar` (11 rows) with the multi-year set.
  - Blocks minor-labor rule evaluation (CS-012) and the solver (CS-016).

### CS-005 — Closing-hours decision & operating-hours config
- **Feature:** [F4](./FEATURES.md) · **Priority:** P0 · **Size:** XS (decision) + S (config)
- **Status:** Not started (business decision open)
- **Developer:** Unassigned · **Started:** — · **Duration:** ~1.5d (est.)
- **Description:** Settle the evening-hours business decision and encode per-store operating hours.
- **Detailed specifications:**
  - Decide: keep 10:30 PM close with minimum evening crew, **or** shorten hours (sales stop ~8–9 PM; Astoria opens 7 AM).
  - Record the decision and rationale in the Project Status doc.
  - Encode per-store open/close + 45 min pre-open / post-close in `coffee_shop_labor_standards.xlsx`.
  - Output bounds the solver's time horizon (CS-016).

### CS-006 — Data directory location & git policy
- **Feature:** [F23](./FEATURES.md) · **Priority:** P0 · **Size:** S (re-root scope; was XS)
- **Status:** ✅ Done (2026-11-11)
- **Developer:** Unassigned · **Started:** 2026-11-11 · **Duration:** ~0.5d (actual)
- **Description:** The git repo was rooted one level too deep (`coffee-scheduler/`), leaving `data/`, the `coffee_shop/` Next.js app, and the status doc outside version control. Re-root the repo at `coffeeshop/` and set a data/PII git policy.
- **Resolution (chosen approach):**
  - **Re-rooted the repo at `coffeeshop/`** (`mv coffee-scheduler/.git .git`); history + the `CoffeeShop-Scheduler` remote preserved, prior files recorded as renames. (The symlink option was rejected — a symlink does not version-control files outside the old tree.)
  - **Absorbed `coffee_shop/`** by removing its throwaway inner `.git` (no remote, only the create-next-app scaffold commit); the Next.js app for F30–F35 is now tracked.
  - Added repo-root `.gitignore` (`.DS_Store`, `.pi/`, Python caches, node_modules/.next belt-and-suspenders) and a **real-PII policy**: synthetic `.xlsx` fixtures + scripts are tracked; real PII goes in `data/private/` (ignored) or `*.pii.*` / `*.real.xlsx`.
  - Added `data/README.md`; removed the misleading empty `coffee-scheduler/data/.gitkeep`; pointed docs at the repo-root `data/`.
- **Outcome:** one repo (`coffee-scheduler/`, `coffee_shop/`, `data/`) with the CS-001 scripts now under version control.

### CS-007 — Multi-year holiday calendar scope
- **Feature:** [F25](./FEATURES.md) · **Priority:** P0 · **Size:** S
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~1d (est.)
- **Description:** Holiday calendar coverage matching the forecast horizon (companion to CS-004 for federal/NY holidays specifically).
- **Detailed specifications:**
  - Load federal + NY State holidays for 2023–2028, effective-dated.
  - Join to daily sales for back-test labelling (CS-011, CS-014).
  - Reconcile with the school calendar so no date is classified twice.
  - *Note:* may be merged into CS-004 if one calendar loader covers both; keep separate if sources differ.

---

## P1 — Core Phase 1

### CS-008 — Calibrate throughput, weather multipliers & targets
- **Feature:** [F5](./FEATURES.md) · **Priority:** P1 · **Size:** M
- **Status:** Ready (F1 done)
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** Turn synthetic/assumed demand and cost parameters into calibrated, auditable values using the F1 weather join.
- **Detailed specifications:**
  - Calibrate throughput seconds/item for all 9 categories + per-transaction register overhead (currently overstated — no basket ID).
  - Derive weather multipliers `ADJ-RAIN`, `ADJ-COLD`, `ADJ-HEAT`, `ADJ-SNOW` from `Daily_Sales_Weather`.
  - Set target utilisation (85%), forecast quantile (P70), labour % targets, overtime budget.
  - Define a back-test window (e.g. last 4 weeks of the sales window) and acceptance band (≤±10% MAPE per category-hour).
  - Append every accepted change to `Calibration_Log` (id, table, row_key, planned/observed/proposed, sample period, approver).
  - Event multipliers are **out of scope** here — see CS-023 (F27).

### CS-009 — Product→category taxonomy ownership
- **Feature:** [F29](./FEATURES.md) (part of F8) · **Priority:** P1 · **Size:** XS
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~0.5d (est.)
- **Description:** Establish one versioned category taxonomy that forecaster, throughput table and explanation layer all read.
- **Detailed specifications:**
  - Add a `Product_Taxonomy` table (or versioned mapping sheet) linking `product_id` → `product_category` (9 categories).
  - Pin a version; the forecast contract (CS-010b), `Throughput_Standards`, and explanation layer reference that version.
  - Add a check that flags any category present in one consumer but missing in another.
  - Blocks/feeds CS-010b (forecast contract) and CS-014 (forecaster).

### CS-010 — Author AGENTS.md + SKILL.md set
- **Feature:** [F6](./FEATURES.md) · **Priority:** P1 · **Size:** M
- **Status:** Partially scaffolded (stubs exist)
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** Flesh out the shared conventions and reusable procedures.
- **Detailed specifications:**
  - Root `AGENTS.md`: stack, layer split (n8n/LangGraph/LangChain/A2A/MCP), data rules, test commands.
  - Per-agent `AGENTS.md` for scheduler, labor_compliance, demand_forecast, staff_comms.
  - `SKILL.md` for `labor-rule-evaluation` (strictest-wins, effective dating, minor age-on-date), plus `synthetic-roster-generation`, `sales-weather-join`, `standards-calibration`, `agent-card-authoring`.
  - Build on the `requirements.txt` CS-001 already created (`pandas==3.1.1`, `openpyxl==3.1.5`, `requests==2.34.2`) — consolidate into `pyproject.toml` if desired, add any new deps, and define the project test command.
  - The repo-wide `.gitignore` and data/real-PII policy are already established by CS-006 (repo re-rooted at `coffeeshop/`; see `data/README.md`) — reference them rather than redefining.

### CS-011 — Author A2A Agent Cards & settle Payroll/HCM
- **Feature:** [F7](./FEATURES.md) · **Priority:** P1 · **Size:** M
- **Status:** Scaffolded (stub JSON; no payroll_hcm dir)
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** Replace stub cards with real, versioned A2A contracts and resolve the Payroll/HCM ambiguity.
- **Detailed specifications:**
  - Real cards for Scheduler, Demand Forecast, Labor Compliance, Staff Comms (capabilities, auth, versioning per `agent-card-authoring`).
  - Placeholder cards for Inventory, Prep, Loyalty.
  - **Decide Payroll/HCM explicitly:** either create `agents/payroll_hcm/` as a runtime agent (+ LangGraph node in CS-015) **or** document it as n8n-only (no card). Record the decision.
  - Build one agent in a second framework to prove A2A interoperability.

### CS-012 — Demand Forecast output contract
- **Feature:** [F8](./FEATURES.md) · **Priority:** P1 · **Size:** S
- **Status:** In design
- **Developer:** Unassigned · **Started:** — · **Duration:** ~1d (est.)
- **Description:** Define the forecast interface so model choice is decoupled from consumers.
- **Detailed specifications:**
  - Schema: prediction by product category (per CS-009 taxonomy version), per hour, per store, with P70 point estimate and interval bounds.
  - Versioned; consumed by the solver (CS-016) and explanation layer (CS-017).
  - Document units, timezone, and null/holiday handling.

### CS-013 — Labor-compliance rule engine (core)
- **Feature:** [F10](./FEATURES.md) · **Priority:** P1 · **Size:** L
- **Status:** Not started (rules table exists, needs verification via CS-021)
- **Developer:** Unassigned · **Started:** — · **Duration:** ~8d (est.)
- **Description:** The authoritative legality service: evaluates effective-dated rules with strictest-wins and exposes hard constraints to the solver and explanations to the LLM.
- **Detailed specifications:**
  - Strictest-wins resolution across overlapping effective-dated rules.
  - Cover: meal breaks, spread-of-hours (Part 146), Fair Workweek premiums & clopening (per-employer flag), ESSTA cover rule, FPC-on-site, minor hours/occupation computed from DOB on the shift date.
  - Expose a query API: given a proposed shift, return pass/fail + reason + any premium cost.
  - The solver and LLM never compute legality themselves.
  - Depends on CS-004 (calendar). Sub-stories CS-013a/b/c below.

### CS-013a — Rule_Eval_Log write path
- **Feature:** [F10](./FEATURES.md) · **Priority:** P1 · **Size:** S
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~1d (est.)
- **Description:** Every rule evaluation is logged — no silent passes.
- **Detailed specifications:**
  - Append to `Rule_Eval_Log` in `coffee_shop_labor_rules.xlsx`: `eval_id, schedule_id, shift_id, employee_id, rule_id, result, premium_cost_usd, evaluated_at, agent`.
  - Write on both pass and fail.
  - Used by the explanation layer (CS-017) and audit.

### CS-013b — Waiver expiry evaluation on shift date
- **Feature:** [F10](./FEATURES.md) · **Priority:** P1 · **Size:** S
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~1d (est.)
- **Description:** Re-check consent waivers against the actual shift date on every generation, not just at rule load.
- **Detailed specifications:**
  - For any rule `waivable_by_consent`, validate `Consent_Waivers.valid_from`/`valid_to` against the shift date.
  - Example: WV-0001 (EMP-1033, `NY-MINOR-1617-SCH-WINDOW`) expires 2027-06-26 — must stop applying after that date.
  - Expired waiver → rule applies as if no waiver.

### CS-013c — Coverage_Rules ↔ Labor_Rules fail-closed reconciliation
- **Feature:** [F10](./FEATURES.md) + [F26](./FEATURES.md) · **Priority:** P1 · **Size:** XS
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~0.5d (est.)
- **Description:** A coverage rule can never silently contradict a legal rule.
- **Detailed specifications:**
  - `Coverage_Rules.COV-FPC` = "LAW — see NYC-FPC-COVERAGE": engine fails closed if coverage and legal rule disagree on HARD vs SOFT.
  - CI / pre-commit check: any `Coverage_Rules` row marked `LAW` whose underlying legal rule is not `VERIFIED` (gates on CS-021) fails.
  - Flag any coverage constraint whose effective dates conflict with the legal rule's effective dates.

### CS-014 — Demand forecast model
- **Feature:** [F9](./FEATURES.md) · **Priority:** P1 · **Size:** L
- **Status:** Blocked (on CS-001, CS-008, CS-012)
- **Developer:** Unassigned · **Started:** — · **Duration:** ~8d (est.)
- **Description:** Train and validate the statistical/ML forecaster that drives staffing need per hour.
- **Detailed specifications:**
  - Train on 6 months of 2023 sales joined with weather; output per CS-012 contract.
  - Handle data limits openly: no annual seasonality, no basket ID, no evening history.
  - Define and run a validation/backtest (reuse CS-008 window and acceptance band).
  - Produce per-category hourly forecasts with P70 + intervals.
  - No LLM in the numeric path.
  - Umbrella story — delivered via CS-014a/b/c below.

### CS-014a — Feature engineering & training dataset
- **Feature:** [F9](./FEATURES.md) · **Priority:** P1 · **Size:** M
- **Status:** Blocked (on CS-001, CS-009)
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** Assemble the model-ready dataset from sales × weather × calendar.
- **Detailed specifications:**
  - Join `Hourly_Sales_Weather` (CS-001) with the category taxonomy (CS-009) and the school/holiday calendars (CS-004/CS-007).
  - Engineer features: hour-of-day, day-of-week, holiday/school flag, weather vars, rolling demand, store id.
  - Encode the known gaps as explicit features/masks: no evening history, no basket ID, 2023-only.
  - Output a versioned feature table the trainer and backtester both read.

### CS-014b — Model selection & training
- **Feature:** [F9](./FEATURES.md) · **Priority:** P1 · **Size:** M
- **Status:** Blocked (on CS-014a, CS-012)
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** Choose and train the statistical/ML forecaster that emits per-category hourly demand with intervals.
- **Detailed specifications:**
  - Evaluate ≥2 candidate approaches (e.g. gradient-boosted quantile regression vs. a classical time-series baseline).
  - Train per store (or with store as a feature) to produce P70 point + interval bounds per the CS-012 contract.
  - No annual seasonality assumed (6-month window); document the choice and its limits.
  - Persist a versioned model artifact + the feature version it was trained on.

### CS-014c — Backtest & validation
- **Feature:** [F9](./FEATURES.md) · **Priority:** P1 · **Size:** S
- **Status:** Blocked (on CS-014b, CS-008)
- **Developer:** Unassigned · **Started:** — · **Duration:** ~1d (est.)
- **Description:** Prove the forecaster meets the acceptance band before the solver consumes it.
- **Detailed specifications:**
  - Backtest on the CS-008 window (e.g. last 4 weeks of the sales window).
  - Report MAPE per category-hour against the ≤±10% acceptance band; record interval coverage vs. nominal (P70).
  - Fail the story if the band isn't met; log results alongside the model version.

### CS-015 — LangGraph state & schedule-generation graph
- **Feature:** [F12](./FEATURES.md) · **Priority:** P1 · **Size:** M
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** Orchestration that turns a request into a schedule end to end.
- **Detailed specifications:**
  - Nodes: gather inputs → call forecast (CS-014) → call compliance (CS-013) → run solver (CS-016) → present result.
  - LangChain wraps tools and prompt templates; LLM routes between tools, never generates numbers.
  - Define graph state schema and error/retry edges.
  - Include the Payroll/HCM node only if CS-011 made it a runtime agent.

### CS-016 — CP-SAT schedule solver
- **Feature:** [F11](./FEATURES.md) · **Priority:** P1 · **Size:** L
- **Status:** Blocked (on CS-002, CS-005, CS-014, CS-013)
- **Developer:** Unassigned · **Started:** — · **Duration:** ~8d (est.)
- **Description:** The deterministic heart: emits a legal, cost-aware weekly schedule.
- **Detailed specifications:**
  - Hard constraints: legality (via CS-013) and coverage.
  - Soft objectives: labour % target, 85% utilisation, overtime budget, cost targets.
  - Respect stations per store (LM/HK 4 stations/6 staff; Astoria 3/4) and fixed tasks.
  - **Enumerated inputs (explicit in code):** forecast output, roster, per-shift `Labor_Rules` eval, `Stations`, `Hourly_Demand`, `Throughput_Standards`, `Coverage_Rules`, `Fixed_Tasks`, `Shift_Rules`, `Cost_Targets`, `School_Calendar` joined to roster enrolment.
  - Output a reviewable schedule object consumed by CS-015 / CS-017.
  - Umbrella story — delivered via CS-016a/b/c/d below.

### CS-016a — Input assembly & data adapters
- **Feature:** [F11](./FEATURES.md) · **Priority:** P1 · **Size:** M
- **Status:** Blocked (on CS-002, CS-005, CS-014)
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** Load and validate every enumerated solver input into typed model structures — nothing implicit.
- **Detailed specifications:**
  - Adapters for: forecast output (CS-012/CS-014), roster (CS-002), `Stations`, `Hourly_Demand`, `Throughput_Standards`, `Coverage_Rules`, `Fixed_Tasks`, `Shift_Rules`, `Cost_Targets`, `School_Calendar` joined to roster enrolment.
  - Fail fast on missing/!typed inputs with a clear message naming the table.
  - Produce one validated solver-input object; unit-test each adapter.

### CS-016b — Hard constraints (legality + coverage)
- **Feature:** [F11](./FEATURES.md) · **Priority:** P1 · **Size:** M
- **Status:** Blocked (on CS-016a, CS-013)
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** Encode the must-hold constraints in CP-SAT.
- **Detailed specifications:**
  - Legality via CS-013 per-shift evaluation (no legality computed in the solver).
  - Coverage: min crew, key-holder open/close, FPC on site, peak-skill, break relief.
  - Stations per store (LM/HK 4 stations/6 staff; Astoria 3/4); `Shift_Rules` (min/max length, 30-min increments, max 5 consec days, 10h rest, no split shifts).
  - Infeasibility must surface the binding constraint(s), not a bare "infeasible".

### CS-016c — Soft objectives & cost function
- **Feature:** [F11](./FEATURES.md) · **Priority:** P1 · **Size:** S
- **Status:** Blocked (on CS-016b)
- **Developer:** Unassigned · **Started:** — · **Duration:** ~1d (est.)
- **Description:** Encode the weighted objective the solver optimises.
- **Detailed specifications:**
  - Terms: labour % target, 85% utilisation, overtime budget, cost targets (`Cost_Targets`).
  - Expose tunable weights; document the default weighting and trade-offs.
  - Include any premiums priced by CS-018 when solving re-plans.

### CS-016d — Solver runner, output object & tuning
- **Feature:** [F11](./FEATURES.md) · **Priority:** P1 · **Size:** M
- **Status:** Blocked (on CS-016b, CS-016c)
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** Run the model to a time/quality bound and emit a reviewable schedule.
- **Detailed specifications:**
  - Configure solver time limit, search params, and determinism (fixed seed) so runs are reproducible.
  - Emit a structured schedule object (shift → employee/station/time) consumed by CS-015/CS-017.
  - Attach the `Rule_Eval_Log` evaluation refs and objective breakdown for explainability.
  - Performance target: solve a full week for all 3 stores within an agreed time budget.

### CS-021 — Compliance verification pass
- **Feature:** [F19](./FEATURES.md) · **Priority:** P1 (gate before any "legally accurate" demo) · **Size:** M
- **Status:** Not started (21/31 rules already VERIFIED)
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** Close the 9 `VERIFY` rows + 1 `ASSUMPTION` against primary sources (not memory).
- **Detailed specifications:**
  - Verify: `NY-SPREAD-10H`, `NY-MEAL-EARLY-LATE-START`, `NY-MEAL-EVENING`, `NY-CALLIN-HOSP`, `FED-MINOR-1415-OCCUPATION`, `NYC-FWW-CHANGE-PREMIUM`, `NYC-FWW-CLOPEN`, `NYC-ESSTA-NO-REPLACE`, `NYC-FPC-COVERAGE`.
  - `NY-TIPCREDIT-EXCLUDED` (assumption): attach a source or a written design-decision rationale.
  - Per rule: open the cited statute/regulation, confirm values (45-min midway, 20-min evening, 3-hr call-in floor, ESSTA cover wording, FPC requirement), set `verification_status` + `verified_on`, fix any non-resolving `source_url`.
  - Gates CS-013c and any demo claiming legal accuracy.

### CS-022 — Coverage↔Law reconciliation CI check
- **Feature:** [F26](./FEATURES.md) · **Priority:** P1 · **Size:** XS
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~0.5d (est.)
- **Description:** Automated guard that the assumption-vs-law boundary stays clean (companion to CS-013c).
- **Detailed specifications:**
  - CI/pre-commit test: any `Coverage_Rules` row marked `LAW` whose legal rule isn't `VERIFIED` fails (depends on CS-021).
  - Flag effective-date conflicts between a coverage constraint and its legal rule.

---

## P2 — Needed for a credible demo

### CS-017 — Schedule explanation layer
- **Feature:** [F13](./FEATURES.md) · **Priority:** P2 · **Size:** S
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~1d (est.)
- **Description:** Plain-language, rule-cited explanations of schedule decisions.
- **Detailed specifications:**
  - LLM explains "why this person at this time" / "why no minor on close", each citing the specific rule/constraint ID from `Rule_Eval_Log` (CS-013a).
  - Reads taxonomy version (CS-009) for category names.
  - No new scheduling logic — explanation only.

### CS-018 — Call-out & swap workflow
- **Feature:** [F14](./FEATURES.md) · **Priority:** P2 · **Size:** L
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~8d (est.)
- **Description:** The headline "smart" day-of capability: stateful call-out/swap with long waits, loops and manager approval.
- **Detailed specifications:**
  - Find eligible, legal cover (skills, hours, minor limits, key-holder/FPC coverage) via CS-013.
  - **Never** require a sick employee to find cover — cite `NYC-ESSTA-NO-REPLACE` by ID in the hard-stop branch (auditable).
  - Price `NY-CALLIN-HOSP` (lesser of 3 hrs or scheduled shift) into the cost objective on report-and-send-home; surface the premium to payroll via CS-019.
  - Produce a replacement candidate the manager approves.
  - Umbrella story — delivered via CS-018a/b/c below.

### CS-018a — Eligibility & legal-cover search
- **Feature:** [F14](./FEATURES.md) · **Priority:** P2 · **Size:** M
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** Find candidate replacements that are available and legal.
- **Detailed specifications:**
  - Rank candidates by skill match, hours headroom (min/max), home/cross-location eligibility, key-holder/FPC coverage.
  - Each candidate passes CS-013 legality for the specific shift (minor limits, rest, spread, etc.).
  - Return an ordered candidate list with the reason each was included/excluded.

### CS-018b — ESSTA no-replace hard-stop & approval loop
- **Feature:** [F14](./FEATURES.md) · **Priority:** P2 · **Size:** M
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** The stateful LangGraph flow with long waits, loops and manager approval.
- **Detailed specifications:**
  - Never require a sick employee to find cover — the sick-out branch cites `NYC-ESSTA-NO-REPLACE` by ID and hard-stops self-replacement (auditable).
  - Long-wait nodes for staff SMS responses (via CS-020), with retries/loops over the CS-018a candidate list.
  - Manager approval gate before a swap is committed to the schedule.

### CS-018c — Call-in pay pricing & payroll surfacing
- **Feature:** [F14](./FEATURES.md) · **Priority:** P2 · **Size:** S
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~1d (est.)
- **Description:** Price the call-in premium and route it to payroll.
- **Detailed specifications:**
  - On report-and-send-home, price `NY-CALLIN-HOSP` (lesser of 3 hrs or scheduled shift) into the cost objective (CS-016c).
  - Surface the premium to payroll via CS-019.
  - Log the premium against the shift in `Rule_Eval_Log`.

### CS-019 — n8n workflows
- **Feature:** [F16](./FEATURES.md) · **Priority:** P2 · **Size:** M
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** Triggers and plumbing; stateful logic stays in LangGraph.
- **Detailed specifications:**
  - Workflows: weekly forecast trigger, nightly POS sync, inbound SMS webhook, payroll/HCM export.
  - Export the workflows to `n8n/`.
  - Event-driven uplift (`ADJ-EVENT-LOCAL`) is **out of scope** — see CS-023.
  - Payroll export target aligns with the CS-011 Payroll/HCM decision.

### CS-020 — Staff Comms (SMS) + consent
- **Feature:** [F15](./FEATURES.md) · **Priority:** P2 · **Size:** M
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** Two-way SMS that drives the call-out workflow.
- **Detailed specifications:**
  - Contact preferences, SMS consent tracking, message templates, inbound SMS handling.
  - Exposed as an A2A service (card from CS-011); wired to CS-018.
  - Respect consent: no messaging without recorded opt-in.

### CS-023 — Event data ingest pipeline
- **Feature:** [F27](./FEATURES.md) · **Priority:** P2 · **Size:** M
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** Feed local events into demand so `ADJ-EVENT-LOCAL` works without manual overrides.
- **Detailed specifications:**
  - Ingest a source (Ticketmaster / Songkick / per-venue) into an effective-dated `Event_Calendar` per store.
  - Expose to forecaster (CS-014) and solver (CS-016) as a multiplier override (+25% demand, +1 staff within 0.5 mi, ±2h/+1h window).
  - Calibrate the +25% / +1 staff values against observed uplift (reuse CS-008 back-test window).

### CS-024 — Rule-change rollout & reload flow
- **Feature:** [F28](./FEATURES.md) · **Priority:** P2 · **Size:** S
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~1d (est.)
- **Description:** Make effective dating behaviour, not just a column.
- **Detailed specifications:**
  - Versioned reload endpoint the solver calls per shift generation.
  - Diff log in `Rule_Eval_Log` (`agent = "labor-compliance-agent/reload"`).
  - n8n trigger watching `coffee_shop_labor_rules.xlsx` for changes, notifying the agent.
  - Verify an `effective_from = 2027-01-01` row takes effect without redeploy.

---

## P3 — Hardening & later phases

### CS-025 — Square Sandbox replay
- **Feature:** [F17](./FEATURES.md) · **Priority:** P3 · **Size:** S
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~1d (est.)
- **Description:** Simulate a live POS feed for demos and integration tests.
- **Detailed specifications:**
  - Replay historical sales through Square Sandbox.
  - Drive the nightly POS sync (CS-019) from the sandbox feed.

### CS-026 — Observability across A2A
- **Feature:** [F18](./FEATURES.md) · **Priority:** P3 · **Size:** M
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** Debuggable, trustworthy inter-agent calls + auditable rule reloads.
- **Detailed specifications:**
  - One trace ID propagated across all A2A calls.
  - Authentication and timeouts on each boundary.
  - Basic tracing/metrics; incorporate the CS-024 reload watch flow.

### CS-027 — Prep / Waste agent (design-only)
- **Feature:** [F20](./FEATURES.md) · **Priority:** P3 · **Size:** L (later)
- **Status:** Design-only (Phase 2)
- **Developer:** Unassigned · **Started:** — · **Duration:** TBD (design now, build Phase 2)
- **Description:** Agent Card + schema designed now, populated later; forecast-driven prep/waste reduction.
- **Detailed specifications:**
  - Draft Agent Card and data schema only.
  - No runtime build this phase; interface must be stable enough that Phase 2 starts without redesign.

### CS-028 — Inventory agent (design-only)
- **Feature:** [F21](./FEATURES.md) · **Priority:** P3 · **Size:** L (later)
- **Status:** Design-only (Phase 2)
- **Developer:** Unassigned · **Started:** — · **Duration:** TBD (design now, build Phase 2)
- **Description:** Agent Card + schema for demand-aware inventory.
- **Detailed specifications:**
  - Draft Agent Card and schema only; lock the interface for Phase 2.

### CS-029 — Loyalty agent (design-only)
- **Feature:** [F22](./FEATURES.md) · **Priority:** P3 · **Size:** L (later)
- **Status:** Design-only (Phase 3)
- **Developer:** Unassigned · **Started:** — · **Duration:** TBD (design now, build Phase 3)
- **Description:** Forecast-aware Loyalty agent; identity/consent schema designed early so later build is additive.
- **Detailed specifications:**
  - Draft Agent Card and identity/consent schema only.
  - Consent model must be defined up front (PII boundary).

---

## P0 — Data-platform blockers

### CS-042 — PostgreSQL platform decision & canonical-store migration
- **Feature:** [F42](./FEATURES.md) · **Priority:** P0 · **Size:** M
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** Make PostgreSQL the canonical store and demote Excel to fixtures and exports.
- **Detailed specifications:**
  - Pin the Postgres version; document the `.env.example` connection convention and the read-only, read-write, and manager roles.
  - Select Alembic as the migration tool and document the workbook-to-table transition, resolving the CS-006/F23 canonical-path decision.
  - Demonstrate one agent reading the canonical database rather than an `.xlsx` file.

### CS-043 — Postgres provisioning, migrations & recovery runbook
- **Feature:** [F43](./FEATURES.md) · **Priority:** P0 · **Size:** S
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~1d (est.)
- **Description:** Provide reproducible local Postgres infrastructure and a recoverable schema lifecycle.
- **Detailed specifications:**
  - Docker Compose starts Postgres 16 and PgBouncer; `alembic upgrade head` works from a fresh checkout.
  - Every migration has tested upgrade and downgrade paths.
  - Add `docs/postgres-backup.md` covering scheduled `pg_dump` and a successful restore drill.

### CS-044 — Idempotent Excel-to-Postgres ETL
- **Feature:** [F44](./FEATURES.md) · **Priority:** P0 · **Size:** M
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** Load all current workbook fixtures into the schema without data loss or duplicate rows.
- **Detailed specifications:**
  - Implement `scripts/etl_excel_to_postgres.py` using upserts/`ON CONFLICT`, and use `COPY` for large tables.
  - Report source/target row counts and fail on stated invariants, including missing `rule_id` or invalid transaction `store_id`.
  - A second run produces the same row counts; schedule the checked ETL in CI.

### CS-047 — Canonical data schema, ERD & migrations
- **Feature:** [F47](./FEATURES.md) · **Priority:** P0 · **Size:** M
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** Define the database contract before consumers depend on it.
- **Detailed specifications:**
  - Publish `docs/schema.md` and an ERD covering every F47 table, PK/FK, delete behaviour, index, partition and effective-dating decision.
  - Model prohibited effective-date overlap with `DATERANGE`/GiST exclusion constraints where applicable.
  - Generate and review Alembic migrations from the approved schema; cross-reference F10 and F26.

## P1 — Product surfaces, data access & ingestion

### CS-030 — Manager UI foundation
- **Feature:** [F30](./FEATURES.md) · **Priority:** P1 · **Size:** M
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** Establish the shared manager-facing Next.js application shell and backend boundary.
- **Detailed specifications:**
  - Choose and document the Next.js-to-backend integration pattern, demo authentication boundary, and LangGraph-state binding convention.
  - Implement shared navigation and layout with an authenticated manager entry point.
  - Screen stories CS-031–CS-035 consume this shared shell rather than creating independent patterns.

### CS-031 — Weekly schedule view
- **Feature:** [F31](./FEATURES.md) · **Priority:** P1 · **Size:** M
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** Deliver the read-only, rule-explainable weekly schedule grid.
- **Detailed specifications:**
  - Support store, role, status and multi-week navigation filters; render assignments, station coverage and meal breaks.
  - Selecting a shift surfaces its `Rule_Eval_Log` references and reasons.
  - Route edits to the solver or call-out flow; no direct grid mutation.

### CS-034 — Rules admin
- **Feature:** [F34](./FEATURES.md) · **Priority:** P1 · **Size:** M
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** Provide guarded maintenance of labor rules and consent waivers.
- **Detailed specifications:**
  - Block submissions that violate F26 reconciliation, use unverified law, or conflict in effective dates.
  - Show waiver expiry warnings, including WV-0001, and route accepted edits through CS-024 with an audit record.
  - Prove the UI cannot silently save an invalid rule.

### CS-045 — Typed agent data-access layer
- **Feature:** [F45](./FEATURES.md) · **Priority:** P1 · **Size:** M
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** Give agents a pooled, role-aware repository API instead of raw SQL.
- **Detailed specifications:**
  - Implement typed models and parameterised repository reads/writes for every F47 table.
  - Enforce the documented roles and store LangGraph checkpoints in Postgres.
  - Integration-test the layer against a test database; prohibit string-built SQL in agent code.

### CS-048 — Scheduled weather ingestion
- **Feature:** [F48](./FEATURES.md) · **Priority:** P1 · **Size:** S
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~1d (est.)
- **Description:** Turn the existing weather builder into an idempotent, observable data job.
- **Detailed specifications:**
  - Schedule weekly n8n execution; upsert hourly/daily weather on location/date/hour keys.
  - Emit OTel/Loki telemetry and fail loudly when any store has missing hours.
  - Preserve Open-Meteo attribution and validate reruns do not duplicate data.

### CS-032 — Call-out approval screen
- **Feature:** [F32](./FEATURES.md) · **Priority:** P2 · **Size:** M
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** Implement the manager’s human-in-the-loop call-out approval surface.
- **Detailed specifications:**
  - Show the affected shift, ranked candidate, eligibility reasons and hard-stop legal branches inline.
  - Approve or redirect the LangGraph workflow, then send accepted results to payroll/HCM and compliance logging.
  - Preserve two-way state binding so a refresh cannot lose the approval state.

### CS-033 — Forecast review screen
- **Feature:** [F33](./FEATURES.md) · **Priority:** P2 · **Size:** M
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** Make forecast quality reviewable by managers without changing calibration directly.
- **Detailed specifications:**
  - Plot P70 versus actuals by store, day, hour and category with a selectable back-test range.
  - Provide per-hour detail including the F1 weather join and a handoff to F5 calibration.
  - Keep the screen read-only and conform to the F8 forecast contract.

### CS-035 — Calibration and rule-evaluation log viewer
- **Feature:** [F35](./FEATURES.md) · **Priority:** P2 · **Size:** S
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~1d (est.)
- **Description:** Expose audit logs as searchable, exportable read-only data.
- **Detailed specifications:**
  - Filter calibration and rule-evaluation records by date, rule, employee, store and result.
  - Export the displayed results to CSV without creating a second write path.

### CS-036 — OpenTelemetry observability foundation
- **Feature:** [F36](./FEATURES.md) · **Priority:** P2 · **Size:** M
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** Define the shared OTel contract and demo deployment shape.
- **Detailed specifications:**
  - Pin SDK versions; document W3C propagation, metric vocabulary, sampling policy and resource attributes.
  - Provide a Compose topology for Collector, Tempo, Mimir, Loki and Grafana.
  - Demonstrate one cross-agent trace visible with a single `trace_id`.

### CS-037 — Per-agent OTel instrumentation
- **Feature:** [F37](./FEATURES.md) · **Priority:** P2 · **Size:** M
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** Instrument every runtime boundary and document emitted telemetry.
- **Detailed specifications:**
  - Trace A2A client/server calls, tools, graph transitions, solving, rule evaluation and SMS operations.
  - Emit the specified solver, rule-eval and forecast metrics; propagate `traceparent` at every A2A boundary.
  - List the emitted spans in each relevant agent `AGENTS.md`.

### CS-038 — OTel Collector deployment
- **Feature:** [F38](./FEATURES.md) · **Priority:** P2 · **Size:** S
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~1d (est.)
- **Description:** Version and run the shared telemetry Collector.
- **Detailed specifications:**
  - Add `otel/collector-config.yaml` with OTLP receivers, batching, resource attributes and tail sampling.
  - Export traces, metrics and logs to Tempo, Mimir and Loki; verify error traces are retained.

### CS-039 — LGTM backend deployment
- **Feature:** [F39](./FEATURES.md) · **Priority:** P2 · **Size:** M
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** Run the local trace, metric and log backends with a documented production route.
- **Detailed specifications:**
  - Compose starts Tempo, Mimir, Loki and their local storage dependencies from one command.
  - Verify each backend receives Collector data; document S3/Kubernetes or managed production equivalents.

### CS-040 — Provisioned Grafana dashboards
- **Feature:** [F40](./FEATURES.md) · **Priority:** P2 · **Size:** S
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~1d (est.)
- **Description:** Deliver the operational dashboards called for by F40.
- **Detailed specifications:**
  - Provision all three data sources and dashboards for A2A RED, solver, rule evaluation, call-out states and trace drill-down.
  - Validate that a pasted `trace_id` correlates its spans, metrics and logs.

### CS-041 — CI observability gate
- **Feature:** [F41](./FEATURES.md) · **Priority:** P2 · **Size:** S
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~1d (est.)
- **Description:** Prevent uninstrumented external execution paths from merging.
- **Detailed specifications:**
  - Add a test-mode span exporter and CI assertion for HTTP, A2A, database and tool call paths.
  - Demonstrate that a new uninstrumented wrapper fails CI and an instrumented one passes.

### CS-046 — Read-only Excel export from Postgres
- **Feature:** [F46](./FEATURES.md) · **Priority:** P2 · **Size:** S
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~1d (est.)
- **Description:** Retain an Excel view while keeping Postgres authoritative.
- **Detailed specifications:**
  - Generate the three existing workbook shapes from canonical tables using the F29 taxonomy.
  - Verify row equivalence with database content and ensure the exporter never writes back.

### CS-049 — POS data ingestion
- **Feature:** [F49](./FEATURES.md) · **Priority:** P2 · **Size:** M
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** Ingest initial and nightly POS transactions into Postgres.
- **Detailed specifications:**
  - Support Square Sandbox and a production adapter; map products through F29 and backfill before incremental sync.
  - Emit batch telemetry and route unmapped products to a visible dead-letter queue.

### CS-050 — Event-feed ingestion
- **Feature:** [F50](./FEATURES.md) · **Priority:** P2 · **Size:** M
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** Implement CS-023's event pipeline on the canonical data platform.
- **Detailed specifications:**
  - Ingest effective-dated events keyed by store, event and start time; cancellations close rows rather than delete them.
  - Publish an `event_calendar` view for the forecaster and solver, using the shared n8n/OTel pattern.

### CS-051 — Rule-change ingestion and reload notification
- **Feature:** [F51](./FEATURES.md) · **Priority:** P2 · **Size:** S
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~1d (est.)
- **Description:** Implement CS-024's reload workflow for Postgres rule changes.
- **Detailed specifications:**
  - Watch inserts, updates and effective-date changes; coalesce a batch into one idempotent notification.
  - Log the reload and OTel event, then prove a running solver adopts a new effective rule within minutes.

## P2 — Analytics stories

### CS-052 — Store-manager analytics foundation
- **Feature:** [F52](./FEATURES.md) · **Priority:** P2 · **Size:** M
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** Establish the store-scoped analytics shell, RBAC and shared filters.
- **Detailed specifications:**
  - Enforce one-store manager visibility using the F45 read-only role.
  - Implement the KPI/insight/drill-down layout, fixed store selector and shared date range.

### CS-053 — Store-manager KPI dashboard
- **Feature:** [F53](./FEATURES.md) · **Priority:** P2 · **Size:** S
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~1d (est.)
- **Description:** Surface daily store health as actionable KPI cards.
- **Detailed specifications:**
  - Render all F53 measures with deltas and drill-down links.
  - Every off-target card exposes the relevant rule or constraint identifier.

### CS-054 — Store-manager operational insights
- **Feature:** [F54](./FEATURES.md) · **Priority:** P2 · **Size:** M
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** Deliver manager-facing operational diagnostics.
- **Detailed specifications:**
  - Provide the F54 hourly heatmap, coverage, productivity, throughput, call-out and minor-utilisation views.
  - Drill from each compliance signal to the filtered `Rule_Eval_Log` and its cited rule.

### CS-056 — C-Suite analytics foundation
- **Feature:** [F56](./FEATURES.md) · **Priority:** P2 · **Size:** M
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** Establish company-wide analytics with drill-through to store context.
- **Detailed specifications:**
  - Enforce C-Suite all-store access and preserve audit context when drilling into CS-052.
  - Implement executive layout, cross-store comparison conventions and PDF/CSV export contract.

### CS-057 — C-Suite KPI dashboard
- **Feature:** [F57](./FEATURES.md) · **Priority:** P2 · **Size:** S
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~1d (est.)
- **Description:** Present company performance and compliance posture on one screen.
- **Detailed specifications:**
  - Render all F57 cards, top movers and drill-throughs with one-line off-target reasons.
  - Support persistent narrative annotations on anomalies.

### CS-058 — Cross-store rollup and manager drill-through
- **Feature:** [F58](./FEATURES.md) · **Priority:** P2 · **Size:** M
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** Compare store performance and preserve context while drilling in.
- **Detailed specifications:**
  - Provide the F58 matrix, sparklines, compliance/call-out/solver rollups and two-store comparison.
  - Clicking a store opens its CS-052 history with the originating executive context recorded.

## P3 — Advanced analytics

### CS-055 — Store-manager deep analytics
- **Feature:** [F55](./FEATURES.md) · **Priority:** P3 · **Size:** M
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** Provide pre-built forensic views for a single store.
- **Detailed specifications:**
  - Implement the F55 elasticity, product-mix, solver, labour-law, ESSTA, FPC and override analyses.
  - Source heavy aggregates from the F47 materialised views and document each metric definition.

### CS-059 — C-Suite deep analytics
- **Feature:** [F59](./FEATURES.md) · **Priority:** P3 · **Size:** L
- **Status:** Not started
- **Developer:** Unassigned · **Started:** — · **Duration:** ~8d (est.)
- **Description:** Provide strategic cross-store analytics without ad-hoc SQL.
- **Detailed specifications:**
  - Implement every F59 cohort, elasticity, forecast-quality, ROI, compliance and coverage analysis.
  - Define refresh cadence and validation for each materialised aggregate.

---

## Backlog summary

| Story | Feature | Priority | Size | Status |
| --- | --- | --- | --- | --- |
| CS-001 | F1 | P0 | S | ✅ Done |
| CS-002 | F2 | P0 | M | Ready |
| CS-003 | F24 | P1 | XS | Not started |
| CS-004 | F3/F25 | P0 | S | Not started |
| CS-005 | F4 | P0 | XS+S | Not started |
| CS-006 | F23 | P0 | S | ✅ Done |
| CS-007 | F25 | P0 | S | Not started |
| CS-008 | F5 | P1 | M | Ready |
| CS-009 | F29 | P1 | XS | Not started |
| CS-010 | F6 | P1 | M | Partial |
| CS-011 | F7 | P1 | M | Scaffolded |
| CS-012 | F8 | P1 | S | In design |
| CS-013 | F10 | P1 | L | Not started |
| CS-013a | F10 | P1 | S | Not started |
| CS-013b | F10 | P1 | S | Not started |
| CS-013c | F10/F26 | P1 | XS | Not started |
| CS-014 | F9 | P1 | L | Blocked (umbrella) |
| CS-014a | F9 | P1 | M | Blocked |
| CS-014b | F9 | P1 | M | Blocked |
| CS-014c | F9 | P1 | S | Blocked |
| CS-015 | F12 | P1 | M | Not started |
| CS-016 | F11 | P1 | L | Blocked (umbrella) |
| CS-016a | F11 | P1 | M | Blocked |
| CS-016b | F11 | P1 | M | Blocked |
| CS-016c | F11 | P1 | S | Blocked |
| CS-016d | F11 | P1 | M | Blocked |
| CS-017 | F13 | P2 | S | Not started |
| CS-018 | F14 | P2 | L | Not started (umbrella) |
| CS-018a | F14 | P2 | M | Not started |
| CS-018b | F14 | P2 | M | Not started |
| CS-018c | F14 | P2 | S | Not started |
| CS-019 | F16 | P2 | M | Not started |
| CS-020 | F15 | P2 | M | Not started |
| CS-021 | F19 | P1 | M | Not started |
| CS-022 | F26 | P1 | XS | Not started |
| CS-023 | F27 | P2 | M | Not started |
| CS-024 | F28 | P2 | S | Not started |
| CS-025 | F17 | P3 | S | Not started |
| CS-026 | F18 | P3 | M | Not started |
| CS-027 | F20 | P3 | L | Design-only |
| CS-028 | F21 | P3 | L | Design-only |
| CS-029 | F22 | P3 | L | Design-only |
| CS-030 | F30 | P1 | M | Not started |
| CS-031 | F31 | P1 | M | Not started |
| CS-032 | F32 | P2 | M | Not started |
| CS-033 | F33 | P2 | M | Not started |
| CS-034 | F34 | P1 | M | Not started |
| CS-035 | F35 | P2 | S | Not started |
| CS-036 | F36 | P2 | M | Not started |
| CS-037 | F37 | P2 | M | Not started |
| CS-038 | F38 | P2 | S | Not started |
| CS-039 | F39 | P2 | M | Not started |
| CS-040 | F40 | P2 | S | Not started |
| CS-041 | F41 | P2 | S | Not started |
| CS-042 | F42 | P0 | M | Not started |
| CS-043 | F43 | P0 | S | Not started |
| CS-044 | F44 | P0 | M | Not started |
| CS-045 | F45 | P1 | M | Not started |
| CS-046 | F46 | P2 | S | Not started |
| CS-047 | F47 | P0 | M | Not started |
| CS-048 | F48 | P1 | S | Not started |
| CS-049 | F49 | P2 | M | Not started |
| CS-050 | F50 | P2 | M | Not started |
| CS-051 | F51 | P2 | S | Not started |
| CS-052 | F52 | P2 | M | Not started |
| CS-053 | F53 | P2 | S | Not started |
| CS-054 | F54 | P2 | M | Not started |
| CS-055 | F55 | P3 | M | Not started |
| CS-056 | F56 | P2 | M | Not started |
| CS-057 | F57 | P2 | S | Not started |
| CS-058 | F58 | P2 | M | Not started |
| CS-059 | F59 | P3 | L | Not started |
