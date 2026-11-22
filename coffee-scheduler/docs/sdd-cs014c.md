# SDD — CS-014c: Forecast backtest and validation (F9)

This document defines the acceptance criteria for the formal CS-014c holdout evaluation.
The traceability gate reads these `F9.ac#` identifiers from tests. CS-014a owns `F9.ac1–ac7`
and CS-014b owns `F9.ac8–ac13`; this story owns `F9.ac14–ac20`.

The source feature set is currently 2023-01-01 through 2023-06-30. The default holdout is
the final 28 inclusive source dates, currently 2023-06-03 through 2023-06-30. The window is
derived from `feature_sets`, never from `date.today()`. The backtest is rolling-origin
one-step-ahead: at each target hour, actual observations strictly before that hour may be
available to the lag/rolling features, matching the existing `forecast_features` semantics.
The actual for the scored target cell is only used after prediction. This result therefore
does not claim fixed-origin 28-day-ahead performance.

`MAPE` is the arithmetic mean of `100 * abs(predicted - actual) / actual` for rows with
`actual > 0`. Zero-actual rows are excluded only from this percentage denominator and must
be counted and reported. A store/category/clock-hour group with no positive actuals is
unscorable and blocks an overall PASS. Each scorable group's MAPE must be at most 10%;
macro/weighted aggregation cannot hide a failing group. Coverage metrics are separate:
P70 hit rate is `actual <= p70` with nominal rate 70%, and P10–P90 central interval coverage
is `lower <= actual <= upper` with nominal rate 80%. Both coverage metrics include zero
actual rows and are reported, not substituted for MAPE.

| AC | Assertion | Test mapping |
|----|-----------|--------------|
| **F9.ac14** | The default holdout is exactly the final 28 inclusive dates of the registered feature source window, and insufficient or reversed source windows are rejected. | `test_ac14_holdout_is_last_28_source_dates` · `test_ac14_short_window_rejected` |
| **F9.ac15** | Backtest refuses a model whose training or selection-validation window reaches the holdout start, or whose feature/taxonomy version differs from the requested feature set. | `test_ac15_temporal_or_version_leakage_rejected` |
| **F9.ac16** | Scoring requires an exact one-to-one match of expected open store/category/hour keys, actual cells, and validated contract forecasts; a missing, extra, or duplicate key fails closed. | `test_ac16_key_alignment_fails_closed` |
| **F9.ac17** | MAPE is calculated per store/category/clock-hour over positive actuals only, while zero-actual counts are retained and groups with no positive actual are explicitly unscorable. | `test_ac17_mape_zero_actual_policy_and_group_counts` · `test_ac17_no_positive_actual_is_unscorable` |
| **F9.ac18** | The report gives distinct P70 hit-rate and P10–P90 interval-coverage values with their nominal rates and counts, including zero-actual rows in both denominators. | `test_ac18_quantile_and_interval_coverage_are_distinct` |
| **F9.ac19** | The gate passes only when every scorable store/category/clock-hour group has MAPE ≤10% and no group is unscorable; a failing group cannot be masked by a passing aggregate. | `test_ac19_each_group_must_meet_band` · `test_ac19_unscorable_group_fails_gate` |
| **F9.ac20** | The schema-versioned report records model, feature, taxonomy, contract and train/validation/holdout identity, metric definitions, counts, detailed metrics, and gate outcome; file writes are atomic and version-scoped. | `test_ac20_report_schema_and_identity` · `test_ac20_atomic_report_write` |

## Edges and adversarial cases

- Sales can be zero: MAPE is undefined for that row; zero rows remain visible in counts and coverage.
- A whole group can be zero: this is `unscorable`, not silently omitted.
- A category can be missing from the forecast despite present in actuals, or vice versa: refuse the run.
- Closed hours are not expected keys; open hours and the close-exclusive boundary follow `contracts.forecast`.
- DST timestamps are compared by the canonical contract key after conversion to `America/New_York`; duplicate business-hour keys fail.
- The source window is exactly 28 days long, shorter than 28 days, reversed, or has no complete observations: fail with a specific diagnostic.
- Model train or internal validation data overlaps the outer holdout: fail before scoring.
- Feature version or taxonomy version mismatch: fail before scoring.
- P70 hit rate and P10–P90 coverage are different measures; do not label the 80% interval coverage as P70 coverage.
- Non-finite prediction, actual, or metric values fail closed.
- A malformed output path or write failure must not leave a partial report; no existing report for a different model version may be overwritten.

## Cross-references and scope

- CS-008 / F5 owns the 28-day calibration comparison window and ≤10% MAPE threshold.
- CS-014a owns `forecast_features`, rolling features and source-window provenance.
- CS-014b owns model selection, persisted model windows, prediction, P10/P70/P90 contract output.
- CS-012 / F8 owns `ForecastSet`, category taxonomy and store-hour contract validation.
- CS-016a may consume only a model whose backtest gate passed.
- No model retraining algorithm changes, event multipliers (CS-023), online evaluation/retraining (CS-086), custom forecast contract, or fixed-origin 28-day claim.

## Expected outcome

A reproducible, honest held-out report that gates every store/category/hour group against the
F5 threshold before the model is treated as ready for solver consumption.
