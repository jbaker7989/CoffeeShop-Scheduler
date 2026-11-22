# CS-014b decision memo — the forecast acceptance metric

**Status:** Open — awaiting ratification of a count-valid acceptance metric.
**Date:** 2026-11-22.
**Context:** CS-014c reopened CS-014b after the backtest failed the F5 accuracy gate.

## The problem we were asked to fix

The CS-014c backtest of the CS-014b forecaster reported **0 of 396** `(location, category,
clock-hour)` groups within the `≤10% MAPE` band (371 failing, 25 unscorable); per-group MAPE
ranged 13.0%–228.76% with **median 71.5%**. Interval calibration, by contrast, was already near
nominal: P70 hit rate 75.98% (nominal 70%), P10–P90 coverage 78.42% (nominal 80%).

## What we changed, and what the evidence shows

We added a third, deterministic, NumPy-only candidate — **`shrunk_empirical_quantile`** — that
pulls noisy small-sample group quantiles toward their better-supported `(loc, cat, hour)` and
global backups by a count weight `n/(n+k)`. It is unit-tested (F9.ac21, F9.ac22) and selected only
if it wins the validation pinball criterion; it does not change the stored contract or schema.

Trained on the seeded 2023-H1 features (last-14-day validation split), the three candidates scored:

| Candidate | Validation pinball (P70) | Validation coverage |
|---|---|---|
| `empirical_quantile` | 1.198 | 0.842 |
| `shrunk_empirical_quantile` | 1.199 | 0.848 |
| **`linear_quantile_regression` (selected)** | **0.965** | 0.777 |

**Re-measured accuracy of the selected model** (fixed-origin predict over the last-14-day
validation window of the seeded 2023-H1 features — a lighter instrument than CS-014c's
rolling-origin 28-day holdout, used here for an independent, in-worktree cross-check):

| Metric | Value |
|---|---|
| WAPE (`Σ\|p70−actual\| / Σactual`, 5,544 scored rows) | **50.9%** |
| Per-group `(loc, cat, hour)` MAPE — median / mean | 66.6% / 70.8% |
| Groups within ≤10% MAPE | **1 of 338** |

These corroborate the CS-014c rolling-origin report (median per-group MAPE **71.5%**, quoted from
`backtest-cs014c-realdata-2023h1-v1.json` on the `cs014c-backtest` branch; not reproduced in this
worktree). WAPE at ~51% is the honest, count-appropriate read: materially better than a per-group
MAPE in the 60–70s, because it stops dividing by tiny hourly counts — which is exactly why WAPE is
the right acceptance metric.

**Two honest findings:**
1. The **linear quantile regression already wins** and shrinkage is **neutral** on the empirical
   family here (1.199 vs 1.198) — the sparse-group regime where shrinkage helps is not where this
   validation split lives. So the within-NumPy model levers do **not** materially move the
   forecaster on this data.
2. The gap from a **71.5% median MAPE to a ≤10% gate cannot be closed by model tuning** in this
   family, and almost certainly not by any model, because of the metric itself (below).

## Root cause: ≤10% per-group MAPE is a metric mismatch for count data

Hourly per-category demand is **small-count** data (many cells 0–5 items). MAPE =
`mean(100·|p̂−actual|/actual)` is dominated by small denominators: even a **perfect
conditional-median** predictor scores enormous MAPE, because integer actuals vary by ±1–2 around
any central value, which is a 30–100% relative error at low counts. A ≤10% per-group MAPE would
require predicting almost every low-count hour to within a fraction of an item — not achievable,
and not a meaningful target for a staffing forecast. The near-nominal P70/coverage numbers confirm
the forecaster's **distribution** is reasonable; the point-MAPE gate is the artifact.

## Recommendation (requires ratification — do not self-approve)

Replace the per-group ≤10% MAPE pass/fail gate with a **count-appropriate acceptance metric**:

1. **WAPE (weighted absolute percentage error)** = `Σ|p̂−actual| / Σactual`, computed per
   `(location, category)` over the holdout — aggregates away the small-denominator pathology and
   is the standard retail-demand accuracy metric. Propose a threshold calibrated to the CS-008
   baseline (the daily-store baseline was ~39%; set the band from the achievable floor, not a
   borrowed 10%).
2. **Pinball loss at P70** as the primary selection/quality metric (already used internally).
3. **Interval calibration** as a separate gate: P70 hit rate and P10–P90 coverage within a few
   points of nominal (already met: 76% / 78%).
Keep MAPE as a *reported diagnostic* at an aggregated granularity, never as the per-group gate.

## Decision gate outcome

- **(A) ≤10% per-group MAPE stays the gate →** CS-014c remains **blocked**; this is not
  attainable and the forecaster should not be forced to a false PASS.
- **(B) The metric above is ratified →** re-run CS-014c under WAPE + pinball + coverage and record
  the result; unblock only if the ratified band is genuinely met.

Until (B) is ratified and met, **CS-014c stays blocked and the forecaster is not fed to CS-016**
(CS-016a adapts the contract shape, not the model's accuracy, so it proceeds independently).
