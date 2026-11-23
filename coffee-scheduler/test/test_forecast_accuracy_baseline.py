"""Forecast accuracy regression guard (F9).

CS-014a built the feature dataset and CS-014b the model, but neither pinned accuracy, so a
feature or model change could silently make the forecaster worse while every other gate stayed
green. This test closes that: it re-measures the forecaster on the reserved final-28-date holdout
and fails if any metric drifts past its recorded tolerance.

It is deliberately metric-agnostic — it defends the measured FLOOR in
`reports/forecast_accuracy_baseline.json` and takes no position on the open F5 acceptance-band
decision (`docs/decisions/CS-014b-acceptance-metric.md`).

DB-gated and rollback-only; runs under both `python3 -m unittest discover -s test` and pytest.
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import unittest
from datetime import date
from pathlib import Path

SCHEDULER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCHEDULER_ROOT))

from agents.demand_forecast.feature_build import (  # noqa: E402
    SOURCE_WINDOW,
    build_feature_set,
    history_cutoff,
)
from agents.demand_forecast.model import QUANTILES, pinball_loss, predict, train  # noqa: E402

TENANT_ID = "00000000-0000-0000-0000-000000000001"
DSN = os.environ.get("DATABASE_URL", "postgresql://coffeeshop:coffeeshop@localhost:5432/coffeeshop")
BASELINE_PATH = SCHEDULER_ROOT / "reports" / "forecast_accuracy_baseline.json"
_VERSION = "test-accuracy-baseline"

_ACTUALS_SQL = """
SELECT location_id, product_category, obs_date, obs_hour, items
FROM forecast_features WHERE feature_version = %s AND obs_date BETWEEN %s AND %s
"""


def load_baseline() -> dict:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def score(records, actual: dict) -> dict:
    """Compute the holdout metrics from contract records plus the actual items per cell.

    Also reports how far the forecast and actual key sets diverge. Silently skipping unmatched
    cells would let a shrinking scored population *improve* the error metrics (the thin evening
    hours are the hardest), so the caller asserts both counts are zero rather than trusting a
    row-count floor.
    """
    forecast_keys = {
        (r.location_id, r.product_category, r.forecast_hour.date(), r.forecast_hour.hour) for r in records
    }
    err_sum = act_sum = 0.0
    predicted: list[float] = []
    actuals: list[float] = []
    hits = covered = 0
    groups: dict[tuple, list[tuple[float, float]]] = {}
    for record in records:
        key = (record.location_id, record.product_category, record.forecast_hour.date(), record.forecast_hour.hour)
        if key not in actual:
            continue
        observed, p70 = actual[key], float(record.p70)
        err_sum += abs(p70 - observed)
        act_sum += observed
        predicted.append(p70)
        actuals.append(observed)
        hits += 1 if observed <= p70 else 0
        covered += 1 if float(record.lower) <= observed <= float(record.upper) else 0
        groups.setdefault((record.location_id, record.product_category, record.forecast_hour.hour), []).append(
            (p70, observed)
        )
    if not actuals or act_sum <= 0:
        raise AssertionError("holdout produced no scorable rows; the measurement is invalid")
    group_mapes = []
    unscorable = 0
    for pairs in groups.values():
        positive = [(p, a) for p, a in pairs if a > 0]
        if positive:
            group_mapes.append(statistics.mean(100 * abs(p - a) / a for p, a in positive))
        else:
            unscorable += 1
    return {
        "wape_pct": 100 * err_sum / act_sum,
        "pinball_p70": pinball_loss(actuals, predicted, QUANTILES["p70"]),
        "p70_hit_rate": hits / len(actuals),
        "interval_coverage": covered / len(actuals),
        "group_mape_median_pct": statistics.median(group_mapes),
        "n_scored": len(actuals),
        "unscorable_groups": unscorable,
        "forecasts_without_actual": len(forecast_keys - set(actual)),
        "actuals_without_forecast": len(set(actual) - forecast_keys),
    }


class BaselineArtifactTests(unittest.TestCase):
    # spec: F9.ac26 — a committed, schema-versioned baseline defines the accuracy floor.
    def test_ac26_baseline_artifact_is_well_formed(self):
        baseline = load_baseline()
        self.assertEqual(baseline["schema_version"], "forecast-accuracy-baseline-v1")
        for name in ("wape_pct", "pinball_p70", "p70_hit_rate", "interval_coverage", "group_mape_median_pct"):
            self.assertIsInstance(baseline["metrics"][name], (int, float))
        for name in ("wape_pct_abs", "pinball_p70_abs", "group_mape_median_pct_abs", "calibration_drift_abs"):
            self.assertGreater(baseline["tolerances"][name], 0)
        # Consumed by the DB test below, so a baseline missing them must fail here, not with a
        # KeyError mid-measurement.
        self.assertGreater(baseline["tolerances"]["min_scored_rows"], 0)
        self.assertGreaterEqual(baseline["tolerances"]["max_unscorable_groups"], 0)
        self.assertGreater(baseline["n_scored"], 0)
        # The holdout is the tail `low_history` is scoped away from. NOTE: the lag/rolling columns
        # are NOT blind to it — they use actuals strictly before each scored row by design, so this
        # floor is a one-step-ahead score, not a 28-day-ahead one (see the baseline's `limitations`).
        self.assertEqual(baseline["holdout_window"][0], history_cutoff(SOURCE_WINDOW).isoformat())
        self.assertEqual(baseline["holdout_window"][1], SOURCE_WINDOW[1].isoformat())
        self.assertTrue(baseline.get("limitations"), "the baseline must state what it does not cover")


_SKIP = None
try:
    import psycopg

    with psycopg.connect(DSN, connect_timeout=3) as _connection:
        _connection.execute("SELECT 1")
except Exception as exc:  # pragma: no cover - environment-dependent skip
    _SKIP = f"psycopg or database unavailable: {exc}"


class _Rollback(Exception):
    """Sentinel that leaves the database unchanged after the measurement."""


@unittest.skipIf(_SKIP, _SKIP or "")
class AccuracyRegressionTests(unittest.TestCase):
    """Re-measure the forecaster and defend the recorded floor."""

    # spec: F9.ac26
    def test_ac26_accuracy_does_not_regress_past_tolerance(self):
        from data_access.session import open_session_context

        baseline = load_baseline()
        recorded, tolerance = baseline["metrics"], baseline["tolerances"]
        holdout_start = history_cutoff(SOURCE_WINDOW)
        holdout = (holdout_start, SOURCE_WINDOW[1])
        fit_window = (SOURCE_WINDOW[0], date.fromordinal(holdout_start.toordinal() - 1))

        with psycopg.connect(DSN) as conn:
            try:
                with conn.transaction():
                    open_session_context(conn, role="app_rw", tenant_id=TENANT_ID)
                    build_feature_set(conn, feature_version=_VERSION)
                    train(conn, model_version=_VERSION, feature_version=_VERSION, window=fit_window)
                    forecasts = predict(conn, _VERSION, target_window=holdout)
                    rows = conn.execute(_ACTUALS_SQL, (_VERSION, *holdout)).fetchall()
                    actual = {(r[0], r[1], r[2], r[3]): float(r[4]) for r in rows}
                    measured = score(forecasts.records, actual)
                    raise _Rollback
            except _Rollback:
                pass

        # The population must be identical to the baseline's, or the metrics below are computed
        # over different rows and are not comparable to the recorded floor. Dropping the thin
        # evening cells would *improve* every error metric, so this is asserted exactly.
        self.assertEqual(
            measured["forecasts_without_actual"], 0,
            "forecast cells have no matching actual; the scored population drifted",
        )
        self.assertEqual(
            measured["actuals_without_forecast"], 0,
            "actual cells were never forecast; the scored population drifted",
        )
        self.assertEqual(
            measured["n_scored"], baseline["n_scored"],
            f"scored {measured['n_scored']} rows vs baseline {baseline['n_scored']}; "
            "refresh reports/forecast_accuracy_baseline.json if this change is intended",
        )
        self.assertGreaterEqual(measured["n_scored"], tolerance["min_scored_rows"])
        self.assertLessEqual(measured["unscorable_groups"], tolerance["max_unscorable_groups"])
        # Error metrics: lower is better, so only upward drift past tolerance is a regression.
        self.assertLessEqual(
            measured["wape_pct"], recorded["wape_pct"] + tolerance["wape_pct_abs"],
            f"WAPE regressed: {measured['wape_pct']:.3f}% vs baseline {recorded['wape_pct']}%",
        )
        self.assertLessEqual(
            measured["pinball_p70"], recorded["pinball_p70"] + tolerance["pinball_p70_abs"],
            f"pinball regressed: {measured['pinball_p70']:.5f} vs baseline {recorded['pinball_p70']}",
        )
        self.assertLessEqual(
            measured["group_mape_median_pct"],
            recorded["group_mape_median_pct"] + tolerance["group_mape_median_pct_abs"],
            f"median group MAPE regressed: {measured['group_mape_median_pct']:.3f}%",
        )
        # Calibration: "worse" means drifting further from nominal in either direction.
        drift = tolerance["calibration_drift_abs"]
        for name in ("p70_hit_rate", "interval_coverage"):
            nominal = baseline["nominal"][name]
            allowed = abs(recorded[name] - nominal) + drift
            self.assertLessEqual(
                abs(measured[name] - nominal), allowed,
                f"{name} drifted from nominal {nominal}: {measured[name]:.4f} (baseline {recorded[name]})",
            )


if __name__ == "__main__":
    unittest.main()
