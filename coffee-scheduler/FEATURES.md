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
- **Status:** Partially scaffolded (stubs exist) · **Size:** M · **Priority:** P1
- **Description:** Flesh out the root AGENTS.md (stack, layer split, data rules, test commands) and per-agent AGENTS.md files, plus the reusable SKILL.md procedures — chiefly `labor-rule-evaluation` (strictest-wins, effective dating, minor age-on-date), and `synthetic-roster-generation`, `sales-weather-join`, `standards-calibration`, `agent-card-authoring`.
- **Expected outcome:** Agents and humans share one set of conventions; the rule-evaluation procedure is written once and reused, not duplicated per agent.

### F7. Agent Cards (A2A contracts)
- **Status:** Scaffolded (stub JSON; Payroll/HCM has no directory)
- **Size:** M · **Priority:** P1
- **Description:** Author real A2A Agent Cards for Scheduler, Demand Forecast, Labor Compliance, Staff Comms and Payroll/HCM, with capabilities, auth, and versioning per the `agent-card-authoring` conventions. Add placeholder cards for Inventory, Prep and Loyalty (Phase 2/3). A2A is used only across real service boundaries. **Payroll/HCM is named in F16 as an n8n export target but has no `agents/payroll_hcm/` directory** — decide explicitly here: either create the runtime agent (and its LangGraph node in F12) or document that payroll is n8n-only and F7 doesn't own a card for it.
- **Expected outcome:** Every runtime boundary has a versioned, authenticated contract; one agent built in a second framework proves interoperability; the Payroll/HCM question is settled (runtime agent or n8n-only).

### F8. Demand Forecast output contract
- **Status:** In design · **Size:** S · **Priority:** P1
- **Description:** Define the forecast output schema: prediction by product category, per hour, per store, with prediction intervals (P70 and bounds). This is the interface the solver and explanation layer consume regardless of the underlying model. Category vocabulary is owned by **F29** — the contract must reference the versioned taxonomy, not invent its own.
- **Expected outcome:** A stable contract that decouples model choice from downstream consumers and pins the category taxonomy.

---

## Phase 1 — Core engine (deterministic)

### F9. Demand forecast model
- **Status:** Blocked (on F1, F5, F8) · **Size:** L · **Priority:** P1
- **Description:** Choose, train and validate a statistical/ML forecast model on 6 months of 2023 sales joined with weather. Must handle the data limits openly: no annual seasonality (6 months), no basket ID, no evening sales history. Produce per-category hourly forecasts with intervals per F8; define a validation/backtest approach.
- **Expected outcome:** A forecaster that drives staffing need per hour with honest uncertainty — the demand input to the solver.

### F10. Labor-compliance rule engine
- **Status:** Not started (rules table exists, needs verification) · **Size:** L · **Priority:** P1
- **Description:** Build the engine that evaluates the effective-dated rules with strictest-wins resolution and exposes them as hard constraints to the solver and as explanations to the LLM. Covers meal breaks, spread-of-hours (Part 146), Fair Workweek premiums & clopening (per-employer flag), Earned Safe/Sick Time (no requirement for a sick employee to find cover), Food Protection Certificate on site, and minor hours/occupation limits computed from DOB on the shift date. **Sub-tasks the engine must own explicitly:**
  - **Rule_Eval_Log write path** — every evaluation appends to `Rule_Eval_Log` in `coffee_shop_labor_rules.xlsx` (eval_id, schedule_id, shift_id, employee_id, rule_id, result, premium_cost_usd, evaluated_at, agent). No silent passes.
  - **Waiver expiry evaluation** — for any rule marked `waivable_by_consent`, re-check `Consent_Waivers.valid_from` / `valid_to` against the shift date on every shift generation, not just at rule load. Example: WV-0001 (EMP-1033, NY-MINOR-1617-SCH-WINDOW) expires 2027-06-26.
  - **Coverage_Rules ↔ Labor_Rules reconciliation** — `Coverage_Rules.COV-FPC` says "LAW — see NYC-FPC-COVERAGE"; the engine must fail closed if a coverage rule and a legal rule ever disagree about whether a constraint is HARD vs SOFT.
- **Expected outcome:** A single authoritative legality service that writes every decision to Rule_Eval_Log, honours waiver expiry on the actual shift date, and never silently contradicts the coverage rules — the solver and LLM never compute legality themselves.

