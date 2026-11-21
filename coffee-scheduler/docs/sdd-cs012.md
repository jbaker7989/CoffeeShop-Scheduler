# CS-012: Demand Forecast Output Contract

**Feature:** F8 · **Status:** failing-first specification and tests

CS-012 defines the versioned forecast envelope consumed independently of the forecasting model.
Records are validated against the pinned category vocabulary, interval ordering, timezone-aware
hourly timestamps, store opening windows, and their logical uniqueness key. Serialization is
lossless for `Decimal` values and maps directly to the forecast persistence columns.

| Acceptance criterion | Contract test methods |
| --- | --- |
| **F8.ac1** | `test_ac1_vocabulary_accepts_pinned_categories_and_fails_closed`; `test_schema_and_example_match_contract`; `test_database_constraints_reject_invalid_forecasts` |
| **F8.ac2** | `test_ac2_versioning_rejects_mismatches_wrong_contract_and_blanks`; `test_serialization_and_row_mapping_round_trip_exact_decimals` |
| **F8.ac3** | `test_ac3_interval_accepts_bounds_and_rejects_invalid_values`; `test_database_constraints_reject_invalid_forecasts` |
| **F8.ac4** | `test_ac4_timezone_requires_aware_exact_hours_and_normalizes_utc` |
| **F8.ac5** | `test_ac5_store_hours_reject_unknown_closed_and_missing_windows` |
| **F8.ac6** | `test_ac6_uniqueness_accepts_distinct_records_and_rejects_duplicate_key`; `test_missing_keys_returns_only_absent_expected_key`; `test_database_constraints_reject_invalid_forecasts` |

The integration test is gated by a short `DATABASE_URL` probe and rolls its transaction back. It
will verify database enforcement of the Python contract once the CS-012 migration exists.
