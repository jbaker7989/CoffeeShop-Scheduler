# CS-016a — Solver input assembly & data adapters (Plan)

## Goal and verified starting point

CS-016a is the first story of the CS-016 CP-SAT solver umbrella (F11). It loads and validates
**every enumerated solver input** into typed, immutable Python structures — nothing implicit —
and produces **one** validated `SolverInputs` object that CS-016b (hard constraints), CS-016c
(objectives) and CS-016d (runner) consume. It introduces **no solver logic and no new tables**;
it is a read-and-validate adapter layer over the canonical Postgres schema and the CS-012
forecast contract.

Verified on `origin/main` (`4dfb175`):

- **Forecast contract** — `contracts/forecast.py` exposes `CONTRACT_VERSION`,
  `PINNED_TAXONOMY_VERSION`, `CANONICAL_TZ`, frozen `ForecastRecord`/`ForecastSet`/`StoreOpenWindow`,
  `validate_set`, `from_row`, `missing_keys`, `load_vocabulary(conn, *, taxonomy_version=…)`,
  and `load_store_hours(conn) -> dict[int, StoreOpenWindow]`. Reuse these; do not reimplement.
- **Data access** — `data_access/session.py` provides the RLS-scoped, pooling-safe session
  (`SET LOCAL ROLE` + transaction-local `app.tenant_id`). `data_access/repository.py` /
  `tables.py` show the typed-read + composite-PK conventions. All reads go through a session
  opened `role="app_ro"` (adapters never write).
- **Canonical tables** (schema `docs/schema.md` §3) the adapters read, all tenant-scoped + RLS +
  soft-delete (`deleted_at IS NULL` filter on every read):
  - `stores` (PK `tenant_id,location_id`) + `store_hours` (PK `+day_of_week`:
    `open_time, close_time, pre_open_minutes, post_close_minutes, first_staff_in, last_staff_out`).
  - `stations` (PK `tenant_id,station_id`: `location_id, station_type, staffed_by_role`).
  - `fixed_tasks` (PK `tenant_id,task_id`: `location_id, task, role, duration_minutes, timing, frequency`).
  - `coverage_rules` (PK `tenant_id,rule_id`: `rule_id, location_id NULL=all, description, value, unit, basis`).
  - `shift_rules` (PK `tenant_id,rule_id`: `rule_id, description, value, unit, basis`).
  - `cost_targets` (PK `tenant_id,location_id`: `target_labor_pct_of_sales, weekly_overtime_budget_usd,
    understaffing_penalty_weight, premium_penalty_weight`).
  - `throughput_standards` (PK `tenant_id,product_category`: `prep_role, seconds_per_unit,
    units_per_labor_hour`; **includes the `(per transaction overhead)` pseudo-row** — keep it,
    flag it, never let it leak into a demand category).
  - `employees` (PK `tenant_id,employee_id`: `role, skill_level, home_location_id,
    cross_location_eligible, is_key_holder, cash_authorised, food_protection_cert, hourly_rate,
    availability_persona, min_hours_week, max_hours_week, dob, is_enrolled, school_calendar_id,
    grade_band, has_other_employer`).
  - `school_calendars` (PK `tenant_id,calendar_id`) + `school_calendar_days`
    (PK `tenant_id,calendar_id,date`: `in_session, day_before_school_day`).
  - `forecasts` (CS-012 contract columns) via `contracts.forecast`.
- `agents/scheduler/` is an **empty stub** — this story populates it. No ortools yet.

**Hourly demand** in the CS-016 enumeration is the **forecast output itself** (per category,
per store, per clock-hour); there is no separate `hourly_demand` table in Postgres. Document this
mapping explicitly: the forecast adapter is the demand adapter.

**Note (not a blocker for this story):** the CS-014b forecaster currently fails its CS-014c
accuracy gate, so a *real* forecast is placeholder-quality. CS-016a adapts the forecast **shape**
(the stable CS-012 contract), not its accuracy, so it is safe to build now. The adapter must work
against any contract-valid `ForecastSet`.

## Design decisions

- **One package: `agents/scheduler/inputs.py`** (+ `agents/scheduler/__init__.py`). Frozen
  dataclasses throughout (mirror `contracts/forecast.py` and `data_access/tables.py`).
- **Typed, fail-closed errors.** `SolverInputError(Exception)` base + specific subclasses
  (`MissingInputError`, `InvalidInputError`, `ReferentialError`). **Every message names the
  offending table and key.** Fail fast — never return a partially-built input object.
- **Money/rates = `Decimal`** (columns are `numeric`); quantities int; times tz-aware or
  `datetime.time`. No float money.
- **Pure validation, injected data.** Each adapter has a thin DB **loader** (RLS session →
  rows) and a **pure builder/validator** (rows → typed records, raises on bad data). The pure
  half is unit-testable with plain dict rows and **no DB** — this is the D-pattern the repo
  requires and mirrors CS-012.