### F11. CP-SAT schedule solver
- **Status:** Blocked (on F2, F4, F9, F10) · **Size:** L · **Priority:** P1
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
- **Status:** Not started · **Size:** M · **Priority:** P1
- **Description:** Define LangGraph state and nodes for the schedule-generation flow: gather inputs → call forecast → call compliance → run solver → present result. LangChain wraps tools and prompt templates; the LLM routes between tools and never generates numbers.
- **Expected outcome:** A runnable orchestration that turns a request into a schedule end to end.

### F13. Schedule explanation layer
- **Status:** Not started · **Size:** S · **Priority:** P2
- **Description:** LLM-generated plain-language explanations of schedule decisions ("why this person at this time", "why no minor on close"), each citing the specific rule or constraint that forced it, drawing on F10's output.
- **Expected outcome:** Trust and transparency for managers; makes the deterministic engine legible.

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
- **Status:** Not started (scaffold exists, not wired) · **Size:** M · **Priority:** P1
- **Description:** Claim ownership of `coffee_shop/app/` as the manager-facing surface for the scheduler. This feature owns the decisions the screens below share: Next.js → backend integration pattern (server actions vs API routes vs n8n-mediated), manager auth (single-tenant demo OK to start), the shared layout / navigation, and the convention for how each screen binds to the LangGraph state in F12. Each screen is a separate feature (F31–F35); this is the plumbing they all use.
- **Expected outcome:** A scaffolded Next.js app a manager can sign into, with shared navigation and a documented integration pattern the screens below can build on.

### F31. Weekly schedule view
- **Status:** Not started · **Size:** M · **Priority:** P1
- **Description:** Per-store, per-day weekly grid showing shift assignments, station coverage and meal-break timing. Hover (or click) on any shift surfaces the rule IDs and `Rule_Eval_Log` reasons that placed it there (from F10). Read-only — edits go through the solver (F11) or the call-out flow (F32). Must handle multi-week navigation and filter by store / role / status. Backed by F12's LangGraph state for the active week.
- **Expected outcome:** A manager can open a store's week, see who works when, and on hover learn exactly which rule made that placement.

### F32. Call-out approval
- **Status:** Not started · **Size:** M · **Priority:** P2
- **Description:** UI surface for the F14 call-out/swap LangGraph workflow. Manager sees the sick employee's shift, the candidate cover the workflow proposed, the eligibility reasons (skills, hours, minor limits, key-holder/FPC), and approves or redirects. The workflow's hard-stop branches (e.g. `NYC-ESSTA-NO-REPLACE`) must be visible inline so the decision is auditable. Approving routes back to F16 to update payroll/HCM and to F10 to log the resolution. Two-way binding to LangGraph state — the screen is the human-in-the-loop step the workflow already waits on.
- **Expected outcome:** A manager can review and approve a replacement in one screen, with the legal reasons visible at decision time.

### F33. Forecast review
- **Status:** Not started · **Size:** M · **Priority:** P2
- **Description:** Chart overlay of P70 forecast vs. actuals by store × day × hour × category (per F8 contract). Used to (a) sanity-check the forecaster before trusting it, and (b) drive calibration cycles (F5). Back-test period selectable; click-through to per-hour detail with the weather join from F1 visible alongside. Read-only — calibration actions route through F5.
- **Expected outcome:** A manager can see "did the forecast match the day" and feed back into calibration without leaving the app.

### F34. Rules admin
- **Status:** Not started · **Size:** M · **Priority:** P1
- **Description:** View / edit `Labor_Rules` rows with the F26 reconciliation check running live: any change that creates an assumption-vs-law conflict, marks an unverified rule as `LAW`, or sets coverage-rule effective dates that conflict with the underlying legal rule is blocked at submit. Surface the `Consent_Waivers` list with expiry warnings (per F10 waiver-expiry sub-task) so managers see WV-0001 expiring 2027-06-26 before it does. All edits route through F28 (rule-change rollout + `Rule_Eval_Log` audit row).
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
- **Status:** Not started · **Size:** M · **Priority:** P2
- **Description:** Adopt OpenTelemetry across the agent platform. This umbrella owns: OTel SDK version pin, OTel Collector deployment shape, W3C `traceparent` propagation contract across every A2A boundary, custom metric vocabulary (solver solve-time, rule-eval counts, LangGraph state transitions), sampling policy, and the Docker Compose file that ships the Collector + Tempo + Mimir + Loki + Grafana together. F18's "one trace ID across all A2A calls, with authentication and timeouts on each boundary" is the goal; this feature picks OTel to deliver it.
- **Expected outcome:** An A2A call across any two agents shows up in Tempo with one `trace_id`; per-call RED metrics land in Mimir; structured logs land in Loki — all visible from one Grafana pane.

