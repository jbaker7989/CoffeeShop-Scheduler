# data/

Canonical data directory for the Coffee Shop Scheduler (repo root: `coffeeshop/`).

## Contents (tracked)
- `coffee_shop_sales.xlsx` — source POS sales (synthetic; 2023-01-01…2023-06-30, stores 3/5/8).
- `coffee_shop_staff.xlsx` — roster: identity (name, sex, DOB) **plus scheduling fields** (CS-002/CS-003).
  See its `Data_Dictionary` sheet for every column, its domain, and the source rule. PII (SSN + address)
  was moved out to `private/coffee_shop_staff_pii.xlsx` (gitignored).
- `coffee_shop_labor_rules.xlsx` — effective-dated labor rules.
- `coffee_shop_labor_standards.xlsx` — store profiles, stations, demand, throughput, coverage, cost targets.
- `weather_forecast.xlsx` — Open-Meteo weather joined to sales (see `../coffee-scheduler/ATTRIBUTION.md`).
- `build_weather_forecast.py` — builds `weather_forecast.xlsx` from sales + Open-Meteo.
- `validate_weather_forecast.py` — validates the weather×sales join (writes `../coffee-scheduler/VALIDATION.md`).
- `build_roster_fields.py` — adds scheduling fields to `coffee_shop_staff.xlsx` + splits PII to `private/` (seed=42).
- `validate_roster_fields.py` — validates the roster schema/legality/feasibility (writes `../coffee-scheduler/ROSTER_VALIDATION.md`).
- `build_school_calendar.mjs` / `validate_school_calendar.mjs` — build/validate the multi-year calendars (CS-004/CS-007). **Require codex's `@oai/artifact-tool` runtime — they do not run with plain Node/Python.**
- `validate_calendars.py` — in-repo Python guard for the committed School_Calendar/Holiday_Calendar (runs anywhere; writes `../coffee-scheduler/CALENDAR_VALIDATION.md`).
- `calibrate_standards.py` — CS-008 calibration: derives weather multipliers + throughput from the sales×weather join, auto-applies, logs to `Calibration_Log` (basis→CALIBRATED). A run against an already-calibrated workbook **refuses before mutation**; restore an uncalibrated baseline before recalibrating.
- `validate_calibration.py` — validates the calibration (throughput units/feasibility, weather unit-fix, audit trail, **category coverage**; writes `../coffee-scheduler/CALIBRATION_VALIDATION.md`).
- `build_product_taxonomy.py` — CS-009: builds the versioned `Product_Taxonomy` + `Taxonomy_Meta` sheets in `coffee_shop_labor_standards.xlsx` from the sales hierarchy. **Idempotent** — rebuilds from the sales source each run, safe to re-run.
- `validate_taxonomy.py` — validates the taxonomy (complete mapping, single-valued category, no drift vs sales, cross-consumer coverage vs `Throughput_Standards`, pinned version; writes `../coffee-scheduler/TAXONOMY_VALIDATION.md`).

## Roster scheduling fields (CS-002 + CS-003)
`coffee_shop_staff.xlsx` → `Staff` sheet adds: `role` (Barista/Register/Shift lead), `skill_level` (1–3;
3 = peak-skill eligible), `home_location_id` (3/5/8), `cross_location_eligible`, `is_key_holder`,
`cash_authorised`, `food_protection_cert` (named per `NYC-FPC-COVERAGE`), `hourly_rate`,
`availability_persona`, `min_hours_week`/`max_hours_week`, and the school fields `is_enrolled`,
`school_calendar_id`, `grade_band`, `has_other_employer`. Values are synthetic but **legal** (no minor is a
key-holder; minor weekly hours respect NY caps) and **solver-feasible** per home store (≥3 key-holders,
≥2 skill-3 baristas, ≥4 FPC holders). Regenerate with `build_roster_fields.py`; verify with
`validate_roster_fields.py`.

## Product taxonomy & category vocabulary (CS-009 / F29)
`coffee_shop_labor_standards.xlsx` owns the single versioned category taxonomy. `Product_Taxonomy` maps
every `product_id` → `product_detail`/`product_type`/`product_category` (80 products → 9 categories);
`Taxonomy_Meta` pins the version (`TAX-2023H1-v1`). Every category-consuming artifact (throughput,
forecaster, explanation layer) reads **this** vocabulary rather than re-deriving its own, so "Coffee" can't
drift between consumers. `calibrate_standards.py` now **fails closed** if sales contains a category absent
from `Throughput_Standards` (was BUG-002: silently scored as 0 labor); `validate_taxonomy.py` and
`validate_calibration.py` each assert that coverage independently. Regenerate with
`build_product_taxonomy.py`; verify with `validate_taxonomy.py`. Bump the version when the source hierarchy
changes.

## Git / PII policy (CS-006)
All workbooks here are **synthetic fixtures** and are committed so the pipeline is reproducible.

**Real PII must never be committed.** If a file ever contains real employee data:
- put it under `data/private/` (gitignored), **or**
- name it `*.pii.*` or `*.real.xlsx` (both gitignored).

Dependencies for the scripts are pinned in `../coffee-scheduler/requirements.txt`
(`pandas`, `openpyxl`, `requests`).
