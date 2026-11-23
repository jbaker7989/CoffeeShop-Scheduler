"""CS-014b model-selection tests: offline helpers plus rollback-only DB coverage."""
from __future__ import annotations

import os
import sys
import unittest
from datetime import date
from pathlib import Path

import numpy as np

SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCHEDULER_ROOT))

from agents.demand_forecast.feature_build import FEATURE_VERSION, SOURCE_WINDOW, build_feature_set  # noqa: E402
from agents.demand_forecast.model import (  # noqa: E402
    APPROACHES,
    DEFAULT_SHRINKAGE_K,
    ModelError,
    deserialize_params,
    enforce_monotone_nonneg,
    feature_matrix,
    fit_empirical_quantiles,
    fit_linear_quantile_regression,
    fit_shrunk_empirical_quantiles,
    pinball_loss,
    predict_empirical,
    predict_linear,
    predict_shrunk_empirical,
    select_model,
    serialize_params,
)

TENANT_ID = "00000000-0000-0000-0000-000000000001"
DSN = os.environ.get("DATABASE_URL", "postgresql://coffeeshop:coffeeshop@localhost:5432/coffeeshop")


def _row(items=10, **overrides):
    row = {
        "location_id": 3, "product_category": "Coffee", "obs_date": date(2023, 1, 1), "obs_hour": 8,
        "items": items, "day_of_week": 6, "is_weekend": True, "is_holiday": False,
        "school_in_session": False, "temperature_2m": 40, "precipitation": 0,
        "items_lag_1h": 9, "items_roll_7d_same_hour": 8, "low_history": False,
    }
    row.update(overrides)
    return row


class ModelHelperTests(unittest.TestCase):
    # spec: F9.ac8
    def test_ac8_select_lower_pinball_candidate(self):
        selected = select_model({"empirical_quantile": {"validation_pinball": 1.1},
                                 "linear_quantile_regression": {"validation_pinball": 0.9}})
        self.assertEqual(selected, "linear_quantile_regression")
        tied = select_model({name: {"validation_pinball": 1.0} for name in ("empirical_quantile", "linear_quantile_regression")})
        self.assertEqual(tied, "empirical_quantile")

    # spec: F9.ac9
    def test_ac9_enforce_monotone_nonneg(self):
        self.assertEqual(enforce_monotone_nonneg(8, -2, 4), (0.0, 4.0, 8.0))
        self.assertEqual(enforce_monotone_nonneg(-5, -2, -3), (0.0, 0.0, 0.0))

    # spec: F9.ac10
    def test_ac10_feature_stats_are_reused_for_prediction(self):
        train = [_row(1, temperature_2m=None), _row(2, obs_hour=9, temperature_2m=50)]
        X, columns, stats = feature_matrix(train)
        repeated, repeated_columns, repeated_stats = feature_matrix(train, stats=stats)
        self.assertEqual(columns, repeated_columns)
        self.assertEqual(stats, repeated_stats)
        np.testing.assert_allclose(X, repeated)

    # spec: F9.ac11
    def test_ac11_training_window_is_six_months(self):
        self.assertEqual(SOURCE_WINDOW, (date(2023, 1, 1), date(2023, 6, 30)))
        self.assertNotIn("year", feature_matrix([_row()])[1])

    # spec: F9.ac12
    def test_ac12_empirical_fallback_and_nonnegative_prediction(self):
        model = fit_empirical_quantiles([_row(3), _row(5), _row(9, obs_hour=9)])
        unseen = predict_empirical([_row(0, location_id=99, product_category="Tea", obs_hour=23)], model)[0]
        values = enforce_monotone_nonneg(unseen["lower"], unseen["p70"], unseen["upper"])
        self.assertEqual(values, tuple(sorted(values)))
        self.assertGreaterEqual(values[0], 0)

    # spec: F9.ac13
    def test_ac13_serialize_round_trip_and_deterministic_linear_fit(self):
        empirical = fit_empirical_quantiles([_row(3), _row(7)])
        self.assertEqual(deserialize_params(serialize_params("empirical_quantile", empirical)), empirical)
        X = np.arange(20, dtype=float).reshape(10, 2)
        y = X[:, 0] + 2
        first = fit_linear_quantile_regression(X, y, 0.7, seed=1729)
        second = fit_linear_quantile_regression(X, y, 0.7, seed=1729)
        np.testing.assert_array_equal(first, second)
        self.assertLess(pinball_loss(y, predict_linear(X, first), 0.7), 1.0)

    # spec: F9.ac21 — count shrinkage pulls a noisy single-sample group toward the better-supported backup.
    def test_ac21_shrinkage_pulls_small_group_toward_backup(self):
        # One extreme single-sample group at hour 8; many well-behaved samples at the same (loc,cat,hour).
        rows = [_row(1000, is_holiday=True)] + [_row(10, is_weekend=False) for _ in range(40)]
        model = fit_shrunk_empirical_quantiles(rows, k=DEFAULT_SHRINKAGE_K)
        raw = fit_empirical_quantiles(rows)
        probe = _row(1000, is_holiday=True)  # hits the n=1 holiday group
        shrunk_p70 = predict_shrunk_empirical([probe], model)[0]["p70"]
        raw_p70 = predict_empirical([probe], raw)[0]["p70"]
        # The raw model echoes the lone 1000; shrinkage must move it decisively toward the ~10 backup.
        self.assertEqual(raw_p70, 1000.0)
        self.assertLess(shrunk_p70, 500.0)
        self.assertGreater(shrunk_p70, 10.0)

    # spec: F9.ac22 — the shrunk candidate is a registered approach that serializes round-trip exactly.
    def test_ac22_shrunk_candidate_registered_and_round_trips(self):
        self.assertIn("shrunk_empirical_quantile", APPROACHES)
        model = fit_shrunk_empirical_quantiles([_row(3), _row(7), _row(9, obs_hour=9)])
        restored = deserialize_params(serialize_params("shrunk_empirical_quantile", model))
        self.assertEqual(restored, model)
        # Deterministic prediction, and the contract repair still yields a sorted, non-negative triple.
        prediction = predict_shrunk_empirical([_row(5)], model)[0]
        self.assertEqual(predict_shrunk_empirical([_row(5)], model)[0], prediction)
        values = enforce_monotone_nonneg(prediction["lower"], prediction["p70"], prediction["upper"])
        self.assertEqual(values, tuple(sorted(values)))
        self.assertGreaterEqual(values[0], 0)
        # A non-finite shrinkage constant must fail closed, never silently produce NaN forecasts.
        for bad in (float("nan"), float("inf"), -1.0):
            with self.assertRaises(ModelError):
                fit_shrunk_empirical_quantiles([_row(3)], k=bad)


