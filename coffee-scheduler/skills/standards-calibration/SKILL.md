# Skill: standards-calibration

Calibrate labor standards against observed demand and staffing
(`data/calibrate_standards.py` → `coffee_shop_labor_standards.xlsx`; validated by
`data/validate_calibration.py`). Derive what the data supports (throughput feasibility, weather
multipliers), **gate** what it doesn't, and log every change to `Calibration_Log`.

## What the data can and cannot calibrate
- **Weather multipliers** — derivable, but **back-test gated**: only apply if the weather-adjusted model
  does not worsen holdout MAPE vs the `(store, day_of_week)` baseline. On 6 months of Jan–Jun data
  weather is confounded with season, so they typically stay `ASSUMPTION` (the unit-fix still applies).
- **Throughput** — no service-time data exists, so per-category seconds are **capacity-anchored**
  (scaled only if modeled peak exceeds floor capacity at util), never claimed as *measured*.
- **Targets** (util, quantile, labour %, OT, penalties) — business params; confirmed, not re-derived.

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
