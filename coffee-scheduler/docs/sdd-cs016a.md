# CS-016a — Solver input assembly and data adapters

Acceptance criteria for F11's input-adapter story (CS-016a). The numbered **F11.acN** ids are the
spec tags carried by `test/test_cs016a_inputs.py`.

1. **F11.ac1**: Every enumerated solver input has a typed adapter and resolves into one `SolverInputs` object.
2. **F11.ac2**: Every location, category, and calendar reference resolves or fails with the table and key named.
3. **F11.ac3**: Forecast demand is validated by the CS-012 contract and exposes demand gaps.
4. **F11.ac4**: Every forecast category has a throughput conversion and the transaction-overhead row is isolated.
5. **F11.ac5**: Employee enrolment and calendars agree, weekly hours are ordered, and roster calendars are loaded.
6. **F11.ac6**: Missing or malformed input fails fast with a typed, table-keyed error; no partial aggregate is returned.
7. **F11.ac7**: Database adapters use the RLS tenant context, `app_ro`, and live rows only.

| Acceptance criterion | Tests |
| --- | --- |
| F11.ac1 | `InputBuilderTests.test_ac1_builds_store_and_employee_inputs` |
| F11.ac2 | `InputBuilderTests.test_ac2_rejects_store_hours_unknown_location` |
| F11.ac3 | `InputBuilderTests.test_ac3_rejects_contract_invalid_demand` |
| F11.ac4 | `InputBuilderTests.test_ac4_isolates_transaction_overhead` |
| F11.ac5 | `InputBuilderTests.test_ac5_rejects_invalid_enrolment_and_hours` |
| F11.ac6 | `InputBuilderTests.test_ac6_rejects_duplicate_calendar_days`, `InputBuilderTests.test_ac6_rejects_duplicate_throughput_category` |
| F11.ac7 | `DatabaseAdapterTests.test_ac7_session_is_read_only_and_tenant_scoped` |
