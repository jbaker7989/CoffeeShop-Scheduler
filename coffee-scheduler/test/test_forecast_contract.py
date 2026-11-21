"""CS-012 / F8 failing-first contract tests for demand-forecast output."""
from __future__ import annotations

import json
import os
import sys
import unittest
from dataclasses import fields
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
if str(SCHEDULER_ROOT) not in sys.path:
    sys.path.insert(0, str(SCHEDULER_ROOT))

from contracts.forecast import (  # noqa: E402
    CONTRACT_VERSION,
    DuplicateRecordError,
    ForecastRecord,
    ForecastSet,
    IntervalError,
    StoreHoursError,
    StoreOpenWindow,
    TimezoneError,
    UnknownCategoryError,
    VersionMismatchError,
    from_json,
    from_row,
    load_vocabulary,
    missing_keys,
    record_from_dict,
    record_to_dict,
    to_json,
    to_row,
    validate_record,
    validate_set,
)

CATEGORIES = {
    "Coffee",
    "Tea",
    "Bakery",
    "Drinking Chocolate",
    "Flavours",
    "Coffee beans",
    "Loose Tea",
    "Branded",
    "Packaged Chocolate",
}
ALLOWED_LOCATIONS = {3, 5, 8}
STORE_HOURS = {
    5: StoreOpenWindow(6, 21, frozenset(range(7))),
    8: StoreOpenWindow(6, 21, frozenset(range(7))),
    3: StoreOpenWindow(7, 20, frozenset(range(7))),
}
TENANT_ID = "00000000-0000-0000-0000-000000000001"
DSN = os.environ.get("DATABASE_URL", "postgresql://coffeeshop:coffeeshop@localhost:5432/coffeeshop")


def valid_record(**overrides) -> ForecastRecord:
    values = {
        "location_id": 5,
        "product_category": "Coffee",
        "forecast_hour": datetime(2026, 1, 5, 9, tzinfo=ZoneInfo("America/New_York")),
        "p70": Decimal("42.37"),
        "lower": Decimal("35.00"),
        "upper": Decimal("50.00"),
        "model_version": "model-2026-01",
        "taxonomy_version": "TAX-2023H1-v1",
    }
    values.update(overrides)
    return ForecastRecord(**values)


def valid_set(records=()) -> ForecastSet:
    records = tuple(records) or (valid_record(),)
    return ForecastSet(
        contract_version=CONTRACT_VERSION,
        taxonomy_version="TAX-2023H1-v1",
        model_version="model-2026-01",
        generated_at=datetime(2026, 1, 5, 8, tzinfo=UTC),
        records=records,
    )


_SKIP = None
try:
    import psycopg
    from psycopg.errors import CheckViolation, ForeignKeyViolation, UniqueViolation

    with psycopg.connect(DSN, connect_timeout=3) as _connection:
        _connection.execute("SELECT 1")
except Exception as exc:  # pragma: no cover - environment-dependent skip
    _SKIP = f"psycopg or database unavailable: {exc}"


