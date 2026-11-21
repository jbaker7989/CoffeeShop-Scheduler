# CS-014b — Model selection & training (Plan)

## Context

**Why this work exists.** CS-014a built the versioned `forecast_features` table; CS-012 pinned
the forecast output contract. CS-014b trains the forecaster that reads the features and emits
per-category hourly **P70 + interval bounds** per the contract, persisting a versioned model
artifact bound to the exact `feature_version` it trained on. CS-014c (backtest) and CS-016a
(solver input) consume the result.

**Current state (verified).**
- `forecast_features` (migration 0019) — one 0-filled row per open store-hour × taxonomy
  category over Jan–Jun 2023, with calendar/weather/lag-rolling features and `low_history`
  masks; `feature_sets` registry carries `feature_version = "forecast-features-v1"`.
- `contracts/forecast.py` — `ForecastRecord`/`ForecastSet`, `validate_set`, `load_vocabulary`,
  `load_store_hours`, and the `forecasts` table hardened by migration 0017 (interval CHECK,
  taxonomy/throughput FKs, soft-delete-aware live-key unique).
- **numpy 2.4.6 is available; scikit-learn / scipy are NOT.** The model is numpy-only.
- Migration head is `0019`; CS-014b adds `0020`.

**Outcome.** A trainer that fits ≥2 candidate approaches, selects the better by validation
pinball loss, persists a `forecast_models` row (params + `feature_version` + metrics), and a
predict path that emits a contract-valid `ForecastSet` (optionally written to `forecasts`) — all
covered by failing-first, `F9.ac8–ac13`-tagged tests passing the CI gate.

**Decisions locked.** numpy-only; DB table for the model artifact (migration `0020`); Codex
develops (module + migration + 3-category tests) via `codex exec`; Claude authors the SDD/ACs,
reviews, and validates. Land on `cs-014b-model-training`; PR into `main`; merge on green.

---

## Design decisions

- **Two candidate approaches (both numpy-only):**
  - **A — conditional empirical quantile (baseline).** Group feature rows by
    `(location_id, product_category, obs_hour, day_type)` where `day_type` folds
    weekend/holiday into a small set of regimes; take the empirical P10/P70/P90 `items` per
    group. A classical, robust, assumption-light time-series baseline. Unseen groups fall back
    to the coarser `(location_id, product_category, obs_hour)` quantiles, then the global one.
  - **B — linear quantile regression (pinball-loss gradient descent).** Standardised feature
    matrix `[hour (cyclical sin/cos), day_of_week, is_weekend, is_holiday, school_in_session,
    temperature_2m, precipitation, items_lag_1h, items_roll_7d_same_hour, low_history]`; fit
    three independent quantile regressors (τ = 0.10, 0.70, 0.90) by minimising pinball loss
    with full-batch gradient descent and a fixed seed. Missing numeric features imputed to the
    train-split mean (recorded in the model), so predict is reproducible.
- **Selection.** Hold out the tail of the window (default last 14 days) as validation. Compute
  the **P70 pinball loss** for each candidate on the validation rows; the lower one wins. Record
  both candidates' metrics and the winner in `forecast_models.candidates` (selection evidence).
- **Interval integrity.** After prediction, the three quantiles are **sorted per row** (fixes
  quantile crossing) and **clamped ≥ 0**, guaranteeing `0 ≤ lower ≤ p70 ≤ upper` so every emitted
  record passes the CS-012 interval invariant and the `forecasts` CHECK.
- **Model ↔ feature binding.** `forecast_models.feature_version` FK-references
  `feature_sets(feature_version)`; `predict` refuses a `feature_version` that differs from the
  one the model stored (F9.ac10) — enforced in code and at the DB.
- **No annual seasonality.** Only the 6-month 2023 window is used; no yearly term. Documented as
  a limitation, consistent with CS-008/CS-014a.
- **Determinism.** Fixed numpy seed; standardisation and imputation constants persisted, so a
  retrain of the same version reproduces identical params, and reload→predict reproduces
  identical predictions.
- **New dependency:** `numpy==2.4.6` added to `requirements.txt` (the only new dep).

---

## Deliverables

### 1. Migration `db/migrations/versions/0020_cs014b_forecast_models.py`
`down_revision = "0019"`. Reversible. One tenant-scoped, FORCE-RLS table with the
`app_current_tenant()` fail-closed policy and `app_rw`/`app_ro` grants (mirrors 0019):
- `forecast_models (tenant_id, model_version, feature_version, approach, taxonomy_version,
  params jsonb, trained_at, train_window_start, train_window_end, validation_window_start,
  validation_window_end, validation_pinball numeric, validation_coverage numeric,
  candidates jsonb, notes)`, PK `(tenant_id, model_version)`, FK
  `(tenant_id, feature_version) → feature_sets`, FK `(tenant_id, taxonomy_version) →
  taxonomy_meta`, `CHECK (approach IN ('empirical_quantile','linear_quantile_regression'))`,
  `CHECK (train_window_start <= train_window_end)`, `CHECK (validation_pinball >= 0)`.

### 2. Model module `agents/demand_forecast/model.py`
Pure helpers (unit-tested offline) + DB-backed train/predict:
- `pinball_loss(actual, predicted, tau)`, `fit_empirical_quantiles(rows)`,
  `fit_linear_quantile_regression(X, y, tau, *, seed, iterations, lr)`,
  `predict_empirical(...)`, `predict_linear(...)`, `enforce_monotone_nonneg(lower, p70, upper)`,
  `select_model(candidate_metrics)`, `serialize_params(...)`, `deserialize_params(...)`,
  `feature_matrix(rows)` (deterministic column order + standardisation/imputation stats).
