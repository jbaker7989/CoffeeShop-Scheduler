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
- **Status:** ✅ Done (2026-11-21) — `contracts/forecast.py` typed contract + fail-closed validators (F8.ac1–ac6), version-stamped JSON envelope/schema + golden example, and migration `0017` hardening the `forecasts` table (NOT NULL + interval CHECK + taxonomy/throughput FKs + soft-delete-aware live-key unique). 10/10 `test_forecast_contract.py` pass (incl. a DB-gated constraint test) and migration `0017` round-trips. Codex (dev) / Claude (plan, review, validate). Note: the `forecasts` table already existed from migration `0001`; CS-012 adds the typed interface + enforcement. Consumers: CS-016a (solver input) and CS-017 (category names via taxonomy version).
- **Developer:** Codex (dev) + Claude (plan/review/validate) · **Started:** 2026-11-21 · **Duration:** ~0.5d (actual)
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
- **Status:** ✅ Done (2026-11-21) — `agents/demand_forecast/feature_build.py` builds a versioned, RLS-scoped `forecast_features` table (+ `feature_sets` registry; migration `0019`) from canonical Postgres: one 0-filled row per open store-hour × taxonomy category over Jan–Jun 2023, with calendar (holiday/school/dow), weather (temp/precip/rain/snow/bucket), and lag/rolling features, plus honest `low_history` per-cell masks and `has_basket_id`/`has_evening_history`/`has_annual_seasonality` provenance flags (all false). All derivation is pure-Python (unit-testable offline); SQL only loads inputs and persists. 17/17 `test_cs014a_feature_build.py` pass (10 offline unit + 7 DB-gated, rollback-only) covering F9.ac1–ac7 (`docs/sdd-cs014a.md`); migration `0019` round-trips. Claude (plan/dev/test). Unblocks CS-014b (now its only remaining dep besides CS-012, both done).
- **Developer:** Claude (plan + dev + test) · **Started:** 2026-11-21 · **Duration:** ~0.5d (actual)
- **Description:** Assemble the model-ready dataset from sales × weather × calendar.
- **Detailed specifications:**
  - Join `Hourly_Sales_Weather` (CS-001) with the category taxonomy (CS-009) and the school/holiday calendars (CS-004/CS-007).
  - Engineer features: hour-of-day, day-of-week, holiday/school flag, weather vars, rolling demand, store id.
  - Encode the known gaps as explicit features/masks: no evening history, no basket ID, 2023-only.
  - Output a versioned feature table the trainer and backtester both read.

### CS-014b — Model selection & training
- **Feature:** [F9](./FEATURES.md) · **Priority:** P1 · **Size:** M
- **Status:** ✅ Done (2026-11-21) — CS-014b adds deterministic NumPy-only empirical-quantile and linear-quantile-regression training, validation pinball selection, and a versioned tenant-scoped model registry in migration `0020`. The selected model is bound to its feature version, emits contract-valid interval forecasts, and is covered by F9.ac8–ac13 unit and rollback-only database tests.
- **Developer:** Codex (dev) + Claude (plan/review/validate) · **Started:** 2026-11-21 · **Duration:** ~0.5d (actual)
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
- **Status:** ✅ Done — requirements pinned, `.env.example` added, `docs/data-platform.md` documents the roles decision, and `load_snapshot` proves canonical DB reads.
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
- **Status:** ✅ Done (2026-11-13) — `docs/schema.md` (ERD + PK/FK/index/partition/effective-dating decisions) and Alembic migrations `0001` (full schema, RLS, roles, partitions, exclusions) + `0002` (coverage effective-dating); round-trip-reversible, verified on the host.
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** Define the database contract before consumers depend on it.
- **Detailed specifications:**
  - Publish `docs/schema.md` and an ERD covering every F47 table, PK/FK, delete behaviour, index, partition and effective-dating decision.
  - Model prohibited effective-date overlap with `DATERANGE`/GiST exclusion constraints where applicable.
  - Generate and review Alembic migrations from the approved schema; cross-reference F10 and F26.

## P1 — Product surfaces, data access & ingestion