- **Soft-delete aware.** Every read filters `deleted_at IS NULL`.
- **Store/category/role vocabularies are referenced, not hard-coded.** Allowed locations come
  from `stores`; allowed categories from `load_vocabulary`; the `(per transaction overhead)`
  pseudo-row is detected by its literal category string and kept on a dedicated field.
- **No new dependencies.** stdlib + psycopg + the existing `contracts`/`data_access` packages.
- **Read-only.** Adapters open the session `role="app_ro"`; they never INSERT/UPDATE.

## Deliverables (all under `coffee-scheduler/`)

### 1. `agents/scheduler/__init__.py` + `agents/scheduler/inputs.py`
Frozen dataclasses (immutable, slots where natural):
- `StoreInput(location_id, store_location, max_staff_on_floor, stations, open_windows: dict[int, StoreOpenWindow])`
- `StationInput(station_id, location_id, station_type, staffed_by_role)`
- `FixedTaskInput(task_id, location_id, task, role, duration_minutes, timing, frequency)`
- `CoverageRuleInput(rule_id, location_id|None, description, value, unit, basis)`
- `ShiftRuleInput(rule_id, description, value, unit, basis)`
- `CostTargetInput(location_id, target_labor_pct_of_sales, weekly_overtime_budget_usd, understaffing_penalty_weight, premium_penalty_weight)`
- `ThroughputInput(product_category, prep_role, seconds_per_unit, units_per_labor_hour)` plus a
  separate `per_transaction_overhead: ThroughputInput | None`.
- `EmployeeInput(employee_id, role, skill_level, home_location_id, cross_location_eligible,
  is_key_holder, cash_authorised, food_protection_cert, hourly_rate, availability_persona,
  min_hours_week, max_hours_week, dob, is_enrolled, school_calendar_id|None, grade_band,
  has_other_employer)`.
- `SchoolCalendarInput(calendar_id, days: dict[date, bool])` (date → in_session), with a
  `roster_calendar_ids` cross-check.
- `DemandCell(location_id, product_category, forecast_hour, p70, lower, upper)` built from the
  `ForecastSet` records (reuse `ForecastRecord`; do not duplicate the contract).
- **`SolverInputs`** — the one aggregate object: stores, stations, fixed_tasks, coverage_rules,
  shift_rules, cost_targets, throughput (+overhead), employees, school_calendars, demand
  (`ForecastSet`), plus `target_window: tuple[date, date]`, `taxonomy_version`, `allowed_categories`,
  `allowed_locations`.

Per-adapter functions, each `build_*` (pure) + `load_*` (DB), e.g.
`load_stores(conn)`/`build_stores(rows, hours_rows)`, …, `load_employees`/`build_employees`,
`load_school_calendars`/`build_school_calendars`, and a demand adapter
`load_demand(conn, *, target_window, model_version)` returning a validated `ForecastSet`
(load `forecasts` rows → `from_row` → `validate_set` with `load_vocabulary`/`load_store_hours`).

Top-level **`assemble_solver_inputs(conn, *, target_window, model_version, taxonomy_version=PINNED_TAXONOMY_VERSION) -> SolverInputs`**:
runs every loader+builder, then cross-validates and returns the aggregate. Tenant id is taken
from the session GUC (reuse the `current_setting` pattern); raise `SolverInputError` if unset.

**Cross-validation (fail-closed) — the "nothing implicit" guarantee:**
- Every `stations.location_id`, `fixed_tasks.location_id`, `cost_targets.location_id`,
  `employees.home_location_id`, and every forecast `location_id` resolves to a `stores` row.
- Every forecast `product_category` and every `throughput_standards` category (excluding the
  overhead pseudo-row) is in `load_vocabulary(conn)`; and every forecast category has a
  throughput row (labor conversion exists) — this is the cross-consumer check CS-009 asked for.
- Every store has a `cost_targets` row and at least one `store_hours` row; every open store-day
  the forecast covers has hours.
- Every enrolled employee's `school_calendar_id` resolves to a loaded `school_calendars` row;
  `is_enrolled == (school_calendar_id is not None)`.
- `min_hours_week <= max_hours_week` for every employee (defense-in-depth; DB also checks).
- The demand `ForecastSet` passes the canonical `validate_set`; and its key set has no gaps for
  the `target_window` open hours (`missing_keys` empty) — or, if gaps are allowed for a partial
  window, they are surfaced on a `SolverInputs.demand_gaps` field, never silently dropped.
- A **CLI** `main(argv)` (argparse: `--database-url`, `--tenant-id`, `--model-version`,
  `--target-window-start/-end`) that assembles and prints a one-line summary of counts
  (stores/stations/employees/demand cells) — proves end-to-end load.

