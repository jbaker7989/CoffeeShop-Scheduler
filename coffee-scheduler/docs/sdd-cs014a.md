# SDD — CS-014a: Feature engineering & training dataset (F9, dataset slice)

This SDD maps the CS-014a acceptance criteria to their tests. These AC ids are the
authoritative definition the spec-traceability gate reads for `spec: F9.ac#` tags. CS-014a
delivers the **feature table** only; the model (CS-014b) and backtest (CS-014c) remain open,
so F9 is **In progress**, not Done.

The builder is `agents/demand_forecast/feature_build.py`; the tests are
`test/test_cs014a_feature_build.py`.

| AC | Assertion | Test |
|----|-----------|------|
| **F9.ac1** | The feature set emits exactly one row per `(store, taxonomy category, date in the 2023 source window, open clock-hour per `store_hours`)`; closed hours and non-open weekdays are absent. | `test_ac1_cell_universe_is_open_hours_only` (unit) · `test_ac1_db_row_count_matches_open_hours` (db) |
| **F9.ac2** | An open hour with no transactions has `items = 0`; a cell's `items` equals the summed `transaction_qty` for that store/category/date/hour. | `test_ac2_zero_fill_for_unsold_hour` (unit) · `test_ac2_db_items_match_transactions` (db) |
| **F9.ac3** | `day_of_week`, `is_weekend`, `is_holiday` and `school_in_session` are derived from `obs_date` (never `date.today()`), joined from `holidays` / `school_calendar_days`. | `test_ac3_calendar_features_from_obs_date` (unit) · `test_ac3_db_calendar_flags_present` (db) |
| **F9.ac4** | Each cell joins its store/date/hour `weather_hourly` row; a cell with no weather row keeps nulls and is never dropped. | `test_ac4_weather_missing_keeps_nulls` (unit) · `test_ac4_db_weather_joined` (db) |
| **F9.ac5** | `items_lag_1h` is the prior open hour for the same store+category; `items_roll_7d_same_hour` is the trailing same-hour mean; both are null at series start and never fabricated. | `test_ac5_rolling_and_lag_series_start_null` (unit) · `test_ac5_db_rolling_present` (db) |
| **F9.ac6** | `has_basket_id`/`has_evening_history`/`has_annual_seasonality` are all false; `low_history` is false for the busiest store-hour and true for at least one late evening hour. | `test_ac6_provenance_flags_all_false` / `test_ac6_low_history_threshold` (unit) · `test_ac6_db_masks` (db) |
| **F9.ac7** | `feature_sets` records version, source window, taxonomy version and row count; a second build of the same version is idempotent (identical row count, no duplicates) and returns zero rows with no tenant context (RLS fail-closed). | `test_ac7_db_idempotent_and_rls_fail_closed` (db) |
