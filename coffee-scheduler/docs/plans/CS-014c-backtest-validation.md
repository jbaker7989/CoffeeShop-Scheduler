# CS-014c — Forecast backtest & validation (Plan)

## Goal and verified starting point

CS-014c must evaluate the persisted CS-014b forecaster against actual demand on a temporal
holdout and produce auditable evidence before CS-016 consumes a forecast. The implementation
must not retrain, retune, or alter CS-014b's algorithms; it must ensure the model artifact was
trained without access to the evaluation dates, generate contract-valid forecasts, align them
to the complete observed key set, calculate the agreed metrics, and produce a clear pass/fail
report.

Verified on `origin/main`:

- `agents/demand_forecast/feature_build.py` owns the versioned `forecast_features` table and
  `feature_sets` registry. Its source window is Jan–Jun 2023, inclusive. Each feature cell is
  an open `(store, category, date, hour)` with zero-filled items and prior-observation lag and
  trailing same-hour features.
- `agents/demand_forecast/model.py` trains empirical quantile and linear quantile candidates,
  selects by P70 pinball loss, stores `forecast_models` with feature/taxonomy version and
  training/validation windows, and exposes `predict` returning a CS-012 `ForecastSet`.
- `contracts/forecast.py` validates the output taxonomy, timezone, store-open window, interval
  order and unique business keys.
- CS-008 uses the final 28 days per store for calibration evaluation and establishes the ≤10%
  MAPE band. Its historical daily-store baseline was informational and around 39%; the new
  forecaster must be measured honestly and the threshold must not be weakened to force PASS.
- Existing CS-014b internal validation selects among candidates. CS-014c must use a distinct
  outer holdout after all model training and model-selection validation dates.

## Locked evaluation design

1. **Outer holdout window.** Read `source_window_start` / `source_window_end` from the selected
   `feature_sets` record. Default to the final 28 inclusive calendar dates, computed as
   `end - 27 days` through `end`. Current expected dates are 2023-06-03–2023-06-30. Permit an
   explicit override only for controlled tests; production/default execution uses the
   registered source window. Do not derive dates from wall-clock time or hard-code June 2023.
2. **Training workflow and no leakage.** Build the versioned feature set first. Train a uniquely
   named model on the source window ending one day before outer holdout start. CS-014b's internal
   validation split must also finish before outer holdout start. Backtest refuses a model whose
   `train_window_end >= holdout_start` or `validation_window_end >= holdout_start`, whose feature
   version differs, or whose taxonomy differs. The model artifact remains the artifact being
   assessed; do not refit on outer-holdout data.
3. **Forecast protocol.** Evaluate rolling-origin one-step-ahead predictions at each open
   business hour. The existing lag/rolling features may use actual observations strictly before
   the row being forecast, which matches `feature_build.rolling_features`; actual items on the
   target row are scoring-only. Document this clearly; this is not a 28-day fixed-origin forecast.
4. **Key alignment and contract.** Generate one `ForecastSet` for the holdout via
   `model.predict`; call the canonical `validate_set`. Load actual `items` and the expected
   feature key set for the same dates. Require exact equality of keys across expected features,
   predictions, and actuals; reject duplicates, missing/extra cells, closed hours, invalid
   intervals, version mismatches, NaN/infinity and malformed timezones. No inner join that drops
   a bad row.
5. **MAPE.** Preserve the F5 requirement at its stated granularity: one group per
   `(location_id, category, business clock hour)`, summarizing all dates in the 28-day holdout.
   For positive actuals only, compute the arithmetic mean of `100 * abs(p70 - actual) / actual`.
   Report `n_total`, `n_positive_actual`, and `n_zero_actual`. A group with no positive actuals
   is `unscorable` and fails the overall gate. Every scorable group's MAPE must be ≤10%; no
   aggregate average may hide a failing group. Also report signed bias separately if useful,
   but never substitute it for MAPE.
6. **Quantile / interval calibration.** Separately report P70 empirical hit rate
   (`actual <= p70`, nominal 70%) and P10–P90 central-interval coverage
   (`lower <= actual <= upper`, nominal 80%). These denominators include every scored row,
   including actual zero. Include counts and observed-minus-nominal percentage-point deltas.
   Coverage is an evaluation diagnostic; the F5 10% MAPE criterion is the pass/fail gate.
