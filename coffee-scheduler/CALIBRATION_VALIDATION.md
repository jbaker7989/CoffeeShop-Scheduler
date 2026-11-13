# Calibration — Validation Report (CS-008)

Generated: 2026-11-12 19:00:23 by `data/validate_calibration.py`.
Overall: **PASS** (6/6 checks passed).

## ✅ A. Throughput units & basis — PASS
- Rows: 10; units_per_labor_hour == 3600/sec mismatches: 0.
- Rows not basis=CALIBRATED: 0.
- Non-positive seconds: none.

## ✅ B. Peak-hour capacity feasibility (<= floor x 3600 x 0.85) — PASS
- store 5: P95 modeled 3599s vs available 18360s -> OK
- store 8: P95 modeled 3596s vs available 18360s -> OK
- store 3: P95 modeled 3648s vs available 12240s -> OK

## ✅ C. Weather conditions imperial + multipliers in range — PASS
- Conditions still using metric units (mm/cm/C): none.
- Multipliers outside (0.5, 1.5): none.
- [info] Weather rows applied as CALIBRATED: none (kept as assumptions — back-test did not validate).

## ✅ D. Calibration_Log audit trail — PASS
- Appended audit rows: 14 (expect >= 14).
- Missing (table,row_key) audit entries: none.
- Rows missing required fields: none.

## ✅ E. Back-test baseline MAPE (informational) — PASS
- store 5: baseline MAPE 39.59% (n=28)
- store 8: baseline MAPE 39.28% (n=28)
- store 3: baseline MAPE 39.63% (n=28)
- [info] Weather multipliers were not applied (did not beat this baseline on 6-month data).

## ✅ F. Every sold category is priced in Throughput_Standards (no silent 0-labor) — PASS
- Sales categories: 9; priced in Throughput_Standards: 9.
- Sold-but-unpriced categories (would be scored 0 labor): none.