### CS-030 — Manager UI foundation
- **Feature:** [F30](./FEATURES.md) · **Priority:** P1 · **Size:** M
- **Status:** ✅ Done (2026-11-16) — FastAPI gateway (`api/`) reusing `data_access/session.py` for RLS-scoped reads + seeded demo login → HttpOnly JWT cookie; Next.js manager shell (`coffee_shop/`) with same-origin `/api/*` rewrite, auth-gated `(manager)` group, `/login`, `/dashboard` (lists the tenant's stores). 10/10 `test_api_foundation.py` pass (auth→RLS→data, fail-closed 401s, no tenant spoof); `next build` clean. Unblocks CS-061/CS-062/CS-068b/CS-070/CS-031–035. (Live browser walkthrough is the one remaining manual check; API path + build are automated.)
- **Developer:** Codex (dev) / Claude (tests) · **Started:** 2026-11-16 · **Duration:** ~1d
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
- **Status:** ✅ Done (2026-11-14) — `compliance/rules_admin.py` + `test/test_rules_admin.py` (18 tests pass via discovery); F34 AC1–AC8 implemented; migration `0002` (coverage effective-dating) in place; BUG-014–018 fixed. **Known caveat:** `test_rules_admin.py` lacks a `unittest.main()` block so it silently runs 0 tests when invoked as a script — tracked separately (file as BUG-019).
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** Provide guarded maintenance of labor rules and consent waivers.
- **Detailed specifications:**
  - Block submissions that violate F26 reconciliation, use unverified law, or conflict in effective dates.
  - Show waiver expiry warnings, including WV-0001, and route accepted edits through CS-024 with an audit record.
  - Prove the UI cannot silently save an invalid rule.

### CS-045 — Typed agent data-access layer
- **Feature:** [F45](./FEATURES.md) · **Priority:** P1 · **Size:** M
- **Status:** ✅ Done (2026-11-14) — Claude dev / Codex tests. `coffee-scheduler/data_access/` (psycopg3 typed repository: session.py, tables.py, repository.py) + migration `0003` (RLS-protected `langgraph_checkpoints`). Parameterised, composite-PK-aware, RLS-trusting, append-only-enforcing, pooling-safe session (`SET LOCAL ROLE` + transaction-local tenant GUC). 7 integration tests (F45.ac1–ac7) pass; fixtures intact. SDD: docs/sdd-cs045.md.
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
- **Status:** ✅ Done — shared SDK bootstrap, deterministic global-provider policy, and two-service Collector-verified demo; F36 AC1–AC17 verified (2026-11-16).
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
- **Feature:** [F38](./FEATURES.md) · **Priority:** P2 · **Size:** S · **Plan:** [docs/plans/CS-038-otel-collector.md](./docs/plans/CS-038-otel-collector.md)
- **Status:** ✅ Done — `otel/collector-config.yaml` + root `docker-compose.observability.yml`; pinned `otel/opentelemetry-collector-contrib:0.164.0`; 12/12 spec tests pass (static + Docker validate + isolated OTLP error-trace retention); F38 AC1–AC11 verified (2026-11-15).
- **Developer:** Claude · **Started:** 2026-11-15 · **Duration:** ~1d
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
- **Status:** ◑ Core landed via migration `0001` (shipped under CS-047); **unblocked** (the F42–F47 data plane is Done — CS-042/043/044/047). `0001` already provides the `tenants` table, `tenant_id` + `ENABLE`/`FORCE` RLS + `NULLIF` `tenant_isolation` policy (USING + WITH CHECK) on domain tables, the non-owner `app_rw`/`app_ro`/`secrets_rw` roles, the `maven-demo` seed, and the pooling-safe `SET LOCAL` session pattern. **Remaining = hardening**, tracked by BUG-020 (tenants-root RLS), BUG-021 (default-partition RLS), BUG-022 (fail-closed cast), BUG-023 (global-table carve-out), BUG-024 (online-migration gotchas), BUG-025 (session guards) + a reusable session-context middleware (ties F45/CS-045). F60 ACs tightened 2026-11-14.
- **Developer:** Unassigned · **Started:** — · **Duration:** ~5d (est., sum of sub-stories)
- **Description:** The foundation: `tenant_id` on every domain table, Postgres RLS as the isolation guarantee, and the session plumbing that sets tenant context per request. Broken into three sub-stories.

#### CS-060a — Schema, `tenant_id` & scale-safe migration
- **Feature:** [F60](./FEATURES.md) · **Priority:** P0 · **Size:** M · **Status:** ✅ Done via migration `0001` — schema built tenant-aware from inception (`tenant_id NOT NULL` + composite `(tenant_id, …)` indexes on every domain table; `maven-demo` seed; ETL takes `--tenant-slug`). Greenfield, so the scale-safe backfill / `CONCURRENTLY` / `NOT VALID`→`VALIDATE` procedure was not needed — it applies to any **future** online column add (BUG-024). Global reference-table carve-out refinement: BUG-023.
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Detailed specifications:**
  - Add `tenants` root table; add `tenant_id UUID NOT NULL REFERENCES tenants(id)` to every F47 domain table; recreate every index / global `UNIQUE` with a leading `tenant_id` (F60.AC1).
  - Migration is **scale-safe**: batched backfill, `CREATE INDEX CONCURRENTLY`, `NOT NULL` via `ADD CONSTRAINT … NOT VALID` → `VALIDATE` (PLAN §7, §15.6); snapshot before cutover.
  - Backfill all existing rows to the `maven-demo` tenant; ETL (F44) gains a `tenant_id` arg.
- **Cross-refs:** F47, F44.

#### CS-060b — RLS policies + non-owner `app_rw` role
- **Feature:** [F60](./FEATURES.md) · **Priority:** P0 · **Size:** S · **Status:** ✅ Done via migration `0001` — `ENABLE` + `FORCE` RLS + `tenant_isolation` (NULLIF form, USING + WITH CHECK) on domain tables; app connects as non-owner `app_rw` (no `BYPASSRLS`). RLS functionally verified (own tenant sees rows, other tenant 0, cross-tenant write rejected by WITH CHECK, no-context denies). **Hardening remaining:** BUG-020 (tenants root table unprotected), BUG-021 (default partitions lack FORCE).
- **Developer:** Unassigned · **Started:** — · **Duration:** ~1d (est.)
- **Detailed specifications:**
  - `ENABLE` **and** `FORCE ROW LEVEL SECURITY` on every domain table; `tenant_isolation` policy with both `USING` and `WITH CHECK` on `current_setting('app.tenant_id', true)::UUID` (F60.AC2).
  - App connects as a non-owner `app_rw` role (DML only, no `BYPASSRLS`); only the migration/ETL role bypasses (F60.AC3). This is the fix for the owner-bypass fail-open.
- **Cross-refs:** architecture §2.4 + §14.1; verified by CS-068.

#### CS-060c — Per-request tenant session context
- **Feature:** [F60](./FEATURES.md) · **Priority:** P0 · **Size:** S · **Status:** ◑ Partial — the per-request contract is implemented and RLS-verified: pooling-safe `SET LOCAL ROLE app_rw` + transaction-local `set_config('app.tenant_id', …, true)` in `compliance/rules_admin.py` (and used by the compliance engine), fail-closed to zero rows with no context. PgBouncer transaction mode is configured (F43). **Remaining:** a reusable session-context middleware (ties F45/CS-045) + the BUG-025 guards (assert `not autocommit`; use `SET LOCAL ROLE` everywhere, not the engine's non-local `SET ROLE` — BUG-013).
- **Developer:** Unassigned · **Started:** — · **Duration:** ~1d (est.)
- **Detailed specifications:**
  - PgBouncer transaction mode; the ORM middleware opens a transaction, `SET LOCAL app.tenant_id` from the JWT, and asserts it is set before the first statement (F60.AC4; `SET LOCAL` needs an explicit txn).
  - A query with no tenant context returns zero rows (fail-closed).
- **Cross-refs:** architecture §2.5 + §14.1.

### CS-061 — Auth + per-tenant RBAC (umbrella)
- **Feature:** [F61](./FEATURES.md) · **Priority:** P0 · **Size:** M
- **Status:** ✅ Done (2026-11-17) — CS-061a core auth, CS-061b hardening, and CS-061c invitations/audit are all ✅ Done. (Multi-tenant membership split and real invite email are deferred follow-ups.)
- **Developer:** Unassigned · **Started:** — · **Duration:** ~6d (est., sum of sub-stories)
- **Description:** Identity, roles, tokens, and the full auth-hardening surface. Blocks every UI route.

#### CS-061a — Core auth (users, roles, login, JWT)
- **Feature:** [F61](./FEATURES.md) · **Priority:** P0 · **Size:** M · **Status:** ✅ Done (2026-11-16) — Argon2id tenant identities, least-privileged/RLS-safe auth persistence, RS256/JWKS login, strict browser cookies, refresh rotation/reuse detection, a fail-closed gateway cutover, and manager UI one-shot refresh recovery. Independent release validation: 8 auth contracts + 3 cookie regressions + 13 gateway + 12 manager UI + 9 CS-030 + 14 live RLS checks; Next tests/lint/build pass. BUG-035–037 resolved.
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Detailed specifications:**
  - `tenant_users` (roles `owner`/`manager`/`staff`/`read_only`, per-tenant email uniqueness) + `saas_admins` (MFA); argon2id hashing (F61.AC1).
  - Login issues an **asymmetrically-signed** JWT (RS256/ES256; reject `alg=none`; validate `iss`/`aud`/`exp`) with tenant/user/role claims; short access token + rotating refresh with reuse detection (F61.AC2).
- **Cross-refs:** PLAN §3; architecture §3.

#### CS-061b — Auth hardening
- **Feature:** [F61](./FEATURES.md) · **Priority:** P0 · **Size:** S · **Status:** ✅ Done (2026-11-16) — per-request canonical principal/tenant revocation, durable reset and session invalidation, tenant-account plus client-IP throttles, AES-256-GCM TOTP/recovery state, narrow `auth_svc` functions, and a reversible 0011 migration. Independent validation: 108 in-scope checks, live PostgreSQL pooled-path proof, and adversarial probes all green. BUG-038–040 resolved.
- **Developer:** Unassigned · **Started:** — · **Duration:** ~1.5d (est.)
- **Detailed specifications:**
  - Per-request revocation: middleware re-checks tenant `status` + user `is_active`, backed by a revocation list, so suspension takes effect within one access-token TTL (F61.AC3).
  - Password reset (time-boxed single-use token), login rate-limiting / lockout, optional TOTP MFA for `owner`/`manager` (F61.AC4).
- **Cross-refs:** PLAN §15.2; architecture §14.2.

#### CS-061c — Invitations, membership & audit log
- **Feature:** [F61](./FEATURES.md) · **Priority:** P1 · **Size:** M · **Status:** ✅ Done (2026-11-17) — migration `0012` adds `tenant_invitations` + append-only `audit_log` (RLS ENABLE, `app_rw` INSERT+SELECT only — no UPDATE/DELETE — plus six `auth_*` SECURITY DEFINER functions granted to `auth_svc`, reversible downgrade); repository seam (ABC + in-memory/database/pooled) + service invite/accept/list/revoke with role-escalation RBAC, digest-only tokens, TTL, audit weaving, and a `LogInvitationNotifier` stub; `POST/GET/DELETE /invitations` + public rate-limited `POST /invitations/accept`; isolation gate extended (audit_log append-only assertion + both new tenant tables). Verified: `test_f61*` 59/59, `test_api_foundation` 9/9, `test_isolation_gate` 14/14, `alembic downgrade 0011`→`upgrade head` clean. Review filed BUG-041..044 (duplicate-invite 500, audit-outage login 500, per-process accept throttle, list ordering). Multi-tenant membership split (`users`/`tenant_memberships`) and real invite email deferred.
- **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Detailed specifications:**
  - `tenant_invitations` + invite→email→accept flow to add members (F61.AC5); consider separating identity (`users`) from `tenant_memberships` so one person joins many tenants.
  - **Append-only** `audit_log` (`app_rw` has INSERT only) covering auth events, admin actions, and impersonation (records `impersonated_by` + target).
- **Cross-refs:** PLAN §15.3–15.4; architecture §14.3–14.4.

### CS-062 — Tenant onboarding
- **Feature:** [F62](./FEATURES.md) · **Priority:** P0 · **Size:** M · **Status:** ✅ Done (2026-11-19) — migration `0016`: atomic signup (tenant+owner, reserved-slug denylist reused from CS-070, duplicate→409 mapping both `UniqueViolation` and `P0001`), token-stub email verification + `require_email_verified` gate, 14-day trial (`onboarding_signup`), nightly `onboarding_suspend_expired_trials`, idempotent jurisdiction-gated `onboarding_provision` (refuses an unverified jurisdiction, never NY-defaults), and the **trial-status fix** (`auth_access_state` widened to `active+trial`). Verified 9/9 CS-062 + existing suites green. **Independent review (Claude):** found + fixed BUG-058 (template seed was ETL-order-dependent → empty on a clean deploy) with `scripts/seed_rule_templates.py` + documented bootstrap order; excluded Codex's unrequested CI scope-creep (`.github/`, `ruff.toml`, `scripts/ci/`, a `pytest` requirement that violates the no-pytest rule).
- **Developer:** Codex (dev+tests) / Claude (review) · **Started:** 2026-11-19 · **Duration:** ~3d (actual)
- **Detailed specifications:**
  - Signup creates tenant + owner in one transaction; slug format + **reserved-slug denylist** validated; duplicate → 409 (F62.AC1).
  - Email verification gates solver use; 14-day trial + nightly suspension job (F62.AC2–AC3).
  - Delivered backend signup/verify/trial/provision flow in migration `0016`, API routes, auth_svc-only
    SECURITY DEFINER ports, an idempotent suspension script, and verified NY templates. The first-run wizard,
    real email, full F19 library, billing exemption, solver, and `tenant_secrets` remain deliberately deferred.
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
- **Feature:** [F68](./FEATURES.md) · **Priority:** P0 (gate) · **Size:** M · **Status:** ✅ **CS-068a (DB gate)** and **CS-068b (HTTP/auth gate)** done — `test/test_isolation_gate.py` remains the DB regression gate and `test/test_cs068b_http_isolation.py` self-seeds two rollback-only tenants to prove the HTTP/auth boundary (401/403/404 discipline, JWT claim attacks, route binding, suspension, rotation/replay, cache/PII scrub, and the F68 spec-tag meta-test). The impersonation flow and `/billing` suspension exemption remain explicitly deferred. **The gate found + fixed two real gaps (Claude review confirmed):** (1) unknown-tenant login passed the slug *string* as a UUID throttle key → DB error/leak; now a deterministic `uuid5` throttle bucket → generic 401 while still throttling; (2) added `no-store`/`Vary: Host` response middleware + a tenant-scoped `GET /invitations/{id}` that 404s a foreign id (no existence disclosure).
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

### CS-087 — Soft-delete data lifecycle
- **Feature:** [F69](./FEATURES.md) · **Priority:** P1 · **Size:** M · **Status:** ✅ Done
- **Detailed specifications:** Tier-A editable records have marker columns, restrictive live-row policies, and
  partial indexes; the repository helper and CI guard prohibit normal hard deletes. Tier-B/C classifications and
  the audited F69 erasure carve-out are documented in `docs/plans/CS-087-soft-delete-lifecycle.md`.

### Migrations 0014/0015 — audit_log hardening (reconstructed 2026-11-19)
- **Feature:** [F61](./FEATURES.md) (CS-061c follow-up) · **Priority:** P1 · **Size:** XS · **Status:** ✅ Done
- The shared data plane was at Alembic `0015` while the repo chain ended at `0013` — another session had
  applied two audit-hardening migrations whose files were never committed. Reverse-engineered from the live DB
  and reconstructed as reversible migrations so repo and DB agree: **`0014`** revokes `app_rw`'s direct
  `INSERT, SELECT` on `audit_log` (and the `audit_log_id_seq` grant) so audit rows are written **only** through
  the `auth_svc` `auth_record_audit` function; **`0015`** adds the `audit_log_append_only` `BEFORE UPDATE/DELETE`
  trigger (`audit_reject_mutation()` → `RAISE 'audit_log is append-only'`) so the table is immutable regardless
  of grants. `test_isolation_gate` updated (audit_log is now function-write-only like the other auth tables);
  round-trip `0013↔0015` verified; all suites green.

### CS-070 — Tenant routing
- **Feature:** [F70](./FEATURES.md) · **Priority:** P0 · **Size:** S · **Status:** ✅ Done (2026-11-19) — strict edge resolver (`api/routing.py`), `resolve_active_slug` (active+trial routable; suspended/churned/unknown → uniform 404), reserved-slug denylist, trusted-proxy client-IP boundary (fixes BUG-045), principal↔route 403 binding before RLS, origin-CSRF for cookie-authed unsafe methods, and safe 308 canonical redirects. 16 CS-070 tests + BUG-045 regression green; f61 65, isolation_gate 14, api_foundation 9, next build+lint clean. **Independent review (Claude):** applied an opt-in gate (`TENANT_ROUTING_ENABLED`) so the default single-tenant demo keeps working (BUG-056); filed BUG-056/BUG-057. Wildcard TLS/CORS-engine/cache-partitioning/CS-068b/CS-062 deferred per plan.
- **Developer:** Codex (dev+tests) / Claude (review) · **Started:** 2026-11-19 · **Duration:** ~1d
- **Detailed specifications:**
  - `api/routing.py` resolves an immutable route context from strict production subdomains or development `/t/<slug>/`, before handler/RLS work; unknown/suspended/churned/lookup failure share one cache-safe 404 (F70.AC1).
  - Shared **reserved-slug denylist**, trusted-proxy forwarding/client-IP boundary, principal↔route binding, exact-origin cookie CSRF check, and safe canonical 308 aliases are covered by `test_cs070_routing.py` / `test_cs070_client_ip.py` (F70.AC2/AC3).
  - Wildcard TLS issuance, HTTP→HTTPS edge redirect, certificate monitoring, full CORS allow-list/cache partitioning, CS-068b attack matrix, and CS-062 signup validation remain deliberately deferred per the locked CS-070 plan.
- **Cross-refs:** PLAN §15.10; architecture §14.10.

---

## Phase 2 — Employee experience & workforce actuals (CS-071 … CS-086)

Closes the employee side of the loop (F71–F77) and the actuals/payroll/MLOps loops (F78–F80). All of these
depend on the Postgres data plane (F42–F47) and multi-tenancy (F60–F70) being in place; several are hard
prerequisites for others (F73 actuals feed F78 payroll and F80 MLOps; F72 availability feeds F11 and F76).

### CS-071 — Employee app shell + `employee` role & self-scoped RLS
- **Feature:** [F71](./FEATURES.md) · **Priority:** P1 · **Size:** M
- **Status:** Not started · **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** Stand up the employee-facing surface and its authorization boundary.
- **Detailed specifications:**
  - Add an `employee` RBAC role (F61) distinct from `manager`; the app authenticates and resolves the signed-in `employee_id`.
  - RLS/service scope: an employee reads only their own rows (shifts, punches, availability, requests, credentials) plus open shifts at eligible locations; cross-employee/cross-tenant access returns 404.
  - Mobile-first shell with the manager-UI integration pattern (F30) reused; no write path to `labor_rules`, schedules, or other employees.
  - Depends on F61 (auth), F60 (RLS). Prove isolation with a test that one employee cannot read another's data.

### CS-072 — My-schedule view (published-only, offline-cached)
- **Feature:** [F71](./FEATURES.md) · **Priority:** P1 · **Size:** S
- **Status:** Not started · **Developer:** Unassigned · **Started:** — · **Duration:** ~2d (est.)
- **Description:** The employee sees their published schedule.
- **Detailed specifications:**
  - Render only **published** schedule versions (F74); drafts are never visible to staff.
  - Last-fetched schedule is cached and shown with a "last updated" stamp when offline.
  - Times rendered in the tenant/store timezone; a shift crossing midnight is shown on the correct business day.

### CS-073 — Availability model & intake
- **Feature:** [F72](./FEATURES.md) · **Priority:** P1 · **Size:** M
- **Status:** Not started · **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** Replace the static `availability_persona` with real, effective-dated availability.
- **Detailed specifications:**
  - New `employee_availability` table: recurring weekly pattern + dated exceptions as time windows, `DATERANGE` effective-dating, RLS by employee; GiST exclusion rejects overlapping/contradictory rows at write.
  - The solver (F11) reads the window **in force for the shift date** (tenant-local, as-of discipline per F10), as a hard unavailable constraint; scheduling outside it requires a logged manager override.
  - Minor availability interacts with F10 school-day limits — the stricter of availability and legality wins.

### CS-074 — Time-off / PTO request & approval workflow
- **Feature:** [F72](./FEATURES.md) · **Priority:** P1 · **Size:** M
- **Status:** Not started · **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** Employees request leave; managers approve; the solver honors it.
- **Detailed specifications:**
  - New `time_off_requests` with a requested → approved/denied state machine and an auditable approval trail (who/when).
  - Approval before generation removes the employee from that day (hard constraint); a request touching an already-published shift surfaces the conflict and routes through F74 re-publish rather than silently dropping the shift.
  - Overlap with an existing approved request is rejected, not double-counted.

### CS-075 — Time & attendance capture (punches, actual-vs-scheduled)
- **Feature:** [F73](./FEATURES.md) · **Priority:** P1 · **Size:** M
- **Status:** Not started · **Developer:** Unassigned · **Started:** — · **Duration:** ~4d (est.)
- **Description:** Capture what actually happened — the missing actuals source of truth.
- **Detailed specifications:**
  - New `time_entries` (clock in/out + break punches) tied to `shift_id`, RLS by tenant/employee; duplicate punches idempotent.
  - Actual hours computed **half-open and DST-correct** reusing the F10 time helpers (never naive wall-clock); a midnight-crossing shift attributes hours to the correct business day; unscheduled punches are captured and flagged, not dropped.
  - Actual-vs-scheduled variance queryable per employee/day/store; a missed/edited punch needs an audited manager correction that never overwrites the raw punch.

### CS-076 — No-show detection → F14 cover trigger
- **Feature:** [F73](./FEATURES.md) · **Priority:** P1 · **Size:** S
- **Status:** Not started · **Developer:** Unassigned · **Started:** — · **Duration:** ~2d (est.)
- **Description:** Turn a missing clock-in into an automatic cover search.
- **Detailed specifications:**
  - A scheduled shift with no clock-in by a configurable grace window past start is flagged **no-show**.
  - Emits the F14 cover-workflow trigger **exactly once** (idempotent) per shift; a late clock-in after the flag resolves it with an audit note.
  - The no-show + resolution is logged for audit and feeds reliability metrics.

### CS-077 — Schedule draft edit + live F10/F26 re-validation
- **Feature:** [F74](./FEATURES.md) · **Priority:** P1 · **Size:** M
- **Status:** Not started · **Developer:** Unassigned · **Started:** — · **Duration:** ~4d (est.)
- **Description:** Let managers hand-edit the solver output without breaking legality.
- **Detailed specifications:**
  - A manual assignment edit is re-validated live against F10 legality and F26 coverage (reuse the F34/F10 evaluation path); an invalid edit is refused with the specific rule/constraint cited inline and the schedule is not saved invalid.
  - Edits operate on a **draft** version; the LLM may explain (F13) but never edits.
  - Optimistic concurrency: two managers editing the same week → the stale save is blocked (version/etag).

### CS-078 — Schedule publish, versioning & notify
- **Feature:** [F74](./FEATURES.md) · **Priority:** P1 · **Size:** M
- **Status:** Not started · **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** Ship the approved schedule to staff and lock it.
- **Detailed specifications:**
  - Publish transitions `draft → published`, writes an immutable version, and notifies affected employees **once** (idempotent) via F15/F71.
  - Publishing a week with an unresolved **hard** violation is refused (not warned); post-publish edits create a new version with a diff.
  - Only the published version is visible to employees (CS-072); every publish/edit is audited.

### CS-079 — Open-shift post + atomic claim with legality gate
- **Feature:** [F75](./FEATURES.md) · **Priority:** P2 · **Size:** M
- **Status:** Not started · **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** Post unfilled shifts; eligible staff claim them, legally.
- **Detailed specifications:**
  - Managers/solver post **open shifts**; a claim is allowed only if the employee passes F10 legality + coverage for that specific shift, else refused with the citing rule.
  - Claims are **atomic**: two employees claiming the same open shift → exactly one wins; the loser sees "already taken" (no double-book).
  - First-come confirms immediately; manager-approval mode holds pending; both audit + notify, and route through F74 as a published change. A claim that triggers overtime/clopening surfaces the premium (F10) before confirm.

### CS-080 — Employee-initiated shift swap
- **Feature:** [F75](./FEATURES.md) · **Priority:** P2 · **Size:** S
- **Status:** Not started · **Developer:** Unassigned · **Started:** — · **Duration:** ~2d (est.)
- **Description:** Staff swap shifts within the guardrails.
- **Detailed specifications:**
  - An employee offers a shift; an eligible peer claims it; the swap is confirmed only if F10 legality holds for **both** parties — legal for one but not the other is refused for both.
  - Confirmed swaps route through F74 (versioned) and notify; the whole exchange is audited (extends F14).

### CS-081 — Fairness objective terms + per-tenant weights
- **Feature:** [F76](./FEATURES.md) · **Priority:** P2 · **Size:** M
- **Status:** Not started · **Developer:** Unassigned · **Started:** — · **Duration:** ~4d (est.)
- **Description:** Make the solver fair, not just cheap and legal.
- **Detailed specifications:**
  - Add **soft** objective terms to F11: undesirable-shift equity (opens/closes/weekends), clopening-load balance, preference satisfaction (F72), hours-equity vs contracted min/max, optional seniority — never overriding a hard F10/coverage constraint.
  - Weights are per-tenant config (F63), deterministic (same inputs+weights → same schedule), with documented defaults and a documented cap so fairness cannot explode labor cost.
  - The explanation layer (F13) can state the fairness trade-off for an assignment; equity convergence over a window is reported and tested at a future date.

### CS-082 — Certification & work-permit expiry tracking (fail-closed)
- **Feature:** [F77](./FEATURES.md) · **Priority:** P2 · **Size:** S
- **Status:** Not started · **Developer:** Unassigned · **Started:** — · **Duration:** ~2d (est.)
- **Description:** Catch expiring FPC/minor permits before they become violations.
- **Detailed specifications:**
  - New `employee_credentials` (type, `DATERANGE` validity, doc reference), RLS by tenant/employee.
  - On the shift date, an **expired** required credential (half-open, tenant-local, mirroring CS-013b) removes the employee from the constraint that needs it (FPC coverage, minor eligibility) — fail-closed; a missing credential is treated as absent, never assumed valid.
  - Warn at configurable thresholds (30/7 days) reusing the waiver-expiry warning tiers; boundary tested at a future date.

### CS-083 — Payroll / HCM export contract + reconciliation
- **Feature:** [F78](./FEATURES.md) · **Priority:** P2 · **Size:** M
- **Status:** Not started · **Developer:** Unassigned · **Started:** — · **Duration:** ~4d (est.)
- **Description:** Export reconciled, auditable pay with real hours behind the premiums.
- **Detailed specifications:**
  - Combine actual hours (F73), scheduled hours, and F10 premiums/OT/spread/call-in per employee/day into a per-(tenant, period) idempotent export via F16/runtime agent; settles the F7 Payroll/HCM-agent question.
  - Reconcile: Σ exported hours == Σ approved `time_entries`; mismatch fails the export (fail-closed). Each premium line cites its `Labor_Rules` rule_id.
  - An unapproved punch or open period cannot be exported; a corrected punch after export forces a versioned re-export with a diff; mid-period rule changes (F28) attributed to the correct date range.

### CS-084 — Employee PII encryption, read-audit & retention
- **Feature:** [F79](./FEATURES.md) · **Priority:** P2 · **Size:** M
- **Status:** Not started · **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** Protect employee SSN/address at rest with auditing and retention.
- **Detailed specifications:**
  - Encrypt SSN/address at rest (column/envelope, decrypted only where needed — mirror `tenant_secrets`); `SELECT *` by `app_rw`/`app_ro` never returns plaintext and a keyless dump yields ciphertext (verify with the F43 backup round-trip).
  - Every PII-field read is audited (who/when/field/employee); masked display is default; unmasking is an explicit audited action.
  - Configurable retention purges/anonymizes on schedule and on an F69 delete; the purge is verifiable. **Raise to P1 before any real employee PII is loaded.**

### CS-085 — Forecast accuracy & drift monitoring
- **Feature:** [F80](./FEATURES.md) · **Priority:** P2 · **Size:** M
- **Status:** Not started · **Developer:** Unassigned · **Started:** — · **Duration:** ~3d (est.)
- **Description:** Track predicted-vs-actual continuously; catch drift early.
- **Detailed specifications:**
  - Compute MAPE per category-hour against **actuals** (F73) + real sales over time; a breach of the F5 acceptance band raises an alert (F40) with the OTel metrics from F37.
  - Detect input/prediction drift and surface it before it degrades schedules.
  - Every served forecast records its model-registry version (ties to CS-086).

### CS-086 — Gated retraining, model registry & rollback
- **Feature:** [F80](./FEATURES.md) · **Priority:** P2 · **Size:** M
- **Status:** Not started · **Developer:** Unassigned · **Started:** — · **Duration:** ~4d (est.)
- **Description:** Improve the model safely, never silently.
- **Detailed specifications:**
  - Reproducible scheduled/triggered retraining; a candidate is **promoted only if it beats the incumbent** on the held-out F5 band, else the incumbent stays and the attempt is logged.
  - Per-tenant model registry (versioned artifacts, promotion/rollback in one audited action); a tenant with thin data falls back to the shared/seed model, flagged.
  - A retrain on a thin/biased recent window cannot silently replace a good model (the gate blocks it).

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
- **Status:** ✅ Fixed (2026-11-14) — declared `Staff_PII` text-column read policy preserves textual identifier values through `read_snapshot`; regression coverage also retains integer-column coercion. Excel cells stored numerically remain intrinsically unable to retain a discarded leading zero.
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
- **Status:** ✅ Fixed (2026-11-14) — vocabulary validation is scoped to structurally in-force, in-jurisdiction rules while remaining fail-closed for applicable current rules.
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
- **Status:** ✅ Fixed (2026-11-14) — every shift, week-shift and break interval is required to be timezone-aware and positive.
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
- **Status:** ✅ Fixed (2026-11-14) — the `pg_isready` healthcheck is image-native; isolated Compose regressions verify healthy services and a closed-port failure.
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
- **Status:** ✅ Fixed (2026-11-14) — session setup requires a transaction and uses transaction-local role and tenant state; PgBouncer commit/rollback regressions pass.
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
- **Status:** ✅ Fixed (2026-11-14) — verification gates evaluate the proposed transition and require newly supplied rationale/evidence where required.
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
- **Status:** ✅ Fixed (2026-11-14) — reconciliation detects coverage date gaps and handles unbounded validity as always-in-effect.
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
- **Status:** ✅ Fixed (2026-11-14) — rules-admin reads and updates address the effective-dated version, not merely the latest `rule_id` row.
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
- **Status:** ✅ Fixed (2026-11-14) — autocommit session setup is rejected and invalid/missing tenant timezones produce typed validation failures.
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
- **Status:** ✅ Fixed (2026-11-14) — verified-status evidence is required for the gate and rules-admin detail reads make no DNS or HTTP requests for manager URLs.
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

### BUG-019 — `test_rules_admin.py` has no `unittest.main()` → runs 0 tests when invoked as a script
- **Feature:** [F34](./FEATURES.md) (CS-034) · **Priority:** P2 · **Size:** XS
- **Status:** 🐞 Open · **Type:** Defect (tests silently do not run) · **Pattern:** —
- **Developer:** Unassigned · **Found:** 2026-11-14 · **Duration:** ~0.1d (est.)
- **Description:** `coffee-scheduler/test/test_rules_admin.py` defines 18 test methods but has **no**
  `if __name__ == "__main__": unittest.main()` block (every other test file in the repo has one). Its own
  docstring says to run it with `python3 coffee-scheduler/test/test_rules_admin.py`, but that invocation
  defines the classes and then exits 0 with **no output and zero tests run** — so the BUG-014..018
  regression tests silently guard nothing in the repo's normal (script) workflow. They only execute under
  `python3 -m unittest` discovery.
- **Reproduction (verified 2026-11-14):** `python3 coffee-scheduler/test/test_rules_admin.py` → no output,
  exit 0; `python3 -m unittest -v …test_rules_admin` → runs 18 tests, OK.
- **Specification (write first):** none needed — the fix *is* the runnability. Add a CI/convention check
  (or a tiny meta-test) that every `test_*.py` ends with a `unittest.main()` entrypoint.
- **Proposed fix:** append `if __name__ == "__main__":\n    unittest.main()` to `test_rules_admin.py`.
- **Risk if unfixed:** the F34 regressions appear green but never run as invoked; a future regression lands
  undetected.

### BUG-020 — F60: the `tenants` root table has no RLS → cross-tenant enumeration
- **Feature:** [F60](./FEATURES.md) (CS-060) · **Priority:** P1 · **Size:** S · **Pattern:** D2
- **Status:** ✅ Fixed (2026-11-14, migration `0004`) — `tenants` gets `ENABLE` RLS (not `FORCE`) + `tenant_self_isolation` policy (`id = NULLIF(current_setting('app.tenant_id', true),'')::uuid`). app roles see only their own tenant; owner/ETL still see all (ENABLE-not-FORCE is deliberate — `FORCE` would break the slug-resolving loader). `test_tenants_rls.py` 4 tests pass. Claude dev / Codex tests.
- **Developer:** Unassigned · **Found:** 2026-11-14 · **Duration:** ~0.5d (est.)
- **Description:** F60/CS-060 scopes RLS to *domain* tables but leaves the **root `tenants` table**
  unprotected: `tenants` has `relrowsecurity=false, relforcerowsecurity=false` and `app_rw` holds `SELECT`
  on it. So any tenant's application session can read **every** tenant row — `slug`, `name`, `timezone`,
  `jurisdiction` — a direct cross-tenant information leak the isolation guarantee is supposed to prevent.
- **Reproduction (verified 2026-11-14):** as `app_rw` with `app.tenant_id = maven-demo`,
  `SELECT slug FROM tenants` returned **two** tenants (`maven-demo` and an injected `rival`). Expected: one.
- **Specification (write first):** a test that an `app_rw` session scoped to tenant A sees exactly **one**
  `tenants` row (its own) and cannot read tenant B's metadata.
- **Proposed fix:** enable+force RLS on `tenants` with a self-scoping policy
  (`id = NULLIF(current_setting('app.tenant_id', true), '')::uuid`), **or** revoke `app_rw` SELECT on
  `tenants` and expose only the caller's tenant through a scoped view/function. Update F60.AC1/AC2 to name
  the root table explicitly.
- **Risk if unfixed:** tenants enumerate each other (competitor discovery, jurisdiction/config leakage) the
  moment a second tenant exists.

### BUG-021 — F60: default partitions of partitioned tables lack RLS/FORCE
- **Feature:** [F60](./FEATURES.md) (CS-060) · **Priority:** P2 · **Size:** S · **Pattern:** D2/D9
- **Status:** ✅ Fixed (migration `0007` + `db/partitions.py::ensure_partition_rls`; verified by `test_bug021_partition_rls.py` + the isolation gate — every partition child is `t|t`) · **Type:** Defect (isolation gap — latent, mitigated only by grant absence)
- **Developer:** Unassigned · **Found:** 2026-11-14 · **Duration:** ~0.5d (est.)
- **Description:** `transactions_default`, `weather_hourly_default`, and `rule_eval_log_default` (the catch-all
  partitions created by migration `0001`) have `relrowsecurity=false, relforcerowsecurity=false`, while
  F60.AC2 requires FORCE RLS on **every** domain table. A query through the partitioned *parent* is isolated
  (the parent has RLS+FORCE), but a **direct** read of a default partition is not. Today this is not
  exploitable — `app_rw` has **no grant** on the default partitions (direct read → `InsufficientPrivilege`)
  — so isolation currently rests on *grant absence*, not RLS, which is fragile defense-in-depth.
- **Reproduction (verified 2026-11-14):** `pg_class` shows the three `*_default` partitions with
  `rls=false, force=false`; direct `SELECT FROM transactions_default` as `app_rw` → permission denied (grant
  absence is the only thing stopping it).
- **Specification (write first):** a test asserting FORCE RLS is present on every partition (parent **and**
  each child, including default + future partitions), and that no app role can read a partition directly
  without RLS applying.
- **Proposed fix:** ENABLE + FORCE RLS and the `tenant_isolation` policy on each partition (default and
  per-period), and make the partition-creation path apply RLS to every new partition; never rely on grant
  absence alone.
- **Risk if unfixed:** any future grant to a partition, or a new non-RLS partition, becomes a silent
  cross-tenant read via direct partition access.

### BUG-022 — F60: RLS policy cast is not fail-closed for empty/invalid tenant context
- **Feature:** [F60](./FEATURES.md) (CS-060) · **Priority:** P2 · **Size:** XS · **Pattern:** D2
- **Status:** ✅ Fixed (migration `0006` `app_current_tenant()` STABLE helper + policy swap on every table; `data_access/session.py` UUID edge guard; F60.AC2 corrected to the function form; verified by `test_bug022_failclosed.py` + the isolation gate — empty/malformed context yields 0 rows, never a cast 500; and **catalog-driven** `test_bug022_catalog_failclosed.py` proves it across **all 42** `tenant_isolation` tables — 29 app_rw-readable return 0 rows on a malformed/empty/injection context, 0 cast errors, 13 grant-restricted from app_rw use the same `app_current_tenant()` helper — so a future table that reverts to the throwing inline form is caught, not just the hand-named ones) · **Type:** Defect (spec bug + unhandled edge; resolved and drift-guarded)
- **Developer:** Unassigned · **Found:** 2026-11-14 · **Duration:** ~0.5d (est.)
- **Description:** Two related gaps around the policy expression:
  1. **Spec bug:** F60.AC2 specifies the filter as `current_setting('app.tenant_id', true)::UUID`. With the
     GUC reset/`SET LOCAL ''`, that is `''::UUID` → `invalid input syntax for type uuid`, which **contradicts
     AC4's** "no context → zero rows." Migration `0001` already fixed the *implementation* with
     `NULLIF(current_setting(...), '')::uuid`, but the AC still mandates the throwing form.
  2. **Unhandled edge:** `NULLIF` only neutralizes the empty string. A middleware bug or hostile token that
     does `SET LOCAL app.tenant_id = '<non-uuid>'` makes **every** query raise a 500 on the cast — not the
     safe "zero rows" AC4 promises.
- **Reproduction:** `SELECT ''::uuid` and `SELECT 'not-a-uuid'::uuid` both raise `invalid input syntax for
  type uuid`; the live `labor_rules` policy uses the `NULLIF` form (empty handled, non-uuid still throws).
- **Specification (write first):** tests that (a) an unset and an empty `app.tenant_id` both yield zero rows
  (never an error), and (b) a non-UUID `app.tenant_id` yields zero rows (or a typed, caught error), never a
  raw cast 500.
- **Proposed fix:** correct AC2 to mandate the `NULLIF` form; make the policy tolerate a malformed value
  (e.g. a `SECURITY DEFINER` helper that returns NULL on bad input, or validate/normalize `app.tenant_id`
  in the session-context middleware before any statement runs).
- **Risk if unfixed:** the published AC is wrong (fails AC4 if built literally), and a malformed tenant
  context DoSes the tenant with 500s instead of failing closed.

### BUG-023 — F60: "every domain table gets `tenant_id`" is over-broad for global reference tables
- **Feature:** [F60](./FEATURES.md) (CS-060) · **Priority:** P3 · **Size:** XS · **Pattern:** D5
- **Status:** 🐞 Open · **Type:** Defect (spec over-generalization) · relates to [[BUG-009]]
- **Developer:** Unassigned · **Found:** 2026-11-14 · **Duration:** ~0.25d (est.)
- **Description:** F60.AC1 says **every** domain table gets `tenant_id UUID NOT NULL`. But global reference
  tables are correctly tenant-less: `weather_codes` has no `tenant_id` and only `SELECT` for `app_rw`
  (verified). Applying `tenant_id` to shared reference data would be wrong (it is the same for all tenants),
  and the story never states the **write** policy for global tables (which tenant, if any, may mutate them).
- **Specification (write first):** a documented list of global/reference tables that are exempt from
  `tenant_id`, plus a test that `app_rw` cannot write them (read-only) and the ETL/global-admin owns their
  writes (ties to the BUG-009 per-tenant-prune guard).
- **Proposed fix:** carve out a named set of global reference tables in F60.AC1; state their grant/write
  policy explicitly.
- **Risk if unfixed:** a literal implementation adds spurious `tenant_id` to reference data, or leaves
  global-table writes ungoverned.

### BUG-024 — F60: scale-safe migration gotchas (CONCURRENTLY on partitioned parents; backfill write window)
- **Feature:** [F60](./FEATURES.md) (CS-060a) · **Priority:** P2 · **Size:** S · **Pattern:** D5
- **Status:** 🐞 Open · **Type:** Defect (migration plan will fail / leave gaps as written)
- **Developer:** Unassigned · **Found:** 2026-11-14 · **Duration:** ~0.5d (est.)
- **Description:** CS-060a's "scale-safe" recipe has two flaws for the partitioned tables (`transactions`,
  `weather_hourly`, `rule_eval_log`):
  1. **`CREATE INDEX CONCURRENTLY` is not supported on a partitioned parent** — Postgres rejects it; you must
     create the index on each partition and `ATTACH`, or create it on the parent `ONLY` and build children
     concurrently. The blanket recipe fails on exactly the big tables it targets.
  2. **Backfill write window:** between `ADD COLUMN tenant_id` (nullable) and `VALIDATE … NOT NULL`,
     concurrent INSERTs (the story deploys app code "alongside") can land `NULL tenant_id`, which is then
     invisible under RLS and blocks `VALIDATE`. No DEFAULT/trigger covers the window.
- **Specification (write first):** a migration test on a partitioned table proving the index path works and
  that no row can acquire a `NULL tenant_id` during the window.
- **Proposed fix:** per-partition `CREATE INDEX CONCURRENTLY` + `ATTACH` (or parent `ONLY` + children); set a
  temporary `DEFAULT`/`BEFORE INSERT` trigger that stamps the cutover tenant during the backfill window, then
  `VALIDATE` and drop the default.
- **Risk if unfixed:** the migration errors out mid-flight on big tables, or silently orphans concurrently
  written rows with `NULL tenant_id`.

### BUG-025 — F60: per-request tenant context is lost on an autocommit connection; role not pinned under pooling
- **Feature:** [F60](./FEATURES.md) (CS-060c) · **Priority:** P2 · **Size:** XS · **Pattern:** D2
- **Status:** ✅ Fixed (verified 2026-11-21) — `data_access/session.py::open_session_context` **refuses an autocommit connection** (`SessionContextError`), and issues transaction-local `SET LOCAL ROLE <role>` + parameterized `set_config('app.tenant_id', …, true)` together inside an explicit transaction; the non-local `SET ROLE` leak is closed with [[BUG-013]] (`compliance/engine.py`, `rules_admin.py` now use `SET LOCAL ROLE`). Autocommit refusal is covered by `test_data_access.py` F45.ac7; the **transaction-locality / no-leak-across-pooled-clients** property is now guarded by `test_bug025_pooling_session.py` (role and tenant GUC both reset at commit; verified end-to-end through PgBouncer 6432). · relates to [[BUG-013]], [[BUG-017]]
- **Developer:** Unassigned · **Found:** 2026-11-14 · **Duration:** ~0.5d (est.)
- **Description:** CS-060c sets tenant context with `SET LOCAL app.tenant_id`, which only persists inside an
  explicit transaction. On an **autocommit** connection the setting evaporates before the next statement →
  every query returns zero rows with no error (silent empty results), and nothing in the story requires the
  middleware to assert `not autocommit`. Separately, CS-060c pins the tenant GUC but says nothing about the
  **role**; the data-plane services use non-local `SET ROLE` (BUG-013), which leaks across pooled backends —
  CS-060c should mandate `SET LOCAL ROLE app_rw` alongside the tenant GUC for pooling safety.
- **Specification (write first):** tests that the middleware refuses (typed error) an autocommit connection,
  and that both the role and the tenant GUC are transaction-local (do not leak to the next pooled client).
- **Proposed fix:** the session-context middleware opens an explicit transaction, asserts `not autocommit`,
  and issues `SET LOCAL ROLE app_rw` + `SET LOCAL app.tenant_id = …` together before the first statement.
- **Risk if unfixed:** silent empty results under a misconfigured pool, and role state bleeding across
  tenants' pooled connections.

### BUG-026 — F60: `langgraph_checkpoints` still throws on a malformed tenant context (BUG-022 fix incomplete)
- **Feature:** [F60](./FEATURES.md) (CS-060) · **Priority:** P2 · **Size:** S · **Pattern:** D5/D2 · relates to [[BUG-022]]
- **Status:** ✅ Fixed (migration `0009` — a **catalog-driven sweep** recreates every `tenant_isolation` policy still on the inline form to `app_current_tenant()`; on the current schema that is `langgraph_checkpoints`, and the sweep is self-correcting for any future drift. Verified: malformed context on `langgraph_checkpoints` now yields 0 rows, 0 inline-form policies remain. Tests: `test_langgraph_checkpoints_fail_closed_on_malformed` + the BUG-027 gate assertion) · **Type:** Defect (incomplete fix — hand-maintained table list drifted) · **Found:** 2026-11-15
- **Developer:** Unassigned · **Duration:** ~0.5d (est.)
- **Description:** Migration `0006` (BUG-022) swaps every `tenant_isolation` policy to the fail-closed
  `tenant_id = app_current_tenant()` form, but its `TENANT_TABLES` list was copied from `0001` and does **not**
  include `langgraph_checkpoints` (created later, in `0003`). That table's policy is therefore still the old
  throwing inline form `tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid`. Fail-closed is
  now **inconsistent across tables**: a malformed/non-UUID `app.tenant_id` yields 0 rows everywhere BUT the
  LangGraph checkpoint store, where it 500s — the exact BUG-022 failure, on the agent's own checkpoint reads.
- **Reproduction (verified 2026-11-15):** `SET LOCAL ROLE app_rw; SELECT set_config('app.tenant_id','not-a-uuid',true);
  SELECT count(*) FROM langgraph_checkpoints;` → `ERROR: invalid input syntax for type uuid: "not-a-uuid"`
  (whereas the same against `stores` returns 0 rows). `pg_policy` shows `langgraph_checkpoints` using the
  inline `NULLIF(...)::uuid` form while every other table uses `app_current_tenant()`.
- **Specification (write first):** a test asserting EVERY `tenant_isolation` policy (every tenant-scoped base
  table + partition child) uses the fail-closed `app_current_tenant()` form, and that a malformed context on
  `langgraph_checkpoints` yields 0 rows, never a cast error.
- **Proposed fix:** migration `0008` swaps `langgraph_checkpoints` to `tenant_id = app_current_tenant()`; to
  prevent recurrence, **derive the swap set from the catalog** (every relation carrying a `tenant_isolation`
  policy) rather than a hand-maintained Python list, so no future table is silently missed. Closes with BUG-027.
- **Risk if unfixed:** a hostile or buggy token can DoS the agent's checkpoint store with 500s instead of
  failing closed, and the "every table is fail-closed" guarantee (F60.AC2) is false.

### BUG-027 — F68: isolation gate asserts FORCE but not the fail-closed policy EXPRESSION
- **Feature:** [F68](./FEATURES.md) (CS-068a) · **Priority:** P2 · **Size:** XS · **Pattern:** D3 · relates to [[BUG-026]]
- **Status:** ✅ Fixed (isolation gate now asserts the policy **expression**: `test_every_tenant_policy_is_fail_closed` checks every `tenant_isolation`/`tenant_self_isolation` policy's USING **and** WITH CHECK reference `app_current_tenant` — would have gone red on BUG-026) · **Type:** Defect (test coverage gap — let BUG-026 through) · **Found:** 2026-11-15
- **Developer:** Unassigned · **Duration:** ~0.25d (est.)
- **Description:** `test/test_isolation_gate.py` asserts `relforcerowsecurity` on every tenant table and
  partition, but never asserts the policy *qual* is the fail-closed `app_current_tenant()` form. That is why
  BUG-026 (a table left on the throwing inline form) passed the gate green. FORCE can be correct while the
  policy still 500s on malformed input.
- **Reproduction:** `langgraph_checkpoints` has FORCE RLS and is in the gate's allow-list, yet 500s on a
  non-UUID context (BUG-026) — the gate stays green.
- **Specification (write first):** extend the coverage meta-test to assert `pg_get_expr(polqual, polrelid)`
  references `app_current_tenant` for every `tenant_isolation` policy (base tables + partition children).
- **Proposed fix:** add the policy-expression assertion to `CoverageGate`; it then fails until BUG-026 lands.
- **Risk if unfixed:** a future migration can revert any table to the throwing/`USING(true)` form while the
  gate stays green — the gate does not actually verify the isolation *rule*, only that RLS is forced.

### BUG-028 — F68: gate coverage test is brittle against future (non-default) partitions
- **Feature:** [F68](./FEATURES.md) (CS-068a) · **Priority:** P3 · **Size:** XS · **Pattern:** D9
- **Status:** ✅ Fixed (gate coverage test now excludes partition children structurally via `pg_inherits`, not a name pattern; regression test `test_future_partition_does_not_break_coverage` proves a 2099 period partition is not flagged) · **Type:** Defect (test brittleness — false failure on a legitimate schema change) · **Found:** 2026-11-15
- **Developer:** Unassigned · **Duration:** ~0.25d (est.)
- **Description:** `test_every_tenant_id_table_is_covered_and_forced` excludes partition children by **name**
  (`table_name NOT LIKE '%_default'`). Partition children inherit the `tenant_id` column, so a future period
  partition (e.g. `transactions_2026_01`) would appear in the scan, not match `%_default`, not be in the
  allow-list, and **fail the gate on a legitimate ops partition add**.
- **Reproduction (verified 2026-11-15):** the `*_default` children already appear in
  `information_schema.columns` with a `tenant_id` column; only the `%_default` name filter hides them. A
  non-default child name would slip past the filter and trip the allow-list assertion.
- **Specification (write first):** the coverage test excludes partition children **structurally** (via
  `pg_inherits` / `pg_partitioned_table`), not by name, and partition children are instead verified by the
  partition-RLS assertion (BUG-021 coverage).
- **Proposed fix:** replace the `NOT LIKE '%_default'` filter with "not a partition child" via `pg_inherits`.
- **Risk if unfixed:** the P0 isolation gate blocks merges whenever ops adds a time-range partition — a
  self-inflicted CI outage that pressures teams to weaken the gate.

### BUG-029 — F68: pooled context-bleed is proven only at the `SET LOCAL` level, not through PgBouncer
- **Feature:** [F68](./FEATURES.md) (CS-068a, ties CS-068b) · **Priority:** P3 · **Size:** S · **Pattern:** D3
- **Status:** ✅ Fixed (`test/test_pool_isolation.py` runs through PgBouncer transaction mode on 6432 with prepared statements disabled; two behavioural tests prove context does not survive across pooled transactions and two concurrent clients never see each other's rows; a `pool_mode=transaction` guard skips gracefully if the admin db is locked down) · **Type:** Gap (uncovered integration edge) · **Found:** 2026-11-15
- **Developer:** Unassigned · **Duration:** ~1d (est.)
- **Description:** The gate's `ContextBleed` test reuses a single psycopg connection across two transactions
  and proves `SET LOCAL` transaction-locality (the underlying mechanism). It does **not** exercise the real
  hazard F68.AC3 names: PgBouncer in transaction mode handing one physical backend to two different logical
  clients. The mechanism is proven; the end-to-end pooled path is not.
- **Specification (write first):** an integration test that routes two tenants' sessions through PgBouncer
  (port 6432, transaction mode) over the same pool and asserts tenant A's role/GUC never reaches tenant B's
  reuse of the physical connection.
- **Proposed fix:** add a PgBouncer-in-the-loop integration test (fits CS-068b's suite); keep the existing
  unit-level `SET LOCAL` assertion as the fast first line.
- **Risk if unfixed:** a pooling misconfiguration (e.g. session vs transaction mode, or a non-local `SET
  ROLE`) that leaks context across tenants would pass the current gate.

### BUG-030 — F60: `app_current_tenant()` has no pinned `search_path`
- **Feature:** [F60](./FEATURES.md) (CS-060) · **Priority:** P3 · **Size:** XS · **Pattern:** D5
- **Status:** ✅ Fixed (migration `0008` recreates `app_current_tenant()` with `SET search_path = pg_catalog`; verified by `test_app_current_tenant_pins_search_path` — `proconfig` = `{search_path=pg_catalog}` — with the BUG-022 fail-closed tests still green) · **Type:** Hardening (defense-in-depth) · **Found:** 2026-11-15
- **Developer:** Unassigned · **Duration:** ~0.25d (est.)
- **Description:** The fail-closed helper `app_current_tenant()` (migration `0006`) is `STABLE` and runs as
  invoker (not `SECURITY DEFINER`), so a hijacked `search_path` is not a privilege-escalation today. But it
  references only built-ins; pinning `SET search_path = pg_catalog` is standard hardening and future-proofs it
  if the body ever grows or the function is later marked `SECURITY DEFINER`.
- **Specification (write first):** a test asserting `app_current_tenant()` has a pinned `search_path`
  (`proconfig` contains `search_path=pg_catalog`).
- **Proposed fix:** `ALTER FUNCTION app_current_tenant() SET search_path = pg_catalog` (or include it in the
  `CREATE FUNCTION` in a follow-up migration).
- **Risk if unfixed:** low today; becomes a real injection/escalation surface if the function is ever made
  `SECURITY DEFINER` or its body is extended, with no guard in place.

### BUG-031 — F30: API gateway connection pool is never closed (unclean shutdown)
- **Feature:** [F30](./FEATURES.md) (CS-030) · **Priority:** P2 · **Size:** XS · **Pattern:** D7
- **Status:** 🐞 Open · **Type:** Defect (resource lifecycle) · **Found:** 2026-11-16
- **Developer:** Unassigned · **Duration:** ~0.25d (est.)
- **Description:** `api/db.py` creates a module-level `psycopg_pool.ConnectionPool` but never closes it. At
  interpreter shutdown its `__del__` tries to join the pool's worker thread, raising
  `PythonFinalizationError: cannot join thread at interpreter shutdown`. Under uvicorn the pool is also not
  drained on app shutdown, leaking server-side connections on reload/restart.
- **Reproduction (verified 2026-11-16):** running `test_api_foundation.py` prints
  `Exception ignored while calling deallocator <ConnectionPool.__del__>: PythonFinalizationError` after the
  suite passes.
- **Specification (write first):** a test asserting the app closes the pool on shutdown (no finalizer error;
  pool `.closed` after the lifespan exits).
- **Proposed fix:** register a FastAPI `lifespan` (or shutdown handler) that calls `pool.close()`; open the
  pool on startup rather than lazily. Keeps tests clean and avoids leaked backends.
- **Risk if unfixed:** leaked PgBouncer/Postgres connections across restarts; noisy finalizer errors mask real
  shutdown problems.

### BUG-032 — F30: Next middleware is deprecated in Next 16 and gates only `/dashboard`
- **Feature:** [F30](./FEATURES.md) (CS-030) · **Priority:** P3 · **Size:** XS · **Pattern:** D9
- **Status:** 🐞 Open · **Type:** Defect (deprecation + narrow matcher) · **Found:** 2026-11-16
- **Developer:** Unassigned · **Duration:** ~0.25d (est.)
- **Description:** `coffee_shop/middleware.ts` triggers Next 16's `middleware-to-proxy` deprecation warning
  (middleware is renamed to `proxy.ts`), and its `config.matcher` covers only `/dashboard/:path*`. When more
  `(manager)` routes land (CS-031–035), they won't be presence-gated by middleware — they'd rely solely on the
  layout's `/me` 401-redirect (defense-in-depth exists, but the first-line gate wouldn't fire).
- **Specification (write first):** a test/check that every authenticated route redirects an unauthenticated
  request to `/login` (not just `/dashboard`).
- **Proposed fix:** migrate to `proxy.ts` (the codemod), and broaden the matcher to all authenticated routes
  while excluding `/login`, `/api`, and Next internals.
- **Risk if unfixed:** a future deprecation removal breaks the gate; new manager routes ship without the
  first-line auth redirect.

### BUG-033 — F30: demo manager password falls back to a known default when env is unset
- **Feature:** [F30](./FEATURES.md) (CS-030) · **Priority:** P2 · **Size:** XS · **Pattern:** D2
- **Status:** 🐞 Open · **Type:** Defect (insecure default) · **Found:** 2026-11-16
- **Developer:** Unassigned · **Duration:** ~0.25d (est.)
- **Description:** `api/config.py` hashes a hard-coded default password (`demo-pass-change-me`) when neither
  `DEMO_MANAGER_PASSWORD_HASH` nor `DEMO_MANAGER_PASSWORD` is set, so the demo runs out of the box. A
  deployment that forgets to set them ships a **publicly known** manager credential.
- **Specification (write first):** a test asserting that in prod mode (`COOKIE_SECURE=true`) startup **fails
  closed** unless a password hash is explicitly provided.
- **Proposed fix:** require `DEMO_MANAGER_PASSWORD_HASH` (raise at startup) whenever `COOKIE_SECURE=true` /
  non-dev; keep the convenience default for local dev only, behind a loud log warning. (Superseded once CS-061
  provides real `tenant_users` auth.)
- **Risk if unfixed:** a known-password manager login reachable in any non-local deployment.

### BUG-034 — F30: API gateway has no CORS/exposure policy (must stay internal)
- **Feature:** [F30](./FEATURES.md) (CS-030) · **Priority:** P3 · **Size:** XS · **Pattern:** D5 · relates to [[CS-070]]
- **Status:** 🐞 Open · **Type:** Gap (deployment/security boundary) · **Found:** 2026-11-16
- **Description:** The gateway sets the `session` cookie and is reachable directly on its port. The design is
  **same-origin only** (browser → Next → rewrite → gateway), so the gateway must never be internet-exposed; it
  currently has no CORS policy and no binding guidance, so a misconfigured deployment could expose it directly
  (where a cross-origin page could drive `/auth/login` against it).
- **Specification (write first):** document that the gateway binds to localhost/internal only and is reached
  solely via the Next rewrite; optionally a deny-by-default CORS config asserted by a test.
- **Proposed fix:** bind the gateway to an internal interface; document the "never public" rule in
  `api/README.md`; fold the public edge (CORS locked to tenant origins) into CS-070's web-security work.
- **Risk if unfixed:** direct gateway exposure bypasses the same-origin assumptions behind the cookie/CSRF
  model.

### BUG-035 — F61/CS-030: production gateway still serves legacy demo auth at `/auth/login`
- **Feature:** [F61](./FEATURES.md) (CS-061a) / [F30](./FEATURES.md) (CS-030) · **Priority:** P0 · **Size:** M · **Pattern:** D1/D2
- **Status:** ✅ Resolved (CS-061a, 2026-11-16) · **Type:** Integration/security-boundary defect · **Found:** 2026-11-16
- **Developer:** Unassigned · **Duration:** ~3d (est.)
- **Root cause:** CS-061a supplies an unmounted `auth.api.create_auth_router(...)`, while the deployed
  `api.app` continues to declare `POST /auth/login` itself. That legacy handler authenticates only the
  `DEMO_MANAGER_*` configuration and emits the CS-030 HS256 `session` cookie; no startup wiring creates an
  `AUTH_DATABASE_URL` auth connection, constructs `TenantAuthService`, includes the RS256 router, or changes
  `current_claims`/`/stores` to consume reconciled CS-061a principals. Mounting both routers would also create
  an ambiguous duplicate `POST /auth/login` route, where Starlette selects the earlier legacy handler.
- **Reproduction (verified 2026-11-16):** inspect `api/app.py` and run
  `python3 -m unittest discover -s test -p 'test_f61*.py' -v`; all eight isolated CS-061a contracts pass,
  yet `api/app.py` imports only `.auth` and defines its own `/auth/login` with `issue_token`/
  `set_session_cookie`, with no `create_auth_router` or `include_router` reference. The existing
  `test_api_foundation.py` codifies that legacy JSON shape and `session` cookie, proving the two contracts
  cannot coexist on the same endpoint.
- **Impact:** integrating the candidate without an explicit gateway cutover leaves the reachable manager API
  on the legacy demo credential/session scheme rather than the CS-061a Argon2id, RS256, refresh-family, and
  browser-cookie boundary. A naive router mount can silently preserve the legacy route because of route order.
- **Acceptance criteria:**
  - Replace or version the legacy `/auth/login` endpoint; exactly one production handler owns each CS-061a
    auth route and it uses a `TenantAuthService` built from the dedicated auth principal.
  - Make gateway startup fail closed for missing/invalid auth DSN, JWT keyring, issuer/audience, or refresh
    pepper; never fall back to `SESSION_SECRET`/`DEMO_MANAGER_*` in production.
  - Authenticate protected gateway requests from validated, canonical CS-061a claims before opening the
    `app_rw` RLS transaction, and prove token tenant claims cannot be overridden by request input.
  - Add ASGI integration tests for duplicate-route absence, JSON/browser transport separation, malformed /
    multiple / missing bearer and cookie credentials, cross-tenant rejection, and a CS-030 migration or
    compatibility plan. Coordinate CS-070 for CSRF/CORS/routing policy rather than weakening the cookie
    attributes.
- **Risk if unfixed:** the application can report CS-061a’s unit contracts green while serving an older,
  incompatible authentication boundary to real gateway traffic.

### BUG-036 — F30/CS-070: manager UI remains wired to the retired `session` cookie after gateway cutover
- **Feature:** [F30](./FEATURES.md) (CS-030) / [F70](./FEATURES.md) (CS-070) · **Priority:** P0 · **Size:** M · **Pattern:** D1
- **Status:** ✅ Resolved (CS-061a, 2026-11-16) · **Type:** Compatibility regression / incomplete cutover · **Found:** 2026-11-16
- **Developer:** Unassigned · **Duration:** ~3d (est.)
- **Root cause:** the CS-061a gateway cutover removes the legacy HS256 `session` authentication route and
  accepts only an RS256 bearer access token or `access_token` cookie, but the checked-in Next manager app
  still posts to `/api/auth/login` expecting the old result and checks/sends only `session`. Specifically,
  `coffee_shop/middleware.ts`, `(manager)/layout.tsx`, and `(manager)/dashboard/page.tsx` call
  `request.cookies.has("session")` or forward `Cookie: session=…`; the gateway now returns no such cookie
  and rejects it on `/me` and `/stores`.
- **Reproduction (verified 2026-11-16):** `rg -n "session|/auth/login|/me" coffee_shop` shows the UI's
  session-only flow, while `python3 -m unittest discover -s test -p 'test_cs061a_gateway_integration.py' -v`
  passes and proves the gateway's sole `/auth/login` response is a CS-061a JSON token pair with no cookie.
  A browser completing the existing UI login therefore remains unauthenticated at the protected layout and
  is redirected/rejected rather than reaching the manager dashboard.
- **Impact:** the backend's secure CS-061a boundary is correct, but the shipped manager UI cannot use it;
  this turns the backend cutover into a user-visible login outage and leaves the documented CS-030
  compatibility claim unfulfilled.
- **Acceptance criteria:**
  - Change the UI login to the browser transport (`/api/auth/browser/login`) and verify it never reads either
    token from JavaScript; the browser must receive the `HttpOnly; Secure; SameSite=Strict` cookies through
    the same-origin rewrite.
  - Replace all `session` checks/forwarding in middleware, manager layout, dashboard, and logout with the
    CS-061a `access_token` / browser refresh behavior; validate `/me` and `/stores` work after login and
    fail closed after logout or refresh replay.
  - Add browser/Next integration coverage for the cookie path and proxy/rewrite behavior, including expired
    access + browser refresh, absence of legacy `/auth/login` response assumptions, and no client-side token
    exposure. Coordinate CSRF/CORS and tenant routing policy with CS-070.
- **Risk if unfixed:** every manager login against the cut-over gateway fails to establish the UI's protected
  session, blocking the product despite green backend authentication tests.

### BUG-037 — F30/CS-070: manager UI never uses the CS-061a browser refresh credential
- **Feature:** [F30](./FEATURES.md) (CS-030) / [F70](./FEATURES.md) (CS-070) · **Priority:** P1 · **Size:** S · **Pattern:** D1
- **Status:** ✅ Resolved (CS-061a, 2026-11-16) · **Type:** Session continuity gap · **Found:** 2026-11-16
- **Developer:** Unassigned · **Duration:** ~1d (est.)
- **Root cause:** the BUG-036 migration changes login, middleware, and server components to use the
  short-lived `access_token`, and correctly proxies `/auth/browser/refresh`, but no UI/middleware/route
  handler ever calls that refresh endpoint. After the fixed 900-second access-token lifetime, middleware still
  sees the stale cookie, then `/me` or `/stores` returns 401 and the layout/dashboard redirects to `/login`.
  The 7-day HttpOnly `refresh_token` is therefore unused by the manager application.
- **Reproduction (verified 2026-11-16):** `rg -n "auth/browser/refresh|refresh_token" coffee_shop` finds
  only the Next rewrite/comment, while `layout.tsx` and `dashboard/page.tsx` redirect on a 401 with no refresh
  attempt. `test_cs061a_manager_ui_compat.py` proves the endpoint itself rotates and detects replay, but does
  not exercise an expired-access UI recovery path because none exists.
- **Impact:** managers are forced to re-enter credentials every 15 minutes despite holding a valid browser
  refresh credential, and the BUG-036 acceptance criterion for browser-refresh behavior is not met.
- **Acceptance criteria:**
  - Add a same-origin refresh flow that preserves `HttpOnly` secrecy, sends the refresh cookie only to
    `/auth/browser/refresh`, relays replacement `Set-Cookie` headers to the browser, and retries the failed
    authenticated UI request exactly once.
  - Expired/malformed/replayed refresh credentials must clear both cookies and redirect only to the fixed
    local `/login` route; no user-controlled return URL may be reflected into a redirect.
  - Add UI/browser integration tests for expired access recovery, refresh replay/failure, duplicate/stale
    cookie rejection, cookie path forwarding, and token non-exposure in rendered HTML, client state, logs, or
    cache keys. Coordinate CSRF/CORS behavior with CS-070.
- **Risk if unfixed:** a valid 7-day refresh session offers no user benefit, producing recurring authentication
  outages and encouraging an insecure client-side token workaround.

### BUG-038 — F61/CS-061b: auth hardening is bypassed over the production (pooled) repository
- **Feature:** [F61](./FEATURES.md) (CS-061b) · **Priority:** P0 · **Size:** M · **Pattern:** D2/D3
- **Status:** ✅ Resolved (CS-061b, 2026-11-16) · **Type:** Integration / security-boundary defect (fail-open) · **Found:** 2026-11-16 (candidate validation)
- **Developer:** Unassigned (Codex to fix) · **Duration:** ~1d (est.)
- **Root cause:** `TenantAuthService` routes **every** CS-061b behaviour through concrete-class checks
  `isinstance(self._repo, DatabaseAuthRepository)` / `isinstance(self._repo, InMemoryAuthRepository)`
  (`coffee-scheduler/auth/service.py` — `authorize_access_request`, `login_json`, `revoke_user_sessions`,
  `request_password_reset`, `redeem_password_reset`, `begin/confirm_mfa_enrollment`). The production gateway
  (`api/auth_gateway.build_auth_service_from_env`) wires `PooledAuthRepository`, which subclasses
  `AuthRepository` — **not** `DatabaseAuthRepository` — and does not implement the CS-061b repository methods
  (`access_state`, `revoke_user_sessions`, `record_login_attempt`, `login_locked`, `get_mfa`, `store_mfa`,
  `issue/consume_password_reset`, …). So over the real production repo every DB branch is skipped and the
  service silently falls back to in-process memory. Concretely, in production:
  - **Tenant suspension is never enforced.** `authorize_access_request` first checks the constructor-frozen
    `self._tenant_status` (always `"active"`; nothing updates it from `tenants.status`) and, because the repo
    is not a `DatabaseAuthRepository`, never calls the owner-run `auth_access_state` function. A suspended /
    churned / trial tenant's unexpired access tokens keep authorizing `/stores` and `/me` → **403 is never
    returned** (F61.AC3.4 / AC3.6 fail open). *(User **deactivation** is still caught, via the CS-061a
    `get_tenant_user_by_id` reconciliation in `authenticate_access_token`; tenant status is the gap.)*
  - **Password-reset redeem crashes and never persists.** `redeem_password_reset` takes the in-memory branch
    and dereferences `self._repo._users`, which `PooledAuthRepository` does not have →
    `AttributeError: 'PooledAuthRepository' object has no attribute '_users'`. The DB password is never
    updated and no session is invalidated (F61.AC4.7–4.9).
  - **Revocation / throttle / MFA are process-local, not durable or shared.** `revoke_user_sessions` matches
    neither isinstance branch, so it never calls `auth_revoke_user_sessions` — refresh families are not
    revoked in the DB and the cutoff lives only in one process's dict; login throttle uses the in-process
    `LoginRateLimiter` and MFA uses the in-process `self._mfa` dict, so the migration-0011 tables
    (`auth_login_attempts`, `auth_mfa_recovery_codes`, `auth_revoked_tokens`) and their functions are **dead
    in production**. A load balancer sending the next request to another instance — or any process restart —
    resets lockout, drops revocations, and loses MFA state (F61.AC3.5, AC4.10–4.13).
- **Reproduction (verified 2026-11-16 against a disposable PostgreSQL 16, migrations `0001..0011` applied):**
  - `coffee-scheduler/test/test_cs061b_production_repo_path.py` (no DB) — three RED assertions: over a
    minimal non-fixture `AuthRepository` that reports its tenant suspended, `authorize_access_request` does
    **not** raise `TenantSuspendedError` and never calls `access_state`; `redeem_password_reset` raises the
    `self._repo._users` `AttributeError`.
  - `coffee-scheduler/test/test_cs061b_service_integration.py` (opt-in `CS061B_DATABASE_INTEGRATION=1`) — the
    **five** service-over-`DatabaseAuthRepository` proofs PASS (suspension, durable session revocation, reset
    consume + session invalidation, throttle lockout, MFA enrol/require/replay — each across a fresh auth_svc
    connection), while `test_production_pooled_repository_enforces_suspension` (service over the real
    `PooledAuthRepository`, tenant suspended after login) FAILS: `TenantSuspendedError not raised`.
  - Direct probe (same DB): `isinstance(PooledAuthRepository(pool), DatabaseAuthRepository) == False`;
    `DatabaseAuthRepository` → `TenantSuspendedError` (correct), `PooledAuthRepository` → authorize returns a
    valid `Principal` for the suspended tenant.
- **Impact:** the migration, the `auth_*` functions, `DatabaseAuthRepository`, and the Codex
  `test_cs061b_database_integration.py` repository-level proof are all correct, but the **reachable gateway**
  never exercises them. Integrating the candidate as-is ships an auth boundary where tenant suspension does
  not take effect, password reset 500s, and revocation/lockout/MFA are per-process — i.e. the CS-061b
  acceptance criteria are not met where it counts, despite a green in-memory contract suite.
- **Acceptance criteria (for the fix):**
  - Drive all CS-061b behaviour through the `AuthRepository` interface, not concrete-class `isinstance`
    checks: declare the CS-061b operations (`access_state`, `revoke_user_sessions`, `record_login_attempt`,
    `login_locked`, `issue/consume_password_reset`, `store/get/consume` MFA) on `AuthRepository` and implement
    them on **both** `InMemoryAuthRepository` and `PooledAuthRepository` (which must delegate to its per-call
    `DatabaseAuthRepository`, as it already does for the CS-061a methods).
  - Enforce tenant `status` per request from the database of record (or an equivalent durable source) rather
    than the constructor-frozen `self._tenant_status`, so a suspended tenant is refused on the next protected
    request through the pooled repo.
  - Back revocation list, session cutoffs, login throttle/lockout, and MFA with the migration-0011 tables in
    production so they are durable and shared across instances; `redeem_password_reset` must persist through
    `auth_consume_password_reset` and never touch an in-memory `_users`.
  - Keep the existing authoritative contracts and the CS-061a suite green; turn
    `test_cs061b_production_repo_path.py` and the pooled case in `test_cs061b_service_integration.py` green
    **without** weakening either test.
- **Secondary findings (same fix pass, lower severity, do not block on their own):**
  - **No IP-scoped throttle.** `LoginAttemptKey.account()` ignores `ip`, and `auth_record_login_attempt` /
    `auth_login_locked` count only by `(tenant_id, email)`; the recorded `ip`/`inet` column is never used, so
    the independent IP throttle / credential-stuffing defence in F61.AC4 is absent (the account lockout and
    "no cross-tenant DoS" invariant do hold). Add a bounded IP-scoped limiter or document the deferral.
  - **Config constants ignored.** `.env.example` advertises `AUTH_LOGIN_MAX_FAILURES`,
    `AUTH_LOGIN_LOCKOUT_SECONDS`, `AUTH_PASSWORD_RESET_TTL_SECONDS`, but `auth/ratelimit.py` hard-codes `5` /
    `900`, the SQL hard-codes `>= 5` / `15 minutes`, and `auth/reset.py` hard-codes `3600`. Read them or drop
    the env keys.
  - **Bespoke MFA cipher.** `MfaSecretCipher` is an unauthenticated, hand-rolled repeating-keystream XOR
    (`SHA256(key‖nonce)`); it encrypts-at-rest and round-trips but is not a vetted AEAD for a payroll-adjacent
    TOTP seed. Consider AES-GCM / Fernet. (Contract only required "encrypted at rest"; flagged as hardening.)
- **Risk if unfixed:** a suspended / non-paying / compromised tenant retains full API access until every
  access token naturally expires and across refreshes; password reset is a hard 500; and account lockout and
  MFA are trivially defeated by a restart or a second instance — the application reports CS-061b green while
  serving a fail-open auth boundary to real gateway traffic.

### BUG-039 — F61/CS-061b: an undecryptable MFA secret crashes login (500) instead of the opaque auth failure
- **Feature:** [F61](./FEATURES.md) (CS-061b) · **Priority:** P2 · **Size:** S · **Pattern:** D2
- **Status:** ✅ Resolved (CS-061b, 2026-11-16) · **Type:** Fail-closed-robustness / security-invariant deviation · **Found:** 2026-11-16 (Claude final independent validation of candidate `2dabcfa`)
- **Developer:** Unassigned · **Duration:** ~0.5d (est.)
- **Root cause:** In `TenantAuthService.login_json` (`coffee-scheduler/auth/service.py`, the MFA block ~L144–155)
  the `try` wrapping the decrypt-and-consume step catches **only** `MfaReplayError`:
  `secret = self._mfa_cipher.decrypt(state['ciphertext']).decode()` runs inside it, but
  `MfaSecretCipher.decrypt` raises `ValueError('invalid MFA ciphertext')` (and `.decode()` can raise
  `UnicodeDecodeError`) whenever the stored `mfa_secret_ciphertext` cannot be authenticated/decrypted. That
  `ValueError` is **not** caught, so it propagates out of the auth service and surfaces to the gateway as an
  uncaught exception (HTTP 500), not the generic `AuthenticationError`.
- **Why it matters (contract):** the CS-061b SDD (`docs/sdd-cs061b.md`, "Security invariants and edge cases")
  requires **"Fail-closed everywhere … a missing/short pepper or MFA key is a refusal or a hard configuration
  error — never an open default"** and **"One opaque failure … refusals that reach a client must be
  externally indistinguishable from an ordinary credential failure — no message, no status … that reveals …
  MFA-enabled state"**. A per-request `ValueError`/500 is neither a clean refusal nor a startup config error,
  and the 500-vs-401 difference is an oracle that distinguishes an MFA-enabled account whose secret is
  undecryptable from an ordinary bad-credential 401. (No token is issued, so there is **no authentication
  bypass** — the failure is closed in the security sense; the defect is the *form* of the failure.)
- **Reachable through ordinary operations:** rotating `AUTH_MFA_ENC_KEY` (the key is global; stored secrets
  are per-user) makes **every** existing MFA secret undecryptable → every MFA owner/manager login 500s until
  re-enrolment — an availability incident, not a graceful failure. Also triggered by at-rest corruption or a
  DB-write tamper of the column.
- **Reproduction (verified 2026-11-16):**
  - `coffee-scheduler/test/test_f61_mfa_cipher_failclosed_contract.py` (no DB, in-memory contract fixture) —
    two RED assertions: after a simulated `AUTH_MFA_ENC_KEY` rotation, and after corrupting the stored
    ciphertext in place, `login_json` raises `ValueError('invalid MFA ciphertext')` where
    `AuthenticationError` is required.
  - Live pooled-repo probe over disposable PostgreSQL (migrations `0001..0011`, `CS061B_DATABASE_INTEGRATION=1`):
    enrol MFA, then `UPDATE tenant_users SET mfa_secret_ciphertext = '\xdeadbeef' WHERE id=…`; the next MFA
    login over the production `PooledAuthRepository` raises an uncaught `ValueError`, not `AuthenticationError`.
    (All other pooled-path hardening checks — suspension, deactivation, session cutoff, jti revocation, durable
    reset + session invalidation, durable throttle lockout, MFA enrol/require/replay/recovery, cross-tenant
    isolation — passed.)
- **Acceptance criteria (for the fix):**
  - A stored MFA secret that cannot be decrypted (key rotation, corruption, tamper) makes `login_json` /
    `login_browser` raise the generic `AuthenticationError` (opaque credential failure), issuing no tokens and
    returning the same status/shape/timing as an ordinary bad password — no uncaught `ValueError`, no 500, no
    MFA-state oracle. Widen the MFA block to map decrypt/`decode` failures to the opaque failure (while still
    treating a legitimately missing cipher config as the documented hard configuration error).
  - Add the failing-first regression above and keep every existing CS-061a/CS-061b contract and the live DB
    integration suite green; do not weaken any negative case.
- **Related (pre-existing, re-confirmed this pass — not introduced by this candidate; see BUG-038 "Secondary
  findings"):** the independent **IP-scoped login throttle is still absent** (`LoginAttemptKey.account()`
  ignores `ip`; `auth_record_login_attempt`/`auth_login_locked` count only by `(tenant_id, email)` — verified
  live: 10 failures from one IP across distinct emails do not throttle a fresh account from that IP), contrary
  to SDD AC4.10's "independently, on the client IP"; and the **login/reset config tunables are ignored**
  (`.env.example` advertises `AUTH_LOGIN_MAX_FAILURES`/`AUTH_LOGIN_LOCKOUT_SECONDS`/`AUTH_PASSWORD_RESET_TTL_SECONDS`
  but `auth/ratelimit.py` hard-codes `5`/`900`, migration `0011` hard-codes `>= 5`/`15 minutes`, and
  `auth/reset.py` hard-codes `3600`). Also **code-slop:** `TenantAuthService.from_environment`
  (`auth/service.py`) is dead (no caller; the gateway uses `build_auth_service_from_env`) and is itself broken
  — it never `return`s the service it builds and references `DatabaseAuthRepository`, which `service.py` does
  not import (a `NameError` if ever called). These do not block on their own but should be closed with the fix.
- **Risk if unfixed:** a routine encryption-key rotation turns into a 500 outage for every MFA-protected
  owner/manager, and the MFA-path status difference gives an account-state oracle the SDD explicitly forbids.

### BUG-040 — F61/CS-061b: migration 0011 is not reversible — downgrade leaks its auth_* functions, breaking the F43.ac3 round-trip
- **Feature:** [F61](./FEATURES.md) (CS-061b) · also regresses [F43](./FEATURES.md).ac3 · **Priority:** P1 · **Size:** S · **Pattern:** D4
- **Status:** ✅ Resolved (CS-061b, 2026-11-16) · **Type:** Migration reversibility regression · **Found:** 2026-11-16 (Claude final independent validation of candidate `2dabcfa`)
- **Developer:** Unassigned · **Duration:** ~0.5d (est.)
- **Root cause:** `db/migrations/versions/0011_cs061b_auth_hardening.py` `upgrade()` creates **11** `auth_*`
  `SECURITY DEFINER` functions with plain `CREATE FUNCTION` (not `CREATE OR REPLACE`), but `downgrade()` only
  `DROP TABLE`s the four new tables and `DROP COLUMN`s the four `tenant_users` columns — it never
  `DROP FUNCTION`s `auth_access_state`, `auth_revoke_access_token`, `auth_revoke_user_sessions`,
  `auth_issue_password_reset`, `auth_consume_password_reset`, `auth_record_login_attempt`,
  `auth_login_locked`, `auth_store_mfa`, `auth_consume_mfa_step`, `auth_get_mfa`, or
  `auth_consume_recovery_code`. After a downgrade those functions survive, so the next `upgrade` aborts at
  `CREATE FUNCTION auth_access_state(...)` with `DuplicateFunction: function "auth_access_state" already
  exists with same argument types`. The CS-061a migration `0010` established the correct pattern — its
  `downgrade()` runs `DROP FUNCTION IF EXISTS` for every function it created — which `0011` does not follow.
- **Reproduction (verified 2026-11-16 on pristine, single-database PostgreSQL 16 clusters — role-clean):**
  - `coffee-scheduler/test/test_migrations_roundtrip.py::...::test_upgrade_downgrade_upgrade_leaves_only_alembic_metadata_at_base`
    (the existing F43.ac3 reversibility gate; runs only when `DATABASE_URL` is set): **PASSES at base
    `f78dc7a` (no 0011)** and **FAILS on the candidate** with `alembic upgrade head failed:
    psycopg.errors.DuplicateFunction: function "auth_access_state" already exists`. A clean regression:
    base→green, candidate→red, same pristine cluster.
  - Equivalent minimal repro: `alembic upgrade head` → `alembic downgrade 0010` → `alembic upgrade head`
    fails at the re-`CREATE FUNCTION`.
  - Note: this contract gate is `@skipUnless(DATABASE_URL)`, so a contract run without a live DB (the common
    path) skips it and never sees the break — which is how the candidate's suite reported green.
- **Impact:** the forward path (`upgrade head`) and all CS-061b runtime behaviour are correct — every live-DB
  integration proof over `DatabaseAuthRepository` and the production `PooledAuthRepository` passed — but
  **rolling `0011` back and re-applying it is impossible** without manually dropping the 11 functions first,
  and the repo's own migration-reversibility contract (F43.ac3) is red. Any production rollback of the
  CS-061b slice is blocked.
- **Acceptance criteria (for the fix):**
  - `0011.downgrade()` drops every object `upgrade()` created, functions included (`DROP FUNCTION IF EXISTS`
    with the exact signatures, mirroring `0010`), so `upgrade → downgrade → upgrade` and `downgrade base →
    upgrade head` both succeed on a clean cluster.
  - `test_migrations_roundtrip.py` passes with `DATABASE_URL` set; run the reversibility gate against a live
    PostgreSQL in CI (or document why it is skipped) so an asymmetric migration cannot regress green again.
- **Risk if unfixed:** a CS-061b rollback leaves the database in a state where the next deploy's
  `alembic upgrade` crashes, turning a routine revert into a stuck migration during an incident.

### BUG-041 — F61/CS-061c: duplicate-invite / already-member returns HTTP 500 over the live DB (in-memory path returns 409)
- **Feature:** [F61](./FEATURES.md) (CS-061c) · **Priority:** P2 · **Size:** XS · **Pattern:** D2 (DB↔in-memory divergence)
- **Status:** 🐞 Open · **Type:** Error-mapping / repository-parity defect · **Found:** 2026-11-17 (Claude review of CS-061c)
- **Root cause:** `auth_create_invitation` (migration `0012`) guards the common conflict cases with
  `RAISE EXCEPTION 'already a member'` and `RAISE EXCEPTION 'pending invitation exists'`, which Postgres
  raises as **SQLSTATE `P0001` (`RaiseException`)**, *not* `UniqueViolation`. But
  `DatabaseAuthRepository.create_invitation` (`auth/repository.py`) only normalizes the conflict when
  `exc.__class__.__name__ == "UniqueViolation"` → `ValueError`; a `RaiseException` falls through and
  re-raises raw. `TenantAuthService.create_invitation` catches only `ValueError` (→ `InvitationConflictError`
  → HTTP 409), so the raw `psycopg.errors.RaiseException` escapes the service and the `POST /invitations`
  handler (which catches only `InvitationPermissionError`/`InvitationError`) returns an unhandled **500**.
  The partial unique index only fires on the narrow concurrent race (two inserts past the `EXISTS` guard),
  so solely that path yields `UniqueViolation`→409; the ordinary sequential duplicate 500s.
- **Reproduction (verified live 2026-11-17, rolled back — no rows persisted):** call
  `auth_create_invitation(maven_tenant,'dupe@x.example','staff',…)` twice → second call raises
  `RaiseException | sqlstate: P0001 | msg: pending invitation exists`. The in-memory repo raises `ValueError`
  for the same case (→ 409), so `test_f61_invitation_contract.py` is green while the production path is not —
  the live-DB test only exercised the invite→accept happy path, not the conflict path.
- **Impact:** re-inviting an email with a live pending invite, or inviting an existing member, returns 500
  instead of the specified 409 — a wrong client contract and a noisy error for a routine, expected case.
- **Acceptance criteria (for the fix):** map `P0001`/`RaiseException` from `auth_create_invitation` to the
  conflict `ValueError` in `DatabaseAuthRepository` (or raise a typed conflict the service catches), so the
  live DB returns 409; add a **live-DB** conflict test (DB-skip pattern) covering already-member and
  duplicate-pending so the DB↔in-memory parity is enforced, not just the happy path.
- **Risk if unfixed:** every duplicate/already-member invite 500s in production; CS-062 onboarding and the
  CS-064 admin surface build on this endpoint and will inherit the wrong status.

### BUG-042 — F61/CS-061c: audit-store outage converts a valid login into a 500 (a genuine auth result is lost)
- **Feature:** [F61](./FEATURES.md) (CS-061c) · **Priority:** P2 · **Size:** S · **Pattern:** D3 (availability coupling)
- **Status:** 🐞 Open · **Type:** Spec deviation / resilience defect · **Found:** 2026-11-17 (Claude review of CS-061c)
- **Root cause:** `TenantAuthService._audit` (`auth/service.py`) wraps `record_audit` and, on any failure,
  logs and **re-raises**. It is invoked on the login success path (`auth.login.succeeded`) *after* the
  credential + MFA checks pass and the session is issuable. So if the audit store (the `auth_record_audit`
  function / `audit_log`) is unavailable, a user who authenticated correctly receives a 500 and no tokens.
  This contradicts the CS-061c plan's explicit requirement — "wrap so **a genuine auth result is never
  lost**, but a failed audit is surfaced (log + metric)". The locked test `test_f61_audit_contract.py::
  test_audit_failure_is_surfaced` currently encodes the re-raise behavior, so it must be revised with the fix.
- **Impact:** an audit-log DB hiccup becomes a full login/accept outage (availability coupling), and the blast
  radius grows as CS-064b adds admin-action/impersonation audit calls onto the same port. It fails *closed*
  (no security hole), but drops valid sessions the spec says to preserve.
- **Acceptance criteria (for the fix):** for success-path/non-security-critical audit events, emit a
  structured error log **and a metric** and continue returning the issued tokens (never silently swallow,
  never fail-open); reserve fail-closed-on-audit only for events where compliance requires a guaranteed trail
  (document which). Update `test_audit_failure_is_surfaced` to assert the event is surfaced via log/metric
  while the valid login still succeeds.
- **Risk if unfixed:** routine audit-store maintenance or transient load takes down all authentication.

### BUG-043 — F61/CS-061c: invitation-accept rate limit is per-process (in-memory), not shared across gateway instances
- **Feature:** [F61](./FEATURES.md) (CS-061c) · **Priority:** P3 · **Size:** S · **Pattern:** D3
- **Status:** 🐞 Open · **Type:** Defense-in-depth gap · **Found:** 2026-11-17 (Claude review of CS-061c)
- **Root cause:** `create_app` (`api/app.py`) throttles the public `POST /invitations/accept` endpoint with
  `invitation_limiter = LoginRateLimiter()` — an **in-memory** limiter instantiated per app/worker. Unlike
  the CS-061b login throttle, which is DB-backed (`auth_record_login_attempt`/`auth_login_locked`) and
  therefore shared across gateway processes, each horizontally-scaled gateway holds its own accept bucket, so
  the effective brute-force limit multiplies by the worker count and resets on restart/redeploy.
- **Impact:** weaker brute-force protection on an anonymous, token-bearing endpoint under multi-worker/
  multi-instance deployment (the production shape). Single-process dev is unaffected, which is why tests pass.
- **Acceptance criteria (for the fix):** back the accept throttle with the DB (reuse the CS-061b
  `auth_login_attempts` port with an `'invite'`/client-IP key) so the limit is shared and durable, matching
  the login throttle; add a test asserting the throttle state is not process-local.
- **Risk if unfixed:** token-guessing / enumeration on `/invitations/accept` scales with gateway replicas.

### BUG-044 — F61/CS-061c: `list_invitations` ordering differs between the DB and in-memory repositories
- **Feature:** [F61](./FEATURES.md) (CS-061c) · **Priority:** P3 · **Size:** XS · **Pattern:** D2
- **Status:** 🐞 Open · **Type:** Repository-parity nit · **Found:** 2026-11-17 (Claude review of CS-061c)
- **Root cause:** `auth_list_invitations` (migration `0012`) orders by
  `(pending & not-expired) DESC, created_at DESC` (newest-first), while
  `InMemoryAuthRepository.list_invitations` sorts by `(status != 'pending', created_at ASC)` (oldest-first).
  Both surface pending-first, but the secondary order is reversed, so the admin list order is not a stable
  contract across the fixture and production paths.
- **Impact:** cosmetic/UX only (no security or data-exposure effect), but a contract divergence of exactly the
  BUG-038 class that a UI (CS-064a) could come to depend on.
- **Acceptance criteria (for the fix):** align the in-memory sort to the DB's `created_at DESC` secondary
  order (or vice-versa) and assert the order in a shared contract test.
- **Risk if unfixed:** the admin invitation list orders differently in tests vs production.

### BUG-045 — F61/CS-061b: login never captures the client IP → per-IP lockout collapses into a tenant-wide DoS, and audit logs 0.0.0.0
- **Feature:** [F61](./FEATURES.md) (CS-061b) · also F30/CS-070 (gateway must forward client IP) · **Priority:** P1 · **Size:** S · **Pattern:** D2/D3 (integration gap + wrong aggregation)
- **Status:** ✅ Resolved (CS-070, 2026-11-19) — login/browser-login/refresh now thread the CS-070 trusted-proxy client IP (`scope["client_ip"]` via `_client_ip`) into both the throttle and the CS-061c audit entry, and `_route_slug` stops the login body overriding the routed tenant. Regression `test_cs070_client_ip.py` (two IPs independent, trusted vs untrusted XFF, real audited IP) passed independent review. **Caveat (BUG-057):** effective only with routing enabled **and** `TRUSTED_PROXY_CIDRS` set; the empty default collapses to the direct peer IP. · **Type:** Security/availability defect (throttle mis-keying) + audit-integrity gap · **Found:** 2026-11-17 (Claude review of the committed auth surface)
- **Root cause:** `auth/api.py` `login` / `browser_login` call `service.login_json(...)` / `login_browser(...)`
  **without** `client_ip`, so it defaults to `"0.0.0.0"`. The CS-061b throttle (`auth_record_login_attempt` /
  `auth_login_locked`, migration `0011:50,52`) locks when `account_failures >= max OR ip_failures >= max`,
  where `ip_failures` counts failed attempts for `(tenant_id, ip)` in the window. Because every login is
  recorded with `ip = 0.0.0.0`, the IP dimension collapses — `ip_failures` becomes *all* failed logins for the
  tenant across every email and every real client. (The `/invitations/accept` route *does* extract
  `request.client.host`; login does not — inconsistent.) Compounding it: Next.js rewrites `/api/*`
  server-side, so even `request.client.host` would be the Next proxy's IP for all users — the same collapse —
  unless a **trusted** `X-Forwarded-For` hop is parsed. The CS-061c audit events (`auth.login.succeeded/failed`)
  likewise record `ip=0.0.0.0` for every login.
- **Reproduction:** (1) **Lockout:** POST 5 failed `/auth/login` for tenant *T* against any email(s);
  `auth_login_locked(T, <any email>, '0.0.0.0', …)` then returns true for **every** user in *T* (tenant-wide),
  because all failures share `0.0.0.0`. A later *successful* login clears only that email's `account` failures
  (`DELETE … WHERE email=…`), not the shared IP failures, so the lockout persists for the full window.
  (2) **Audit:** after any login, `SELECT ip FROM audit_log WHERE action LIKE 'auth.login%'` is `0.0.0.0` on
  every row.
- **Impact:** a single attacker locks out an entire tenant with 5 failed logins (availability/DoS); the per-IP
  brute-force control is non-functional and inverted into a weapon; `audit_log.ip` is forensically useless
  (defeats architecture §14.3). Supersedes the stale BUG-039 "IP throttle absent" note — the IP branch **is**
  implemented in SQL; it is defeated by the missing IP capture.
- **Acceptance criteria (SDD):**
  - **Failing-first** `spec: F61.ac4` — two distinct client IPs each submit `<max` failed logins for the same
    tenant; **neither** is locked (today both share `0.0.0.0` → the tenant locks). **Failing-first**
    `spec: F61.ac5` — an audited login records the real client IP, not `0.0.0.0`.
  - Gateway derives the client IP from a **trusted** source: `request.client.host`, overridden by the
    rightmost-untrusted `X-Forwarded-For` hop **only** when the immediate peer is the configured trusted Next
    proxy; never trust a client-supplied XFF blindly (IP spoofing would evade/weaponize the throttle).
  - Thread `client_ip` through `login` / `browser_login` / the refresh routes into both the throttle and the
    audit entry; keep every existing CS-061b throttle/MFA/reset contract green.
- **Risk if unfixed:** trivial tenant-wide login DoS; no real per-IP brute-force protection; audit trail blind
  on IP.
- **Cross-refs:** the trusted-proxy / forwarded-header boundary is owned by **CS-070**; fix IP capture there and
  consume it in CS-061b.

### BUG-046 — F69/CS-087: `restore()` / `list_including_deleted()` leave `app.show_deleted` on, leaking soft-deleted rows into the rest of the transaction
- **Feature:** [F69](./FEATURES.md) (CS-087) · **Priority:** P3 · **Size:** XS · **Pattern:** D3 (state leak / API asymmetry)
- **Status:** 🐞 Open · **Type:** Transaction-scoped visibility leak · **Found:** 2026-11-17 (Claude review of CS-087)
- **Root cause:** In `data_access/soft_delete.py`, `soft_delete()` saves and restores `app.show_deleted`
  (`_show_deleted_for_write` / `_restore_show_deleted_setting`), but `restore()` and `list_including_deleted()`
  call `enable_show_deleted(conn)` (`SET LOCAL app.show_deleted='on'`) and **never** restore it. `SET LOCAL`
  persists to end of transaction, so any later query in the same transaction bypasses the restrictive
  `soft_delete_hidden` SELECT policy and sees soft-deleted rows.
- **Reproduction:** within one `open_session_context` transaction, call `list_including_deleted(conn, 'stores', …)`
  (or `restore(…)`), then run a normal `SELECT` on a Tier-A table → soft-deleted rows appear in the normal read.
  `soft_delete()` in the same position does not leak (it restores the prior setting).
- **Impact:** low today (admin "show deleted"/restore paths typically run in their own request/transaction,
  which ends and resets `SET LOCAL`), but any code mixing a restore/list-including-deleted with an ordinary read
  in one transaction silently defeats the hide-by-default guarantee — the exact inconsistency `soft_delete()`
  was written to avoid.
- **Acceptance criteria (SDD):** `spec: F69` — a test that, within one transaction, calls
  `list_including_deleted` then a normal read and asserts the normal read still hides soft-deleted rows. Make
  `restore()` / `list_including_deleted()` save/restore `app.show_deleted` as `soft_delete()` does (or wrap the
  scoped read in a context manager that resets on exit). Keep `test_soft_delete*` green.
- **Risk if unfixed:** a future admin/restore flow sharing a transaction with ordinary reads silently exposes
  deleted rows.

---

### BUG-047 — F61/CS-061c: invitation accept limiter drops the client-IP key
- **Feature:** [F61](./FEATURES.md) (CS-061c) · **Priority:** P2 · **Size:** XS · **Pattern:** D2 (key aggregation)
- **Status:** ✅ Fixed (2026-11-18) — distinct IP buckets; invitation accept and login-throttle suites pass (15 tests) · **Type:** Availability / rate-limit isolation defect · **Found:** 2026-11-18 (code-quality review)
- **Root cause:** `/invitations/accept` constructs `LoginAttemptKey("invite", "accept", ip)`, but
  `LoginAttemptKey.account()` returns only `(tenant_id, email)`. `LoginRateLimiter` keys failures and locks
  exclusively with that account tuple, so all client IPs share `("invite", "accept")`; the supplied `ip`
  is ignored. One client's failed tokens can lock the endpoint for all clients, and any successful accept
  clears the shared bucket. This is distinct from BUG-043's per-process durability gap and complements it.
- **Reproduction:** send `MAX_FAILED_ATTEMPTS` invalid accept attempts from client A, then attempt a valid
  invitation from client B against the same app instance. Client B receives 429 even though its IP had no
  failures. A success from either client also clears the other's counter.
- **Acceptance criteria:**
  - Add an explicit IP-bucket operation for anonymous invitation acceptance; do not encode endpoint scope
    by overloading the account key or silently discard `ip`.
  - A failures-to-lock sequence from IP A does not lock IP B; failures from A still lock A at threshold.
  - Success clears only the relevant IP bucket; expiry/window boundary and concurrent access remain correct.
  - Keep the CS-061b account lockout contracts and BUG-043's future distributed-persistence requirement
    separate and intact.
- **Risk if unfixed:** a single client can deny invitation acceptance for all clients sharing one gateway.
- **Cross-refs:** related to BUG-043 (distributed persistence); BUG-047 covers distinct-client isolation.

### BUG-048 — F61/CS-061a: arbitrary-claims contract token signer is part of the production service API
- **Feature:** [F61](./FEATURES.md) (CS-061a) · **Priority:** P1 · **Size:** XS · **Pattern:** D2 (test seam exposure)
- **Status:** ✅ Fixed (2026-11-18) — signer moved to test support; auth, gateway, manager UI, and invitation suites pass (38 tests) · **Type:** Security-boundary / testability defect · **Found:** 2026-11-18 (code-quality review)
- **Root cause:** `TenantAuthService.issue_contract_token()` lives on the production service class and can
  sign caller-selected tenant, user, role, and overridden claim values using its private token service. Its
  docstring says it is test-only, but every production service instance still exposes this capability.
- **Acceptance criteria:** move arbitrary-claims signing into test-only support; remove the method from
  `TenantAuthService`; migrate adversarial tests to the test helper; prove the production service no longer
  exposes an unchecked token-minting method. Preserve all existing invalid-claim and cross-tenant tests.
- **Risk if unfixed:** future application code can accidentally use a contract-only signing path to mint
  tokens outside the validated login/refresh flow.

### BUG-049 — F61/CS-061b: repository hardening operations are described as mandatory but are not abstract
- **Feature:** [F61](./FEATURES.md) (CS-061b) · **Priority:** P2 · **Size:** XS · **Pattern:** D2 (interface contract)
- **Status:** ✅ Fixed (2026-11-18) — required DB-backed operations are abstract; incomplete adapter rejected and all production adapters are concrete · **Type:** Adapter completeness / maintainability defect · **Found:** 2026-11-18 (code-quality review)
- **Root cause:** `AuthRepository` says CS-061b hardening operations are mandatory, but methods such as
  `access_state`, `revoke_access_token`, reset, throttle, and MFA operations are ordinary methods that raise
  `NotImplementedError`, not abstract methods. An incomplete adapter can instantiate and fail only when a
  security path calls the missing operation. The mandatory invitation/audit methods already use
  `@abstractmethod`, so enforcement is inconsistent.
- **Acceptance criteria:** make every required persisted repository capability fail at adapter
  construction time (`@abstractmethod`); ensure the in-memory, database, pooled, and test adapters satisfy
  the contract; add a minimal incomplete-adapter test that proves instantiation is rejected. Move
  `set_tenant_status` and `set_user_active` out of the shared production interface/service and into test-only
  fixture support because only `InMemoryAuthRepository` implements them.
- **Risk if unfixed:** an adapter can silently omit revocation or persistence behavior until a live request
  reaches that path.

### BUG-050 — F61/CS-061b/c: dense one-line auth code obscures state transitions and duplicates work
- **Feature:** [F61](./FEATURES.md) (CS-061b/c) · **Priority:** P3 · **Size:** XS · **Pattern:** D4 (code clarity)
- **Status:** ✅ Fixed (2026-11-18) — auth helpers/routes expanded; reset/throttle/invitation suites pass (25 tests) · **Type:** Technical debt / reviewability · **Found:** 2026-11-18 (code-quality review)
- **Root cause:** security-sensitive limiter/reset helpers and invitation route handlers compress validation,
  mutation, exceptions, and returns into semicolon-separated or one-line statements. The reset mint path
  computes the same HMAC digest twice. This style makes ordering and state changes harder to audit.
- **Acceptance criteria:** expand the affected `auth/ratelimit.py`, `auth/reset.py`, and invitation endpoint
  code in `api/app.py` into conventional typed methods/blocks; compute each digest once; preserve externally
  observable behavior and all existing auth/invitation contracts. No unrelated formatting sweep.
- **Risk if unfixed:** reviewers can miss ordering, expiry, or lock-state mistakes in authentication paths.

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
| CS-012 | F8 | P1 | S | ✅ Done |
| CS-013 | F10 | P1 | L | ✅ Done — `compliance/engine.py`; 27 tests pass (claims_legal_accuracy gated on CS-021) |
| CS-013a | F10 | P1 | S | ✅ Done — append-only `rule_eval_log` write path |
| CS-013b | F10 | P1 | S | ✅ Done — half-open waiver expiry on shift date |
| CS-013c | F10/F26 | P1 | XS | ✅ Done — coverage↔law fail-closed reconciliation (VERIFIED-gate stays with CS-021/022) |
| CS-014 | F9 | P1 | L | Blocked (umbrella) |
| CS-014a | F9 | P1 | M | Blocked |
| CS-014b | F9 | P1 | M | ✅ Done (2026-11-21) |
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
| CS-034 | F34 | P1 | M | ✅ Done — `rules_admin.py` + 18 tests (F34 AC1–AC8, BUG-014–018 fixed); caveat: missing `unittest.main()` → BUG-019 |
| CS-035 | F35 | P2 | S | Not started |
| CS-036 | F36 | P2 | M | Not started |
| CS-037 | F37 | P2 | M | Not started |
| CS-038 | F38 | P2 | S | ✅ Done — pinned OTel Collector config + `docker-compose.observability.yml`; AC1–AC11 verified, 12/12 tests |
| CS-039 | F39 | P2 | M | Not started |
| CS-040 | F40 | P2 | S | Not started |
| CS-041 | F41 | P2 | S | Not started |
| CS-042 | F42 | P0 | M | ✅ Done — requirements pinned, `.env.example`, `docs/data-platform.md`, roles decision, `load_snapshot` proof |
| CS-043 | F43 | P0 | S | ✅ Done — compose+PgBouncer, reversible migration, backup/restore drill (F43 AC1–AC5 verified) |
| CS-044 | F44 | P0 | M | ✅ Done — `scripts/etl_excel_to_postgres.py`; idempotent, 11 tests pass |
| CS-045 | F45 | P1 | M | ✅ Done — psycopg typed repo + migration 0003 (checkpoints RLS); 7 tests (Claude dev/Codex tests) |
| CS-046 | F46 | P2 | S | Not started |
| CS-047 | F47 | P0 | M | ✅ Done — `docs/schema.md` + migrations `0001`/`0002`, round-trip verified |
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
| CS-060 | F60 | P0 | M | ◑ Core done via `0001`; unblocked; hardening BUG-020..025 + session middleware remain |
| CS-060a | F60 | P0 | M | ✅ Done via `0001` — tenant-aware schema + composite indexes + maven-demo seed (greenfield) |
| CS-060b | F60 | P0 | S | ✅ Done via `0001` — FORCE RLS + NULLIF policy + non-owner app_rw; hardening BUG-020/021 |
| CS-060c | F60 | P0 | S | ◑ Partial — SET LOCAL pattern impl + RLS-verified; reusable middleware + BUG-025 remain |
| CS-061 | F61 | P0 | M | ✅ Done — CS-061a/b/c (membership split + invite email deferred) |
| CS-061a | F61 | P0 | M | ✅ Done |
| CS-061b | F61 | P0 | S | ✅ Done |
| CS-061c | F61 | P1 | M | ✅ Done — `0012` invitations + append-only audit_log; BUG-041..044 filed |
| CS-062 | F62 | P0 | M | ✅ Done — backend onboarding; wizard/email/billing deferred |
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
| CS-070 | F70 | P0 | S | Ready for independent review |
| CS-087 | F69 | P1 | M | ✅ Done — restrictive soft-delete lifecycle, helper, guard, and schema contract |
| CS-071 | F71 | P1 | M | Not started — employee app shell + `employee` role & self-scoped RLS |
| CS-072 | F71 | P1 | S | Not started — my-schedule view (published-only, offline-cached) |
| CS-073 | F72 | P1 | M | Not started — availability model & intake (effective-dated, solver constraint) |
| CS-074 | F72 | P1 | M | Not started — time-off / PTO request & approval workflow |
| CS-075 | F73 | P1 | M | Not started — time & attendance capture (punches, actual-vs-scheduled) |
| CS-076 | F73 | P1 | S | Not started — no-show detection → F14 cover trigger (idempotent) |
| CS-077 | F74 | P1 | M | Not started — schedule draft edit + live F10/F26 re-validation |
| CS-078 | F74 | P1 | M | Not started — schedule publish, versioning & notify |
| CS-079 | F75 | P2 | M | Not started — open-shift post + atomic claim with legality gate |
| CS-080 | F75 | P2 | S | Not started — employee-initiated shift swap |
| CS-081 | F76 | P2 | M | Not started — fairness objective terms + per-tenant weights |
| CS-082 | F77 | P2 | S | Not started — certification & work-permit expiry (fail-closed) |
| CS-083 | F78 | P2 | M | Not started — payroll/HCM export contract + reconciliation |
| CS-084 | F79 | P2 | M | Not started — employee PII encryption, read-audit & retention |
| CS-085 | F80 | P2 | M | Not started — forecast accuracy & drift monitoring |
| CS-086 | F80 | P2 | M | Not started — gated retraining, model registry & rollback |

### Defects (found 2026-11-12)

| Story | Feature | Priority | Size | Status |
| --- | --- | --- | --- | --- |
| BUG-001 | F2 | P1 | S | ✅ Fixed — proportional allocation and feasibility-floor regressions |
| BUG-002 | F5 | P2 | XS | ✅ Fixed (under CS-009) — fail-closed category coverage |
| BUG-003 | F5 | P1 | S | ✅ Fixed — re-run guard prevents mutable calibration input reuse |
| BUG-004 | F5/F9 | P2 | XS | ✅ Fixed (`9151e2f`) — dominant weather effect, not a compound product (D5) |
| BUG-005 | F44 | P1 | XS | ✅ Fixed — declared PII text-column read policy preserves textual identifiers |
| BUG-006 | F44 | P1 | XS | ✅ Fixed (`9151e2f`) — `columns()` unions all rows' keys (D8) |
| BUG-007 | F44 | P2 | XS | ✅ Fixed (`9151e2f`) — `validate_snapshot` covers `employee_pii` via `KEYS` (D9) |
| BUG-008 | F44 | P2 | XS | ✅ Fixed (`9151e2f`) — `shifts` deduped from a set (D1) |
| BUG-009 | F44 | P3 | XS | ✅ Fixed (`9151e2f`) — `weather_codes` excluded from per-tenant prune |
| BUG-010 | F10 | P2 | XS | ✅ Fixed — structural vocabulary scope retains fail-closed validation |
| BUG-011 | F10 | P2 | XS | ✅ Fixed — secondary shift and break timestamps are validated |
| BUG-012 | F43 | P0 | XS | ✅ Fixed — image-native PgBouncer healthcheck and lifecycle regression coverage |
| BUG-013 | F43/F10 | P0 | XS | ✅ Fixed — transaction-local role and tenant context under pooling |
| BUG-014 | F34 | P1 | S | ✅ Fixed — transition-scoped verification gate |
| BUG-015 | F34 | P2 | S | ✅ Fixed — coverage-gap reconciliation with unbounded validity semantics |
| BUG-016 | F34 | P2 | S | ✅ Fixed — effective-dated version addressing |
| BUG-017 | F34 | P3 | XS | ✅ Fixed — explicit transaction contract and timezone validation |
| BUG-018 | F34 | P2 | S | ✅ Fixed — verified evidence gate and no read-path URL networking |
| BUG-019 | F34 | P2 | XS | 🐞 Open — `test_rules_admin.py` missing `unittest.main()`; runs 0 tests as a script |
| BUG-020 | F60 | P1 | S | ✅ Fixed (`0004`) — tenants ENABLE RLS + self-policy; app roles see only own tenant; 4 tests |
| BUG-021 | F60 | P2 | S | 🐞 Open — default partitions lack RLS/FORCE (latent; mitigated only by grant absence) |
| BUG-022 | F60 | P2 | XS | ✅ Fixed — `app_current_tenant()` fail-closed; catalog-driven test proves 0 rows (not a cast 500) across all 42 tenant_isolation tables |
| BUG-023 | F60 | P3 | XS | 🐞 Open — AC1 "every domain table gets tenant_id" over-broad vs global reference tables |
| BUG-024 | F60 | P2 | S | 🐞 Open — migration: CONCURRENTLY unsupported on partitioned parents; backfill NULL-tenant window |
| BUG-025 | F60 | P2 | XS | ✅ Fixed — autocommit refused + transaction-local role/tenant (no pool leak); `test_bug025_pooling_session.py` |
| BUG-047 | F61 | P2 | XS | ✅ Fixed — invitation accept failures/clears are isolated by canonical client-IP bucket |
| BUG-048 | F61 | P1 | XS | ✅ Fixed — arbitrary-claims signer exists only in test support |
| BUG-049 | F61 | P2 | XS | ✅ Fixed — enforce required repository methods at adapter instantiation |
| BUG-050 | F61 | P3 | XS | ✅ Fixed — explicit auth state transitions and one digest calculation per reset |
| BUG-051 | F61 | P2 | S | ✅ Fixed — centralized typed pooled repository delegation |
| BUG-052 | F61 | P2 | XS | ✅ Fixed — one JWT validation per protected authorization |
| BUG-053 | F61 | P1 | S | ✅ Fixed — logout revokes access token and presented refresh family |
| BUG-054 | F45 | P2 | XS | 🐞 Open — validate and bound repository list pagination |
| BUG-055 | F36/F38 | P1 | M | 🐞 Open — limit collector exposure and require TLS for remote OTLP |

### BUG-051 — F61: centralize pooled auth repository delegation
- **Feature:** [F61](./FEATURES.md) · **Priority:** P2 · **Size:** S
- **Status:** ✅ Fixed — centralized typed delegation with pool lifecycle regression coverage
- **Description:** `PooledAuthRepository` repeats pool lifecycle and connection borrowing logic across every method, while many CS-061b/c methods accept untyped `*args`. The duplication makes a security-sensitive adapter harder to audit and lets the wrapper drift from the `AuthRepository` contract.
- **Story:** As an auth maintainer, I want every pooled repository operation to use one shared connection/delegation path and retain the abstract port's explicit signatures, so connection lifecycle and forwarding behavior are consistent and statically reviewable.
- **Acceptance criteria:**
  - Add one private delegation helper that opens a closed pool, borrows exactly one connection, creates the transient database repository, invokes the named operation, and returns its result.
  - Every public adapter method keeps an explicit signature and return annotation matching `AuthRepository`; keyword-only arguments remain keyword-only.
  - Exceptions propagate unchanged and the pool connection context exits on both success and failure.
  - Focused fake-pool tests cover closed-pool open, one borrow/delegation, returned value, keyword forwarding, and exception cleanup.

### BUG-052 — F61: validate access token once per authorization
- **Feature:** [F61](./FEATURES.md) · **Priority:** P2 · **Size:** XS
- **Status:** ✅ Fixed — authorization reuses the single validated claim set
- **Description:** `authorize_access_request` calls `authenticate_access_token` and then validates the same RS256 JWT a second time to read `jti` and `iat`. This doubles signature verification on every protected request and splits identity reconciliation from the claims that drive revocation checks.
- **Story:** As a gateway operator, I want authorization to validate a signed token once and carry its validated claims through canonical membership and revocation checks, so each request has one consistent validation result and avoids duplicate crypto work.
- **Acceptance criteria:**
  - A private helper returns both reconciled `Principal` and the validated claims from one `validate_access_token` call.
  - `authenticate_access_token` retains its existing public return contract and errors.
  - `authorize_access_request` derives UUID `jti` and UTC `iat` only from the helper's validated claims and fails closed for malformed claims.
  - Tests assert exactly one validation call per authorized request and cover invalid signature, missing/malformed `jti`/`iat`, inactive membership, and revocation outcomes.

### BUG-053 — F61: persist single-session logout revocation
- **Feature:** [F61](./FEATURES.md) · **Priority:** P1 · **Size:** S
- **Status:** ✅ Fixed — logout revokes the presented access token and known refresh family
- **Description:** `revoke_token(jti)` writes hard-coded fixture tenant/user UUIDs, which violates real tenant/user foreign-key identity and cannot safely revoke the caller's actual session. The reachable `/auth/logout` route only clears cookies, leaving the access token usable until expiry and its refresh family able to mint more credentials.
- **Story:** As a tenant user, I want logout to revoke the presented access token and its presented refresh-token family server-side before dropping browser credentials, so the logged-out session cannot continue making protected requests or rotate refresh credentials.
- **Acceptance criteria:**
  - The service accepts the access-token credential, validates it and reconciles canonical membership, then persists the token's actual `jti`, tenant, user, and expiry; callers cannot supply identity/JTI fields independently.
  - If a refresh cookie is present and maps to a live record, revoke its family using the stored digest lookup; never store/log raw refresh secrets.
  - `/auth/logout` uses the same unambiguous access credential extraction as protected routes and only accepts the browser refresh cookie for family revocation.
  - Missing, expired, malformed, or ambiguous credentials still produce idempotent logout and clear both cookie paths; valid credentials are revoked before returning success. Persistence errors are not swallowed as successful revocation.
  - Tests cover bearer and browser-cookie logout, subsequent access rejection, refresh-family rejection, absent/expired/duplicate credential cookie-clearing, and correct real tenant/user/JTI/expiry arguments.

### BUG-054 — F45: bound and validate repository list pagination
- **Feature:** [F45](./FEATURES.md) · **Priority:** P2 · **Size:** XS
- **Status:** 🐞 Open
- **Description:** `Repository.list()` converts `limit` and `offset` with `int()` but does not reject negative values or cap page size. PostgreSQL treats a negative LIMIT as unlimited, so a caller can accidentally or deliberately retrieve the entire tenant table; negative offsets and lossy values also create surprising behavior.
- **AC1:** `limit` accepts only a non-boolean integer in the inclusive range 1..1000; default remains 100.
- **AC2:** `offset` accepts only a non-boolean integer greater than or equal to zero; default remains zero.
- **AC3:** Invalid pagination raises a descriptive `ValueError` before any cursor or SQL operation is used.
- **AC4:** Valid pagination continues to bind values as SQL parameters and preserves existing filters and ordering.
- **Edge / adversarial cases:** zero/negative limit, limit 1000 and 1001, negative offset, bool, float, numeric string, enormous integer, and invalid arguments on a fake connection that must not execute SQL.
- **Cross-references:** `data_access/repository.py::Repository.list`, `test/test_data_access.py`, F45 repository contracts.
- **Expected outcome:** List reads have a predictable bounded cost and explicit input contract.

### BUG-055 — F36/F38: protect telemetry endpoints and require TLS off loopback
- **Feature:** [F36](./FEATURES.md) + [F38](./FEATURES.md) · **Priority:** P1 · **Size:** M
- **Status:** 🐞 Open
- **Description:** The observability compose file publishes OTLP and health-check ports on all host interfaces, while the SDK always sets `insecure=True`. Collector OTLP receivers are unauthenticated. A machine reachable on the host network can submit telemetry or probe health, and telemetry sent to a remote endpoint has no transport encryption.
- **AC1:** Compose publishes development OTLP and health ports on `127.0.0.1` only while preserving service-to-service access on the compose network.
- **AC2:** SDK configuration permits plaintext only for an explicitly recognized loopback endpoint.
- **AC3:** A non-loopback endpoint requires TLS credentials and fails closed before exporter construction when credentials are absent or invalid.
- **AC4:** Production Collector configuration supports TLS and optional client authentication while local compose configuration remains explicitly local-development mode.
- **AC5:** Documentation describes the local-only trust boundary and production TLS/auth settings without committing secrets.
- **Edge / adversarial cases:** IPv4 and IPv6 loopback spellings, hostname aliases, DNS names resolving to loopback, malformed endpoints, remote hostname with missing CA, incomplete client cert/key pair, and accidental insecure override.
- **Cross-references:** `otel/telemetry.py`, `otel/collector-config.yaml`, root `docker-compose.observability.yml`, `test/test_cs036_otel_foundation.py`, `test/test_cs038_otel_collector.py`.
- **Expected outcome:** The collector is not exposed to the LAN by default and remote OTLP transport is encrypted and authenticated.

### BUG-056 — F70/CS-070: tenant routing was unconditionally enabled, 404-ing the existing unprefixed manager-UI calls
- **Feature:** [F70](./FEATURES.md) (CS-070) · also F30 · **Priority:** P2 · **Size:** S · **Pattern:** D3 (integration regression)
- **Status:** ✅ Gated in review (2026-11-19); frontend migration follow-up open · **Type:** Deploy-time integration regression · **Found:** 2026-11-19 (Claude review of CS-070)
- **Root cause:** the CS-070 production app (`api.app.__getattr__('app')`) installed `TenantRoutingMiddleware`
  **unconditionally** (default mode `development`, requiring a `/t/<slug>/` prefix). The manager-UI server
  components still fetch **unprefixed** `/me` and `/stores`, and the shell posts to unprefixed `/auth/login`
  (via the `/api/*` rewrite). With routing active, every non-global unprefixed path returns the cache-safe 404,
  so `uvicorn api.app:app` breaks the authenticated demo. Unit tests passed only because they build `create_app`
  **without** `routing_config`. Contradicts SDD AC3.4 ("routes that do not install tenant routing preserve the
  existing same-origin behavior").
- **Reproduction (verified 2026-11-19):** `create_app` with a default dev `TenantRoutingConfig` → `GET /me`,
  `GET /stores`, `POST /auth/login` all **404**; only `/t/maven-demo/me` reaches the handler.
- **Impact:** running the gateway with the pre-CS-070 frontend breaks login + dashboard.
- **Fix applied in review:** `tenant_routing_config_from_env()` now returns `None` unless
  `TENANT_ROUTING_ENABLED=true`, so the default is the CS-030/CS-061 single-tenant same-origin behavior and
  routing is opt-in (SDD AC3.4). CS-070 tests (which pass `routing_config` explicitly) and `test_api_foundation`
  stay green.
- **Remaining follow-up:** migrate the manager-UI server components + login/session-refresh to the `/t/<slug>/`
  dev topology (and prod subdomains) so routing can be enabled end-to-end; until then the shell runs with routing
  off. Tracked for CS-031–035 / the CS-070 frontend handoff.
- **Risk if unfixed (follow-up):** routing cannot be turned on in a real multi-tenant deploy until the shell uses
  routed URLs.

### BUG-057 — F70/CS-061b: the BUG-045 client-IP fix is inert unless `TRUSTED_PROXY_CIDRS` is configured
- **Feature:** [F70](./FEATURES.md) (CS-070) / [F61](./FEATURES.md) (CS-061b) · **Priority:** P3 · **Size:** XS · **Pattern:** D3 (config-dependent control)
- **Status:** 🐞 Open · **Type:** Deployment-config hazard (brute-force control) · **Found:** 2026-11-19 (Claude review of CS-070)
- **Root cause:** `extract_client_ip` trusts `X-Forwarded-For` only when the direct peer is in
  `config.trusted_networks`; otherwise it returns the direct peer IP. `TRUSTED_PROXY_CIDRS` defaults to empty, so
  with routing enabled but no CIDR set, every request behind the Next proxy resolves to the **Next server's IP** —
  the per-IP throttle still collapses to one shared bucket (better than BUG-045's `0.0.0.0`, but not per-client).
- **Impact:** BUG-045 is only truly resolved when the operator configures `TRUSTED_PROXY_CIDRS` with the
  Next/ingress network; a misconfigured deploy silently keeps a tenant-wide-ish throttle collapse.
- **Acceptance criteria:** document `TRUSTED_PROXY_CIDRS` as required when routing is enabled (`.env.example` +
  the routing docs); emit a startup warning (or fail closed) if routing is enabled in production with no trusted
  CIDR; add a test asserting the warning/behavior.
- **Risk if unfixed:** a production deploy believes BUG-045 is fixed while the per-IP throttle is still collapsed.

### BUG-058 — F62/CS-062: `labor_rule_templates` seed is bootstrap-order-dependent (empty on a clean deploy)
- **Feature:** [F62](./FEATURES.md) (CS-062) · **Priority:** P2 · **Size:** S · **Pattern:** D3 (migration/ETL order dependency)
- **Status:** ✅ Fixed in review (2026-11-19) — idempotent post-ETL seed step + documented bootstrap order · **Type:** Order-dependency / provisioning-correctness defect · **Found:** 2026-11-19 (Claude review of CS-062, exposed by shared-DB corruption)
- **Root cause:** migration `0016` seeds `labor_rule_templates` with `INSERT … SELECT FROM` maven-demo's
  `labor_rules`, but on a standard bootstrap `alembic upgrade head` runs the whole chain (including `0016`)
  **before** the ETL (`scripts/etl_excel_to_postgres.py`) loads `labor_rules`. So on a clean deploy the template
  is seeded **empty**, and `onboarding_provision` then refuses every tenant ("jurisdiction has no verified rule
  set") — provisioning (F62.AC4) is silently unmet. It only worked in the dev DB because `labor_rules` were
  already ETL-loaded when `0016` was first applied.
- **Reproduction (verified 2026-11-19):** with maven-demo's `labor_rules` absent, re-applying `0016` left
  `labor_rule_templates` at **0 rows** → `test_cs062 …provision…` failed with `RaiseException: jurisdiction has
  no verified rule set`. Re-running the ETL then the new seed step restored a 31-rule NY template and the suite
  passed 9/9.
- **Fix applied in review:** added `scripts/seed_rule_templates.py` — idempotent `INSERT … SELECT … ON CONFLICT
  (jurisdiction, rule_id, effective_from) DO NOTHING`, run **after** the ETL — and documented the bootstrap
  order **migrate → ETL → seed_rule_templates**. `0016` keeps its best-effort snapshot (a harmless no-op when
  `labor_rules` are absent); `onboarding_provision` already fails safe (refuses) on an empty template.
- **Follow-up:** a fully self-contained template (literal seed independent of ETL, or an ETL-integrated template
  load) would remove the ordering requirement entirely.
- **Risk if unfixed:** a real new tenant is provisioned with **zero** labor rules on a clean deploy.
- **Follow-up done (2026-11-21, CS-012 branch):** the CI `tests` job now runs `scripts/seed_rule_templates.py` after the ETL, implementing the documented `migrate → ETL → seed_rule_templates` order so the onboarding LiveDatabaseTests pass in CI.

### BUG-059 — F8/CS-012: forecast exact-hour validation uses the record's own timezone, not the business timezone
- **Feature:** [F8](./FEATURES.md) (CS-012) · **Priority:** P1 · **Size:** XS · **Status:** ✅ Fixed (2026-11-21) — `validate_record` now runs the exact-hour check on the `America/New_York`-local time; regression `spec: F8.ac4` covers the `+05:30`→`09:30` case · **Type:** Correctness (validation bypass) · **Found:** 2026-11-21 (Claude review of CS-012)
- **Description:** `validate_record` (F8.ac4) checks `forecast_hour.minute/second/microsecond == 0` against the value's *own* tzinfo, then separately converts to `America/New_York` only for the store-hours check. A timestamp that is on the hour in a half-hour-offset zone is half-past the hour in the business timezone, so a sub-hourly business-time forecast is accepted. This lets demand land on a grid the solver (CS-016a) and hourly throughput join (`throughput_standards`) do not expect.
- **Reproduction (verified):** `2026-01-05T20:00:00+05:30` has `minute == 0` in its own zone but is `09:30` `America/New_York`; `validate_record(...)` **accepts** it for store 5 (open 06–21) even though it is half past the hour.
- **Story:** As a forecast consumer, I want a forecast hour to be an exact clock hour *in the business timezone*, so every record maps to exactly one hourly staffing slot regardless of the producer's timezone representation.
- **Acceptance criteria:**
  - F8.ac4 computes the exact-hour check on the `CANONICAL_TZ`-local time (`forecast_hour.astimezone(ZoneInfo(CANONICAL_TZ))`): `minute == second == microsecond == 0` there, else `TimezoneError`.
  - A record on the hour only in a non-whole-hour offset zone (e.g. `+05:30`) that resolves to `HH:30` business-time is rejected.
  - Existing whole-hour UTC and `America/New_York` inputs still pass; a regression test tagged `spec: F8.ac4` covers the `+05:30` case.

### BUG-060 — F8/CS-012: forecast vocabulary admits the `(per transaction overhead)` pseudo-category and does not pin the taxonomy version
- **Feature:** [F8](./FEATURES.md) (CS-012) · **Priority:** P1 · **Size:** S · **Status:** ✅ Fixed (2026-11-21) — `load_vocabulary` now reads `product_taxonomy` for the pinned `taxonomy_version` (9 real categories, fail-closed on unknown version); migration `0018` adds a CHECK excluding `(per transaction overhead)`. DB-gated regressions cover the vocabulary and the CHECK · **Type:** Data-integrity / spec gap · **Found:** 2026-11-21 (Claude review of CS-012)
- **Description:** `load_vocabulary` returns `SELECT DISTINCT product_category FROM throughput_standards` and `del`s its `taxonomy_version` argument, so (1) it includes the non-product row `(per transaction overhead)` as a valid category and (2) it does not actually pin the vocabulary to a taxonomy version — the very decoupling CS-012 exists to provide. Migration `0017`'s FK `forecasts(tenant_id, product_category) → throughput_standards` has the same blind spot: a forecast for `(per transaction overhead)` passes both the Python validator and the DB FK.
- **Reproduction (verified):** `throughput_standards` for maven-demo contains 10 rows — the 9 product categories plus `(per transaction overhead)`; `load_vocabulary(conn)` returns all 10, so `validate_set` accepts a forecast whose `product_category == "(per transaction overhead)"`.
- **Story:** As a forecast consumer, I want the forecast vocabulary to be exactly the pinned taxonomy's real product categories, so a forecast can never be emitted or stored for a register-overhead pseudo-row or a stale taxonomy version.
- **Acceptance criteria:**
  - `load_vocabulary` sources the category set from `product_taxonomy` for the given `taxonomy_version` (the pinned `TAX-2023H1-v1` by default), returning exactly the 9 product categories; it honours (does not discard) `taxonomy_version` and fails closed when the version is unknown.
  - The register-overhead row is excluded from the forecast category domain; `validate_set` rejects a record whose category is `(per transaction overhead)`.
  - The DB forecast→category domain is pinned to real categories (e.g. FK to a product-category/taxonomy table rather than `throughput_standards`, or an exclusion of the overhead row), so the database cannot store an overhead forecast; covered by a `spec: F8.ac1` regression test (Python and DB-gated).

### BUG-061 — F8/CS-012: `load_store_hours` keeps only the first weekday's window and ignores non-zero open-time minutes
- **Feature:** [F8](./FEATURES.md) (CS-012) · **Priority:** P2 · **Size:** S · **Status:** 🐞 Open · **Type:** Correctness (latent) · **Found:** 2026-11-21 (Claude review of CS-012)
- **Description:** `load_store_hours` builds one `StoreOpenWindow` per store via `grouped.setdefault(location_id, (open_time.hour, close_hour, set()))`, so the open/close hours come from whichever weekday row is read first and all other rows only contribute their weekday to `open_weekdays`. A store with different hours by day (e.g. shorter Sundays) is validated against a single day's window. Separately, the close time is rounded up for a non-zero minute (20:30 → 21) but the open time is floored to its hour, so a `06:30` open would wrongly admit a `06:00` pre-open forecast. Latent today because the seeded three stores share uniform, on-the-hour open times.
- **Story:** As a forecast validator, I want each store's open window to reflect its actual per-weekday open and close times, so a forecast is accepted only within the hours that store is genuinely open on that day.
- **Acceptance criteria:**
  - Store hours are modelled per `(location_id, weekday)` (not one window per store), and F8.ac5 validates a record against the window for that record's local weekday.
  - Open-time minutes are handled consistently with close-time minutes (an `HH:30` open excludes the `HH:00` slot), documented in `docs/contracts/forecast-output.md`.
  - A regression test tagged `spec: F8.ac5` covers a store with differing weekday hours and a non-zero open-minute boundary.

### BUG-062 — F8/CS-012: migration `0017` forecast hardening assumes an empty `forecasts` table
- **Feature:** [F8](./FEATURES.md) (CS-012) · **Priority:** P2 · **Size:** S · **Status:** 🐞 Open · **Type:** Operational safety · **Found:** 2026-11-21 (Claude review of CS-012)
- **Description:** `0017` applies `SET NOT NULL`, a CHECK, two FKs and a unique index directly to `forecasts`. This is safe now because no producer writes forecasts yet, but once CS-014/F9 (or a loaded environment) has rows, the migration will either fail outright (a null, an out-of-range interval, an unknown category/version, or a duplicate live key) or lock the table while validating. There is no pre-check, backfill, or `NOT VALID … VALIDATE CONSTRAINT` staging.
- **Story:** As an operator migrating a populated database, I want the forecast-hardening migration to apply without a long lock or a hard failure on existing data, so enabling the contract does not require downtime or manual cleanup.
- **Acceptance criteria:**
  - A documented pre-migration validation query (or migration step) reports rows that would violate each new constraint before they are enforced.
  - The constraints are added with a non-blocking strategy where applicable (e.g. `ADD CONSTRAINT … NOT VALID` then `VALIDATE CONSTRAINT`, `CREATE UNIQUE INDEX CONCURRENTLY`), or the migration documents why an exclusive lock is acceptable.
  - The runbook states the expected behaviour when pre-existing rows violate the contract (fail-fast vs. quarantine), cross-referenced from `docs/contracts/forecast-output.md`.

### BUG-063 — CI: `check_test_first.py` is a no-op under the `coffee-scheduler/` repo layout
- **Area:** CI gate (`test/SKILL.md` job 2) · **Priority:** P2 · **Size:** XS · **Status:** 🐞 Open · **Type:** CI integrity (silent pass) · **Found:** 2026-11-21 (Claude review of CS-012)
- **Description:** `scripts/ci/check_test_first.py` compares `git log … --name-only` paths (which are repo-root-relative, e.g. `coffee-scheduler/contracts/forecast.py`) against `PRODUCTION_PREFIXES` that omit the `coffee-scheduler/` segment (`"contracts/"`, `"db/"`, …). No path ever matches, so the guard reports "no production files added; nothing to order" for every PR and never enforces test-first. Observed on this PR, which added `contracts/` and `db/migrations/` yet the guard saw no production files.
- **Story:** As a reviewer, I want the test-first guard to actually detect production files in this repo's `coffee-scheduler/` layout, so a PR that lands implementation before its tests is caught.
- **Acceptance criteria:**
  - The guard normalises paths (strips the leading `coffee-scheduler/`) or anchors prefixes to the real tree so `contracts/`, `db/`, `api/`, etc. match.
  - A self-test proves a commit adding production code before its test fails the guard, and a correctly ordered pair passes.
  - The existing excluded prefixes (`test/`, `scripts/ci/`, `docs/`, `db/migrations/versions/`) keep their current meaning after normalisation.

### BUG-064 — CI: test/runtime dependencies (`pytest`, `PyYAML`, `httpx`) are undeclared and only patched inline in CI
- **Feature:** [F6](./FEATURES.md) (test tooling) · **Priority:** P2 · **Size:** XS · **Status:** ◑ Mitigated (inline in CI) · **Type:** Dependency hygiene · **Found:** 2026-11-21 (first real CI run, CS-012)
- **Description:** The suite needs `pytest` (runner), `PyYAML` (`test_cs038_otel_collector.py`) and `httpx` (Starlette/FastAPI `TestClient`), none of which are in `requirements.txt` or `otel/requirements.txt`. The first real CI run failed three times in a row as each surfaced; they were patched by inline `pip install` lines in the `tests`/`migration-roundtrip` jobs. Local runs only passed because those packages happened to be present globally. The inline pins can drift from local environments.
- **Story:** As a contributor, I want the project's test dependencies declared and pinned in one managed file, so local and CI environments install the same versions and a new test dependency is added in one obvious place.
- **Acceptance criteria:**
  - Add `coffee-scheduler/requirements-dev.txt` pinning at least `pytest`, `PyYAML`, `httpx` (and audit for `psycopg-pool`), installed by both Postgres CI jobs in place of the inline pins.
  - `AGENTS.md` documents installing dev requirements before running the suite.
  - A clean environment (only `requirements.txt` + `requirements-dev.txt`) collects and runs the full suite without `ModuleNotFoundError`.

### BUG-065 — F8/CS-012: PostgreSQL interval constraint accepts `NaN` and infinity values
- **Feature:** [F8](./FEATURES.md) (CS-012) · **Priority:** P1 · **Size:** XS · **Status:** 🐞 Open · **Type:** Database contract-integrity gap · **Found:** 2026-11-21 (review of CS-012)
- **Root cause:** Migration `0017_cs012_forecast_contract.py` constrains intervals with only `lower >= 0 AND lower <= p70 AND p70 <= upper`. PostgreSQL `numeric` supports special values. Its ordering semantics treat `NaN` as equal to itself and greater than ordinary numbers, so tuples such as `(NaN, NaN, NaN)` and `(0, NaN, NaN)` satisfy these comparisons. Positive infinity can also satisfy the ordered bounds. The Python `_validate_interval_value` rejects non-finite `Decimal`s, but direct SQL, another writer, or a path that skips `validate_set` bypasses that check.
- **Impact:** Non-finite demand can be persisted despite F8.ac3 requiring rejection. Downstream forecast consumers may fail during arithmetic, serialization, or optimization, and the database currently reports the row as valid.
- **Reproduction:** With a valid tenant, store, category, and taxonomy version, insert a forecast whose interval values are `NaN` (or whose `p70` and `upper` are `Infinity`). The current CHECK expression can evaluate true even though the Python validator rejects those values.
- **Story:** As a forecast consumer, I want PostgreSQL to reject non-finite interval values as well as the Python contract validator, so no writer can persist a forecast that the contract considers invalid.
- **Acceptance criteria:**
  - A new reversible migration adds a database constraint that rejects `NaN`, `Infinity`, and `-Infinity` independently in each of `lower`, `p70`, and `upper`, while continuing to accept finite ordered bounds.
  - A DB-backed regression proves the all-`NaN` tuple, `p70=NaN`, and positive/negative infinity in each interval position are rejected by PostgreSQL. Every attempted row uses valid foreign keys so FK failures are excluded from what is under test; tuples that satisfy the `0017` ordering CHECK assert the violated constraint is `forecasts_interval_finite` (proving the new constraint, not the old ordering CHECK, does the rejecting), while special values that also violate `0017` (every `-Infinity` placement and bound-inverting NaN/`Infinity`) may report either named check and are attributed to the finite predicate separately.
  - The existing Python interval validator and valid finite boundary cases remain unchanged and pass; migration upgrade/downgrade/upgrade remains reversible.
- **Plan:** [BUG-065 plan](./docs/plans/BUG-065-forecast-finite-numeric.md).
- **Resolution (implemented, pending review):** Migration `0021_bug065_forecast_finite_interval` adds the reversible, per-column `forecasts_interval_finite` CHECK (`v <> 'NaN'/'Infinity'/'-Infinity'` for each of `lower`, `p70`, `upper`), preceded by `lock_timeout` + `LOCK TABLE ... ACCESS EXCLUSIVE` and a preflight that raises an actionable error on pre-existing non-finite rows; downgrade drops only the new constraint. Regressions: `test_database_rejects_non_finite_interval_values` (NaN/±Infinity in each column, ordering-passing tuples attributed to the new constraint by name, finite boundaries accepted) and `test_migration_0021_preflight_rejects_non_finite_legacy_rows` (atomic preflight failure). Verified on disposable PostgreSQL 16.15 — focused contract suite 12/12, migration suite 3/3, upgrade→downgrade→upgrade reversible. Python `_validate_interval_value` unchanged.
