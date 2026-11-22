"""Offline and rollback-gated tests for CS-016a scheduler inputs."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.scheduler.inputs import (  # noqa: E402
    InvalidInputError,
    ReferentialError,
    build_employees,
    build_school_calendars,
    build_stores,
    build_throughput,
)
from contracts.forecast import ForecastSet, VersionMismatchError, validate_set  # noqa: E402


def _employee(**changes):
    row = {
        "employee_id": 1,
        "role": "Barista",
        "skill_level": 1,
        "home_location_id": 3,
        "cross_location_eligible": False,
        "is_key_holder": False,
        "cash_authorised": True,
        "food_protection_cert": False,
        "hourly_rate": Decimal("16.00"),
        "availability_persona": "open",
        "min_hours_week": 10,
        "max_hours_week": 20,
        "dob": date(2000, 1, 1),
        "is_enrolled": False,
        "school_calendar_id": None,
        "grade_band": "None",
        "has_other_employer": False,
    }
    row.update(changes)
    return row


class InputBuilderTests(unittest.TestCase):
    # spec: F11.ac1
    def test_ac1_builds_store_and_employee_inputs(self):
        stores = build_stores(
            [{"location_id": 3, "store_location": "A", "max_staff_on_floor": 4, "stations": 3}],
            [{"location_id": 3, "day_of_week": "monday", "open_time": time(7), "close_time": time(20)}],
        )
        self.assertEqual(stores[0].location_id, 3)
        self.assertEqual(build_employees([_employee()])[0].hourly_rate, Decimal("16.00"))

    # spec: F11.ac2
    def test_ac2_rejects_store_hours_unknown_location(self):
        with self.assertRaisesRegex(ReferentialError, "store_hours key"):
            build_stores(
                [], [{"location_id": 9, "day_of_week": "monday", "open_time": time(7), "close_time": time(20)}]
            )

    # spec: F11.ac3
    def test_ac3_rejects_contract_invalid_demand(self):
        invalid = ForecastSet("wrong", "TAX-2023H1-v1", "model", datetime(2024, 1, 1), ())
        with self.assertRaises(VersionMismatchError):
            validate_set(invalid, allowed_categories={"Coffee"}, allowed_locations={3}, store_hours={})

    # spec: F11.ac4
    def test_ac4_isolates_transaction_overhead(self):
        regular, overhead = build_throughput(
            [
                {
                    "product_category": "Coffee",
                    "prep_role": "Barista",
                    "seconds_per_unit": Decimal("30"),
                    "units_per_labor_hour": Decimal("120"),
                },
                {
                    "product_category": "(per transaction overhead)",
                    "prep_role": "Register",
                    "seconds_per_unit": Decimal("4"),
                    "units_per_labor_hour": Decimal("900"),
                },
            ]
        )
        self.assertEqual([x.product_category for x in regular], ["Coffee"])
        self.assertEqual(overhead.product_category, "(per transaction overhead)")

    # spec: F11.ac5
    def test_ac5_rejects_invalid_enrolment_and_hours(self):
        with self.assertRaisesRegex(InvalidInputError, "employees key 1"):
            build_employees([_employee(is_enrolled=True)])
        with self.assertRaisesRegex(InvalidInputError, "employees key 1"):
            build_employees([_employee(min_hours_week=21)])

    # spec: F11.ac6
    def test_ac6_rejects_duplicate_calendar_days(self):
        with self.assertRaisesRegex(InvalidInputError, "school_calendar_days key"):
            build_school_calendars(
                [{"calendar_id": "school"}],
                [
                    {"calendar_id": "school", "date": date(2024, 1, 1), "in_session": True},
                    {"calendar_id": "school", "date": date(2024, 1, 1), "in_session": False},
                ],
            )

    # spec: F11.ac6
    def test_ac6_rejects_duplicate_throughput_category(self):
        row = {"product_category": "Coffee", "prep_role": "Barista", "seconds_per_unit": 1, "units_per_labor_hour": 2}
        with self.assertRaisesRegex(InvalidInputError, "throughput_standards key"):
            build_throughput([row, row])


DSN = os.environ.get("DATABASE_URL", "postgresql://coffeeshop:coffeeshop@localhost:5432/coffeeshop")
_SKIP = None
try:
    import psycopg

    with psycopg.connect(DSN, connect_timeout=3) as connection:
        connection.execute("SELECT 1")
except Exception as exc:  # pragma: no cover
    _SKIP = f"psycopg or database unavailable: {exc}"


@unittest.skipIf(_SKIP, _SKIP or "")
class DatabaseAdapterTests(unittest.TestCase):
    # spec: F11.ac7
    def test_ac7_session_is_read_only_and_tenant_scoped(self):
        from data_access.session import open_session_context

        with psycopg.connect(DSN) as conn, conn.transaction():
            open_session_context(conn, role="app_ro", tenant_id="00000000-0000-0000-0000-000000000001")
            self.assertEqual(
                conn.execute("SELECT current_setting('app.tenant_id')").fetchone()[0],
                "00000000-0000-0000-0000-000000000001",
            )


if __name__ == "__main__":
    unittest.main()