### F37. OTel SDK instrumentation per agent
- **Status:** Not started · **Size:** M · **Priority:** P2
- **Description:** Each runtime agent (Scheduler, Demand Forecast, Labor Compliance, Staff Comms) and the CP-SAT solver emit OTel spans for: every A2A call (client + server), every tool invocation, every LangGraph node transition, every solver iteration, every rule evaluation, every SMS send / receive. Custom spans for `solver.solve_time_ms`, `rule_eval.count{rule_id,result}`, `forecast.quantile_p70`. Trace context propagated via W3C `traceparent` on every A2A boundary. Each agent's `AGENTS.md` lists the spans it emits.
- **Expected outcome:** Every agent boundary, tool call and internal step is a span — all sharing one `trace_id` across the call chain.

### F38. OTel Collector deployment
- **Status:** Not started · **Size:** S · **Priority:** P2
- **Description:** Deploy the OTel Collector as a single Docker Compose service. Receivers: OTLP/gRPC + OTLP/HTTP on standard ports. Processors: tail-based sampling (keep 100% of errors, 10% of success), batch, resource attributes (`service.name`, `service.version`, `deployment.environment`). Exporters: Tempo (traces), Mimir via `prometheusremotewrite` (metrics), Loki (logs). Config versioned at `coffee-scheduler/otel/collector-config.yaml`.
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
- **Status:** Not started · **Size:** M · **Priority:** P0
- **Description:** Move the project off `.xlsx` as the source of truth onto PostgreSQL. This umbrella owns: the Postgres version pin, the connection-string convention (`.env.example`, never committed), the migration tool (Alembic), the role / user model (read-only agent role, read-write agent role, manager role), and the path from current workbooks to schema-versioned tables. **F23 (data directory decision) is settled here: Postgres is the canonical store.** The Excel files become fixtures and exports.
- **Expected outcome:** Every agent reads from and writes to Postgres; Excel is a derived artefact, not a source.

### F43. Postgres provisioning & migrations
- **Status:** Not started · **Size:** S · **Priority:** P0
- **Description:** Postgres 16 deployed via Docker Compose for local dev. Schema migrations managed by Alembic — one revision per change, with `upgrade` and `downgrade` scripts. Connection pooling via PgBouncer in front of Postgres (single Compose service). Backup / restore runbook at `coffee-scheduler/docs/postgres-backup.md` — at minimum a `pg_dump` schedule via n8n, and a documented restore drill.
- **Expected outcome:** A fresh checkout can `docker compose up postgres && alembic upgrade head` and have a working database.

### F44. ETL: Excel → Postgres
- **Status:** Not started · **Size:** M · **Priority:** P0
- **Description:** Idempotent ETL script (`scripts/etl_excel_to_postgres.py`) that reads each `coffee_shop_*.xlsx` and `weather_forecast.xlsx` and writes to the Postgres schema defined in F47. Uses `COPY` for the big tables (`transactions`, `weather_hourly`, `weather_daily`, `rule_eval_log`). Validates row counts after load and asserts known invariants (e.g. "every rule in `labor_rules` has a `rule_id`"; "every transaction has a valid `store_id`"). Reports per-table counts and any anomalies. Safe to re-run; uses upserts / `ON CONFLICT` everywhere. Tagged and runnable from CI on a schedule.
- **Expected outcome:** A single command migrates the current Excel data into Postgres with no manual steps and no data loss; the script is idempotent.

### F45. Agent data-access layer
- **Status:** Not started · **Size:** M · **Priority:** P1
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

## Known limitations (state openly, not features)
- Only 6 months of sales history, all from 2023.
- No basket ID in the sales data (register overhead overstated).
- No sales history for evening hours (last transaction 20:00–20:59 at every store).
- Open-Meteo API is non-commercial only (CC BY 4.0; attribution required — see F1).
- Staff, labour standards and adjustments are synthetic or assumed.
- Roster, labour rules and labour standards are committed to the repo as synthetic data (see F23 for git policy).