- `train(conn, *, model_version, feature_version, taxonomy_version, window, validation_days)` —
  loads feature rows under the tenant session, splits train/validation, fits both candidates,
  selects, persists the `forecast_models` row (idempotent replace), returns a summary.
- `load_model(conn, model_version)` / `predict(conn, model_version, *, target_window) ->
  ForecastSet` (refuses a feature_version mismatch; emits contract-valid records) /
  `persist_forecasts(conn, forecast_set)` (writes to the `forecasts` table).
- `main()` CLI (`--database-url/--tenant-id/--model-version/--feature-version`).

### 3. SDD — `docs/sdd-cs014b.md`
Defines `F9.ac8 … F9.ac13` as `**F9.ac#**` ids the traceability gate reads; maps each to its
test. FEATURES.md F9 stays **In progress** (CS-014c/backtest still open) so the gate does not
demand the remaining umbrella ACs.

### 4. Tests — `test/test_cs014b_model.py`
`unittest` style with `if __name__ == "__main__": unittest.main()` (both discover and pytest).
The three categories below.

### 5. Status — `requirements.txt` += `numpy==2.4.6`; FEATURES.md F9 note; Backlog CS-014b → Done.

---

## F9 acceptance criteria (CS-014b scope)

- **F9.ac8 — ≥2 candidates + recorded selection.** Training fits both the empirical-quantile and
  the linear-quantile-regression candidates and persists the one with the lower validation
  pinball loss; both candidates' metrics are recorded in `forecast_models.candidates`.
- **F9.ac9 — interval invariant.** Every emitted record satisfies `0 ≤ lower ≤ p70 ≤ upper`
  (quantile crossing fixed, non-negative clamp), so `contracts.forecast.validate_set` accepts a
  predicted `ForecastSet`.
- **F9.ac10 — model↔feature binding.** The persisted model records `model_version` and the exact
  `feature_version` it trained on (FK to `feature_sets`); `predict` refuses a `feature_version`
  other than the stored one.
- **F9.ac11 — no annual seasonality.** Training reads only the 6-month window features and uses
  no yearly term; the limitation is documented.
- **F9.ac12 — contract-conforming & persistable.** A predicted `ForecastSet` uses only the pinned
  taxonomy vocabulary and open store-hours and persists to the `forecasts` table under the 0017
  constraints (interval CHECK, taxonomy/throughput FKs, live-key unique).
- **F9.ac13 — deterministic round-trip.** With a fixed seed, retraining a version reproduces
  identical params, and `load_model` → `predict` reproduces identical predictions.

---

## The 3 test categories (all must pass before merge)

1. **Unit (offline)** — `pinball_loss` values; linear quantile fit recovers a known quantile on
   synthetic data within tolerance; empirical-quantile grouping returns correct per-group
   quantiles with the documented fallback; `enforce_monotone_nonneg` sorts + clamps; `select_model`
   picks the lower-pinball candidate; `serialize/deserialize` round-trips; prediction non-negative.
2. **DB integration (skip-gated, rollback-only)** — build features (reuse CS-014a
   `build_feature_set`), `train`, assert a `forecast_models` row with `feature_version` + approach
   + candidate metrics; `predict` → `validate_set` passes; `persist_forecasts` writes rows that
   satisfy the 0017 constraints; a mismatched `feature_version` is refused; a second `train` with
   the same seed yields identical params (determinism).
3. **Migration round-trip + CI gates** — `0020` up→down→up; spec-traceability / test-first /
   spec-lint / review-checklist; ruff clean on new files.

---

## Orchestration (execution)

1. Branch `cs-014b-model-training` off `main` (done).
2. **Claude** authors `docs/sdd-cs014b.md` (ACs) and this plan.
3. **Codex** (`codex exec --cd <repo> -s workspace-write -c approval_policy='"never"'`) implements
   `agents/demand_forecast/model.py`, migration `0020`, `test/test_cs014b_model.py`, and the
   `requirements.txt`/FEATURES/Backlog edits, to the SDD + conventions (unittest + `spec: F9.ac#`
   tags + `unittest.main()`, ruff py311/line-120, numpy-only, typed errors, no LLM in the numeric
   path).
4. **Claude independently reviews** the diff against F9.ac8–ac13 and the repo's defect patterns,
   strengthening tests where coverage is thin.
5. **Local gate (all three categories)** mirroring CI: unit under `unittest discover` **and**
   `pytest`; DB integration + migration round-trip against live Postgres (migrate to `0020`, build
   features); the 4 CI scripts; ruff on new files.
6. **Commits** (test-first ordering): commit 1 = `test/test_cs014b_model.py` + `docs/sdd-cs014b.md`
   + FEATURES note; commit 2 = `model.py` + migration `0020` + `requirements.txt` + Backlog.
7. **On green:** push, open PR, watch CI, **merge**, update Backlog/FEATURES status.

**Attribution:** commit footer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`;
PR body footer `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

## Edge cases explicitly covered

- Quantile crossing (raw lower > upper) → sorted per row; negative predictions → clamped to 0.
- Unseen empirical group at predict → documented fallback to coarser grouping, never a crash.
- Missing numeric feature (null weather/lag at series start) → imputed to the recorded train mean.
- `feature_version` mismatch between model and request → refused (code + FK).
- Determinism: fixed seed + persisted standardisation/imputation constants.
- Contract edges: forecast_hour is an exact America/New_York business hour within the store's open
  window (features already encode open hours); the register-overhead pseudo-category is never
  predicted.

## Out of scope (deliberately)

The formal MAPE backtest + acceptance band (CS-014c), event multipliers (CS-023), solver input
assembly (CS-016a), and any online retraining/registry/rollback (CS-086). CS-014b delivers the
trained, selectable, contract-emitting model + its persisted artifact.