class ForecastContractTests(unittest.TestCase):
    # spec: F8.ac1 — Pinned taxonomy categories are accepted and unknown or empty vocabularies fail closed.
    def test_ac1_vocabulary_accepts_pinned_categories_and_fails_closed(self):
        validate_record(
            valid_record(),
            allowed_categories=CATEGORIES,
            allowed_locations=ALLOWED_LOCATIONS,
            store_hours=STORE_HOURS,
        )
        with self.assertRaises(UnknownCategoryError):
            validate_record(
                valid_record(product_category="Unknown category"),
                allowed_categories=CATEGORIES,
                allowed_locations=ALLOWED_LOCATIONS,
                store_hours=STORE_HOURS,
            )
        with self.assertRaises(UnknownCategoryError):
            validate_record(
                valid_record(),
                allowed_categories=set(),
                allowed_locations=ALLOWED_LOCATIONS,
                store_hours=STORE_HOURS,
            )

    # spec: F8.ac2 — A set pins non-blank contract, taxonomy, and model versions across every record.
    def test_ac2_versioning_rejects_mismatches_wrong_contract_and_blanks(self):
        validate_set(valid_set(), allowed_categories=CATEGORIES, allowed_locations=ALLOWED_LOCATIONS, store_hours=STORE_HOURS)
        for record in (
            valid_record(taxonomy_version="TAX-other"),
            valid_record(model_version="model-other"),
        ):
            with self.assertRaises(VersionMismatchError):
                validate_set(
                    valid_set((record,)),
                    allowed_categories=CATEGORIES,
                    allowed_locations=ALLOWED_LOCATIONS,
                    store_hours=STORE_HOURS,
                )
        with self.assertRaises(VersionMismatchError):
            validate_set(
                ForecastSet("wrong-contract", "TAX-2023H1-v1", "model-2026-01", datetime.now(UTC), (valid_record(),)),
                allowed_categories=CATEGORIES,
                allowed_locations=ALLOWED_LOCATIONS,
                store_hours=STORE_HOURS,
            )
        for taxonomy_version, model_version in ((" ", "model-2026-01"), ("TAX-2023H1-v1", " ")):
            with self.assertRaises(VersionMismatchError):
                validate_set(
                    ForecastSet(CONTRACT_VERSION, taxonomy_version, model_version, datetime.now(UTC), (valid_record(),)),
                    allowed_categories=CATEGORIES,
                    allowed_locations=ALLOWED_LOCATIONS,
                    store_hours=STORE_HOURS,
                )

    # spec: F8.ac3 — Finite prediction intervals satisfy 0 <= lower <= p70 <= upper.
    def test_ac3_interval_accepts_bounds_and_rejects_invalid_values(self):
        validate_record(
            valid_record(lower=Decimal("42.37"), p70=Decimal("42.37"), upper=Decimal("42.37")),
            allowed_categories=CATEGORIES,
            allowed_locations=ALLOWED_LOCATIONS,
            store_hours=STORE_HOURS,
        )
        for overrides in (
            {"lower": Decimal("51"), "upper": Decimal("50")},
            {"p70": Decimal("51"), "upper": Decimal("50")},
            {"lower": Decimal("-0.01")},
            {"p70": Decimal("NaN")},
            {"p70": None},
        ):
            with self.assertRaises(IntervalError):
                validate_record(
                    valid_record(**overrides),
                    allowed_categories=CATEGORIES,
                    allowed_locations=ALLOWED_LOCATIONS,
                    store_hours=STORE_HOURS,
                )

    # spec: F8.ac4 — Forecast hours are exact business-timezone clock hours, normalized to New York for validation.
    def test_ac4_timezone_requires_aware_exact_hours_and_normalizes_utc(self):
        validate_record(
            valid_record(forecast_hour=datetime(2026, 1, 5, 14, tzinfo=UTC)),
            allowed_categories=CATEGORIES,
            allowed_locations=ALLOWED_LOCATIONS,
            store_hours=STORE_HOURS,
        )
        for forecast_hour in (
            datetime(2026, 1, 5, 9),
            datetime(2026, 1, 5, 9, 1, tzinfo=ZoneInfo("America/New_York")),
            datetime(2026, 1, 5, 20, tzinfo=timezone(timedelta(hours=5, minutes=30))),
        ):
            with self.assertRaises(TimezoneError):
                validate_record(
                    valid_record(forecast_hour=forecast_hour),
                    allowed_categories=CATEGORIES,
                    allowed_locations=ALLOWED_LOCATIONS,
                    store_hours=STORE_HOURS,
                )

    # spec: F8.ac5 — Known stores emit only open-day, half-open-window forecast hours.
    def test_ac5_store_hours_reject_unknown_closed_and_missing_windows(self):
        validate_record(
            valid_record(),
            allowed_categories=CATEGORIES,
            allowed_locations=ALLOWED_LOCATIONS,
            store_hours=STORE_HOURS,
        )
        invalid_cases = (
            valid_record(location_id=99),
            valid_record(forecast_hour=datetime(2026, 1, 5, 5, tzinfo=ZoneInfo("America/New_York"))),
            valid_record(forecast_hour=datetime(2026, 1, 5, 21, tzinfo=ZoneInfo("America/New_York"))),
            valid_record(location_id=3),
        )
        for record in invalid_cases:
            with self.assertRaises(StoreHoursError):
                validate_record(
                    record,
                    allowed_categories=CATEGORIES,
                    allowed_locations=ALLOWED_LOCATIONS,
                    store_hours={**STORE_HOURS, 3: StoreOpenWindow(7, 20, frozenset())},
                )
        with self.assertRaises(StoreHoursError):
            validate_record(
                valid_record(location_id=5),
                allowed_categories=CATEGORIES,
                allowed_locations=ALLOWED_LOCATIONS,
                store_hours={8: STORE_HOURS[8], 3: STORE_HOURS[3]},
            )

    # spec: F8.ac6 — Each location/category/forecast-hour logical key appears at most once.
    def test_ac6_uniqueness_accepts_distinct_records_and_rejects_duplicate_key(self):
        unique = valid_set((valid_record(), valid_record(product_category="Tea")))
        validate_set(unique, allowed_categories=CATEGORIES, allowed_locations=ALLOWED_LOCATIONS, store_hours=STORE_HOURS)
        with self.assertRaises(DuplicateRecordError):
            validate_set(
                valid_set((valid_record(), valid_record())),
                allowed_categories=CATEGORIES,
                allowed_locations=ALLOWED_LOCATIONS,
                store_hours=STORE_HOURS,
            )

    # spec: F8.ac2 — Version-stamped envelopes and forecast records preserve exact values across serialization.
    def test_serialization_and_row_mapping_round_trip_exact_decimals(self):
        forecast_set = valid_set()
        self.assertEqual(from_json(to_json(forecast_set)), forecast_set)
        self.assertEqual(from_json(to_json(forecast_set)).records[0].p70, Decimal("42.37"))
        record = valid_record()
        self.assertEqual(record_from_dict(record_to_dict(record)), record)
        row = to_row(record, TENANT_ID)
        self.assertEqual(
            set(row),
            {"tenant_id", "location_id", "product_category", "forecast_hour", "p70", "lower", "upper", "model_version", "taxonomy_version"},
        )
        self.assertEqual(from_row(row), record)

    # spec: F8.ac1 — JSON schema and canonical example remain aligned with the record and vocabulary contract.
    def test_schema_and_example_match_contract(self):
        schema = json.loads((SCHEDULER_ROOT / "contracts/forecast_output.schema.json").read_text())
        record_schema = schema["properties"]["records"]["items"]
        self.assertEqual(set(record_schema["properties"]), {field.name for field in fields(ForecastRecord)})
        self.assertEqual(
            schema["required"],
            ["contract_version", "taxonomy_version", "model_version", "generated_at", "records"],
        )
        example = from_json((SCHEDULER_ROOT / "contracts/examples/forecast_output.example.json").read_text())
        validate_set(example, allowed_categories=CATEGORIES, allowed_locations=ALLOWED_LOCATIONS, store_hours=STORE_HOURS)

    # spec: F8.ac6 — Completeness reports precisely the expected logical keys that are absent.
    def test_missing_keys_returns_only_absent_expected_key(self):
        present = valid_record()
        absent = (5, "Tea", present.forecast_hour)
        expected = ((present.location_id, present.product_category, present.forecast_hour), absent)
        self.assertEqual(missing_keys(valid_set((present,)), expected), frozenset({absent}))


