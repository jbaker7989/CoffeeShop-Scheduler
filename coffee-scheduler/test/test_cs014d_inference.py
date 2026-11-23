"""CS-014d — forward inference features (F9 inference slice).

Three categories: offline unit tests for the pure row builder, a DB-gated rollback-only test that
scores a FUTURE week end to end, and the static CI gates run separately.

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
    SOURCE_WINDOW,
    FeatureBuildError,
    build_cell_universe,
    build_feature_set,
)
from agents.demand_forecast.inference import (  # noqa: E402
    INFERENCE_COLUMNS,
    build_inference_rows,
    build_rows,
)
from agents.demand_forecast.model import ModelError, predict, train  # noqa: E402
from contracts.forecast import StoreOpenWindow  # noqa: E402

TENANT_ID = "00000000-0000-0000-0000-000000000001"
DSN = os.environ.get("DATABASE_URL", "postgresql://coffeeshop:coffeeshop@localhost:5432/coffeeshop")

HOURS = {5: StoreOpenWindow(open_hour=6, close_hour=8, open_weekdays=frozenset(range(7)))}
WINDOW = (date(2026, 12, 7), date(2026, 12, 8))


class InferenceRowTests(unittest.TestCase):
    def _rows(self, *, priors=None, weather=None):
        cells = build_cell_universe(HOURS, ["Coffee"], WINDOW)
        return build_rows(
            cells,
            holidays={date(2026, 12, 8)},
            school_days={date(2026, 12, 7): True, date(2026, 12, 8): False},
            weather=weather or {},
            priors=priors if priors is not None else {(5, "Coffee", 6): (Decimal("3.5"), True)},
        )

    # spec: F9.ac27 — inference rows carry no target and never invent an unobserved lag.
    def test_ac27_rows_are_target_free_and_do_not_invent_a_lag(self):
        rows = self._rows()
        self.assertEqual(len(rows), 4)  # 2 days x 2 open hours x 1 category
        for row in rows:
            self.assertEqual(set(row), set(INFERENCE_COLUMNS))
            self.assertNotIn("items", row)  # the target is never fabricated for a future week
            self.assertNotIn("revenue", row)
            self.assertIsNone(row["items_lag_1h"])  # the previous open hour has not happened

    # spec: F9.ac27 — calendar flags come from the row's own date, and absent weather stays null.
    def test_ac27_calendar_from_date_and_missing_weather_is_null(self):
        rows = self._rows()
        by_date = {(row["obs_date"], row["obs_hour"]): row for row in rows}
        monday = by_date[(date(2026, 12, 7), 6)]
        tuesday = by_date[(date(2026, 12, 8), 6)]
        self.assertEqual(monday["day_of_week"], 0)
        self.assertFalse(monday["is_weekend"])
        self.assertFalse(monday["is_holiday"])
        self.assertTrue(monday["school_in_session"])
        self.assertTrue(tuesday["is_holiday"])
        self.assertFalse(tuesday["school_in_session"])
        # weather_hourly holds 2023 actuals only, so a forward cell is honestly null, not guessed.
        self.assertIsNone(monday["temperature_2m"])
        self.assertIsNone(monday["precipitation"])

    # spec: F9.ac28 — the training priors supply the rolling mean and the low-history flag.
    def test_ac28_priors_supply_rolling_mean_and_low_history(self):
        rows = self._rows()
        hour6 = [row for row in rows if row["obs_hour"] == 6]
        hour7 = [row for row in rows if row["obs_hour"] == 7]
        for row in hour6:
            self.assertEqual(row["items_roll_7d_same_hour"], Decimal("3.5"))
            self.assertTrue(row["low_history"])
        # No prior for hour 7: the unknown stays null rather than defaulting to zero demand.
        for row in hour7:
            self.assertIsNone(row["items_roll_7d_same_hour"])
            self.assertIsNone(row["low_history"])

    # spec: F9.ac28 — an empty prior set is still built, but never silently turned into zeros.
    def test_ac28_absent_priors_do_not_become_zero(self):
        rows = self._rows(priors={})
        self.assertTrue(rows)
        self.assertTrue(all(row["items_roll_7d_same_hour"] is None for row in rows))


_SKIP = None
try:
    import psycopg

    with psycopg.connect(DSN, connect_timeout=3) as _connection:
        _connection.execute("SELECT 1")
except Exception as exc:  # pragma: no cover - environment-dependent skip
    _SKIP = f"psycopg or database unavailable: {exc}"


class _Rollback(Exception):
    """Sentinel leaving the database unchanged after the assertions."""


@unittest.skipIf(_SKIP, _SKIP or "")
class InferenceDatabaseTests(unittest.TestCase):
    """Score a FUTURE week that has no observed demand, rollback-only."""

    # spec: F9.ac29
    def test_ac29_future_window_yields_contract_valid_forecasts(self):
        from agents.demand_forecast.model import persist_forecasts
        from data_access.session import open_session_context

        version = "test-cs014d"
        with psycopg.connect(DSN) as conn:
            try:
                with conn.transaction():
                    open_session_context(conn, role="app_rw", tenant_id=TENANT_ID)
                    build_feature_set(conn, feature_version=version)
                    train(conn, model_version=version, feature_version=version,
                          window=(SOURCE_WINDOW[0], date(2023, 6, 2)))

                    # The future window has no forecast_features rows at all: that is the point.
                    stored = conn.execute(
                        "SELECT count(*) FROM forecast_features WHERE feature_version = %s "
                        "AND obs_date BETWEEN %s AND %s",
                        (version, *WINDOW),
                    ).fetchone()[0]
                    self.assertEqual(stored, 0)

                    rows = build_inference_rows(conn, window=WINDOW, history_feature_version=version)
                    self.assertGreater(len(rows), 0)
                    forecasts = predict(conn, version, target_window=WINDOW, rows=rows)
                    self.assertEqual(len(forecasts.records), len(rows))
                    # predict() applies the canonical contract validation, so a future week is
                    # emitted under exactly the same guarantees as a historical one.
                    self.assertTrue(all(record.forecast_hour.year == 2026 for record in forecasts.records))
                    self.assertEqual(persist_forecasts(conn, forecasts), len(forecasts.records))
                    raise _Rollback
            except _Rollback:
                pass

    # spec: F9.ac29 — fail closed rather than emitting an empty or unsupported forecast set.
    def test_ac29_fails_closed_on_bad_window_or_empty_rows(self):
        from data_access.session import open_session_context

        version = "test-cs014d-guard"
        with psycopg.connect(DSN) as conn:
            try:
                with conn.transaction():
                    open_session_context(conn, role="app_rw", tenant_id=TENANT_ID)
                    build_feature_set(conn, feature_version=version)
                    train(conn, model_version=version, feature_version=version,
                          window=(SOURCE_WINDOW[0], date(2023, 6, 2)))
                    with self.assertRaises(FeatureBuildError):
                        build_inference_rows(conn, window=(WINDOW[1], WINDOW[0]),
                                             history_feature_version=version)
                    with self.assertRaises(FeatureBuildError):
                        build_inference_rows(conn, window=WINDOW, history_feature_version="no-such-version")
                    with self.assertRaises(ModelError):
                        predict(conn, version, target_window=WINDOW, rows=[])
                    raise _Rollback
            except _Rollback:
                pass


if __name__ == "__main__":
    unittest.main()
