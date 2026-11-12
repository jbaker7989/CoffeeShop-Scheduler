# data/

Canonical data directory for the Coffee Shop Scheduler (repo root: `coffeeshop/`).

## Contents (tracked)
- `coffee_shop_sales.xlsx` — source POS sales (synthetic; 2023-01-01…2023-06-30, stores 3/5/8).
- `coffee_shop_staff.xlsx` — roster identity (synthetic names + DOB).
- `coffee_shop_labor_rules.xlsx` — effective-dated labor rules.
- `coffee_shop_labor_standards.xlsx` — store profiles, stations, demand, throughput, coverage, cost targets.
- `weather_forecast.xlsx` — Open-Meteo weather joined to sales (see `../coffee-scheduler/ATTRIBUTION.md`).
- `build_weather_forecast.py` — builds `weather_forecast.xlsx` from sales + Open-Meteo.
- `validate_weather_forecast.py` — validates the weather×sales join (writes `../coffee-scheduler/VALIDATION.md`).

## Git / PII policy (CS-006)
All workbooks here are **synthetic fixtures** and are committed so the pipeline is reproducible.

**Real PII must never be committed.** If a file ever contains real employee data:
- put it under `data/private/` (gitignored), **or**
- name it `*.pii.*` or `*.real.xlsx` (both gitignored).

Dependencies for the scripts are pinned in `../coffee-scheduler/requirements.txt`
(`pandas`, `openpyxl`, `requests`).
