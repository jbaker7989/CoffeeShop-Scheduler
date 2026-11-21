# CS-012 — Demand Forecast Output Contract (Plan)

## Context

**Why this work exists.** The coffee-shop scheduler's spine is `forecast → solver → explanation`. The forecast model (CS-014/F9) is *blocked on* a pinned output interface, and the CP-SAT solver (CS-016a) and explanation layer (CS-017) must consume forecasts without knowing which model produced them. CS-012/F8 defines that stable, versioned contract so model choice is decoupled from consumers and the category vocabulary is pinned to the F29 taxonomy (`TAX-2023H1-v1`, 9 categories) rather than reinvented.

**Current state (verified).**
- A `forecasts` table already exists in migration `0001_initial_schema.py:181` — columns `tenant_id, forecast_id uuid, location_id int, product_category text, forecast_hour timestamptz, p70 numeric, lower numeric, upper numeric, model_version text, taxonomy_version text`; PK `(tenant_id, forecast_id)`; FK `(tenant_id, location_id) → stores`. **Every contract column is nullable and there are no CHECK/FK constraints on category, version, or interval ordering.** Soft-delete columns (`deleted_at/deleted_by`) were added in `0013`.
- **No typed contract, validator, JSON schema, or docs exist.** Orphan compiled artifacts (`test/__pycache__/test_forecast_contract.*.pyc`, `compliance/__pycache__/forecast_validate.*.pyc`) exist with **no `.py` sources and are not in git** — ignore/overwrite them cleanly.
- `agents/demand_forecast/` and `agents/scheduler/` are empty stubs. No solver/ortools code exists yet.
- Taxonomy is pinned via `data/build_product_taxonomy.py` (`VERSION = "TAX-2023H1-v1"`); DB tables `taxonomy_meta` (PK `tenant_id, taxonomy_version`) and `product_taxonomy` (FK → taxonomy_meta). `throughput_standards` PK is `(tenant_id, product_category)` — this is the join key between a forecast and labor conversion, and the natural vocabulary anchor.
- 9 categories: `Coffee, Tea, Bakery, Drinking Chocolate, Flavours, Coffee beans, Loose Tea, Branded, Packaged Chocolate`. 3 stores: `5` (Lower Manhattan), `8` (Hell's Kitchen), `3` (Astoria). Hours (CS-005): LM/HK 06:00–21:00, Astoria 07:00–20:30. Canonical tz **America/New_York**.

**Outcome.** A pinned `forecast-output-v1` contract: a pure Python typed model + validators (fail-closed), a version-stamped JSON envelope + JSON Schema, DB constraints that make malformed forecast rows impossible, and docs covering units, timezone and null/holiday handling — all covered by failing-first, spec-tagged tests that pass the CI gate.

**Decisions locked with the user:** Full DB hardening (new migration `0017`). Codex implements via `codex exec`; Claude independently reviews/validates. Land on branch `cs-012-forecast-output-contract` and open a PR into `main`.

---

## Design decisions

- **Contract unit = items (quantity) sold per `product_category`, per store, per clock-hour.** This is the `items` field lineage in `weather_hourly`/`Hourly_Sales_Weather`, and what `throughput_standards.seconds_per_unit` multiplies to get labor-seconds. Documented explicitly; `p70/lower/upper` are all in this unit.
- **Logical key** of a forecast record: `(location_id, product_category, forecast_hour, model_version)`. `tenant_id` + `forecast_id` are storage concerns injected at the DB edge, not part of the business object.
- **Vocabulary is referenced, not hardcoded.** Pure validators take `allowed_categories` / `allowed_locations` / `store_hours` as arguments (dependency injection); thin DB *loaders* fetch them from `product_taxonomy`/`throughput_standards`/`store_hours` under RLS. Keeps validation DB-free and unit-testable and mirrors the CS-016a "adapter" pattern. `PINNED_TAXONOMY_VERSION = "TAX-2023H1-v1"` is the documented default a producer must stamp.
- **Precision:** `p70/lower/upper` modeled as `Decimal` (columns are `numeric`) to avoid float-rounding bugs; JSON serializes Decimals as strings.
- **Fail-closed, typed errors** (mirrors `compliance/engine.py`): `ForecastContractError` base + `UnknownCategoryError`, `VersionMismatchError`, `IntervalError`, `TimezoneError`, `StoreHoursError`, `DuplicateRecordError`. Every message names the offending value. Empty `allowed_categories` ⇒ reject (never pass vacuously).
- **No new dependencies.** Everything uses stdlib (`dataclasses`, `datetime`, `zoneinfo`, `decimal`, `json`, `uuid`). JSON Schema parity is checked with a hand-rolled structural test, **not** the `jsonschema` lib.

---

## Deliverables

### 1. Python contract package — `coffee-scheduler/contracts/`
New package (`__init__.py`, `forecast.py`). Follows the repo's frozen-dataclass precedent (`compliance/engine.py`, `data_access/tables.py`).

- `CONTRACT_VERSION = "forecast-output-v1"`, `PINNED_TAXONOMY_VERSION = "TAX-2023H1-v1"`, `CANONICAL_TZ = "America/New_York"`.
- `@dataclass(frozen=True) ForecastRecord`: `location_id, product_category, forecast_hour (tz-aware datetime), p70, lower, upper, model_version, taxonomy_version`.
- `@dataclass(frozen=True) ForecastSet`: `contract_version, taxonomy_version, model_version, generated_at, records: tuple[ForecastRecord, ...]`.
- Validators (pure): `validate_record(r, *, allowed_categories, allowed_locations, store_hours)` and `validate_set(fs, *, allowed_categories, allowed_locations, store_hours)`.
- DB adapters (thin, under `data_access` session): `load_vocabulary(conn, taxonomy_version)`, `load_store_hours(conn)` — RLS-scoped reads returning the inputs the pure validators need. Plus `to_row(r, tenant_id)` / `from_row(row)` round-tripping the `forecasts` columns, and a `missing_keys(fs, window)` completeness helper for the solver.
- Serialization: `ForecastSet.to_json()/from_json()` version-stamped envelope `{contract_version, taxonomy_version, model_version, generated_at, records:[...]}`; `forecast_hour` as ISO-8601 with offset; Decimals as strings.

**Invariants → F8 acceptance criteria** (each becomes a test tag):
- **F8.ac1 vocabulary** — every `product_category ∈ allowed_categories` (the pinned taxonomy's 9); empty vocabulary ⇒ fail-closed; unknown category rejected by name.
- **F8.ac2 versioning** — a set declares one `contract_version`+`taxonomy_version`+`model_version`; every record matches; all non-empty; no mixed versions in a set.
- **F8.ac3 interval** — `lower ≤ p70 ≤ upper` and `lower ≥ 0` (Decimal); `None`/`NaN` rejected.
- **F8.ac4 timezone** — `forecast_hour` tz-aware (`tzinfo` and `utcoffset()` not None) and on an exact hour (`minute==second==microsecond==0`); naive/sub-hourly rejected; America/New_York documented as canonical.
- **F8.ac5 store + hours/holiday** — `location_id ∈ {3,5,8}`; the record's local hour falls inside that store's open window and not on a closed day (per `store_hours`). **Null/holiday policy: closed hours are omitted, never emitted as null; a record for a closed hour is rejected. No silent nulls.**
- **F8.ac6 uniqueness** — at most one record per `(location_id, product_category, forecast_hour)` in a set; duplicate key ⇒ `DuplicateRecordError`. `missing_keys()` lets consumers detect gaps.

### 2. JSON Schema + example — `coffee-scheduler/contracts/`
- `forecast_output.schema.json` (draft 2020-12), version-stamped, mirroring the envelope + record.
- `examples/forecast_output.example.json` — one golden valid payload.
- Parity test asserts schema `required`/`properties` == dataclass fields (structural, no jsonschema dep) and that the golden example round-trips via `from_json`.

### 3. DB hardening — `coffee-scheduler/db/migrations/versions/0017_cs012_forecast_contract.py`
`down_revision = "0016"`. Reversible upgrade/downgrade. On the current (empty) `forecasts` table so no backfill risk:
- `SET NOT NULL` on `location_id, product_category, forecast_hour, p70, lower, upper, model_version, taxonomy_version`.
- `CHECK (lower >= 0 AND lower <= p70 AND p70 <= upper)`.
- FK `(tenant_id, taxonomy_version) → taxonomy_meta(tenant_id, taxonomy_version)`.
- FK `(tenant_id, product_category) → throughput_standards(tenant_id, product_category)` — pins the category vocabulary at the DB (the cross-consumer check CS-009 asked for).
- Partial `UNIQUE (tenant_id, location_id, product_category, forecast_hour, model_version) WHERE deleted_at IS NULL` (respects soft-delete).
- `downgrade()` drops each in reverse; verified by the existing `test/test_migrations_roundtrip.py` (upgrade→base→head) plus targeted constraint-existence assertions.

### 4. Docs
- `docs/contracts/forecast-output.md` — canonical human contract: field table, **units (items/category/store/hour)**, **timezone (America/New_York, stored `timestamptz`)**, **null/holiday handling** (closed hours omitted; holidays forecast normally unless the store is closed; no silent nulls), **versioning + bump rules** (`contract_version` vs `taxonomy_version` vs `model_version`), the `forecasts`-table column mapping, the golden example, and consumer guidance for CS-016a (solver input adapter) and CS-017 (category names via taxonomy version).
- `docs/sdd-cs012.md` — SDD mapping `F8.ac1..ac6 → test names` (feeds spec-traceability).
- `docs/plans/CS-012-forecast-output-contract.md` — in-repo copy of this plan (the repo's per-story plan convention).
- `FEATURES.md` F8 — add an explicit `**Acceptance criteria:**` block with `ac1..ac6` (hedge-free: no "etc./TBD/TODO", or `spec_lint.py` fails). Also fix the stale **"CS-010b"** reference in CS-009 (`Backlog.md`) to **CS-012** (same artifact, two IDs).

### 5. Tests — `coffee-scheduler/test/test_forecast_contract.py`
`unittest` style, **with `if __name__ == "__main__": unittest.main()`** (avoids BUG-019's silent 0-tests). Must pass under both `python3 -m unittest discover -s test` and `pytest`.
- Offline unit tests per invariant, each tagged `# spec: F8.acN` — a valid set passes; every invariant has a failing case asserting the specific typed error + message.
- Decimal round-trip (`to_json`/`from_json` preserves exact value); `to_row`/`from_row` round-trip against the `forecasts` column list.
- Schema↔dataclass parity + golden-example validation.
- **DB-gated integration test** (skip-gate pattern from `test_api_foundation.py:38` — probe `psycopg.connect(..., connect_timeout=3)`, `@unittest.skipIf`): under `app_rw` + `maven-demo` tenant via `data_access.session.tenant_session`, insert a valid forecast through `Repository`, and assert the new constraints reject (a) `lower>upper`, (b) unknown category (FK), (c) unknown taxonomy_version (FK), (d) duplicate live key. All writes rolled back.

### 6. CI-gate alignment — `coffee-scheduler/scripts/ci/check_test_first.py`
Add `contracts/` to the production-prefix list so the test-first gate covers the new package (currently only `auth/ compliance/ data_access/ api/ agents/ db/ otel/ n8n/`). Keep CS-012 **out** of `known_gaps.txt` — every F8 AC must be genuinely covered.

---

## Orchestration (execution, after approval)

Claude orchestrates; **Codex implements** via `codex exec`; **Claude independently reviews/validates**.

1. **Branch.** `git checkout -b cs-012-forecast-output-contract` off `main` (never commit to `main`).
2. **Spec brief for Codex.** Write a focused task brief (this plan + the exact file list, invariants, the `forecasts` DDL, conventions: `unittest`+`spec:` tags + `unittest.main()`, ruff py311/line-120, failing-first, Decimal, typed fail-closed errors, **no new deps**). Confirm `codex exec` flags via `codex --help` at run start.
3. **Test-first commit ordering** (so `check_test_first.py` passes): **commit 1** = `test/test_forecast_contract.py` (failing) + `FEATURES.md` ACs + `docs/sdd-cs012.md`; **commit 2** = `contracts/` package + migration `0017` + remaining docs + the `check_test_first.py` prefix edit. (Docs/FEATURES aren't "production"; `db/` is — so the test commit must precede the migration commit.)
4. **Codex runs** each stage; Claude reviews the diff against the invariants and the repo's D1–D9 defect patterns (`test/SKILL.md`) before accepting.
5. **Full local gate before pushing** (mirror CI exactly):
   - `ruff check --config ruff.toml <new .py files>` (ratchet lints new files only).
   - `python3 -m unittest discover -s test` **and** `python -m pytest test/ -q --ignore=test/test_migrations_roundtrip.py`.
   - Bring up Postgres (`docker-compose up -d`), `alembic -c alembic.ini upgrade head` (in `db/`), seed via `python ../scripts/etl_excel_to_postgres.py --database-url ... --tenant-slug maven-demo`, then run the DB-gated test and `pytest test/test_migrations_roundtrip.py -q`.
   - Run the 4 CI scripts locally: `check_spec_traceability.py`, `check_test_first.py`, `spec_lint.py`, `review_checklist.py` (all with `--base origin/main` where applicable).
6. **Only when everything is green:** commit (with the required attribution footer), push the branch, and open a PR into `main` with `gh pr create`. Update `Backlog.md` CS-012 status (`In design → Done`) and the F8 status in the same final commit. Report the full/focused/skipped test results honestly (a skipped DB test is **not** a pass).

**Attribution** (per session policy): commit footer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`; PR body footer `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

---

## Edge cases explicitly covered (the "minimize bugs" mandate)

- Empty/whitespace `allowed_categories`, `taxonomy_version`, or `model_version` ⇒ fail-closed, never a vacuous pass.
- Naive datetimes, fixed-offset-but-wrong, and sub-hourly timestamps ⇒ `TimezoneError`; DST transition days handled via `zoneinfo` local-hour check.
- `Decimal('NaN')`/`Infinity`, negative demand, `lower==upper==p70` boundary (allowed), off-by-one at open/close hour boundaries (half-open window: `open ≤ hour < close`).
- Mixed `taxonomy_version`/`model_version` within one set; duplicate logical keys; a record for a store the taxonomy/hours don't cover.
- Closed-hour / holiday rows rejected rather than stored as null (no `NULL` demand leaks to the solver).
- DB vs Python divergence prevented by enforcing the same invariants in migration `0017` and the validators, with the DB-gated test proving the constraints actually bite.
- Precision loss across JSON/DB round-trips (Decimal-as-string).
- Soft-delete-aware uniqueness (partial unique `WHERE deleted_at IS NULL`).
- Orphan `.pyc` artifacts overwritten; `contracts/` added to the test-first prefix list so future changes stay gated.

## Verification (end-to-end acceptance)

CS-012 is done when, on the branch: all F8.ac1–ac6 tests pass under both unittest-discover and pytest; the DB-gated test and migration round-trip pass against a live Postgres; `ruff` is clean on new files; all 4 CI scripts pass locally; the PR's CI run is green; and `docs/contracts/forecast-output.md` documents units, timezone, null/holiday handling and versioning with a round-trippable golden example.

## Out of scope (deliberately)

The forecast **model**/training (CS-014/F9), the solver input **adapter** assembly (CS-016a), the explanation layer (CS-017), and event/weather demand multipliers (CS-023). CS-012 delivers only the pinned interface + its enforcement.
