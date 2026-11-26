"""CS-012 / F8 failing-first contract tests for demand-forecast output."""
from __future__ import annotations

import json
import os
import sys
import unittest
from dataclasses import fields
from datetime import UTC, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock
from zoneinfo import ZoneInfo

SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
if str(SCHEDULER_ROOT) not in sys.path:
    sys.path.insert(0, str(SCHEDULER_ROOT))

from contracts.forecast import (  # noqa: E402
    CONTRACT_VERSION,
    DuplicateRecordError,
    ForecastContractError,
    ForecastRecord,
    ForecastSet,
    IntervalError,
    InvalidForecastPayload,
    StoreHoursError,
    StoreOpenWindow,
    TimezoneError,
    UnknownCategoryError,
    VersionMismatchError,
    from_json,
    from_row,
    load_store_hours,
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
    # spec: F8.ac1/ac2 — malformed but valid JSON fails in the documented contract family.
    def test_malformed_envelope_shapes_and_types_are_typed_errors(self):
        for payload in ("null", "[]", "{}", json.dumps({"contract_version": None}),
                        json.dumps({"contract_version": CONTRACT_VERSION, "taxonomy_version": "t",
                                    "model_version": "m", "generated_at": "2026-01-05T08:00:00+00:00",
                                    "records": {}}),
                        json.dumps({"contract_version": CONTRACT_VERSION, "taxonomy_version": "t",
                                    "model_version": "m", "generated_at": None, "records": []})):
            with self.subTest(payload=payload), self.assertRaises(ForecastContractError):
                from_json(payload)

    def test_malformed_record_shapes_and_scalars_are_typed_errors(self):
        base={"location_id":5,"product_category":"Coffee","forecast_hour":"2026-01-05T09:00:00-05:00",
              "p70":"42","lower":"35","upper":"50","model_version":"m","taxonomy_version":"t"}
        for malformed in (None, [], {**base,"product_category":[]}, {k:v for k,v in base.items() if k!="p70"},
                          {**base,"location_id":True}, {**base,"forecast_hour":{}}, {**base,"p70":"not-a-number"}):
            with self.subTest(malformed=malformed), self.assertRaises(InvalidForecastPayload):
                record_from_dict(malformed)
        self.assertEqual(record_from_dict(base).product_category, "Coffee")
        with self.assertRaises(InvalidForecastPayload):
            from_json(json.dumps({"contract_version":CONTRACT_VERSION,"taxonomy_version":"t","model_version":"m",
                                  "generated_at":"2026-01-05T08:00:00+00:00","records":[None]}))

    def test_direct_dataclass_values_cannot_bypass_shape_checks(self):
        for record in (valid_record(product_category=[]), valid_record(forecast_hour="bad"),
                       valid_record(p70="42"), valid_record(model_version=None)):
            with self.assertRaises(ForecastContractError):
                validate_set(valid_set((record,)), allowed_categories=CATEGORIES,
                             allowed_locations=ALLOWED_LOCATIONS, store_hours=STORE_HOURS)
        for field_value in (None, 1):
            with self.assertRaises(ForecastContractError):
                validate_set(ForecastSet(CONTRACT_VERSION, field_value, "m", datetime.now(UTC), ()),
                             allowed_categories=CATEGORIES, allowed_locations=ALLOWED_LOCATIONS,
                             store_hours=STORE_HOURS)
    def test_load_store_hours_keeps_per_weekday_windows(self):
        conn = Mock()
        conn.execute.return_value.fetchall.return_value = [
            (5, "Monday", time(6), time(21)),
            (5, "Saturday", time(7), time(18)),
        ]
        self.assertEqual(load_store_hours(conn), {
            5: {
                0: StoreOpenWindow(6, 21, frozenset({0})),
                5: StoreOpenWindow(7, 18, frozenset({5})),
            },
        })

    def test_load_store_hours_ceils_open_minutes(self):
        conn = Mock()
        conn.execute.return_value.fetchall.return_value = [
            (5, "Monday", time(6, 30), time(20, 30)),
        ]
        window = load_store_hours(conn)[5]
        # Read either shape so the regression isolates rounding.
        if not isinstance(window, StoreOpenWindow):
            window = window[0]
        self.assertEqual((window.open_hour, window.close_hour), (7, 21))

    def test_load_store_hours_ceils_seconds_and_microseconds(self):
        conn = Mock()
        conn.execute.return_value.fetchall.return_value = [
            (5, "Monday", time(6, 0, 1), time(20, 0, 1)),
            (5, "Tuesday", time(6, 0, 0, 1), time(20, 0, 0, 1)),
        ]
        windows = load_store_hours(conn)[5]
        self.assertEqual((windows[0].open_hour, windows[0].close_hour), (7, 21))
        self.assertEqual((windows[1].open_hour, windows[1].close_hour), (7, 21))

    def test_validate_set_uses_record_weekday_window(self):
        hours = {5: {
            0: StoreOpenWindow(6, 21, frozenset({0})),
            5: StoreOpenWindow(7, 18, frozenset({5})),
        }}
        for day, hour, accepted in ((5, 6, True), (10, 7, True), (10, 6, False), (11, 9, False)):
            with self.subTest(day=day, hour=hour):
                record = valid_record(forecast_hour=datetime(
                    2026, 1, day, hour, tzinfo=ZoneInfo("America/New_York"),
                ))
                if accepted:
                    validate_set(valid_set((record,)), allowed_categories=CATEGORIES,
                                 allowed_locations=ALLOWED_LOCATIONS, store_hours=hours)
                else:
                    with self.assertRaises(StoreHoursError):
                        validate_set(valid_set((record,)), allowed_categories=CATEGORIES,
                                     allowed_locations=ALLOWED_LOCATIONS, store_hours=hours)

    def test_validate_set_accepts_uniform_window_mapping(self):
        validate_set(valid_set(), allowed_categories=CATEGORIES,
                     allowed_locations=ALLOWED_LOCATIONS, store_hours=STORE_HOURS)

    def test_validate_set_weekday_boundary_across_utc(self):
        record = valid_record(forecast_hour=datetime(2026, 1, 11, 2, tzinfo=UTC))
        hours = {5: {
            5: StoreOpenWindow(7, 22, frozenset({5})),
            6: StoreOpenWindow(7, 18, frozenset({6})),
        }}
        validate_set(valid_set((record,)), allowed_categories=CATEGORIES,
                     allowed_locations=ALLOWED_LOCATIONS, store_hours=hours)

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

    # spec: F8.ac3 — PostgreSQL rejects non-finite NaN/Infinity interval values (BUG-065).
    def test_database_rejects_non_finite_interval_values(self):
        from data_access.repository import Repository
        from data_access.session import tenant_session

        NAN, INF, NINF = Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")
        ZERO, ONE = Decimal("0"), Decimal("1")

        base = datetime(2026, 1, 6, 0, tzinfo=ZoneInfo("America/New_York"))

        def row_at(offset, **overrides):
            # A different calendar window from the sibling test so a stray commit cannot
            # collide on the live-key unique index; every FK stays valid so only the
            # finite-value constraint is under test. The DB has no store-hours CHECK, so
            # any distinct timestamp is fine for exercising the numeric constraints.
            when = base + timedelta(hours=offset)
            return to_row(valid_record(forecast_hour=when, **overrides), TENANT_ID)

        with tenant_session(DSN, role="app_rw", tenant_id=TENANT_ID) as conn:
            forecasts = Repository(conn, "forecasts")

            # (C) The new constraint applies the SAME finite-exclusion sub-predicate to
            # each of lower/p70/upper. Pin that structurally so the behavioural checks
            # below are tied to the deployed constraint, then prove the sub-predicate
            # itself rejects every special and accepts every finite value — deterministic
            # per-column attribution that does not depend on the 0017 ordering CHECK.
            constraint_def = conn.execute(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'forecasts_interval_finite'"
            ).fetchone()[0]
            for column in ("lower", "p70", "upper"):
                for literal in ("'NaN'::numeric", "'Infinity'::numeric", "'-Infinity'::numeric"):
                    self.assertIn(f"{column} <> {literal}", constraint_def)
            for value, finite in ((NAN, False), (INF, False), (NINF, False),
                                  (ZERO, True), (Decimal("-5"), True), (Decimal("42.37"), True)):
                accepted = conn.execute(
                    "SELECT (%s::numeric <> 'NaN'::numeric "
                    "AND %s::numeric <> 'Infinity'::numeric "
                    "AND %s::numeric <> '-Infinity'::numeric)",
                    (value, value, value),
                ).fetchone()[0]
                self.assertEqual(accepted, finite, f"finite predicate misjudged {value!r}")

            # (A) Tuples that SATISFY the 0017 ordering CHECK, so only the new
            # forecasts_interval_finite constraint can reject them. A CheckViolation here
            # proves the new constraint — not the pre-existing ordering CHECK — rejects
            # the row, and the constraint name is asserted to remove all ambiguity.
            ordering_passing = {
                20: dict(lower=NAN, p70=NAN, upper=NAN),     # all-NaN (satisfies ordered compares)
                21: dict(lower=ZERO, p70=NAN, upper=NAN),    # (0, NaN, NaN)
                22: dict(lower=ZERO, p70=ONE, upper=NAN),    # NaN isolated in upper
                23: dict(lower=ZERO, p70=ONE, upper=INF),    # (0, 1, Infinity)
                24: dict(upper=INF),                         # +Infinity isolated in upper
                25: dict(lower=INF, p70=INF, upper=INF),     # +Infinity in lower (ordered tuple)
                26: dict(lower=ZERO, p70=INF, upper=INF),    # +Infinity in p70 (ordered tuple)
            }
            for hour, overrides in ordering_passing.items():
                with self.assertRaises(CheckViolation) as ctx, conn.transaction():
                    forecasts.insert(**row_at(hour, **overrides))
                self.assertEqual(ctx.exception.diag.constraint_name, "forecasts_interval_finite")

            # (B) Specials in positions that ALSO break the 0017 ordering CHECK (every
            # -Infinity placement, plus NaN/+Infinity where they invert the bounds). These
            # must still be rejected end-to-end; the DB co-violates both constraints, so
            # either firing is a correct rejection. Per-column finite attribution for these
            # is proved deterministically by group (C) above.
            also_ordering_rejected = {
                30: dict(lower=NAN),     # NaN in lower (NaN <= p70 is false)
                31: dict(p70=NAN),       # NaN in p70 (NaN <= upper is false)
                32: dict(lower=INF),     # +Infinity in lower (Inf <= p70 is false)
                33: dict(p70=INF),       # +Infinity in p70 (Inf <= upper is false)
                34: dict(lower=NINF),    # -Infinity in lower
                35: dict(p70=NINF),      # -Infinity in p70
                36: dict(upper=NINF),    # -Infinity in upper
            }
            for hour, overrides in also_ordering_rejected.items():
                with self.assertRaises(CheckViolation) as ctx, conn.transaction():
                    forecasts.insert(**row_at(hour, **overrides))
                self.assertIn(
                    ctx.exception.diag.constraint_name,
                    {"forecasts_interval_finite", "forecasts_interval_valid"},
                )

            # (D) Finite boundaries remain accepted: equal bounds at zero and an ordinary
            # ordered decimal interval. force_rollback discards them so the cases stay
            # isolated and nothing persists.
            with conn.transaction(force_rollback=True):
                forecasts.insert(**row_at(40, lower=ZERO, p70=ZERO, upper=ZERO))
                forecasts.insert(**row_at(41, lower=Decimal("35.00"), p70=Decimal("42.37"), upper=Decimal("50.00")))

            # A negative finite lower is still rejected by the ORDERING constraint, not the
            # finite one — the finite constraint must not over-reject ordinary negatives.
            with self.assertRaises(CheckViolation) as ctx, conn.transaction():
                forecasts.insert(**row_at(42, lower=Decimal("-0.01")))
            self.assertEqual(ctx.exception.diag.constraint_name, "forecasts_interval_valid")

            raise psycopg.Rollback


if __name__ == "__main__":
    unittest.main()
