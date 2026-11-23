# CS-014b (reopened) — Forecaster improvement & honest acceptance (Plan)

## Why this is reopened

CS-014c built an honest backtest and the forecaster **failed the F5 accuracy gate**: on the
seeded 2023-H1 holdout, **0 of 396** `(location, category, clock-hour)` groups fell within the
`≤10% MAPE` band (371 failing, 25 unscorable), MAPE 13.0%–228.76%, **median 71.5%**; P70 hit rate
75.98%, P10–P90 coverage 78.42% (both near nominal). The decision was to **reopen CS-014b** and
improve the forecaster rather than weaken the gate.

## The honest finding that frames this work

**`≤10% MAPE per (store, category, clock-hour) group is almost certainly unattainable for this
data, by any model — it is a metric mismatch, not only a model deficiency.**

- Hourly per-category demand is **small-count** data (many cells 0–5 items). MAPE =
  `mean(100·|p̂−actual|/actual)` is dominated by small denominators: a perfect **conditional-median**
  predictor still scores enormous MAPE because integer actuals vary ±1–2 around any central value,
  which is a 30–100% relative error at low counts. MAPE on low counts is a known pathology.
- The coverage diagnostics are already near nominal (P70≈76%, P10–P90≈78%), i.e. the **interval
  calibration is good**; the point-MAPE gate is what fails.
- Therefore this story must do **two** things, and the plan commits to both:
  1. **Genuinely improve** the forecaster on count-appropriate accuracy (lower pinball, lower
     WAPE, tighter well-calibrated intervals), and
  2. **Measure the achievable floor** and bring a **decision gate**: recommend an acceptance
     metric that is valid for count data (WAPE / aggregated MAPE / MASE / pinball + coverage),
     for the user to ratify. We will not massage the ≤10% number, and we will not claim a PASS
     the data cannot support.

The constraint from the original CS-014b stands: **NumPy only** (no scikit-learn/scipy),
deterministic, no annual seasonality (6-month window), no LLM in the numeric path.

## Verified starting point (`origin/main` `4dfb175`)

- `agents/demand_forecast/model.py` — two candidates: `empirical_quantile` (grouped conditional
  quantiles with `(loc,cat,hour,day_type)` → `(loc,cat,hour)` → global fallback) and
  `linear_quantile_regression` (full-batch GD pinball on a standardized 11-feature matrix).
  `select_model` picks lowest P70 validation pinball. `predict` emits a CS-012 `ForecastSet`.
- `agents/demand_forecast/feature_build.py` — `forecast_features` cells with calendar, weather,
  `items_lag_1h`, `items_roll_7d_same_hour`, `low_history`.
- `agents/demand_forecast/backtest.py` (on branch `cs014c-backtest`) — the rolling-origin,
  gate-enforcing evaluator and JSON report. We reuse it unchanged as the measuring instrument;
  CS-014b does not alter CS-014c's evaluation.

## Improvement levers (NumPy-only, ranked by expected payoff)

1. **Target transform.** Fit/predict on `log1p(items)` (or Anscombe `2√(items+3/8)`) and invert,
   so the regression models proportional rather than additive error — the right space for counts.
2. **Hierarchical shrinkage for the empirical model.** Replace raw per-group quantiles with
   **count-shrunk** quantiles: blend the `(loc,cat,hour,day_type)` estimate toward the coarser
   `(loc,cat,hour)` and global estimates with weight `n/(n+k)` (James–Stein-style), killing the
   noisy small-group over-fit that inflates MAPE. Deterministic, numpy-only.
3. **Richer design matrix for the linear candidate.** Add `hour×day_of_week` interaction terms,
   a per-`(loc,cat)` base-rate offset, and the existing lag/rolling features in transformed space.
4. **A third candidate: conditional-mean Poisson-ish predictor** — a deterministic
   iteratively-reweighted least squares (log link) in numpy for the P70 center, paired with the
   empirical residual quantiles for the interval. Added to `APPROACHES` and selected by the same
   pinball criterion (no change to the selection contract).
5. **Zero-aware grouping** — separate the structural-zero cells (closed/low-history, already
   masked by `low_history`) from genuine low-demand cells so quantiles aren't diluted.

## Deliverables (all under `coffee-scheduler/`)

### 1. `agents/demand_forecast/model.py` (extend, do not break the contract)
- Keep `train`/`load_model`/`predict`/`persist_forecasts`/`main` signatures and the
  `forecast_models` schema (migration `0020`) **unchanged** — the stored `params` jsonb already
  holds arbitrary payloads, so new approaches persist without a migration.
- Add the improvement levers as new/extended pure helpers (unit-testable offline), e.g.
  `shrink_quantiles`, `transform_target`/`inverse_target`, an extended `feature_matrix`, and the
  new candidate fitter. Extend `APPROACHES` if a new approach is added; `select_model` is unchanged.
- Determinism and idempotency preserved (fixed seed; delete-then-insert on `model_version`).

### 2. `test/test_cs014b_model.py` (extend) — spec `F9.ac8–ac13` stay green, add improvement ACs
- New offline unit tests for each new helper (shrinkage reduces small-group variance; target
  transform round-trips; new candidate fits deterministically and beats the old pinball on a
  synthetic skewed-count fixture).
- Keep the existing DB-gated rollback-only lifecycle test green.

### 3. Re-run CS-014c backtest as the measuring instrument (no change to `backtest.py`)
- Train the improved model, run the existing backtest, and record the **new** MAPE distribution
  **and** the count-appropriate metrics (WAPE = Σ|p̂−a|/Σa, aggregated MAPE, pinball, coverage)
  side-by-side with the old numbers, in a dated note. Honest FAIL stays FAIL if ≤10% isn't met.

### 4. `docs/sdd-cs014b.md` (append improvement ACs) + a decision-gate memo
- `docs/decisions/CS-014b-acceptance-metric.md` — the decision gate: present the achievable floor,
  recommend a count-valid acceptance metric and threshold to replace/augment ≤10% per-group MAPE,
  with the evidence. This is the artifact the user ratifies; **F5/CS-008's band is only changed
  with explicit approval** (do not self-approve a weaker gate).

### 5. `FEATURES.md` (F9) + `Backlog.md`
- `FEATURES.md` F9: note the forecaster improvement + the acceptance-metric memo; no hedge words;
  keep F9 "In progress".
- `Backlog.md`: update **CS-014b** with the reopen outcome and link the memo; update **CS-014c**
  to reflect the re-measured numbers; leave all other entries (and every BUG row) untouched.

## Roles & orchestration (this story)

- **Claude develops + writes the tests** in this worktree (`cs014b-model-improvement`), to this
  plan and the SDD.
- **Codex independently runs the three test categories and reviews** the diff + the re-measured
  backtest before acceptance.
- **Test-first ordering** (`check_test_first.py`, production prefix `agents/`): test edits land in
  the commit before the `agents/demand_forecast/model.py` change.

## The three test categories (all must pass before commit/push)

1. **Unit (offline)** under both `unittest discover` and `pytest` — existing F9.ac8–ac13 plus the
   new improvement ACs.
2. **DB-gated** rollback-only lifecycle test against live Postgres (compose up, upgrade head,
   re-seed ETL + rule templates).
3. **Static gates + round-trip** — `ruff` clean on changed files; spec-traceability, test-first,
   spec-lint, review-checklist (`--base origin/main`); migration round-trip green.

## Decision gate (explicit — this is the point of the story)

After the improvement pass, exactly one of:
- **(A) Gate still fails ≤10% MAPE (expected).** Deliver the improved forecaster + the decision
  memo recommending a count-valid acceptance metric; CS-014c stays blocked pending the user's
  ratification of a new band. Report the honest numbers.
- **(B) A ratified new band passes.** Only if the user approves the memo's metric/threshold, re-run
  CS-014c to a PASS under the new band and unblock it.

We commit and push the improved model + tests + memo regardless of A/B (the improvement and the
evidence are the deliverable); we do **not** unblock CS-014c or feed CS-016 until a gate is
legitimately met.

## Out of scope

Changing the CS-012 contract, the `forecast_models` schema, or CS-014c's evaluation logic; adding
sklearn/scipy; annual-seasonality modeling (no data); and any solver work (CS-016a runs in
parallel and does not depend on forecaster accuracy).
