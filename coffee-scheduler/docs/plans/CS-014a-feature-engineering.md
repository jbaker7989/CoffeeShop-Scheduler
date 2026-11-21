# CS-014a — Feature engineering & training dataset (Plan)

## Context

**Why this work exists.** The forecaster (CS-014b/c, F9) needs one model-ready table that
joins six months of 2023 sales with weather and the calendars, at the grain the forecast
output contract (CS-012 / F8) emits: **per store, per taxonomy category, per clock-hour**.
CS-012 pinned the *output* interface; CS-014a builds the *input* table. The trainer
(CS-014b) and backtester (CS-014c) must both read the same versioned feature set so a model
is always tied to the exact features it was trained on.

**Current state (verified against the live DB).**
- `transactions` — 149,116 line rows, `transaction_date` 2023-01-01…2023-06-30, with
  `store_id, transaction_time (time), product_category, transaction_qty, unit_price`.
- `weather_hourly` — 9,231 rows keyed `(tenant_id, location_id, date, hour)` with
  `temperature_2m, precipitation, rain, snowfall, weather_bucket, in_trading_hours` (store-
  level items/revenue, **not** per category — the per-category target comes from `transactions`).
- `product_taxonomy` — 9 real categories under `TAX-2023H1-v1`; `throughput_standards` holds
  the same 9 plus `(per transaction overhead)` (the category anchor FK, as in migration 0017).
- `school_calendar_days (date, in_session)` and `holidays (date, jurisdiction)` cover 2023–2028.
- `store_hours` — 3 stores, open 7 days: Astoria (3) 07:00–20:30; LM (5) / HK (8) 06:00–21:00.
- Canonical tenant `maven-demo` = `00000000-0000-0000-0000-000000000001`.

**Outcome.** A deterministic, RLS-scoped, versioned `forecast_features` table (+ a
`feature_sets` registry), built by `agents/demand_forecast/feature_build.py` from canonical
Postgres, 0-filled over every open store-hour, carrying calendar/weather/rolling features and
honest masks for the three known data gaps — all covered by failing-first, `F9.ac#`-tagged
tests that pass the CI gate.

**Decisions locked.** DB table (not a file artifact), Postgres-canonical. Claude develops and
tests directly (no Codex). Land on `cs-014a-feature-engineering`; PR into `main`; merge on green.

---

## Design decisions

- **Grain = the contract grain.** One `forecast_features` row per
  `(location_id, product_category, obs_date, obs_hour)` for every hour a store is open on that
  weekday (reusing `contracts.forecast.load_store_hours` semantics so features and the output
  contract agree exactly). Closed hours and non-open weekdays are **absent** — never a null row.
- **Target is per-category items.** `items = Σ transaction_qty` for that store/category/date/hour
  from `transactions`; an open hour with no sale is **0-filled**, not missing (the model must
  learn zeros; the contract omits closed hours, so we never emit one).
- **Python owns the derivation.** SQL only loads canonical rows (aggregated sales, weather,
  holidays, school days, store hours, categories). The cell universe, 0-fill, calendar flags,
  masks and lag/rolling are computed in pure stdlib Python, so the real logic is unit-testable
  offline — the DB only supplies inputs and persistence.