_SKIP = None
try:
    import psycopg

    with psycopg.connect(DSN, connect_timeout=3) as _connection:
        _connection.execute("SELECT 1")
except Exception as exc:  # pragma: no cover - environment-dependent skip
    _SKIP = f"psycopg or database unavailable: {exc}"


class _Rollback(Exception):
    """Sentinel to leave the database unchanged after an integration assertion."""


@unittest.skipIf(_SKIP, _SKIP or "")
class ModelDatabaseTests(unittest.TestCase):
    """Build, train, and predict inside a rollback-only tenant transaction."""

    # spec: F9.ac8
    # spec: F9.ac9
    # spec: F9.ac10
    # spec: F9.ac12
    # spec: F9.ac13
    def test_db_model_lifecycle(self):
        from agents.demand_forecast.model import load_model, persist_forecasts, predict, train
        from data_access.session import open_session_context

        with psycopg.connect(DSN) as conn:
            try:
                with conn.transaction():
                    open_session_context(conn, role="app_rw", tenant_id=TENANT_ID)
                    build_feature_set(conn, feature_version="test-cs014b")
                    summary = train(conn, model_version="test-cs014b", feature_version="test-cs014b")
                    stored = load_model(conn, "test-cs014b")
                    self.assertEqual(stored["feature_version"], "test-cs014b")
                    self.assertEqual(set(stored["candidates"]), set(APPROACHES))
                    # spec: F9.ac11 — the model records only the six-month 2023 window, train before validation.
                    self.assertEqual(stored["train_window_start"], date(2023, 1, 1))
                    self.assertEqual(stored["validation_window_end"], date(2023, 6, 30))
                    self.assertLess(stored["train_window_end"], stored["validation_window_start"])
                    target = (date(2023, 6, 24), date(2023, 6, 30))
                    forecasts = predict(conn, "test-cs014b", target_window=target)
                    self.assertGreater(len(forecasts.records), 0)
                    self.assertEqual(persist_forecasts(conn, forecasts), len(forecasts.records))
                    with self.assertRaises(ModelError):
                        predict(conn, "test-cs014b", target_window=target, feature_version=FEATURE_VERSION)
                    second = train(conn, model_version="test-cs014b", feature_version="test-cs014b")
                    self.assertEqual(summary["approach"], second["approach"])
                    self.assertEqual(load_model(conn, "test-cs014b")["params"], stored["params"])
                    raise _Rollback
            except _Rollback:
                pass


if __name__ == "__main__":
    unittest.main()
