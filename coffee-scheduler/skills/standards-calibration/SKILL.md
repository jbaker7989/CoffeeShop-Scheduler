# Skill: standards-calibration

Calibrate labor standards against observed demand and staffing
(`data/calibrate_standards.py` → `coffee_shop_labor_standards.xlsx`; validated by
`data/validate_calibration.py`). Derive what the data supports (throughput feasibility, weather
multipliers), **gate** what it doesn't, and log every change to `Calibration_Log`.

## Procedure

1. Confirm source workbook, immutable baseline, taxonomy version, sample window, metric, and minimum sample
   size before calculating candidate values.
2. Validate complete category/key coverage and schema before computing; missing mandatory inputs are errors,
   not zero-valued observations.
3. Compare candidates to the documented baseline on a time-separated holdout using the agreed metric.
   Keep assumptions unchanged if evidence is insufficient or performance worsens.
4. Record each accepted change with source, period, old/new value, rationale, evidence and approver in the
   calibration log. Upsert by stable key; do not append duplicate history on retries.
5. Apply only from the immutable baseline and refuse partially or fully calibrated workbooks unless the
   supported explicit force/reset procedure is used.
6. Run independent validation and review the exact output diff before accepting it.

## What the data can and cannot calibrate
- **Weather multipliers** — derivable, but **back-test gated**: only apply if the weather-adjusted model
  does not worsen holdout MAPE vs the `(store, day_of_week)` baseline. On 6 months of Jan–Jun data
  weather is confounded with season, so they typically stay `ASSUMPTION` (the unit-fix still applies).
- **Throughput** — no service-time data exists, so per-category seconds are **capacity-anchored**
  (scaled only if modeled peak exceeds floor capacity at util), never claimed as *measured*.
- **Targets** (util, quantile, labour %, OT, penalties) — business params; confirmed, not re-derived.

## Validation

- Include low-sample, zero-sample, missing-category, mixed calibration-state, and rerun cases.
- Verify the validator independently checks coverage and recomputes the expected output rather than
  importing the same fallback used by the generator.
- Confirm every modified parameter has an audit entry and every held-back candidate has a recorded reason.
- Use only the documented source window and gate; do not tune and evaluate on the same holdout.

## Guardrails (lessons from BUG-002/003/004 — see `test/SKILL.md` D2/D3/D4/D5)
- **No silent-zero categories (D2).** Labor uses `sec.get(cat, 0)` — a sales `product_category` missing
  from `Throughput_Standards` would score **0** labor and inflate feasibility. Assert category coverage
  (sales categories ⊆ throughput keys) and **raise** listing any unmapped category. Applies the moment a
  taxonomy (CS-009) or POS feed (CS-049) changes the category set.
- **Validator must assert independently (D3).** `validate_calibration.py` must **not** reuse the same
  `.get(cat, 0)` fallback it is checking — that inherits the blind spot. It asserts coverage itself.
- **Idempotency (D4).** The script must read the **raw baseline**, not the sheet it overwrites, or it
  double-scales on a second run and double-appends `CAL-####` rows. Guard with the `basis` flag
  (`CALIBRATED` → refuse/no-op unless `--force`) and upsert log rows by `(table, row_key)`. A
  "`git restore` first" note is not a guard.
- **One combination rule, one helper (D5).** Multipliers are estimated **independently**, so applying
  them must not silently compound (`base × rain × cold`). Pick and document the combination rule
  (dominant-condition or capped blend), implement it once, and share that helper between the back-test
  and the runtime forecaster (CS-014) so they can't diverge.
- **Low-n honesty.** Keep a condition as `ASSUMPTION` when its sample is below `MIN_N`; log the
  derivation and the reason it was kept. Clamp multipliers to a sane range.

## Failure modes

- Missing/extra category or malformed key: fail before workbook mutation and list the offending key.
- Insufficient or season-confounded evidence: preserve the baseline and record why the candidate was not
  applied.
- Repeated or mixed-state execution: refuse before mutation or log append; never silently scale twice.
- Generator/validator disagreement, incomplete audit data, or holdout degradation: reject the candidate and
  retain the last known-good values.
