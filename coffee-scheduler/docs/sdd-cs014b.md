# SDD — CS-014b: Model selection & training (F9, model slice)

Maps the CS-014b acceptance criteria to their tests. These AC ids are the authoritative
definition the spec-traceability gate reads for `spec: F9.ac#` tags. CS-014b delivers the
trained, selectable model + its persisted artifact; the formal MAPE backtest (CS-014c) remains
open, so F9 stays **In progress**, not Done.

The model module is `agents/demand_forecast/model.py`; the tests are
`test/test_cs014b_model.py`. Model output conforms to `contracts/forecast.py` (CS-012) and
persists to the `forecasts` table hardened by migration `0017`.

| AC | Assertion | Test |
|----|-----------|------|
| **F9.ac8** | Training fits ≥2 candidate approaches (conditional empirical quantile and linear quantile regression) and persists the one with the lower validation pinball loss; both candidates' metrics are recorded in `forecast_models.candidates`. | `test_ac8_select_lower_pinball_candidate` (unit) · `test_ac8_db_two_candidates_recorded` (db) |
| **F9.ac9** | Every emitted record satisfies `0 ≤ lower ≤ p70 ≤ upper` (quantile crossing fixed, non-negative clamp), so `contracts.forecast.validate_set` accepts the predicted `ForecastSet`. | `test_ac9_enforce_monotone_nonneg` (unit) · `test_ac9_db_predictions_pass_validate_set` (db) |
| **F9.ac10** | The persisted model records `model_version` and the exact `feature_version` it trained on (FK to `feature_sets`); `predict` refuses a `feature_version` other than the stored one. | `test_ac10_db_model_binds_feature_version` / `test_ac10_db_feature_version_mismatch_refused` (db) |
| **F9.ac11** | Training reads only the 6-month window features and uses no annual-seasonality term. | `test_ac11_training_window_is_six_months` (unit) · `test_ac11_db_model_records_train_window` (db) |
| **F9.ac12** | A predicted `ForecastSet` uses only the pinned taxonomy vocabulary and open store-hours and persists to the `forecasts` table under the `0017` constraints. | `test_ac12_db_forecasts_persist_under_constraints` (db) |
| **F9.ac13** | With a fixed seed, retraining a version reproduces identical params, and `load_model` → `predict` reproduces identical predictions. | `test_ac13_serialize_round_trip` (unit) · `test_ac13_db_training_is_deterministic` (db) |
