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
- **Status:** ✅ Done (2026-11-12) — all 6 validation checks PASS (CS-003 folded in)
- **Developer:** Unassigned · **Started:** 2026-11-12 · **Duration:** ~0.5d (actual)
- **Description:** Extend `coffee_shop_staff.xlsx` from identity-only to a schedulable roster.
- **Detailed specifications:**
  - Add typed columns: `role`, `skill_level`, `home_location_id`, `cross_location_eligible`, `is_key_holder`, `cash_authorised`, `fpc_holder`, `hourly_rate`, `enrolled_in_school`, `availability_persona`, `min_hours_week`, `max_hours_week`.
  - Map minor occupation restrictions to the `role` vocabulary (align with `FED-MINOR-1415-OCCUPATION`).
  - `location_id = store_id`; home location must be one of the 3 stores.
  - Validate: every active employee has role, home location, rate, and min/max hours; no min > max.
  - Output consumed directly by the solver (CS-016).
- **Delivered:**
  - `data/build_roster_fields.py` (deterministic, seed=42) + `data/validate_roster_fields.py` (read-only); `ROSTER_VALIDATION.md` PASS report.
  - **Naming reconciliation:** the FPC column is `food_protection_cert` (not the backlog's `fpc_holder`) to match the `NYC-FPC-COVERAGE` rule's own note; `skill_level==3` = peak-skill eligible (`COV-PEAK-SKILL`). CS-013/CS-016 should consume these names.
  - Values are legal (no minor is a key-holder/shift lead; minor weekly hours within NY caps) and solver-feasible per home store (≥3 key-holders, ≥2 skill-3 baristas, ≥4 FPC holders). Store floor/station counts used: LM(5)=6/4, HK(8)=6/4, Astoria(3)=4/3.
  - Persona→availability-window mapping deferred to CS-016a (the field is a label the solver interprets).

### CS-003 — Roster school-enrolment & multi-employer typed fields
- **Feature:** [F24](./FEATURES.md) (part of F2) · **Priority:** P1 · **Size:** XS
- **Status:** ✅ Done (2026-11-12) — folded into CS-002
- **Developer:** Unassigned · **Started:** 2026-11-12 · **Duration:** incl. in CS-002
- **Description:** Make school enrolment a typed join key and add the multi-employer flag, so compliance rules evaluate cleanly.
- **Detailed specifications:**
  - Replace free-text "school enrolment" with `is_enrolled` (bool), `school_calendar_id` (FK to School_Calendar), `grade_band`.
  - Add `has_other_employer` (default `False`) to support `NY-MINOR-ALL-MULTIEMPLOYER`.
  - For the 3 employees aged 15 (`FED-MINOR-1415-OCCUPATION`), record parent/school certificate on file via the existing `Consent_Waivers` table.
  - Depends on CS-002; blocks CS-016.
- **Delivered:** `is_enrolled`, `school_calendar_id` (`NYC-DOE-2026-27`, blank iff not enrolled), `grade_band`, `has_other_employer` added in the same pass as CS-002. All 8 minors enrolled (HS); 1–2 flagged `has_other_employer` to exercise the multi-employer rule. Note: the existing `Consent_Waivers` WV-0001 (EMP-1033) already records that employee's certificate.

### CS-004 — Load NYC school calendar (multi-year)
- **Feature:** [F3](./FEATURES.md) + [F25](./FEATURES.md) · **Priority:** P0 · **Size:** S
- **Status:** ✅ Done (2026-11-12) — CS-007 merged into this delivery
- **Developer:** Unassigned · **Started:** 2026-11-12 · **Duration:** ~0.5d (actual)
- **Description:** Load NYC public schools + federal/NY holiday calendars as effective-dated data covering the full forecast/back-test horizon.
- **Detailed specifications:**
  - Load school days, holidays and breaks for **2023–2028** (back-tests use 2023; forward planning runs ≥1 yr ahead).
  - Each row effective-dated so a later edit never silently re-classifies a past date.
  - Expose an `IN_SESSION` lookup by date; no `None` rows allowed for any date the solver can emit.
  - Replace the 2026-only `Holiday_Calendar` (11 rows) with the multi-year set.
  - Blocks minor-labor rule evaluation (CS-012) and the solver (CS-016).
- **Delivered:** `School_Calendar` now has 2,192 dated rows spanning 2023–2028, with `NYC-DOE-2026-27` retained for every enrolled-minor roster key, effective dates, daily `in_session` and `day_before_school_day` flags, source provenance, and 416 explicitly marked 2027–28 planning-assumption rows. `Holiday_Calendar` now has 144 separate US-Federal and NY-State holiday rows (same-date jurisdictional records retained). `data/build_school_calendar.mjs` regenerates the workbook data; `data/validate_school_calendar.mjs` verifies coverage, active-record uniqueness, roster-key compatibility, planning flags, and holiday provenance.
- **Review follow-up (2026-11-12):** a `/code-review` of the delivery was triaged. Added `data/validate_calendars.py` — an in-repo Python guard that runs **without** codex's `@oai/artifact-tool` (the `.mjs` scripts cannot run in plain Node/Python) and adds the full-date-coverage + row-count assertions the `.mjs` validator lacked; all 4 checks PASS against the committed data (2192 rows, 0 gaps/dups, FK resolves, 66 federal holidays present). Fixed two real-but-currently-latent source bugs: Election Day = first Tue **after** first Mon (previously first Tue — harmless in 2023–2028 since no Nov 1 is a Tuesday), and hardened `validate_school_calendar.mjs` (dynamic `getUsedRange()` + `actual==expected` row assertion + empty-cell handling). The `.mjs` edits are source-only — the committed xlsx is already correct, so **codex must re-run them on the next calendar change**. The FED/NY-STATE same-date duplication (66 rows) was kept by design (NY observes federal holidays; match by date).

### CS-005 — Closing-hours decision & operating-hours config
- **Feature:** [F4](./FEATURES.md) · **Priority:** P0 · **Size:** XS (decision) + S (config)
- **Status:** ✅ Done (2026-11-12) — suggested answer set adopted
- **Developer:** Unassigned · **Started:** 2026-11-12 · **Duration:** ~0.5d (actual)
- **Description:** Settle the evening-hours business decision and encode per-store operating hours.
- **Detailed specifications:**
  - Decide: keep 10:30 PM close with minimum evening crew, **or** shorten hours (sales stop ~8–9 PM; Astoria opens 7 AM).
  - Record the decision and rationale in the Project Status doc.
  - Encode per-store open/close + 45 min pre-open / post-close in `coffee_shop_labor_standards.xlsx`.
  - Output bounds the solver's time horizon (CS-016).
- **Delivered:** Decision recorded in `docs/decisions/CS-005-closing-hours.md` (now Decided). Encoded via `data/apply_closing_hours.py`: LM(5)/HK(8) **06:00–21:00** (staff 05:15–21:45), Astoria(3) **07:00–20:30** (staff 06:15–21:15); Astoria open corrected 06:00→07:00 (data fix). No dead-hour evening crew (Q4=C). `Hours_Validation` hours-to-close recomputed. Solver time horizon (CS-016) now defined.

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
- **Status:** ✅ Done (2026-11-12) — merged into CS-004
- **Developer:** Unassigned · **Started:** 2026-11-12 · **Duration:** incl. in CS-004
- **Description:** Holiday calendar coverage matching the forecast horizon (companion to CS-004 for federal/NY holidays specifically).
- **Detailed specifications:**
  - Load federal + NY State holidays for 2023–2028, effective-dated.
  - Join to daily sales for back-test labelling (CS-011, CS-014).
  - Reconcile with the school calendar so no date is classified twice.
  - *Note:* may be merged into CS-004 if one calendar loader covers both; keep separate if sources differ.
- **Delivered:** Separate jurisdictional holiday rows now coexist with independent school-session records; consumers can identify a holiday without overwriting an NYCPS instructional-day classification.

---

## P1 — Core Phase 1

### CS-008 — Calibrate throughput, weather multipliers & targets
- **Feature:** [F5](./FEATURES.md) · **Priority:** P1 · **Size:** M
- **Status:** ✅ Done (2026-11-12) — all 5 validation checks PASS
- **Developer:** Unassigned · **Started:** 2026-11-12 · **Duration:** ~0.5d (actual)
- **Description:** Turn synthetic/assumed demand and cost parameters into calibrated, auditable values using the F1 weather join.
- **Detailed specifications:**
  - Calibrate throughput seconds/item for all 9 categories + per-transaction register overhead (currently overstated — no basket ID).
  - Derive weather multipliers `ADJ-RAIN`, `ADJ-COLD`, `ADJ-HEAT`, `ADJ-SNOW` from `Daily_Sales_Weather`.
  - Set target utilisation (85%), forecast quantile (P70), labour % targets, overtime budget.
  - Define a back-test window (e.g. last 4 weeks of the sales window) and acceptance band (≤±10% MAPE per category-hour).
  - Append every accepted change to `Calibration_Log` (id, table, row_key, planned/observed/proposed, sample period, approver).
  - Event multipliers are **out of scope** here — see CS-023 (F27).
- **Delivered:** `data/calibrate_standards.py` (generator) + `data/validate_calibration.py` (validator → `CALIBRATION_VALIDATION.md`). 14 `Calibration_Log` rows appended (auto-approved).
  - **Throughput:** filled the empty `units_per_labor_hour` (=3600/sec) for all 9; corrected the per-line register overhead **25s→19.58s** via estimated basket size B≈1.28 (the no-basket-id fix); capacity-anchored seconds against observed peak — modeled P95 peak (~3600s) fits floor capacity at 85% with large headroom, so scale=1.0 (seconds confirmed-feasible, not re-derived; POS has no service-time data). `basis`→CALIBRATED.
  - **Weather multipliers — derived but NOT applied.** The back-test (last 4 wks/store) showed the data-derived multipliers (RAIN 0.92, COLD 0.70, HEAT 1.15 n=6, SNOW n=0) **worsen** MAPE (~42% vs ~39% baseline): with only 6 months of Jan–Jun data, weather is confounded with season. Per the acceptance gate, assumptions were kept (basis stays ASSUMPTION), but the **metric→imperial unit bug in the condition text was fixed** (the thresholds were in mm/°C/cm while the data is °F/in). Derivations + rationale logged.
  - **Targets:** util 0.85 / P70 / labour% / OT / penalties confirmed (retained); logged.
  - **Known limits:** 6-month season-confounded weather signal; no service-time data (throughput capacity-anchored, not measured); no basket id (overhead amortized). A richer forecaster may revisit weather effects at CS-014.

### CS-009 — Product→category taxonomy ownership
- **Feature:** [F29](./FEATURES.md) (part of F8) · **Priority:** P1 · **Size:** XS
- **Status:** ✅ Done (2026-11-12) — F29 AC1–AC5 met; all 6 tests + taxonomy/calibration validators PASS; **BUG-002 closed**
- **Developer:** Unassigned · **Started:** 2026-11-12 · **Duration:** ~0.5d (actual)
- **Description:** Establish one versioned category taxonomy that forecaster, throughput table and explanation layer all read.
- **Detailed specifications:**
  - Add a `Product_Taxonomy` table (or versioned mapping sheet) linking `product_id` → `product_category` (9 categories).
  - Pin a version; the forecast contract (CS-010b), `Throughput_Standards`, and explanation layer reference that version.
  - Add a check that flags any category present in one consumer but missing in another.
  - Blocks/feeds CS-010b (forecast contract) and CS-014 (forecaster).
- **Delivered:**
  - `data/build_product_taxonomy.py` (idempotent generator) → `Product_Taxonomy` (80 rows: `product_id`→detail/type/category, version-stamped) + `Taxonomy_Meta` (version `TAX-2023H1-v1`, source `2023-01-01..2023-06-30`, counts) in `coffee_shop_labor_standards.xlsx`.
  - `data/validate_taxonomy.py` → `TAXONOMY_VALIDATION.md` (5 checks PASS, independent of the generator per D3).
  - `coffee-scheduler/test/test_taxonomy.py` — SDD/TDD: 6 stdlib tests tagged `spec: F29.ac#`, committed failing-first before the impl.
  - **BUG-002 fixed here:** `calibrate_standards.py` now **fails closed** (`assert_category_coverage`) on any sales category absent from `Throughput_Standards` (was silently scored 0 labor); `validate_calibration.py` gained an independent coverage check **F**. The cross-consumer check the spec asked for is exactly this guard.
  - Scope held to XS: the forecast contract (CS-012) and explanation layer consume the pinned version later; not built here.

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
- **Status:** ✅ Done (2026-11-12) — `coffee-scheduler/compliance/engine.py`; unverified rules still bind, `claims_legal_accuracy` stays false until CS-021
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
- **Status:** ✅ Done (2026-11-12) — append-only insert into Postgres `rule_eval_log` as `app_rw`; xlsx is not the write path
- **Developer:** Unassigned · **Started:** — · **Duration:** ~1d (est.)
- **Description:** Every rule evaluation is logged — no silent passes.
- **Detailed specifications:**
  - Append to `Rule_Eval_Log` in `coffee_shop_labor_rules.xlsx`: `eval_id, schedule_id, shift_id, employee_id, rule_id, result, premium_cost_usd, evaluated_at, agent`.
  - Write on both pass and fail.
  - Used by the explanation layer (CS-017) and audit.

### CS-013b — Waiver expiry evaluation on shift date
- **Feature:** [F10](./FEATURES.md) · **Priority:** P1 · **Size:** S
- **Status:** ✅ Done (2026-11-12) — half-open waiver window; WV-0001 stops applying on 2027-06-26
- **Developer:** Unassigned · **Started:** — · **Duration:** ~1d (est.)
- **Description:** Re-check consent waivers against the actual shift date on every generation, not just at rule load.
- **Detailed specifications:**
  - For any rule `waivable_by_consent`, validate `Consent_Waivers.valid_from`/`valid_to` against the shift date.
  - Example: WV-0001 (EMP-1033, `NY-MINOR-1617-SCH-WINDOW`) expires 2027-06-26 — must stop applying after that date.
  - Expired waiver → rule applies as if no waiver.

### CS-013c — Coverage_Rules ↔ Labor_Rules fail-closed reconciliation
- **Feature:** [F10](./FEATURES.md) + [F26](./FEATURES.md) · **Priority:** P1 · **Size:** XS
- **Status:** ✅ Done (2026-11-12) — `reconcile_coverage` fails closed on a missing or non-blocking LAW citation. The VERIFIED-only CI gate stays with CS-021 / CS-022.
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
- **Status:** ✅ Done (2026-11-13) — Codex dev / Claude validate+document. Compose adds PgBouncer (6432, transaction pooling); migration `0001` round-trips (`test_migrations_roundtrip.py`, `spec: F43.ac3`); `docs/postgres-backup.md` backup/restore drill executed (restore + RLS spot-check = 1). Plan & validation in [docs/plans/CS-043-postgres-provisioning.md](./docs/plans/CS-043-postgres-provisioning.md). All F43 AC1–AC5 verified on the host. **Two follow-ups logged** (not blockers): (1) engine uses non-local `SET ROLE` → role leaks onto pooled backends (tenant isolation still holds via the local GUC) — switch to `SET LOCAL ROLE`; (2) the round-trip test drops data on the target DB — give it a scratch database.
- **Developer:** Codex (dev) + Claude (validate/doc) · **Started:** 2026-11-13 · **Duration:** ~1d (est.)
- **Description:** Provide reproducible local Postgres infrastructure and a recoverable schema lifecycle.
- **Detailed specifications:**
  - Docker Compose starts Postgres 16 and PgBouncer; `alembic upgrade head` works from a fresh checkout.
  - Every migration has tested upgrade and downgrade paths.
  - Add `docs/postgres-backup.md` covering scheduled `pg_dump` and a successful restore drill.

### CS-044 — Idempotent Excel-to-Postgres ETL
- **Feature:** [F44](./FEATURES.md) · **Priority:** P0 · **Size:** M
- **Status:** ✅ Done (2026-11-12) — `scripts/etl_excel_to_postgres.py` imports the workbook fixtures as an idempotent tenant-scoped snapshot, validates invariants before mutation, uses COPY staging for high-volume tables, and keeps PII opt-in.
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

## Phase 1 — SaaS multi-tenancy (CS-060 … CS-070)

Stories for features **F60–F70** (see [`FEATURES.md`](./FEATURES.md) and the plan docs
[`../PLAN.md`](../PLAN.md) / [`docs/architecture/saas-multi-tenancy.md`](./docs/architecture/saas-multi-tenancy.md)).
Convert the single-tenant deployment to a shared-schema multi-tenant SaaS; Maven Roasters becomes the
`maven-demo` seed tenant. Sequenced in three waves: **Wave 1 (P0 foundation)** CS-060 → CS-061 → CS-068 →
CS-070 → CS-062; **Wave 2 (P1 SaaS surface)** CS-063/CS-067/CS-066/CS-064/CS-065; **Wave 3 (P2)** CS-069.
CS-068 is a **gate** — no second tenant is provisioned until its suite is green. These depend on the
Postgres data plane (F42–F47); until that lands they are **Blocked**.

### CS-060 — Tenancy model & Row-Level Security (umbrella)
- **Feature:** [F60](./FEATURES.md) · **Priority:** P0 · **Size:** M
- **Status:** Blocked (on F42–F47 Postgres data plane)
- **Developer:** Unassigned · **Started:** — · **Duration:** ~5d (est., sum of sub-stories)
- **Description:** The foundation: `tenant_id` on every domain table, Postgres RLS as the isolation guarantee, and the session plumbing that sets tenant context per request. Broken into three sub-stories.

#### CS-060a — Schema, `tenant_id` & scale-safe migration
- **Feature:** [F60](./FEATURES.md) · **Priority:** P0 · **Size:** M · **Status:** Blocked
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Detailed specifications:**
  - Add `tenants` root table; add `tenant_id UUID NOT NULL REFERENCES tenants(id)` to every F47 domain table; recreate every index / global `UNIQUE` with a leading `tenant_id` (F60.AC1).
  - Migration is **scale-safe**: batched backfill, `CREATE INDEX CONCURRENTLY`, `NOT NULL` via `ADD CONSTRAINT … NOT VALID` → `VALIDATE` (PLAN §7, §15.6); snapshot before cutover.
  - Backfill all existing rows to the `maven-demo` tenant; ETL (F44) gains a `tenant_id` arg.
- **Cross-refs:** F47, F44.

#### CS-060b — RLS policies + non-owner `app_rw` role
- **Feature:** [F60](./FEATURES.md) · **Priority:** P0 · **Size:** S · **Status:** Blocked
- **Developer:** Unassigned · **Started:** — · **Duration:** ~1d (est.)
- **Detailed specifications:**
  - `ENABLE` **and** `FORCE ROW LEVEL SECURITY` on every domain table; `tenant_isolation` policy with both `USING` and `WITH CHECK` on `current_setting('app.tenant_id', true)::UUID` (F60.AC2).
  - App connects as a non-owner `app_rw` role (DML only, no `BYPASSRLS`); only the migration/ETL role bypasses (F60.AC3). This is the fix for the owner-bypass fail-open.
- **Cross-refs:** architecture §2.4 + §14.1; verified by CS-068.

#### CS-060c — Per-request tenant session context
- **Feature:** [F60](./FEATURES.md) · **Priority:** P0 · **Size:** S · **Status:** Blocked
- **Developer:** Unassigned · **Started:** — · **Duration:** ~1d (est.)
- **Detailed specifications:**
  - PgBouncer transaction mode; the ORM middleware opens a transaction, `SET LOCAL app.tenant_id` from the JWT, and asserts it is set before the first statement (F60.AC4; `SET LOCAL` needs an explicit txn).
  - A query with no tenant context returns zero rows (fail-closed).
- **Cross-refs:** architecture §2.5 + §14.1.

### CS-061 — Auth + per-tenant RBAC (umbrella)
- **Feature:** [F61](./FEATURES.md) · **Priority:** P0 · **Size:** M
- **Status:** Blocked (on CS-060)
- **Developer:** Unassigned · **Started:** — · **Duration:** ~6d (est., sum of sub-stories)
- **Description:** Identity, roles, tokens, and the full auth-hardening surface. Blocks every UI route.

#### CS-061a — Core auth (users, roles, login, JWT)
- **Feature:** [F61](./FEATURES.md) · **Priority:** P0 · **Size:** M · **Status:** Blocked
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Detailed specifications:**
  - `tenant_users` (roles `owner`/`manager`/`staff`/`read_only`, per-tenant email uniqueness) + `saas_admins` (MFA); argon2id hashing (F61.AC1).
  - Login issues an **asymmetrically-signed** JWT (RS256/ES256; reject `alg=none`; validate `iss`/`aud`/`exp`) with tenant/user/role claims; short access token + rotating refresh with reuse detection (F61.AC2).
- **Cross-refs:** PLAN §3; architecture §3.

#### CS-061b — Auth hardening
- **Feature:** [F61](./FEATURES.md) · **Priority:** P0 · **Size:** S · **Status:** Blocked
- **Developer:** Unassigned · **Started:** — · **Duration:** ~1.5d (est.)
- **Detailed specifications:**
  - Per-request revocation: middleware re-checks tenant `status` + user `is_active`, backed by a revocation list, so suspension takes effect within one access-token TTL (F61.AC3).
  - Password reset (time-boxed single-use token), login rate-limiting / lockout, optional TOTP MFA for `owner`/`manager` (F61.AC4).
- **Cross-refs:** PLAN §15.2; architecture §14.2.

#### CS-061c — Invitations, membership & audit log
- **Feature:** [F61](./FEATURES.md) · **Priority:** P1 · **Size:** M · **Status:** Blocked
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Detailed specifications:**
  - `tenant_invitations` + invite→email→accept flow to add members (F61.AC5); consider separating identity (`users`) from `tenant_memberships` so one person joins many tenants.
  - **Append-only** `audit_log` (`app_rw` has INSERT only) covering auth events, admin actions, and impersonation (records `impersonated_by` + target).
- **Cross-refs:** PLAN §15.3–15.4; architecture §14.3–14.4.

### CS-062 — Tenant onboarding
- **Feature:** [F62](./FEATURES.md) · **Priority:** P0 · **Size:** M · **Status:** Blocked (on CS-061, CS-070)
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Detailed specifications:**
  - Signup creates tenant + owner in one transaction; slug format + **reserved-slug denylist** validated; duplicate → 409 (F62.AC1).
  - Email verification gates solver use; 14-day trial + nightly suspension job (F62.AC2–AC3).
  - First-run wizard (store, employee, hours, plan); **idempotent** provisioning seeding jurisdiction rules + default standards (F62.AC4); a jurisdiction with no verified rule set is **refused**, not NY-defaulted.
- **Cross-refs:** F10/F19 (jurisdiction rule templates); architecture §4 + §14.13.

### CS-063 — Per-tenant configuration
- **Feature:** [F63](./FEATURES.md) · **Priority:** P1 · **Size:** M · **Status:** Blocked (on CS-060, CS-061)
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Detailed specifications:**
  - Structured per-tenant config (jurisdiction, per-store hours, coverage rules, cost targets, demand adjustments) in RLS-protected tables; non-secret flags in `tenants.settings` JSONB (F63.AC1).
  - Per-tenant **credentials** in an encrypted `tenant_secrets` table — never plaintext JSONB (F63.AC2); a `SELECT *` / admin UI never exposes a secret.
- **Cross-refs:** architecture §5 + §14.1; PLAN §4.

### CS-064 — SaaS admin surface (umbrella)
- **Feature:** [F64](./FEATURES.md) · **Priority:** P1 · **Size:** L · **Status:** Blocked (on CS-061, CS-070)
- **Developer:** Unassigned · **Started:** — · **Duration:** ~8d (est., sum of sub-stories)
- **Description:** Separate admin app/auth/deploy for tenant operations. Broken into read UI + actions.

#### CS-064a — Tenant list, detail & usage
- **Feature:** [F64](./FEATURES.md) · **Priority:** P1 · **Size:** M · **Status:** Blocked
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Detailed specifications:** tenant list (search/filter/paginate), tenant detail (usage, last login, plan, payment status), aggregate dashboard (MRR, active tenants, totals); separate app/auth/deploy, tenant users cannot reach `/admin` (F64.AC1, AC4).

#### CS-064b — Admin actions (suspend / impersonate / plan)
- **Feature:** [F64](./FEATURES.md) · **Priority:** P1 · **Size:** M · **Status:** Blocked
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Detailed specifications:** suspend/activate/churn (append-only audit); capped-duration impersonation recording `impersonated_by`; plan management via Stripe (F64.AC2–AC3). Depends on CS-061c audit log.

### CS-065 — Billing & plan tiers (umbrella)
- **Feature:** [F65](./FEATURES.md) · **Priority:** P1 · **Size:** L · **Status:** Blocked (on CS-062)
- **Developer:** Unassigned · **Started:** — · **Duration:** ~8d (est., sum of sub-stories)
- **Description:** Stripe integration + plan enforcement. Broken into integration + enforcement.

#### CS-065a — Stripe integration & signed webhooks
- **Feature:** [F65](./FEATURES.md) · **Priority:** P1 · **Size:** M · **Status:** Blocked
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Detailed specifications:** `Customer` + `Subscription` per tenant; three plan tiers; webhook handler with **signature verification** + idempotency/event-log, mapping events to `tenants.plan`/`status` (F65.AC1–AC2).

#### CS-065b — Plan enforcement & dunning
- **Feature:** [F65](./FEATURES.md) · **Priority:** P1 · **Size:** M · **Status:** Blocked
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Detailed specifications:** payment-failure → 7-day grace → suspend; **downgrade enforcement** when usage exceeds the new plan; proration, tax (Stripe Tax/VAT), dunning emails (F65.AC3–AC4).

### CS-066 — Usage tracking & quotas
- **Feature:** [F66](./FEATURES.md) · **Priority:** P1 · **Size:** M · **Status:** Blocked (on CS-060; CS-065 for billing)
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Detailed specifications:**
  - Per-tenant metered counters from real events (solver runs, forecasts, SMS, storage, API) rolled up per period (F66.AC1).
  - Quota enforcement with **429** + `Retry-After` + quota context (F66.AC2); DB-level noisy-neighbor guards (`statement_timeout`, `work_mem`, connection limits, fair-share solver queue) (F66.AC3).
- **Cross-refs:** PLAN §15.7; architecture §14.7.

### CS-067 — Tenant-scoped observability
- **Feature:** [F67](./FEATURES.md) · **Priority:** P1 · **Size:** M · **Status:** Blocked (on CS-060; extends F37)
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Detailed specifications:**
  - `tenant.id`/`tenant.slug` as OTel **resource attributes** (not metric labels); Grafana `tenant` variable; Loki scoped by `tenant.id`; per-tenant alerting (F67.AC1–AC2).
  - Span/log **PII-scrubbing** processor (no SSN/address/DOB, no raw SMS, no LLM payloads with PII) (F67.AC3).
- **Cross-refs:** PLAN §15.5; architecture §14.5.

### CS-068 — Multi-tenant isolation test suite (GATE)
- **Feature:** [F68](./FEATURES.md) · **Priority:** P0 (gate) · **Size:** M · **Status:** Blocked (on CS-060, CS-061, CS-070)
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Detailed specifications:**
  - Cross-tenant read/insert/update/delete per table denied under RLS; owner-connection denied (proves `FORCE`); `WITH CHECK` write rejection; 401/403/404 behaviour (F68.AC1–AC2).
  - **Pooled-connection context-bleed** test; JWT `alg=none`/tamper/expired-refresh; impersonation escalation; cache-key isolation; PII-scrubbing assertion (F68.AC3–AC4).
  - Every test `spec: F68.ac#`; wired as a CI gate before any multi-tenant deploy (F68.AC5).
- **Cross-refs:** PLAN §9 + §15.14; architecture §11 + §14.14. **No second tenant provisioned until green.**

### CS-069 — Data export / delete (GDPR)
- **Feature:** [F69](./FEATURES.md) · **Priority:** P2 · **Size:** M · **Status:** Blocked (on CS-060)
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Detailed specifications:**
  - Per-tenant export (signed, time-limited URL; completeness verified) (F69.AC1).
  - Tenant delete timeline: soft-delete (`churned`) → grace → hard delete → **backup purge**, reactivation window, audit-logged (F69.AC2); **employee-level** right-to-be-forgotten (F69.AC3); documented **per-tenant restore** path + encrypted backups (F69.AC4).
- **Cross-refs:** PLAN §15.12; architecture §14.12.

### CS-070 — Tenant routing
- **Feature:** [F70](./FEATURES.md) · **Priority:** P0 · **Size:** S · **Status:** Blocked (on CS-061)
- **Developer:** Unassigned · **Started:** — · **Duration:** ~1d (est.)
- **Detailed specifications:**
  - Resolver sets tenant context from subdomain (prod) / path-prefix (dev) before any data access; unknown/suspended → 404 (F70.AC1).
  - **Reserved-slug denylist** enforced (F70.AC2); wildcard TLS; **CORS** locked to tenant origins; **CSRF** + secure cookie flags (F70.AC3). Custom domains noted as a later feature.
- **Cross-refs:** PLAN §15.10; architecture §14.10.

---

## Defects — found by code test pass (2026-11-12)

Bugs and unhandled corner cases found by reading + exercising the committed `data/*.py` scripts against
the real workbooks (all four validators PASS today — these are latent defects the current green state
hides). Each is written **SDD-first**: the *Specification* is a failing test to add to the relevant
`validate_*.py` (or a new `test_*.py`) **before** any code change, so the fix is proven by a red→green
test, per `test/SKILL.md`. Reproductions below were run and confirmed; resolved items record their
closing regression evidence.

### BUG-001 — `build_roster_fields.py` crashes / mis-allocates when the adult÷minor partition drifts
- **Feature:** [F2](./FEATURES.md) (regression risk in CS-002/CS-003) · **Priority:** P1 · **Size:** S
- **Status:** ✅ Fixed (2026-11-12) — proportional, largest-remainder allocation now derives quotas from
  live cohort counts; it enforces the five-adult feasibility floor before any workbook mutation. Covered
  by `data/test_bug_regressions.py` (`F2.ac1`–`F2.ac3`).
- **Developer:** Unassigned · **Found:** 2026-11-12 · **Duration:** ~1d (est.)
- **Description:** The generator hard-codes the home-store quotas `adult_quota={5:12,8:12,3:8}` (sum 32)
  and `minor_quota={5:3,8:3,3:2}` (sum 8) and fills each store by **positional slicing**
  `pool[k:k+n]`. These constants silently assume the roster partitions into **exactly 32 adults + 8
  minors**. That split is a function of each employee's DOB and the frozen `AS_OF = 2026-11-12`. If any
  of those change — an employee is added/removed/edited, or `AS_OF` is ever advanced — the counts shift
  and the code fails **two different ways**, neither guarded:
  - **Overflow (≥33 adults):** the surplus employee is never placed into `by_store`, so it never
    receives `role`/`skill_level`/etc., and the write loop `ws.cell(..., e[name])` raises
    **`KeyError: 'role'`** — an uncaught crash, mid-write, after PII has already been split out.
  - **Underflow (≤31 adults or a store short):** a store silently gets fewer crew than its quota,
    dropping it below the solver-feasibility floor (≥3 key-holders / ≥2 skill-3 baristas / ≥4 FPC),
    which only surfaces later as a `validate_roster_fields.py` check-E FAIL with no pointer to the cause.
- **Reproduction (verified 2026-11-12):**
  - `age_on(dob, AS_OF)` gives **32 adults / 8 minors** at `AS_OF=2026-11-12` **and** `2027-06-01`, but
    **33 / 7** at `2028-01-01` (one employee crosses 18) — so the quotas are correct only for a window.
  - Simulating 33 adults through the exact quota/slice logic leaves `employee_id=32` unplaced and the
    write loop raises `KeyError: 'role'`.
- **Specification (write these tests first — they must fail on today's code):**
  - A test that feeds a roster of 33 adults (or sets `AS_OF=2028-01-01`) and asserts
    `build_roster_fields.main()` **either places every employee or raises a clear, typed error**
    (e.g. `SystemExit("roster partition 33/7 ≠ quota 32/8 — update quotas")`), never a bare `KeyError`.
  - A test asserting `sum(adult_quota.values()) + sum(minor_quota.values()) == len(employees)` and that
    every tracked `employee_id` ends up with all 15 `NEW_COLS` populated (no blank scheduling cells).
- **Proposed fix:** derive quotas from the actual adult/minor counts (proportional split with a
  documented remainder rule) instead of hard-coding; assert total coverage before writing; fail closed
  with a descriptive message if a store would drop below its feasibility floor.
- **Risk if unfixed:** the "regenerate the roster deterministically" guarantee (a core project
  principle) breaks the first time the staff list or the as-of date changes.

### BUG-002 — Calibration silently treats unknown product categories as **zero labor** (shared validator blind spot)
- **Feature:** [F5](./FEATURES.md) (CS-008) · **Priority:** P2 · **Size:** XS
- **Status:** ✅ Fixed (2026-11-12) under **CS-009** — `assert_category_coverage` fails closed in `calibrate_standards.py`; `validate_calibration.py` check **F** asserts coverage independently (D3); regression test `test_ac4_calibration_refuses_unknown_category`.
- **Developer:** Unassigned · **Found:** 2026-11-12 · **Duration:** ~0.5d (est.)
- **Description:** In `calibrate_standards.py → throughput_capacity`, per-line labor is
  `r.transaction_qty * sec.get(r.product_category, 0)` — any sales `product_category` **absent from
  `Throughput_Standards`** contributes **0** labor-seconds, understating modeled peak load and making
  the capacity-feasibility check pass too easily (it would under-scale `seconds_per_unit`).
  `validate_calibration.py` recomputes capacity with the **identical** `sec_by_cat.get(cat, 0)` fallback
  (line ~71), so the validator shares the blind spot and cannot catch it. A renamed category, a stray
  whitespace/casing difference, a NaN, or a genuinely new product line would all slip through silently.
- **Reproduction (verified 2026-11-12):** today `sales_categories − throughput_categories = {}` and 0
  NaN, so the defect is dormant — but both the generator and its validator use `.get(cat, 0)`, so the
  guard that *should* catch a mismatch is the very thing that swallows it.
- **Specification (write first):**
  - A test asserting **every** distinct non-null `product_category` in `coffee_shop_sales.xlsx` is a key
    in `Throughput_Standards` (set-equality or subset), failing loudly if sales contains an unmapped
    category — add it to `validate_calibration.py` as a new check **F**.
  - A unit test that injects a synthetic `"Smoothies"` sales row and asserts calibration **raises**
    (fail-closed) rather than scoring its labor as 0.
- **Proposed fix:** replace `.get(cat, 0)` with an explicit coverage assertion (raise/`SystemExit`
  listing the unmapped categories) in both the generator and the validator.
- **Risk if unfixed:** a future taxonomy change (CS-009) or POS feed (CS-049) silently corrupts the
  capacity anchor, and the validator reports PASS.

### BUG-003 — `calibrate_standards.py` is not idempotent and has no re-run guard
- **Feature:** [F5](./FEATURES.md) (CS-008) · **Priority:** P1 · **Size:** S
- **Status:** ✅ Fixed (2026-11-12) — `refuse_if_already_calibrated` aborts before any mutation when an
  applicable row is already calibrated. Covered by `data/test_bug_regressions.py` (`F5.ac1`–`F5.ac3`).
- **Developer:** Unassigned · **Found:** 2026-11-12 · **Duration:** ~1d (est.)
- **Description:** The script reads `seconds_per_unit` / overhead **from the same sheet it then
  overwrites**, and flips `basis`→`CALIBRATED` in place. A second run (without a prior
  `git restore data/coffee_shop_labor_standards.xlsx`) re-reads the **already-scaled** seconds and
  re-amortizes + re-scales them a second time, and appends a **duplicate** block of `CAL-####`
  `Calibration_Log` rows. There is no "already calibrated → abort/no-op" guard; the only safeguard is a
  human remembering the README note. For a pipeline whose whole premise is deterministic, reproducible
  regeneration, a generator that corrupts its own output on a second invocation is a real trap.
- **Reproduction (by construction):** `basis` is `CALIBRATED` in the committed workbook after CS-008;
  re-running the script consumes those calibrated values as if they were the raw baseline (double
  application) and grows `Calibration_Log` by another full set of rows each time.
- **Specification (write first):**
  - A test that runs calibration **twice** on a temp copy and asserts the resulting workbook bytes /
    the `Throughput_Standards` values / the `Calibration_Log` row count are **identical** to a single
    run (idempotency), OR that the second run exits non-zero with a clear "already calibrated" message.
- **Proposed fix (either):** (a) read the **raw baseline** from an immutable source (a separate
  `*_baseline` sheet or a committed constants block) rather than the mutable output; or (b) detect
  `basis == CALIBRATED` and refuse to re-apply unless `--force`, upserting (not appending) log rows by
  `(table,row_key)`.
- **Risk if unfixed:** an accidental double-run (CI, a teammate, a rebase) silently biases every
  downstream forecast/cost number with no error.

### BUG-004 — Weather multipliers compound multiplicatively when conditions co-occur
- **Feature:** [F5](./FEATURES.md)/[F9](./FEATURES.md) (CS-008 → CS-014) · **Priority:** P2 · **Size:** XS
- **Status:** ✅ Fixed (2026-11-13, `9151e2f`) — `weather_adjustment` applies a single **dominant** independently-estimated effect (never a compound product); covered by `test_weather_adjustment.py` (D5)
- **Developer:** Unassigned · **Found:** 2026-11-12 · **Duration:** ~0.5d (est.)
- **Description:** The multipliers are documented as **independently estimated** (each condition's ratio
  measured on its own against the normal-day baseline — overlap is a stated limitation). But the
  back-test `pred()` applies them by looping every matching condition and multiplying:
  `p *= mult` per hit. A day that is simultaneously RAIN **and** COLD gets `base × mult_rain × mult_cold`
  — a compounded adjustment the estimation never justified (the single-condition ratios already each
  absorb the co-occurring weather). This over/under-shoots on multi-condition days. It is **masked
  today** only because the gate keeps all four multipliers unapplied; it becomes a live forecasting
  error the moment any multiplier is applied (richer data in CS-014).
- **Specification (write first):**
  - A test asserting that for a row matching N conditions, the applied adjustment equals the project's
    **chosen** combination rule (e.g. the single dominant condition, or an explicitly additive/blended
    rule) — **not** the naïve product — pinning the intended semantics before CS-014 reuses `pred`.
- **Proposed fix:** pick and document a combination rule (dominant-condition, or capped blend) and
  implement it in one shared helper used by both the back-test and the runtime forecaster.
- **Risk if unfixed:** CS-014 inherits a silent multi-condition bias on exactly the extreme days that
  matter most for staffing.

### BUG-005 — ETL `null()` coerces every whole-number value to `int`, corrupting zero-padded identifier strings
- **Feature:** [F44](./FEATURES.md) (CS-044) · **Priority:** P1 · **Size:** XS
- **Status:** ◑ Mostly fixed (2026-11-13) · **Pattern:** D7 — The first fix (`9151e2f`, `null()`→`return value`) over-corrected and broke integer-column COPY (`89250.0` into an `integer` column); `706b33a` restored whole-number float→int coercion, which leaves strings untouched and passes the string-preservation regression test. **Residual follow-up:** a ZIP/SSN *stored numerically* in the workbook is already lossy before `null()` sees it — the complete fix is reading identifier columns as `dtype=str`. Low priority (current fixtures store none numerically).
- **Developer:** Unassigned · **Found:** 2026-11-12 · **Duration:** ~0.5d (est.)
- **Description:** `scripts/etl_excel_to_postgres.py → null()` returns
  `int(value) if isinstance(value, float) and value.is_integer() else value`. The coercion is applied by
  **value shape, not by target column type**, so any field that *looks* numeric but is semantically a
  string is silently converted to an integer before it reaches the typed Postgres column. The clearest
  victim is `employee_pii.zipcode`: all 40 rows are coerced to `int` (verified). A New-England ZIP such
  as `02134` becomes `2134` — the leading zero is lost on write to a text column, silently and with no
  error. The same hazard applies to any zero-padded or long numeric-looking identifier (SSN, phone,
  external POS codes).
- **Reproduction (verified 2026-11-12):** `read_snapshot(include_pii=True)` → every `zipcode` is
  `type int`; current fixtures are all NYC (`10001`…`11215`) so no leading zero is lost **today**, but
  the coercion itself is unconditional. Inject a `02134` ZIP → stored value becomes `2134`.
- **Specification (write first):**
  - A test asserting that identifier/text-destined fields (`zipcode`, `ssn`, any column typed `text` in
    migration `0001`) retain their **string** form through `read_snapshot`, including leading zeros — a
    `02134` fixture must round-trip as `"02134"`, never `2134`.
- **Proposed fix:** coerce by **declared target column type** (drive from the migration's column types, or
  an explicit per-field policy), not by `value.is_integer()`. Numeric columns int-ify; text columns keep
  the string. Never apply a blanket shape-based transform to values bound for text columns.
- **Risk if unfixed:** silent PII/address corruption the moment real employee data outside NYC is loaded,
  or a POS feed (CS-049) brings zero-padded product codes — with the loader reporting success.

### BUG-006 — ETL `columns()` infers the column set from `data[0]` only; keys unique to later rows are silently dropped
- **Feature:** [F44](./FEATURES.md) (CS-044) · **Priority:** P1 · **Size:** XS
- **Status:** ✅ Fixed (2026-11-13, `9151e2f`) · **Pattern:** D8 — `columns()` now returns the **union of keys across all rows** (`list(dict.fromkeys(...))`); covered by `test_columns_are_the_union_of_every_row`.
- **Developer:** Unassigned · **Found:** 2026-11-12 · **Duration:** ~0.5d (est.)
- **Description:** `columns(rows)` returns `list(rows[0])` — the keys of the **first row only**.
  `copy_stage` then stages/COPYs just those columns and `upsert` inserts only them, writing `row.get(c)`
  for each. Any key that appears on a **later** row but not on row 0 is dropped from the load entirely,
  with no error. The `read_snapshot` path adds keys per-row conditionally (`labor_rules` sets
  `value_text`/`notes` only when a value fails the time regex, lines ~164-169), so a row-0-without /
  row-N-with divergence is one data change away.
- **Reproduction (verified 2026-11-12):** `columns([{"a":1},{"a":2,"b":99}])` → `["a"]`; the `b=99` cell
  is silently excluded from COPY. Current workbooks are homogeneous (every sheet column present on every
  row, confirmed), so nothing is dropped **today**.
- **Specification (write first):**
  - A test that stages a two-row snapshot where only the second row carries an extra populated column and
    asserts that column's value **reaches the target** (or that the loader raises on a ragged snapshot) —
    never a silent drop.
- **Proposed fix:** compute the column set as the **union of keys across all rows** (or assert all rows
  share row 0's keys and fail loudly on a ragged snapshot). Do not infer a table's schema from a single
  sampled row.
- **Risk if unfixed:** a future workbook or transform that populates a column on only some rows loads a
  partial table and passes every count check (the dropped column isn't a key).

### BUG-007 — ETL `validate_snapshot` never validates `employee_pii` (key/null checks skip the most sensitive table)
- **Feature:** [F44](./FEATURES.md) (CS-044) · **Priority:** P2 · **Size:** XS
- **Status:** ✅ Fixed (2026-11-13, `9151e2f`) · **Pattern:** D9 — `validate_snapshot` now iterates `set(snapshot.tables) & set(KEYS)` (derived from the load, not a parallel `PUBLIC_TABLES` list), so `employee_pii` is validated; covered by `test_duplicate_private_employee_is_rejected_before_load`.
- **Developer:** Unassigned · **Found:** 2026-11-12 · **Duration:** ~0.5d (est.)
- **Description:** The pre-load key/null-uniqueness loop iterates `PUBLIC_TABLES & set(snapshot.tables)`,
  and `employee_pii` is **not in `PUBLIC_TABLES`** (verified `False`). So when `--include-pii` is set, the
  one table carrying SSNs and addresses is the one table whose natural key (`tenant_id, employee_id`) is
  **not** checked for duplicates or nulls before mutation. It still fails closed at the DB PK, but that
  contradicts F44.ac4's "validate before any database mutation" contract — and does so for PII.
- **Reproduction (verified 2026-11-12):** `"employee_pii" in PUBLIC_TABLES` → `False`; a duplicate or
  NULL PII `employee_id` would pass `validate_snapshot` and only surface as a DB error mid-load.
- **Specification (write first):**
  - A test injecting a duplicate (and a NULL) `employee_id` into an `include_pii=True` snapshot and
    asserting `validate_snapshot` **raises** — i.e. the validator's table set covers **every** table that
    gets loaded, PII included.
- **Proposed fix:** validate the full set of tables present in the snapshot (derive the validated set from
  `KEYS`/`LOAD_ORDER`, not a separate `PUBLIC_TABLES` constant that can drift), so no loaded table escapes
  the key/null check.
- **Risk if unfixed:** the validator's coverage silently diverges from what the loader writes; the
  "validate first" guarantee is false for new or restricted tables.

### BUG-008 — ETL derives `shifts`/`schedules` one-row-per-eval, aborting on the normal many-evals-per-shift case
- **Feature:** [F44](./FEATURES.md) (CS-044) · **Priority:** P2 · **Size:** XS
- **Status:** ✅ Fixed (2026-11-13, `9151e2f`) · **Pattern:** D1 — `derive_legacy_schedule_rows` builds `shifts` from a **set** of `(shift_id, schedule_id)`, so many evaluations per shift collapse to one row instead of aborting on a duplicate key.
- **Developer:** Unassigned · **Found:** 2026-11-12 · **Duration:** ~0.5d (est.)
- **Description:** `t["shifts"]` is built one row per `rule_eval_log` row carrying a `shift_id`
  (lines ~176-177). `shifts`'s natural key is `(tenant_id, shift_id)`, so two rule evaluations for the
  **same** shift — the normal case, since many rules are evaluated per shift — produce duplicate keys and
  `validate_snapshot` aborts the whole load. The derivation implicitly assumes ≤1 eval per shift.
- **Reproduction (verified 2026-11-12):** current `Rule_Eval_Log` has exactly **1** row with a `shift_id`
  (1 shift, 0 duplicates), so the path is effectively untested; a realistic multi-eval log would raise
  `duplicate natural key`.
- **Specification (write first):**
  - A test with two `rule_eval_log` rows sharing one `shift_id` asserting the snapshot yields **one**
    `shifts` row (deduped) and a valid `schedules`/`shifts` set — not a duplicate-key abort.
- **Proposed fix:** derive `shifts` (and `schedules`) from the **distinct** `shift_id`/`schedule_id`
  values across the eval log, mirroring how `schedules` is already built from a set.
- **Risk if unfixed:** the ETL cannot load any realistic rule-evaluation history; the derivation is a
  1-row stub.

### BUG-009 — ETL orphan-delete for the global `weather_codes` uses `scope = TRUE`, clobbering across tenants
- **Feature:** [F44](./FEATURES.md) (CS-044) · **Priority:** P3 · **Size:** XS
- **Status:** ✅ Fixed (2026-11-13, `9151e2f`) · **Pattern:** — `tables_to_prune` excludes `weather_codes` (and any global reference table) from the per-tenant orphan-delete, so a tenant load can never prune shared reference data.
- **Developer:** Unassigned · **Found:** 2026-11-12 · **Duration:** ~0.5d (est.)
- **Description:** The post-load orphan-delete (line ~265) removes target rows absent from the current
  load. For every tenant-scoped table the scope is `t.tenant_id = %s` (safe), but for the global
  `weather_codes` the scope is `TRUE` — it deletes **all** codes not in this load. Single-tenant today
  that is intended, but once a second tenant loads a different code subset (F60+ multi-tenancy), its load
  deletes codes the first tenant relies on. A shared reference table shouldn't be pruned by a
  single-tenant ETL run.
- **Reproduction (by construction):** two sequential loads with differing `Weather_Codes` sheets leave
  only the second load's codes; the first tenant's extra codes are gone.
- **Specification (write first):**
  - A test (or documented decision) pinning that a global reference load is **upsert-only** (no delete),
    or is explicitly owned by a single global-admin path — not pruned by a per-tenant ETL run.
- **Proposed fix:** make `weather_codes` upsert-only in the per-tenant ETL (skip the orphan-delete for
  global tables), or gate global-table pruning behind an explicit `--manage-global-refs` flag.
- **Risk if unfixed:** the first multi-tenant load silently removes another tenant's weather reference
  data.

### BUG-010 — Compliance pre-judge vocabulary check halts on superseded / foreign-jurisdiction rules
- **Feature:** [F10](./FEATURES.md) (CS-013) · **Priority:** P2 · **Size:** XS
- **Status:** 🐞 Open · **Type:** Defect (regression introduced by `6e39b61`, over-broad fail-closed) · **Pattern:** D1/D3
- **Developer:** Unassigned · **Found:** 2026-11-13 · **Duration:** ~0.5d (est.)
- **Description:** Commit `6e39b61` moved the unsupported-vocabulary guard in
  `compliance/engine.py → judge()` so it now runs over **every** rule in the snapshot before any
  applicability filter. The intent was correct (an unsupported rule must not be hidden by an inapplicable
  employer flag — the old check only scanned `applicable` rules, so a flag/age/school/season/trigger
  filter could swallow a rule the engine cannot handle). But `load_snapshot` loads the **entire**
  `labor_rules` table, and effective-dating deliberately retains superseded rows. The new check therefore
  raises `ComplianceConfigError: unsupported rule vocabulary` for rules that can **never** bind this shift:
  a superseded rule (`effective_to` in the past), a future-dated rule (`effective_from` ahead), or a rule
  for a jurisdiction the tenant does not operate in. One such row halts **all** compliance evaluation for
  the tenant.
- **Reproduction (verified 2026-11-13):** a snapshot with a supported current rule plus
  (a) a rule `effective_from=2019-01-01, effective_to=2020-01-01` with `metric="legacy_metric"`, or
  (b) a `CA-STATE` rule with unknown vocab while the tenant is NY — each raises
  `unsupported rule vocabulary ['OLD-2019' | 'CA-ONLY']` instead of evaluating the shift. **Latent today:**
  the live `labor_rules` table has 31 rows, 0 superseded, 0 future-dated, jurisdictions all NY/NYC/US-FED,
  so nothing triggers it yet — it fires the first time effective-dating produces a superseded row (a core
  CS-013 feature) or a multi-jurisdiction tenant lands (F60+).
- **Specification (write first):**
  - A test with a supported in-force rule **plus** a superseded (`effective_to` past) rule and a
    foreign-jurisdiction rule, both carrying unsupported vocabulary, asserting evaluation **succeeds** on
    the applicable rule and does **not** raise.
  - A test preserving `6e39b61`'s intent: an **in-force, in-jurisdiction** rule whose vocabulary is
    unsupported but which is filtered out by an employer flag must still **raise** (the hole the commit
    closed stays closed).
- **Proposed fix:** scope the pre-judge vocabulary check to rules that pass the **structural** filters
  (`_in_force` + `_jurisdiction_ok`) — rules that could bind for *some* employee this period — rather than
  the full snapshot or only the fully-`applicable` set. Keep it ahead of the employee-specific filters
  (flag / age / school / season / trigger) so a rule hidden by those still fails closed.
- **Risk if unfixed:** the first superseded rule or multi-jurisdiction tenant makes the compliance engine
  refuse every shift — a hard outage of the legality service — on data the design explicitly supports.

### BUG-011 — `_validate_proposal_times` ignores `week_shifts` / `breaks`; naive datetimes assume server-local tz
- **Feature:** [F10](./FEATURES.md) (CS-013) · **Priority:** P2 · **Size:** XS
- **Status:** 🐞 Open · **Type:** Defect (fail-open on a contract violation — dormant) · **Pattern:** D6
- **Developer:** Unassigned · **Found:** 2026-11-13 · **Duration:** ~0.5d (est.)
- **Description:** The guard `_validate_proposal_times` added in `6e39b61` rejects a timezone-naive or
  `end_at <= start_at` **main** shift, but it does not look at the secondary timestamp inputs — the
  `ShiftSpan`s in `proposal.week_shifts` and the `Break`s in `proposal.breaks`. Those datetimes flow into
  `_week_hours`, `_rest_gap`, `_day_hours`, `_span_hours`, `_midway_break`, and `_break_minutes_inside`,
  all of which call `.astimezone(zone)`. In Python, `.astimezone()` on a **naive** datetime silently
  interprets it as **server-local** time rather than raising. On any host whose local timezone differs
  from the tenant's, weekly-hours totals and meal-window placement are then computed against the wrong
  wall clock — the exact UTC-vs-local hazard the engine's own AC5 test pins for the main shift, left open
  for the inputs that feed the weekly and break rules.
- **Reproduction (verified 2026-11-13):** a proposal with a **naive** `ShiftSpan` in `week_shifts`
  evaluates a `hours_per_week` rule with **no raise**; a proposal with a **naive** `Break` evaluates a
  `break_minutes` meal rule with **no raise**. Both should fail closed on the contract violation.
  **Latent today:** current callers pass tz-aware datetimes.
- **Specification (write first):**
  - A test asserting `evaluate` **raises** `ComplianceConfigError` when any `week_shifts` `ShiftSpan`
    start/end is timezone-naive.
  - A test asserting the same for a timezone-naive `Break` start/end.
  - (Optional) a test that a `ShiftSpan`/`Break` with `end <= start` is rejected, matching the main-shift rule.
- **Proposed fix:** extend `_validate_proposal_times` to assert tz-awareness (and `end > start`) for every
  `week_shifts` and `breaks` datetime, reusing the same check applied to the main shift.
- **Risk if unfixed:** a scheduler on a UTC (or any non-tenant-tz) host silently miscomputes weekly hours
  and meal-break compliance for shifts whose secondary inputs arrive naive — wrong legality with no error.

### BUG-012 — PgBouncer healthcheck requires `bash`, which the pinned image does not contain
- **Feature:** [F43](./FEATURES.md) (CS-043) · **Priority:** P0 · **Size:** XS
- **Status:** 🐞 Open · **Type:** Defect (availability gate falsely fails) · **Pattern:** deployment healthcheck mismatch
- **Developer:** Unassigned · **Found:** 2026-11-13 · **Duration:** ~0.5d (est.)
- **Description:** The tracked `docker-compose.yml` declares PgBouncer healthy only when this command
  succeeds: `timeout 2 bash -c '</dev/tcp/127.0.0.1/6432'`. The pinned
  `edoburu/pgbouncer:v1.23.1-p2` image intentionally ships a minimal userspace and does **not** include
  `bash`. Docker therefore records every probe as `timeout: can't execute 'bash': No such file or
  directory`, leaving the service permanently `unhealthy` even though PgBouncer has started and clients
  can execute SQL through port 6432. This is not cosmetic: F43 AC1 promises that both services become
  healthy, and downstream Compose `depends_on: condition: service_healthy`, CI gates, and an operator's
  readiness decision must treat an unhealthy pooler as unavailable.
- **Reproduction (verified 2026-11-13):** `docker compose ps` reports PostgreSQL as healthy and PgBouncer
  as unhealthy. `docker inspect --format '{{range .State.Health.Log}}{{.Output}}{{end}}'
  coffeeshop-pgbouncer-1` returns the missing-`bash` error repeatedly, while a psycopg connection to
  `localhost:6432/coffeeshop` succeeds.
- **Specification (write these tests first):**
  - A Compose/lifecycle test starts the pinned services and asserts the `postgres` **and** `pgbouncer`
    containers reach `healthy` within the configured retry window; a merely-running PgBouncer container
    is not sufficient.
  - The PgBouncer health command must use an executable that exists in the pinned image and validate an
    actual listener or authenticated lightweight query, not only container process existence.
  - An adversarial test temporarily targets an unused listener/incorrect port and asserts PgBouncer becomes
    unhealthy; the replacement probe must not turn every configuration error into a false positive.
  - The test must report the container health log on timeout so the missing binary, authentication failure,
    DNS failure, and closed listener are distinguishable.
- **Proposed fix:** replace the shell-specific probe with a POSIX-available command supplied by the image
  (or an authenticated PgBouncer-native query via its installed client), pin the probe to `127.0.0.1:6432`,
  and prove it both passes for the real service and fails for a closed/wrong port. Do not install `bash`
  ad hoc into the runtime image merely to retain a brittle `/dev/tcp` probe.
- **Cross-references:** F43 AC1, `docker-compose.yml`, CS-043 lifecycle validation, PgBouncer image
  contract, and CI service-readiness checks.
- **Risk if unfixed:** every fresh environment advertises a failed data plane despite a listening pooler;
  automated deployments may block forever or operators may normalize/ignore an unhealthy signal and miss
  a real subsequent outage.

### BUG-013 — Compliance session setup uses non-local `SET ROLE`, leaking privileges across PgBouncer transaction-pool backends
- **Feature:** [F43](./FEATURES.md) + [F10](./FEATURES.md) (CS-043 / CS-013) · **Priority:** P0 · **Size:** XS
- **Status:** 🐞 Open · **Type:** Defect (pooled authorization state leaks between transactions) · **Pattern:** pooled-session state leak
- **Developer:** Unassigned · **Found:** 2026-11-13 · **Duration:** ~0.5d (est.)
- **Description:** `coffee-scheduler/compliance/engine.py → prepare_session()` executes `SET ROLE app_rw`,
  then sets the tenant GUC with `set_config('app.tenant_id', ..., true)`. PgBouncer is configured for
  **transaction pooling**, so a backend is returned to the pool after commit/rollback. The `true` GUC is
  transaction-local and clears correctly, but `SET ROLE` is session-scoped. With PgBouncer's default
  `server_reset_query_always = 0`, the next client assigned that backend inherits `current_user = app_rw`
  even if it never requested a role. Current RLS remains fail-closed when the tenant GUC is absent, so the
  observed path exposes zero rows rather than another tenant's rows; nevertheless, authorization state
  leaks and a future policy/change that relies on role separation can convert this into cross-client access.
  It also violates F43 AC4's explicit transaction-local role/session contract.
- **Reproduction (verified 2026-11-13):** connect through PgBouncer on `6432`, begin a transaction, call
  `prepare_session(..., tenant_id)`, commit, then obtain a fresh pooled connection without calling
  `prepare_session`. `SELECT current_user` can return `app_rw`, proving role state outlives the request.
  `SELECT current_setting('app.tenant_id', true)` is empty, which explains why the current RLS predicate
  still returns no tenant rows.
- **Specification (write these tests first):**
  - A two-client PgBouncer integration test runs `prepare_session` for client A, commits, then opens client
    B without preparation; client B must see its authenticated baseline role and no tenant context.
  - Within one explicit transaction, the compliance engine must set both `SET LOCAL ROLE app_rw` and
    `set_config('app.tenant_id', tenant_id, true)` before repository reads/writes; it must successfully
    read/write only that tenant's permitted rows.
  - After commit **and** after rollback, a subsequent pooled transaction must have neither `app_rw` role
    elevation nor a tenant GUC. Test both paths because failed compliance evaluations are expected.
  - An adversarial test calls the engine outside the transaction wrapper and asserts a descriptive failure
    rather than silently issuing session-scoped role changes.
  - The test must use PgBouncer port `6432`, not a direct PostgreSQL connection, so it detects the actual
    pooling behavior.
- **Proposed fix:** make `prepare_session` require an active transaction and replace `SET ROLE app_rw` with
  `SET LOCAL ROLE app_rw`; retain `set_config(..., true)` in that same transaction. Add a narrow database
  session helper so every compliance read/log write follows this ordering. Do **not** rely solely on
  `server_reset_query_always = 1`: reset configuration is defense in depth, while transaction-local SQL
  is the application-level correctness guarantee.
- **Cross-references:** F43 AC4, F10/CS-013 compliance engine, `tenant_isolation` RLS policy in migration
  `0001`, `docker-compose.yml` PgBouncer transaction mode, and CS-045 future repository layer.
- **Risk if unfixed:** a pooled backend can carry privileges from one client to another. The current tenant
  GUC limits impact, but the system's authorization model becomes fragile, non-compositional, and unsafe to
  extend.

### BUG-014 — Rules-admin verification gate keys off the merged `after` state, not the edit
- **Feature:** [F34](./FEATURES.md) (CS-034) · **Priority:** P1 · **Size:** S
- **Status:** 🐞 Open · **Type:** Defect (AC2/AC6 correctness — one bypass is a compliance hole) · **Pattern:** D2
- **Developer:** Unassigned · **Found:** 2026-11-14 · **Duration:** ~0.5d (est.)
- **Description:** `compliance/rules_admin.py → _verification_gate(before, after)` decides purely from the
  merged `after` row (`after = before + changes`) and never checks whether the **edit** actually touched
  `enforcement` or `verification_status`. Because the "design-decision rationale" is read as
  `rationale = bool(after['notes'])` and the "verified source" as merely non-empty
  `source_citation` **and** `source_url` (not actually `VERIFIED`), three wrong outcomes follow:
  1. **AC6 bypass (security/compliance):** a rule with **pre-existing** `notes` can be switched to a
     blocking `enforcement` (`HARD`/`PROCESS`) while still unverified **without any new justification** —
     the gate treats the inherited note as the rationale. 22 of 31 seed rules carry notes, so the bypass is
     broadly available. AC6 requires this to be blocked.
  2. **Over-block (usability):** editing an **unrelated** field (e.g. `priority`) on a rule that is already
     `HARD` + not-`VERIFIED` **and** has no notes is rejected with "design decision required…", so a typo
     fix is impossible without re-justifying.
  3. **AC2 over-fire (usability):** editing an unrelated field on an already-`VERIFY`, non-blocking rule is
     rejected with "rule not yet verified; verify in CS-021 first.", even though the edit changed neither
     status nor enforcement.
- **Reproduction (verified 2026-11-14, pure `_verification_gate`):**
  - `SOFT_COST→HARD` on an unverified rule with pre-existing notes → **ALLOWED** (should be BLOCKED).
  - edit `priority` only on a HARD+unverified no-notes rule → **BLOCKED** (should be ALLOWED).
  - edit `value_text` only on a VERIFY non-blocking rule → **BLOCKED** (should be ALLOWED).
- **Specification (write first):**
  - A test asserting the gate fires **only** when `enforcement` or `verification_status` is in `changes`
    and the edit introduces a blocking/unverified transition.
  - A test that the justification must come from **this** edit: a `HARD`-making edit with no new
    `source_citation`/`source_url`/`notes` in `changes` is **blocked** even if the row already had notes.
  - A test that an unrelated-field edit on a `HARD`+unverified or `VERIFY` rule is **allowed**.
- **Proposed fix:** gate on the **transition** — compute `blocking_introduced = after.enforcement ∈ BLOCKING
  and ('enforcement' in changes or 'verification_status' in changes)`; require the rationale/source to be
  present in `changes` (not inherited); and define "verified source" as an actually-`VERIFIED` citation,
  not two non-empty strings.
- **Risk if unfixed:** managers silently promote unverified rules to legally-binding `HARD` with no audit
  justification (defeats the assumption-vs-law guard F34 exists to enforce), while legitimate edits are
  blocked.

### BUG-015 — AC3 effective-date overlap is unreachable with unbounded coverage validity, and its "overlap = conflict" semantics are suspect
- **Feature:** [F34](./FEATURES.md) (CS-034) · **Priority:** P2 · **Size:** S
- **Status:** 🐞 Open · **Type:** Defect (dead code + semantic gap) · **Pattern:** D5
- **Developer:** Unassigned · **Found:** 2026-11-14 · **Duration:** ~0.5d (est.)
- **Description:** `reconcile_for_admin` only evaluates the `effective_date_overlap` branch when a coverage
  row's `validity` is **bounded** (`validity.lower is not None or validity.upper is not None`). But the ETL
  (post wiring) sets **every** coverage row's `validity` to an **unbounded** `[,)` range, so the branch is
  now **unreachable on real data** — AC3's date-conflict can never fire. Separately, the branch treats an
  **overlap** of coverage-vs-law dates as a *conflict*, which is likely inverted: a coverage rule being in
  effect at the same time as the legal rule it cites is normal; the real conflict is a coverage window with
  **no** backing in-effect legal rule.
- **Reproduction (verified 2026-11-14):** `reconcile_for_admin(proposed, [proposed], [coverage with
  unbounded validity])` returns `[]` — no overlap conflict considered.
- **Specification (write first):**
  - A test with a **bounded** coverage validity that does **not** cover the legal rule's effective window,
    asserting the intended conflict is raised; and a test that an unbounded (always-in-effect) coverage row
    cited by an in-effect legal rule is **clean**.
  - Pin the intended semantics explicitly (gap vs overlap) before anyone builds UI on it.
- **Proposed fix:** decide and document the rule: flag a coverage window **not fully covered** by an
  in-effect, `VERIFIED` legal rule (gap), rather than flagging mere overlap; make the check independent of
  whether validity happens to be bounded.
- **Risk if unfixed:** AC3 reports `clean` for genuinely mis-dated coverage/law pairs — a silent
  false-negative in exactly the guard F34 advertises.

### BUG-016 — Rules-admin only ever reads/edits the latest effective-dated version of a rule
- **Feature:** [F34](./FEATURES.md) (CS-034) · **Priority:** P2 · **Size:** S
- **Status:** 🐞 Open · **Type:** Defect (effective-dating limitation) · **Pattern:** D6
- **Developer:** Unassigned · **Found:** 2026-11-14 · **Duration:** ~0.5d (est.)
- **Description:** `_rule_row` selects `... WHERE rule_id=%s ORDER BY effective_from DESC LIMIT 1`, so
  `get_rule`/`patch_rule` always act on the **newest** version of a rule_id. `list_rules` returns **every**
  version (each with an `etag` keyed on `effective_from`), but a PATCH of any older version fetches the
  latest as `before`, so the caller's `If-Match` (computed from the older row) never matches → **409
  Conflict**. Older effective-dated versions are therefore unviewable in detail and uneditable. Latent
  today (0 rules have >1 version) but effective-dating is a first-class F10/F47 feature.
- **Reproduction (by construction):** two `labor_rules` rows sharing a `rule_id` with different
  `effective_from`; `get_rule`/`patch_rule` on the older one is impossible (always targets the latest;
  PATCH → 409).
- **Specification (write first):** a test with two versions of one rule_id asserting both are individually
  viewable and editable (address the version, not just rule_id).
- **Proposed fix:** key the admin API on `(rule_id, effective_from)` (or a surrogate version id), not
  `rule_id` alone; `_rule_row`/UPDATE/etag must carry the version.
- **Risk if unfixed:** when rules are effective-dated, the admin silently edits the wrong (latest) row or
  refuses with a confusing 409.

### BUG-017 — Rules-admin RLS context is silently lost on an autocommit connection; a null tenant timezone crashes waiver listing
- **Feature:** [F34](./FEATURES.md) (CS-034) · **Priority:** P3 · **Size:** XS
- **Status:** 🐞 Open · **Type:** Defect (unenforced transaction contract + unguarded edge) · **Pattern:** D2
- **Developer:** Unassigned · **Found:** 2026-11-14 · **Duration:** ~0.5d (est.)
- **Description:** Every entry point calls `prepare_session` which uses **transaction-local** `SET LOCAL
  ROLE app_rw` + `set_config(..., true)`. These only persist within one open transaction. On an
  **autocommit** connection each statement is its own transaction, so the role/tenant GUC evaporate before
  the subsequent query — RLS context is lost (wrong role, or fail-closed zero rows) with no error. Nothing
  enforces or documents that the caller must pass a single non-autocommit transaction (the tests happen to).
  Separately, `list_waivers` with `today=None, tz=None` does `ZoneInfo(<tenants.timezone>)`; a null/empty
  tenant timezone raises an unhandled exception instead of a typed error.
- **Specification (write first):** a test that `prepare_session` on an autocommit connection raises a typed
  error (or the service opens its own transaction); a test that a tenant with null timezone yields a typed
  `ValidationError`, not a raw `ZoneInfoNotFoundError`.
- **Proposed fix:** assert `not conn.autocommit` in `prepare_session` (or wrap work in an explicit
  transaction); validate the tenant timezone and raise a typed error on null/unknown.
- **Risk if unfixed:** an integrator wiring the HTTP adapter with an autocommit pool gets silent
  cross-tenant-safe-but-empty results or a role bypass; a mis-seeded tenant 500s on waiver views.
- **Note:** related to [[BUG-013]] (session-role handling under pooling).

### BUG-018 — Rules-admin `source_url` handling: unverified citations satisfy the gate, every detail view makes a blocking external fetch, DNS-rebinding TOCTOU
- **Feature:** [F34](./FEATURES.md) (CS-034) · **Priority:** P2 · **Size:** S
- **Status:** 🐞 Open · **Type:** Defect (weak gate + network-in-request-path + residual SSRF) · **Pattern:** D2
- **Developer:** Unassigned · **Found:** 2026-11-14 · **Duration:** ~0.5d (est.)
- **Description:** Three issues around `validate_source_url` / `source_url`:
  1. **Gate too weak (ties to BUG-014):** `_verification_gate` accepts any non-empty
     `source_citation` + `source_url` as a "verified source" without checking the URL resolved 200 or the
     rule is `VERIFIED`, so AC6 can be satisfied with arbitrary unverified strings.
  2. **Blocking fetch on read:** `get_rule` calls `validate_source_url` (a live HTTPS `HEAD`, 3 s timeout)
     on **every** rule detail view, putting an external, latency-bound, failure-prone network call on the
     read path — a self-inflicted DoS/latency vector and a per-view outbound request.
  3. **DNS-rebinding TOCTOU:** the guard resolves the host and screens the IPs, then `urllib` re-resolves
     by hostname for the actual request, so a rebinding host can pass the screen and still be fetched
     (internal reach). Impact is bounded today (advisory; only the HTTP status is used, no body returned),
     but it is a real residual SSRF.
- **Specification (write first):** a test that a non-`VERIFIED` citation does **not** satisfy the LAW gate;
  a test that `get_rule` performs **no** network call (validation is explicit/deferred, or cached); a test
  that a rebinding/resolve-then-connect path cannot reach a private IP (pin the resolved IP for the request).
- **Proposed fix:** make `source_url` validation explicit/opt-in (not on every read) and cache
  `source_url_valid` + `verified_on`; require an actually-`VERIFIED` citation for the LAW gate; connect to
  the **pinned** screened IP (Host header preserved) to close the rebinding window.
- **Risk if unfixed:** AC6 is satisfiable with junk citations; rule views depend on an external host's
  availability; a determined actor can still probe internal hosts.

---

## Backlog summary

| Story | Feature | Priority | Size | Status |
| --- | --- | --- | --- | --- |
| CS-001 | F1 | P0 | S | ✅ Done |
| CS-002 | F2 | P0 | M | ✅ Done |
| CS-003 | F24 | P1 | XS | ✅ Done |
| CS-004 | F3/F25 | P0 | S | ✅ Done (CS-007 merged) |
| CS-005 | F4 | P0 | XS+S | ✅ Done |
| CS-006 | F23 | P0 | S | ✅ Done |
| CS-007 | F25 | P0 | S | ✅ Done (merged into CS-004) |
| CS-008 | F5 | P1 | M | ✅ Done |
| CS-009 | F29 | P1 | XS | ✅ Done |
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
| CS-034 | F34 | P1 | M | 📋 Planned — **not built** (prior "✅ Done 2026-11-12" was inaccurate: no `rules_admin.py`/`test_rules_admin.py` exist, nothing verified; corrected 2026-11-13). Plan reviewed & reconciled to the live schema in docs/plans/CS-034-rules-admin.md. Blockers surfaced: AC3 needs new migration `0002` (coverage effective-dating); AC2/AC6 gate merged; SSRF + optimistic-concurrency + half-open waiver edge cases added; depends on CS-021/CS-022 (both Not started). |
| CS-035 | F35 | P2 | S | Not started |
| CS-036 | F36 | P2 | M | Not started |
| CS-037 | F37 | P2 | M | Not started |
| CS-038 | F38 | P2 | S | Not started |
| CS-039 | F39 | P2 | M | Not started |
| CS-040 | F40 | P2 | S | Not started |
| CS-041 | F41 | P2 | S | Not started |
| CS-042 | F42 | P0 | M | Not started |
| CS-043 | F43 | P0 | S | ✅ Done — compose+PgBouncer, reversible migration, backup/restore drill (F43 AC1–AC5 verified) |
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

### Phase 1 — SaaS multi-tenancy (found 2026-11-12)

| Story | Feature | Priority | Size | Status |
| --- | --- | --- | --- | --- |
| CS-060 | F60 | P0 | M | Blocked (umbrella) |
| CS-060a | F60 | P0 | M | Blocked |
| CS-060b | F60 | P0 | S | Blocked |
| CS-060c | F60 | P0 | S | Blocked |
| CS-061 | F61 | P0 | M | Blocked (umbrella) |
| CS-061a | F61 | P0 | M | Blocked |
| CS-061b | F61 | P0 | S | Blocked |
| CS-061c | F61 | P1 | M | Blocked |
| CS-062 | F62 | P0 | M | Blocked |
| CS-063 | F63 | P1 | M | Blocked |
| CS-064 | F64 | P1 | L | Blocked (umbrella) |
| CS-064a | F64 | P1 | M | Blocked |
| CS-064b | F64 | P1 | M | Blocked |
| CS-065 | F65 | P1 | L | Blocked (umbrella) |
| CS-065a | F65 | P1 | M | Blocked |
| CS-065b | F65 | P1 | M | Blocked |
| CS-066 | F66 | P1 | M | Blocked |
| CS-067 | F67 | P1 | M | Blocked |
| CS-068 | F68 | P0 | M | Blocked (gate) |
| CS-069 | F69 | P2 | M | Blocked |
| CS-070 | F70 | P0 | S | Blocked |

### Defects (found 2026-11-12)

| Story | Feature | Priority | Size | Status |
| --- | --- | --- | --- | --- |
| BUG-001 | F2 | P1 | S | 🐞 Open — roster quota crash on partition drift |
| BUG-002 | F5 | P2 | XS | ✅ Fixed (under CS-009) — fail-closed category coverage |
| BUG-003 | F5 | P1 | S | 🐞 Open — calibration not idempotent / no re-run guard |
| BUG-004 | F5/F9 | P2 | XS | ✅ Fixed (`9151e2f`) — dominant weather effect, not a compound product (D5) |
| BUG-005 | F44 | P1 | XS | ◑ Mostly fixed (`9151e2f`+`706b33a`) — float→int restored; numeric-stored ID dtype follow-up (D7) |
| BUG-006 | F44 | P1 | XS | ✅ Fixed (`9151e2f`) — `columns()` unions all rows' keys (D8) |
| BUG-007 | F44 | P2 | XS | ✅ Fixed (`9151e2f`) — `validate_snapshot` covers `employee_pii` via `KEYS` (D9) |
| BUG-008 | F44 | P2 | XS | ✅ Fixed (`9151e2f`) — `shifts` deduped from a set (D1) |
| BUG-009 | F44 | P3 | XS | ✅ Fixed (`9151e2f`) — `weather_codes` excluded from per-tenant prune |
| BUG-010 | F10 | P2 | XS | 🐞 Open — vocab check (`6e39b61`) halts on superseded/foreign rules (D1/D3) |
| BUG-011 | F10 | P2 | XS | 🐞 Open — `_validate_proposal_times` skips week_shifts/breaks; naive→server tz (D6) |
| BUG-012 | F43 | P0 | XS | 🐞 Open — PgBouncer healthcheck requires missing `bash`; AC1 fails |
| BUG-013 | F43/F10 | P0 | XS | 🐞 Open — non-local `SET ROLE` leaks privilege state across pooled backends |
| BUG-014 | F34 | P1 | S | 🐞 Open — verification gate reads `after` state not the edit: AC6 bypass via pre-existing notes + over-blocks unrelated edits (D2) |
| BUG-015 | F34 | P2 | S | 🐞 Open — AC3 effective-date overlap unreachable w/ unbounded validity + inverted semantics (D5) |
| BUG-016 | F34 | P2 | S | 🐞 Open — only the latest effective-dated rule version is viewable/editable (D6) |
| BUG-017 | F34 | P3 | XS | 🐞 Open — RLS context lost on autocommit conn; null tenant tz crashes waiver listing (D2) |
| BUG-018 | F34 | P2 | S | 🐞 Open — `source_url`: junk citations satisfy gate, blocking fetch per view, DNS-rebind TOCTOU (D2) |