### 2. `test/test_cs016a_inputs.py`
`unittest` style, **ends with `if __name__ == "__main__": unittest.main()`**, passes under both
`python3 -m unittest discover -s test` and `pytest`. Each test tagged `# spec: F11.acN`.
- **Offline unit tests** (no DB) for every pure builder/validator with plain dict rows: a valid
  build passes; each failure mode raises the specific typed error naming the table/key
  (unknown location, unknown category, missing throughput for a forecast category, enrolled
  employee with no calendar, min>max hours, overhead pseudo-row handling, duplicate keys,
  contract-invalid demand).
- **DB-gated integration test** (skip-gate: probe `psycopg.connect(DSN, connect_timeout=3)`,
  `@unittest.skipIf`): under `app_ro`/`app_rw` + `maven-demo` tenant via
  `data_access.session.open_session_context`, **rollback-only** (`conn.transaction()` + a
  `_Rollback` sentinel) — seed a minimal forecast via `model.persist_forecasts` or a direct
  insert, run `assemble_solver_inputs`, and assert the aggregate is well-formed and the
  cross-checks bite (e.g. a forecast category with no throughput row fails). Reuse the exact
  rollback pattern from `test/test_cs014b_model.py`.
- **Migration round-trip** is unaffected (no new migration) but the suite must stay green.

### 3. `docs/sdd-cs016a.md`
Numbered ACs **F11.ac1–ac7** with the AC→test-name map (feeds `check_spec_traceability.py`):
1. `ac1` every enumerated input has a typed adapter and resolves into one `SolverInputs`.
2. `ac2` referential integrity: every location/category/calendar FK resolves or fails by name.
3. `ac3` forecast demand is contract-valid (`validate_set`) and gap-aware (`missing_keys`).
4. `ac4` throughput coverage: every forecast category has a labor-conversion row; overhead
   pseudo-row isolated.
5. `ac5` employee integrity: enrolment↔calendar, min≤max, roster calendars loaded.
6. `ac6` fail-fast: a missing/mistyped input raises a typed error naming the table; no partial object.
7. `ac7` read-only + RLS: adapters open `app_ro`, filter `deleted_at IS NULL`, honor tenant GUC.

### 4. `FEATURES.md` (F11) + `Backlog.md` (CS-016a)
- `FEATURES.md` **F11**: add a short `- **CS-016a (input adapters, done):** …` bullet pointing at
  `docs/sdd-cs016a.md` (F11.ac1–ac7). Do **not** flip F11's status to Done (solver unbuilt) and do
  **not** add an inline numbered `**ACn**` block. No hedge words (`etc./TBD/TODO/and so on/and more`).
- `Backlog.md` **CS-016a**: status → `✅ Done (2026-11-22)` with a one-paragraph summary (adapters,
  one validated `SolverInputs`, cross-checks, tests). Leave every other entry untouched; in
  particular do **not** edit CS-014b/CS-014c or any BUG row.

## Orchestration & roles (this story)

- **Codex develops + writes the tests** (`codex exec` in this worktree), to this plan and
  `docs/sdd-cs016a.md` exactly.
- **Claude independently runs the three test categories and reviews** the diff against the repo's
  D1–D9 defect patterns before accepting; fixes or returns defects.
- **Test-first commit ordering** so `check_test_first.py` (production prefix `agents/`) passes:
  **commit 1** = `test/test_cs016a_inputs.py` + `docs/sdd-cs016a.md` + `FEATURES.md` (failing);
  **commit 2** = `agents/scheduler/` implementation + `Backlog.md`. (docs/FEATURES are not
  "production"; `agents/` is — so the test commit precedes the code commit.)

## The three test categories (all must pass before commit/push)

1. **Unit (offline)** under **both** runners: `python3 -m unittest discover -s test` and
   `python -m pytest test/test_cs016a_inputs.py -q`.
2. **DB-gated integration** (rollback-only) against live Postgres: `docker compose up -d`,
   `alembic -c alembic.ini upgrade head`, re-seed
   (`python ../scripts/etl_excel_to_postgres.py … --tenant-slug maven-demo` **and**
   `python scripts/seed_rule_templates.py`), then run the DB test.
3. **Static gates + round-trip**: `ruff check --config ruff.toml <new .py>` clean;
   `check_spec_traceability.py`, `check_test_first.py`, `spec_lint.py`, `review_checklist.py`
   (all `--base origin/main`); `pytest test/test_migrations_roundtrip.py -q`.

## Verification (acceptance)

CS-016a is done when, on branch `cs016a-input-adapters`: F11.ac1–ac7 tests pass under both
runners; the DB-gated test and migration round-trip pass against live Postgres; `ruff` is clean
on new files; all four CI scripts pass locally; and `assemble_solver_inputs` returns one
validated `SolverInputs` with every referential cross-check enforced. Then commit (with the
attribution footer) and push to GitHub.

## Out of scope (deliberately)

CP-SAT variables/constraints (CS-016b), the objective function (CS-016c), the solver runner and
output object (CS-016d), any forecast-model change (CS-014b), and any new migration. CS-016a is
only the typed, validated input boundary.