7. **Report artifact.** Emit schema-versioned JSON with run ID, generated timestamp, model
   version, feature version, taxonomy version, contract version, approach, train/validation/
   holdout dates, protocol name, MAPE definition and limit, zero policy, quantile definitions,
   aggregate row counts, all per-store/category/hour groups, unscorable/failing groups, and an
   overall gate outcome. Write to a caller-selected path or stdout with deterministic key order;
   for file output, write a temporary sibling and atomically replace only the matching
   model-version report. Do not persist row-level transaction data or PII.
8. **Failure behavior.** Fail before emitting PASS for an unavailable DB, absent feature/model,
   invalid source window, insufficient dates, model/version/window leakage, incomplete key
   coverage, invalid or non-finite inputs/outputs, no scorable groups, report schema errors, or
   any MAPE breach. Where evaluation completes but misses the threshold, preserve an explicit
   `FAIL` report and return a failing CLI exit status. Never massage the score or suppress the
   failing data to obtain a successful status.

## Acceptance criteria and test-first contract

The authoritative numbered ACs and AC-to-test mapping are in `docs/sdd-cs014c.md` (`F9.ac14`
through `F9.ac20`). Before production code, add those tests and prove they fail for the expected
missing implementation. Required classes:

- Offline unit tests for date derivation, exact key alignment, MAPE/zero policy, unscorable
  groups, each-group gate, quantile-vs-interval metrics, report schema, and atomic output.
- PostgreSQL integration test (rollback-only and skip-gated only when the declared test database
  is absent) that builds features, trains a strict outer-split model, backtests all stores,
  and verifies persisted model provenance + report metrics. It must assert a truthful pass or
  fail outcome from measured metrics, never assume the real dataset passes.
- Existing CS-014a/b and forecast contract suites remain green. Run full suite, Ruff, spec
  traceability, spec lint, test-first guard and review checklist before merge.

## Implementation boundaries

- New code belongs in `agents/demand_forecast/backtest.py` (pure helpers + DB orchestration),
  with CLI entrypoint for feature version, model version, tenant, optional explicit test window,
  and output path. Reuse `feature_build`, `model.train`, `model.predict`, data access tenant
  session, and `contracts.forecast`; do not duplicate their rules.
- Use the already-persisted `forecast_models` provenance; do not add a migration unless the
  implementation demonstrates that a file report cannot meet the auditable evidence requirement.
- Do not make overall average MAPE the gate. Report metrics at the required store/category/hour
  granularity. Keep metric arithmetic as pure functions for synthetic edge-case tests.
- Model version should be unique and safe as a report identifier; reject traversal and unsafe
  names before deriving a report path. Atomic file write must not leave truncated evidence.
- No changes to model algorithm, training selection, output contract, solver, tenant routing,
  event features, future forecast horizons, or annual seasonality.

## Test/run sequence and integration

1. Codex commits this planning/SDD package separately before implementation.
2. Claude implements the CS-014c contracts test-first on this isolated branch: tests + test
   mappings first; run red; commit failing tests; then minimum implementation; run green.
3. Codex independently runs focused offline tests, skip-gated DB integration against migrated
   PostgreSQL, existing forecast suites, full project suite, Ruff and CI spec/review scripts.
4. Review the real-data report. A completed code test suite with a real-data `FAIL` is not
   acceptance evidence; leave the story blocked and report the failing groups for a follow-up
   model-improvement story. If the report passes, preserve it with the implementation.
5. Only after tests and acceptance gates pass: commit production changes after test commit, push,
   open PR, wait for all CI jobs, then merge. No merge if the real-data gate, tests, or CI fail.

## Risks and mitigations

- **Zero actuals make MAPE undefined:** report zero counts; an all-zero group is unscorable and
  fails the gate, preventing silent denominator manipulation.
- **The advertised P70 can be confused with interval coverage:** report its 70% hit rate
  separately from the P10–P90 80% interval coverage.
- **Feature rows contain lagged actuals:** state rolling one-step semantics; test lag derivation
  is strictly prior, and exclude current `items` from model predictors.
- **Internal model selection can leak:** require validation and train windows to finish before
  outer holdout and verify version binding before scoring.
- **Good global score can conceal weak categories:** per-group gate and explicit failure list.
- **Holdout window is coupled to data:** derive from the registered versioned feature window,
  with test cases across leap day, short ranges, and future years.
- **Report may be overwritten or truncated:** version-scoped names, path validation, temporary
  sibling write, fsync where supported, and atomic replacement.

## Out of scope

Model algorithm improvements, re-training/online drift (CS-086), MLOps registry, event/weather
multiplier calibration (CS-023), service-time calibration (CS-008), fixed-origin 28-day-ahead
evaluation, solver consumption, forecast UI, and changes to the CS-012 interchange schema.
