# BUG-066 — Normalize malformed forecast payload errors

## Goal

Ensure malformed but syntactically valid forecast JSON fails through a typed forecast-contract error, not raw `AttributeError`, `TypeError`, or `KeyError` from parser and validator internals.

## Scope

- Harden structural parsing in `contracts/forecast.py` (`from_json`, `_parse_datetime`, and `record_from_dict`).
- Harden business validation in `validate_set` and `validate_record` for directly constructed dataclasses as well as parsed payloads.
- Add focused parser and validator cases in `test/test_forecast_contract.py`.
- Preserve the separation between structural parsing and business validation: parsing verifies shape/types; `validate_set` verifies contract semantics.

## Specification and test-first sequence

1. Define a documented `InvalidForecastPayload` error as a `ForecastContractError` subclass.
2. Add failing-first `spec: F8.ac1`/`F8.ac2` tests for a non-object envelope, missing required envelope keys, `records` set to a string or object, a non-object record, missing record keys, and wrong scalar types.
3. Add validator tests for direct dataclass construction with null/numeric version strings, a list-valued category, non-Decimal interval values, and a non-datetime forecast timestamp. Assert a typed contract error for each malformed value.
4. Implement narrow type and shape checks before dereferencing values or using them as collection keys. Preserve useful field names in the error message without including full payload contents.
5. Keep syntactically invalid JSON distinct from a structurally invalid forecast payload, while documenting which typed error callers should catch.

## Edge cases

- `null`, booleans, numbers, arrays, and objects supplied where strings are required.
- A list-valued `product_category` must not cause unhashable-key `TypeError` during allowed-category lookup.
- `records` missing, null, scalar, or object; one malformed record inside an otherwise-valid list.
- Missing versus explicitly null required fields produce stable, field-specific errors.
- Decimal strings that are malformed or non-finite are rejected through the contract error type; valid finite decimal strings retain exact values.
- Directly constructed dataclasses are not allowed to bypass structural validation merely because the JSON parser was skipped.
- Valid example JSON and row mapping continue to round-trip without normalization loss.

## Completion evidence

- All malformed-input cases raise `InvalidForecastPayload` or another documented `ForecastContractError` subtype; no raw `AttributeError`, `TypeError`, or `KeyError` escapes from contract validation.
- Existing F8 contract tests pass, including serialization, vocabulary, interval, timezone, store-hours, and uniqueness cases.
- `git diff --check` is clean, and the Backlog status records focused-test evidence.