@unittest.skipIf(_SKIP, _SKIP or "")
class ForecastDatabaseConstraintTests(unittest.TestCase):
    # spec: F8.ac1 — The pinned taxonomy vocabulary contains only real product categories.
    def test_load_vocabulary_excludes_register_overhead(self):
        from data_access.session import tenant_session

        with tenant_session(DSN, role="app_ro", tenant_id=TENANT_ID) as conn:
            vocabulary = load_vocabulary(conn)

        self.assertEqual(vocabulary, CATEGORIES)
        self.assertNotIn("(per transaction overhead)", vocabulary)

    # spec: F8.ac1 — Database constraints reject invalid intervals, vocabulary/version FKs, and duplicate live keys.
    def test_database_constraints_reject_invalid_forecasts(self):
        from data_access.repository import Repository
        from data_access.session import tenant_session

        def row_at(hour, **overrides):
            when = datetime(2026, 1, 5, hour, tzinfo=ZoneInfo("America/New_York"))
            return to_row(valid_record(forecast_hour=when, **overrides), TENANT_ID)

        with tenant_session(DSN, role="app_rw", tenant_id=TENANT_ID) as conn:
            forecasts = Repository(conn, "forecasts")
            forecasts.insert(**row_at(9))

            # Each negative case uses a distinct forecast_hour so only the intended
            # constraint can fire (the live-key unique index excludes taxonomy_version
            # and the interval columns, so a shared hour would mask the FK/CHECK).
            # assertRaises wraps conn.transaction() so the failed statement rolls back
            # to its savepoint and the outer transaction stays usable.
            with self.assertRaises(CheckViolation), conn.transaction():
                forecasts.insert(**row_at(10, lower=Decimal("51"), upper=Decimal("50")))
            with self.assertRaises(ForeignKeyViolation), conn.transaction():
                forecasts.insert(**row_at(11, product_category="Unknown category"))
            with self.assertRaises(ForeignKeyViolation), conn.transaction():
                forecasts.insert(**row_at(12, taxonomy_version="TAX-unknown"))
            with self.assertRaises(CheckViolation), conn.transaction():
                forecasts.insert(**row_at(13, product_category="(per transaction overhead)"))
            # Re-insert the hour-9 row: identical live logical key → unique violation.
            with self.assertRaises(UniqueViolation), conn.transaction():
                forecasts.insert(**row_at(9))
            raise psycopg.Rollback


if __name__ == "__main__":
    unittest.main()
