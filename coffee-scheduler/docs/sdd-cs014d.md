# SDD — CS-014d: forward inference features (F9, inference slice)

Maps the CS-014d acceptance criteria to their tests. These AC ids are what the spec-traceability
gate reads for `spec: F9.ac#` tags.

`forecast_features` is a **training** table: `items` and `revenue` are `NOT NULL` because each row
carries an observed target. A future week has none, so CS-014d builds target-free inference rows
in memory and `model.predict(..., rows=...)` scores them. No migration, no fabricated demand, and
the training table remains the only source of supervised data.

The module is `agents/demand_forecast/inference.py`; the tests are `test/test_cs014d_inference.py`.

This slice is what makes BUG-069 resolvable: with the roster valid for a forward week, legality is
evaluated against the employees' real ages and the school calendar that actually covers the
scheduling dates.

| AC | Assertion | Test |
|----|-----------|------|
| **F9.ac27** | Inference rows carry exactly the model's feature columns with no target (`items`/`revenue` absent), `items_lag_1h` is null because the previous open hour has not occurred, calendar flags derive from the row's own date, and weather absent from `weather_hourly` stays null rather than being guessed. | `test_ac27_rows_are_target_free_and_do_not_invent_a_lag` · `test_ac27_calendar_from_date_and_missing_weather_is_null` (unit) |
| **F9.ac28** | The trailing same-hour mean and the low-history flag come from the named training feature set's per-(location, category, hour) priors; a cell with no prior keeps nulls and is never defaulted to zero demand. | `test_ac28_priors_supply_rolling_mean_and_low_history` · `test_ac28_absent_priors_do_not_become_zero` (unit) |
| **F9.ac29** | A future window holding zero `forecast_features` rows still yields a contract-valid `ForecastSet` that persists, and the builder fails closed on an inverted window, an unknown history feature version, or an empty row set. | `test_ac29_future_window_yields_contract_valid_forecasts` · `test_ac29_fails_closed_on_bad_window_or_empty_rows` (db) |