- **Honest gap masks** (`feature_sets` flags, all `false`): `has_basket_id` (no reliable basket
  grouping — CS-008), `has_evening_history` (sales thin out before the 21:00 close),
  `has_annual_seasonality` (6-month window). Per-cell `low_history` flags open hours whose
  window-total volume is negligible (`< 5%` of the store's busiest hour); `in_trading_hours`
  is carried through from `weather_hourly`.
- **Versioned + reproducible.** `FEATURE_VERSION = "forecast-features-v1"`. `feature_sets`
  records version, source window, taxonomy version, row count and provenance flags. A rebuild
  of the same version is idempotent (delete-then-insert within one transaction).
- **No new dependencies** — stdlib + `psycopg`, reusing `data_access.session`.

---

## Deliverables

### 1. Migration `db/migrations/versions/0019_cs014a_forecast_features.py`
`down_revision = "0018"`. Reversible. Two tenant-scoped tables mirroring migration 0001's
RLS pattern (`ENABLE` + `FORCE ROW LEVEL SECURITY`, `tenant_isolation` `NULLIF` policy, grants
to `app_rw`/`app_ro`):
- `feature_sets (tenant_id, feature_version, source_window_start, source_window_end,
  taxonomy_version, row_count, has_basket_id, has_evening_history, has_annual_seasonality,
  built_at, notes)`, PK `(tenant_id, feature_version)`, FK `(tenant_id, taxonomy_version) →
  taxonomy_meta`.
- `forecast_features (tenant_id, feature_version, location_id, product_category, obs_date,
  obs_hour, items, revenue, day_of_week, is_weekend, is_holiday, school_in_session,
  temperature_2m, precipitation, rain, snowfall, weather_bucket, in_trading_hours,
  items_lag_1h, items_roll_7d_same_hour, low_history)`, PK
  `(tenant_id, feature_version, location_id, product_category, obs_date, obs_hour)`,
  FK to `feature_sets` (ON DELETE CASCADE), `throughput_standards` (category anchor), `stores`,
  with `CHECK (obs_hour BETWEEN 0 AND 23)` and `CHECK (items >= 0)`.
- `downgrade()` drops both in reverse; proven by `test_migrations_roundtrip.py`.

### 2. Builder `agents/demand_forecast/feature_build.py`
- Constants: `FEATURE_VERSION`, `SOURCE_WINDOW = (date(2023,1,1), date(2023,6,30))`,
  `LOW_HISTORY_SHARE = Decimal("0.05")`, `FEATURE_COLUMNS`.
- Pure helpers (unit-tested offline): `calendar_features(obs_date, holidays, school_days)`,
  `low_history_flag(hour_share)`, `build_cell_universe(store_hours, categories, window)`,
  `rolling_features(cells)` (lag-1 open hour + trailing-7-day same-hour mean per store+category),
  `provenance_flags()`.
- `build_feature_set(conn, *, feature_version, taxonomy_version, window)` — runs inside a caller
  transaction with role + tenant already set; loads inputs via parameterised SQL, assembles
  rows in Python, idempotently replaces the version (delete-then-insert), upserts the
  `feature_sets` row, returns the row count.
- `main()` CLI (`--database-url/--tenant-id/--feature-version`) opens `tenant_session(role="app_rw")`
  and prints a source/target summary — matching the other `scripts/`/agent entry points.

### 3. SDD — `docs/sdd-cs014a.md`
Defines `F9.ac1 … F9.ac7` (the dataset ACs below) as `**F9.ac#**` ids the traceability gate
picks up; maps each to its test name. F9 stays **In progress** in FEATURES.md (not "Done"), so
the gate does not yet demand the CS-014b/c model ACs.

### 4. Tests — `test/test_cs014a_feature_build.py`
`unittest` style with `if __name__ == "__main__": unittest.main()` (both discover and pytest).
Offline unit tests per helper + a DB-gated `FeatureBuildDatabaseTests` (skip-gate probe) that
builds against live Postgres **rollback-only** and asserts the ACs on persisted rows.

### 5. Status — `FEATURES.md` F9 → In progress (CS-014a); `Backlog.md` CS-014a → ✅ Done.

---

## F9 acceptance criteria (CS-014a scope)

- **F9.ac1 — cell universe.** Exactly one row per `(store, taxonomy category, date-in-window,
  open clock-hour per store_hours)`; closed hours / non-open weekdays are absent.
- **F9.ac2 — target 0-fill.** An open hour with no transactions has `items = 0`; a cell's
  `items` equals the summed `transaction_qty` for that store/category/date/hour.
- **F9.ac3 — calendar features.** `day_of_week`, `is_weekend`, `is_holiday`, `school_in_session`
  are derived from `obs_date` (not `date.today()`), joined from `holidays` / `school_calendar_days`.
- **F9.ac4 — weather features.** Each cell joins its store/date/hour `weather_hourly` row; a
  cell with no weather row keeps nulls and is never dropped.
- **F9.ac5 — rolling/lag.** `items_lag_1h` is the prior open hour for the same store+category;
  `items_roll_7d_same_hour` is the trailing same-hour mean; both are null at series start and
  never fabricated.
- **F9.ac6 — honest masks.** `has_basket_id`/`has_evening_history`/`has_annual_seasonality` are
  all false; `low_history` is false for the busiest store-hour and true for at least one late
  evening hour.
- **F9.ac7 — versioned & reproducible.** `feature_sets` records version, source window,
  taxonomy version and row count; a second build of the same version is idempotent (identical
  row count, no duplicates) and returns zero rows without tenant context (RLS fail-closed).

---

## Orchestration (execution)

1. Branch `cs-014a-feature-engineering` off `main`.
2. **Commit 1 (test-first):** `test/test_cs014a_feature_build.py` (failing) + `docs/sdd-cs014a.md`
   + FEATURES.md F9 update. (Docs/FEATURES are not "production"; `agents/` is — so the test
   commit must precede the builder commit.)
3. **Commit 2 (impl):** `agents/demand_forecast/feature_build.py` + migration `0019` +
   `Backlog.md`.
4. **Local gate (all three categories), mirroring CI:**
   - **Unit:** `python3 -m unittest discover -s test` **and** `python -m pytest test/ -q
     --ignore=test/test_migrations_roundtrip.py`.
   - **DB integration:** Postgres up, `alembic upgrade head` (through 0019), run the DB-gated
     build test.
   - **Migration round-trip + CI gates:** `pytest test/test_migrations_roundtrip.py -q`; the 4
     CI scripts (`check_spec_traceability`, `check_test_first`, `spec_lint`, `review_checklist`);
     `ruff check --config ruff.toml` on the new files.
5. **On green:** push, open the PR, watch CI, **merge**, update Backlog/FEATURES status.

**Attribution:** commit footer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`;
PR body footer `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

## Edge cases explicitly covered

- Open hour with zero sales → `items = 0` (not absent, not null).
- Astoria's 20:30 close → the 20:00 hour is included, 21:00 excluded (contract parity).
- Series-start `items_lag_1h` / `items_roll_7d_same_hour` → null, never a fabricated 0.
- A store/date/hour with no `weather_hourly` row → weather columns null, cell retained.
- Idempotent rebuild (delete-then-insert within one transaction) → no duplicate-key growth.
- RLS: no tenant context → zero rows (fail-closed), proven in the DB test.
- Category vocabulary pinned to the 9 taxonomy categories; the `(per transaction overhead)`
  pseudo-category is never emitted (FK anchor is `throughput_standards`, value is a real category).

## Out of scope (deliberately)

Model selection/training (CS-014b), backtest/MAPE (CS-014c), event multipliers (CS-023),
and any solver wiring (CS-016a). CS-014a delivers only the versioned feature table + its builder.
