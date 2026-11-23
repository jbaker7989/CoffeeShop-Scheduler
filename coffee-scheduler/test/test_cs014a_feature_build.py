"""CS-014a — feature engineering & training dataset (F9, dataset slice).

Three test categories (all must pass before merge):
  1. Unit (offline)      — the pure derivation helpers, no database.
  2. DB integration      — build against live Postgres, rollback-only, assert on rows.
  3. Migration/CI gates  — 0019 round-trip + the static CI scripts (run separately).

Runs under both `python3 -m unittest discover -s test` and `python -m pytest test/`.
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCHEDULER_ROOT))

from agents.demand_forecast.feature_build import (  # noqa: E402
    APPLICABLE_HOLIDAY_JURISDICTIONS,
    FEATURE_VERSION,
    HOLDOUT_DAYS,
    LOW_HISTORY_SHARE,
    SOURCE_WINDOW,
    build_cell_universe,
    build_feature_set,
    calendar_features,
    history_cutoff,
    low_history_flag,
    low_history_map,
    provenance_flags,
    rolling_features,
    weather_features,
    zero_fill,
)
from contracts.forecast import PINNED_TAXONOMY_VERSION, StoreOpenWindow  # noqa: E402

TENANT_ID = "00000000-0000-0000-0000-000000000001"
OTHER_TENANT_ID = "00000000-0000-0000-0000-0000000000ff"
DSN = os.environ.get("DATABASE_URL", "postgresql://coffeeshop:coffeeshop@localhost:5432/coffeeshop")

# A tiny, fully-known store-hours fixture for the offline universe tests.
TINY_HOURS = {5: StoreOpenWindow(open_hour=6, close_hour=8, open_weekdays=frozenset(range(7)))}
TINY_WINDOW = (date(2023, 1, 1), date(2023, 1, 2))  # Sun, Mon


# --- Category 1: unit (offline) ------------------------------------------------


class CellUniverseTests(unittest.TestCase):
    # spec: F9.ac1 — one cell per (store, category, date-in-window, open clock-hour); closed hours absent.
    def test_ac1_cell_universe_is_open_hours_only(self):
        cells = build_cell_universe(TINY_HOURS, {"Coffee", "Tea"}, TINY_WINDOW)
        # 2 dates x 2 open hours (6, 7 — close 8 is exclusive) x 2 categories.
        self.assertEqual(len(cells), 2 * 2 * 2)
        hours = {hour for (_loc, _cat, _d, hour) in cells}
        self.assertEqual(hours, {6, 7})
        self.assertTrue(all(loc == 5 for (loc, _c, _d, _h) in cells))
        # No duplicate cells.
        self.assertEqual(len(cells), len(set(cells)))

    # spec: F9.ac1 — a non-open weekday contributes no cells.
    def test_ac1_closed_weekday_absent(self):
        weekday_only = {5: StoreOpenWindow(6, 8, frozenset({0}))}  # Mondays only
        cells = build_cell_universe(weekday_only, {"Coffee"}, TINY_WINDOW)
        dates = {d for (_l, _c, d, _h) in cells}
        self.assertEqual(dates, {date(2023, 1, 2)})  # the Monday only


class ZeroFillTests(unittest.TestCase):
    # spec: F9.ac2 — an open hour with no sale is 0, a sold hour carries the summed qty.
    def test_ac2_zero_fill_for_unsold_hour(self):
        cells = [(5, "Coffee", date(2023, 1, 2), 6), (5, "Coffee", date(2023, 1, 2), 7)]
        sales = {(5, "Coffee", date(2023, 1, 2), 6): (12, Decimal("48.00"))}
        filled = zero_fill(cells, sales)
        self.assertEqual(filled[(5, "Coffee", date(2023, 1, 2), 6)], (12, Decimal("48.00")))
        self.assertEqual(filled[(5, "Coffee", date(2023, 1, 2), 7)], (0, Decimal("0.00")))


class CalendarFeatureTests(unittest.TestCase):
    # spec: F9.ac3 — calendar flags come from obs_date, not today.
    def test_ac3_calendar_features_from_obs_date(self):
        holidays = {date(2023, 1, 2)}
        school = {date(2023, 1, 2): False}
        monday = calendar_features(date(2023, 1, 2), holidays, school)
        self.assertEqual(monday["day_of_week"], 0)
        self.assertFalse(monday["is_weekend"])
        self.assertTrue(monday["is_holiday"])
        self.assertFalse(monday["school_in_session"])
        sunday = calendar_features(date(2023, 1, 1), holidays, school)
        self.assertTrue(sunday["is_weekend"])
        self.assertFalse(sunday["is_holiday"])
        # A date with no school row defaults to not-in-session, never raising.
        self.assertFalse(calendar_features(date(2023, 3, 15), set(), {})["school_in_session"])


class WeatherFeatureTests(unittest.TestCase):
    # spec: F9.ac4 — present weather maps through; a missing row keeps nulls, never drops the cell.
    def test_ac4_weather_missing_keeps_nulls(self):
        wx = {
            (5, date(2023, 1, 2), 6): {
                "temperature_2m": Decimal("41.0"),
                "precipitation": Decimal("0.0"),
                "rain": Decimal("0.0"),
                "snowfall": Decimal("0.0"),
                "weather_bucket": "clear",
                "in_trading_hours": True,
            }
        }
        present = weather_features((5, date(2023, 1, 2), 6), wx)
        self.assertEqual(present["temperature_2m"], Decimal("41.0"))
        self.assertEqual(present["weather_bucket"], "clear")
        self.assertIs(present["in_trading_hours"], True)
        missing = weather_features((5, date(2023, 1, 2), 7), wx)
        self.assertEqual(
            missing,
            {
                "temperature_2m": None,
                "precipitation": None,
                "rain": None,
                "snowfall": None,
                "weather_bucket": None,
                "in_trading_hours": None,
            },
        )


class RollingFeatureTests(unittest.TestCase):
    # spec: F9.ac5 — lag1 is the prior open hour; roll7 is the trailing same-hour mean; nulls at series start.
    def test_ac5_rolling_and_lag_series_start_null(self):
        # Same (store, category); three consecutive days at hour 6 with items 10, 20, 30.
        rows = [
            (5, "Coffee", date(2023, 1, 1), 6, 10),
            (5, "Coffee", date(2023, 1, 2), 6, 20),
            (5, "Coffee", date(2023, 1, 3), 6, 30),
        ]
        roll = rolling_features(rows)
        lag1_day1, r7_day1 = roll[(5, "Coffee", date(2023, 1, 1), 6)]
        lag1_day2, r7_day2 = roll[(5, "Coffee", date(2023, 1, 2), 6)]
        lag1_day3, r7_day3 = roll[(5, "Coffee", date(2023, 1, 3), 6)]
        # Series start: no prior.
        self.assertIsNone(lag1_day1)
        self.assertIsNone(r7_day1)
        # lag1 = immediately prior open hour of the same series.
        self.assertEqual(lag1_day2, 10)
        self.assertEqual(lag1_day3, 20)
        # roll7 = mean of prior same-hour observations.
        self.assertEqual(r7_day2, Decimal("10"))
        self.assertEqual(r7_day3, Decimal("15"))

    # spec: F9.ac5 — lag crosses hours within a day before advancing the date.
    def test_ac5_lag_orders_by_date_then_hour(self):
        rows = [
            (5, "Coffee", date(2023, 1, 1), 6, 5),
            (5, "Coffee", date(2023, 1, 1), 7, 9),
        ]
        roll = rolling_features(rows)
        self.assertIsNone(roll[(5, "Coffee", date(2023, 1, 1), 6)][0])
        self.assertEqual(roll[(5, "Coffee", date(2023, 1, 1), 7)][0], 5)


class MaskTests(unittest.TestCase):
    # spec: F9.ac6 — the three dataset-gap flags are all false.
    def test_ac6_provenance_flags_all_false(self):
        flags = provenance_flags()
        self.assertEqual(
            flags,
            {"has_basket_id": False, "has_evening_history": False, "has_annual_seasonality": False},
        )

    # spec: F9.ac6 — low_history is a strict `< threshold` share test.
    def test_ac6_low_history_threshold(self):
        self.assertTrue(low_history_flag(Decimal("0.04")))
        self.assertFalse(low_history_flag(Decimal(LOW_HISTORY_SHARE)))  # exactly at threshold is not low
        self.assertFalse(low_history_flag(Decimal("0.90")))

    def test_constants(self):
        self.assertEqual(FEATURE_VERSION, "forecast-features-v2")
        self.assertEqual(SOURCE_WINDOW, (date(2023, 1, 1), date(2023, 6, 30)))

    # spec: F9.ac23 — low_history is target-derived, so it must ignore the reserved holdout tail.
    def test_ac23_low_history_ignores_holdout_dates(self):
        window = (date(2023, 1, 1), date(2023, 3, 1))  # 60 days, so a 28-day tail leaves history
        cutoff = history_cutoff(window)
        self.assertEqual(cutoff, date(2023, 2, 2))  # first of the final 28 inclusive dates
        hours = {5: StoreOpenWindow(open_hour=6, close_hour=8, open_weekdays=frozenset(range(7)))}
        cells = build_cell_universe(hours, ["Coffee"], window)
        # Hour 6 is busy only BEFORE the cutoff; hour 7 is busy only INSIDE the holdout tail.
        sales = {}
        for location_id, category, day, hour in cells:
            busy_early = hour == 6 and day < cutoff
            busy_late = hour == 7 and day >= cutoff
            if busy_early or busy_late:
                sales[(location_id, category, day, hour)] = (100, Decimal("500.00"))
        filled = zero_fill(cells, sales)
        scoped = low_history_map(cells, filled, cutoff=cutoff)
        # Pre-holdout history sees only hour 6, so hour 7 is (correctly) the low-history hour.
        self.assertFalse(scoped[(5, 6)])
        self.assertTrue(scoped[(5, 7)])
        # Without a cutoff the holdout spike leaks in and flips hour 7 — the defect being fixed.
        leaky = low_history_map(cells, filled, cutoff=None)
        self.assertFalse(leaky[(5, 7)])
        self.assertNotEqual(scoped[(5, 7)], leaky[(5, 7)])

    # spec: F9.ac23 — a window shorter than the holdout still yields a flag for every store-hour.
    def test_ac23_low_history_falls_back_on_short_window(self):
        cells = build_cell_universe(TINY_HOURS, ["Coffee"], TINY_WINDOW)
        filled = zero_fill(cells, {cells[0]: (50, Decimal("10.00"))})
        flags = low_history_map(cells, filled, cutoff=history_cutoff(TINY_WINDOW))
        self.assertEqual({key[0] for key in flags}, {5})
        self.assertEqual({key[1] for key in flags}, {6, 7})
        self.assertEqual(HOLDOUT_DAYS, 28)

    # spec: F9.ac24 — the rolling mean truncates to the 7 most recent prior same-hour observations.
    def test_ac24_rolling_truncates_to_seven_observations(self):
        # Ten prior same-hour observations: 1..10 on consecutive days at hour 6.
        rows = [(5, "Coffee", date(2023, 1, day), 6, day) for day in range(1, 11)]
        rolling = rolling_features(rows)
        # The 10th row's mean must use only observations 3..9 (the last 7), not all nine priors.
        tenth = rolling[(5, "Coffee", date(2023, 1, 10), 6)]
        self.assertEqual(tenth[0], 9)  # lag-1 is the 9th observation
        self.assertEqual(tenth[1], Decimal(sum(range(3, 10))) / Decimal(7))
        self.assertNotEqual(tenth[1], Decimal(sum(range(1, 10))) / Decimal(9))

    # spec: F9.ac24 — holiday reads are scoped to the jurisdictions that apply to these stores.
    def test_ac24_holiday_jurisdictions_are_scoped(self):
        from agents.demand_forecast.feature_build import _HOLIDAY_SQL

        self.assertIn("jurisdiction", _HOLIDAY_SQL)
        self.assertEqual(APPLICABLE_HOLIDAY_JURISDICTIONS, ("US-FED", "NY-STATE"))

    # spec: F9.ac25 — `revenue` is derived from the target, so no trainer may select it.
    def test_ac25_trainer_never_selects_revenue(self):
        from agents.demand_forecast.model import _FEATURE_SQL

        self.assertNotIn("revenue", _FEATURE_SQL.lower())
        self.assertIn("items", _FEATURE_SQL.lower())


# --- Category 2: DB integration (skip-gated) -----------------------------------

_SKIP = None
try:
    import psycopg

    with psycopg.connect(DSN, connect_timeout=3) as _c:
        _c.execute("SELECT 1")
except Exception as exc:  # pragma: no cover - environment-dependent skip
    _SKIP = f"psycopg or database unavailable: {exc}"

TEST_VERSION = "test-cs014a"


class _Rollback(Exception):
    """Sentinel to unwind the build transaction so the test leaves no rows behind."""


@unittest.skipIf(_SKIP, _SKIP or "")
class FeatureBuildDatabaseTests(unittest.TestCase):
    """Build the feature set against live Postgres inside a rolled-back transaction."""

    def _build_and_assert(self, assertions):
        from data_access.session import open_session_context

        with psycopg.connect(DSN) as conn:
            try:
                with conn.transaction():
                    open_session_context(conn, role="app_rw", tenant_id=TENANT_ID)
                    count = build_feature_set(conn, feature_version=TEST_VERSION)
                    assertions(conn, count)
                    raise _Rollback
            except _Rollback:
                pass

    def _features(self, conn, where="", params=()):
        return conn.execute(
            f"SELECT * FROM forecast_features WHERE feature_version = %s {where}",
            (TEST_VERSION, *params),
        ).fetchall()

    # spec: F9.ac1 — persisted row count equals the open-hour cell universe.
    def test_ac1_db_row_count_matches_open_hours(self):
        from contracts.forecast import load_store_hours, load_vocabulary

        def check(conn, count):
            categories = load_vocabulary(conn, taxonomy_version=PINNED_TAXONOMY_VERSION)
            hours = load_store_hours(conn)
            expected = len(build_cell_universe(hours, categories, SOURCE_WINDOW))
            self.assertGreater(expected, 0)
            self.assertEqual(count, expected)
            db_count = conn.execute(
                "SELECT count(*) FROM forecast_features WHERE feature_version = %s", (TEST_VERSION,)
            ).fetchone()[0]
            self.assertEqual(db_count, expected)
            # The register-overhead pseudo-category is never emitted.
            cats = {
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT product_category FROM forecast_features WHERE feature_version = %s",
                    (TEST_VERSION,),
                ).fetchall()
            }
            self.assertNotIn("(per transaction overhead)", cats)

        self._build_and_assert(check)

    # spec: F9.ac2 — a cell's items equal the summed transaction_qty; unsold open hours are 0.
    def test_ac2_db_items_match_transactions(self):
        def check(conn, _count):
            row = conn.execute(
                "SELECT location_id, product_category, obs_date, obs_hour, items "
                "FROM forecast_features WHERE feature_version = %s AND items > 0 LIMIT 1",
                (TEST_VERSION,),
            ).fetchone()
            self.assertIsNotNone(row)
            loc, cat, d, hr, items = row
            expected = conn.execute(
                "SELECT COALESCE(SUM(transaction_qty), 0) FROM transactions "
                "WHERE store_id = %s AND product_category = %s AND transaction_date = %s "
                "AND EXTRACT(HOUR FROM transaction_time)::int = %s",
                (loc, cat, d, hr),
            ).fetchone()[0]
            self.assertEqual(items, expected)
            # At least one open hour was zero-filled (no sale), proving we don't drop empties.
            zeros = conn.execute(
                "SELECT count(*) FROM forecast_features WHERE feature_version = %s AND items = 0",
                (TEST_VERSION,),
            ).fetchone()[0]
            self.assertGreater(zeros, 0)

        self._build_and_assert(check)

    # spec: F9.ac3 — calendar flags are populated and not null.
    def test_ac3_db_calendar_flags_present(self):
        def check(conn, _count):
            nulls = conn.execute(
                "SELECT count(*) FROM forecast_features WHERE feature_version = %s "
                "AND (day_of_week IS NULL OR is_weekend IS NULL OR is_holiday IS NULL "
                "OR school_in_session IS NULL)",
                (TEST_VERSION,),
            ).fetchone()[0]
            self.assertEqual(nulls, 0)
            # A known 2023 federal holiday (Jan 2, New Year's Day observed) is flagged.
            flagged = conn.execute(
                "SELECT bool_and(is_holiday) FROM forecast_features "
                "WHERE feature_version = %s AND obs_date = DATE '2023-01-02'",
                (TEST_VERSION,),
            ).fetchone()[0]
            self.assertTrue(flagged)

        self._build_and_assert(check)

    # spec: F9.ac4 — the vast majority of cells carry a joined weather row.
    def test_ac4_db_weather_joined(self):
        def check(conn, count):
            joined = conn.execute(
                "SELECT count(*) FROM forecast_features WHERE feature_version = %s "
                "AND temperature_2m IS NOT NULL",
                (TEST_VERSION,),
            ).fetchone()[0]
            self.assertGreater(joined, count * 0.5)

        self._build_and_assert(check)

    # spec: F9.ac5 — rolling/lag populated past each series start, null exactly at the start.
    def test_ac5_db_rolling_present(self):
        def check(conn, _count):
            populated = conn.execute(
                "SELECT count(*) FROM forecast_features WHERE feature_version = %s "
                "AND items_lag_1h IS NOT NULL",
                (TEST_VERSION,),
            ).fetchone()[0]
            self.assertGreater(populated, 0)
            # The earliest cell of a series has a null lag.
            start_null = conn.execute(
                "SELECT items_lag_1h FROM forecast_features WHERE feature_version = %s "
                "ORDER BY location_id, product_category, obs_date, obs_hour LIMIT 1",
                (TEST_VERSION,),
            ).fetchone()[0]
            self.assertIsNone(start_null)

        self._build_and_assert(check)

    # spec: F9.ac6 — provenance flags false; busiest store-hour not low_history, a late hour is.
    def test_ac6_db_masks(self):
        def check(conn, _count):
            fs = conn.execute(
                "SELECT has_basket_id, has_evening_history, has_annual_seasonality "
                "FROM feature_sets WHERE feature_version = %s",
                (TEST_VERSION,),
            ).fetchone()
            self.assertEqual(tuple(fs), (False, False, False))
            # The single busiest (store, hour) in items terms is never flagged low_history.
            busiest = conn.execute(
                "SELECT location_id, obs_hour FROM forecast_features WHERE feature_version = %s "
                "GROUP BY location_id, obs_hour ORDER BY SUM(items) DESC LIMIT 1",
                (TEST_VERSION,),
            ).fetchone()
            low = conn.execute(
                "SELECT bool_or(low_history) FROM forecast_features WHERE feature_version = %s "
                "AND location_id = %s AND obs_hour = %s",
                (TEST_VERSION, busiest[0], busiest[1]),
            ).fetchone()[0]
            self.assertFalse(low)
            # At least one cell is flagged low_history (the thin evening hours).
            any_low = conn.execute(
                "SELECT count(*) FROM forecast_features WHERE feature_version = %s AND low_history",
                (TEST_VERSION,),
            ).fetchone()[0]
            self.assertGreater(any_low, 0)

        self._build_and_assert(check)

    # spec: F9.ac7 — idempotent rebuild (same count, no duplicates) and RLS fail-closed by tenant.
    def test_ac7_db_idempotent_and_rls_fail_closed(self):
        def check(conn, count):
            second = build_feature_set(conn, feature_version=TEST_VERSION)
            self.assertEqual(second, count)
            distinct = conn.execute(
                "SELECT count(*) FROM (SELECT DISTINCT location_id, product_category, obs_date, obs_hour "
                "FROM forecast_features WHERE feature_version = %s) q",
                (TEST_VERSION,),
            ).fetchone()[0]
            self.assertEqual(distinct, count)  # no duplicate logical cells
            fs_count = conn.execute(
                "SELECT row_count FROM feature_sets WHERE feature_version = %s", (TEST_VERSION,)
            ).fetchone()[0]
            self.assertEqual(fs_count, count)
            # Switch tenant context inside the same txn: RLS hides every row (fail-closed).
            conn.execute("SELECT set_config('app.tenant_id', %s, true)", (OTHER_TENANT_ID,))
            hidden = conn.execute(
                "SELECT count(*) FROM forecast_features WHERE feature_version = %s", (TEST_VERSION,)
            ).fetchone()[0]
            self.assertEqual(hidden, 0)
            # Restore so build/teardown stays consistent before rollback.
            conn.execute("SELECT set_config('app.tenant_id', %s, true)", (TENANT_ID,))

        self._build_and_assert(check)


if __name__ == "__main__":
    unittest.main()
