"""CS-014b demand-model training, selection, and contract prediction.

The model intentionally has no annual term: it only learns from the supplied six-month
feature window.  Numeric work is deterministic and uses NumPy alone.
"""
from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from agents.demand_forecast.feature_build import FEATURE_VERSION, SOURCE_WINDOW
from contracts.forecast import (
    CANONICAL_TZ,
    CONTRACT_VERSION,
    PINNED_TAXONOMY_VERSION,
    ForecastRecord,
    ForecastSet,
    load_store_hours,
    load_vocabulary,
    validate_set,
)

QUANTILES = {"lower": 0.10, "p70": 0.70, "upper": 0.90}
DEFAULT_VALIDATION_DAYS = 14
DEFAULT_SEED = 1729
APPROACHES = ("empirical_quantile", "linear_quantile_regression", "shrunk_empirical_quantile")
DEFAULT_SHRINKAGE_K = 20.0

_NUMERIC_COLUMNS = (
    "day_of_week", "is_weekend", "is_holiday", "school_in_session", "temperature_2m",
    "precipitation", "items_lag_1h", "items_roll_7d_same_hour", "low_history",
)
_FEATURE_COLUMNS = ("hour_sin", "hour_cos", *_NUMERIC_COLUMNS)
_FEATURE_SQL = """
SELECT location_id, product_category, obs_date, obs_hour, items, day_of_week, is_weekend,
       is_holiday, school_in_session, temperature_2m, precipitation, items_lag_1h,
       items_roll_7d_same_hour, low_history
FROM forecast_features
WHERE feature_version = %s AND obs_date BETWEEN %s AND %s
ORDER BY obs_date, obs_hour, location_id, product_category
"""


class ModelError(RuntimeError):
    """Raised when a model artifact or its feature data is invalid or unavailable."""


def _value(row: Mapping[str, Any], name: str) -> Any:
    return row.get(name)


def _number(value: Any) -> float:
    if value is None:
        return np.nan
    return float(value)


def pinball_loss(actual, predicted, tau) -> float:
    """Return mean quantile (pinball) loss for the supplied quantile ``tau``."""
    actual_array = np.asarray(actual, dtype=float)
    predicted_array = np.asarray(predicted, dtype=float)
    if actual_array.size == 0 or actual_array.shape != predicted_array.shape:
        raise ModelError("pinball inputs must be non-empty and have identical shapes")
    error = actual_array - predicted_array
    return float(np.mean(np.maximum(tau * error, (tau - 1.0) * error)))


def feature_matrix(rows, stats: dict | None = None) -> tuple[np.ndarray, list[str], dict]:
    """Make the deterministic standardized design matrix and its reusable statistics."""
    row_list = list(rows)
    if not row_list:
        raise ModelError("cannot construct features from no rows")
    raw = np.empty((len(row_list), len(_FEATURE_COLUMNS)), dtype=float)
    for index, row in enumerate(row_list):
        hour = _number(_value(row, "obs_hour"))
        raw[index, 0] = np.sin(2.0 * np.pi * hour / 24.0)
        raw[index, 1] = np.cos(2.0 * np.pi * hour / 24.0)
        for offset, name in enumerate(_NUMERIC_COLUMNS, start=2):
            raw[index, offset] = _number(_value(row, name))
    if stats is None:
        impute = np.nanmean(raw, axis=0)
        impute = np.where(np.isnan(impute), 0.0, impute)
        filled = np.where(np.isnan(raw), impute, raw)
        mean = filled.mean(axis=0)
        std = filled.std(axis=0)
        std = np.where(std == 0.0, 1.0, std)
        result_stats = {"impute": impute.tolist(), "mean": mean.tolist(), "std": std.tolist()}
    else:
        try:
            impute = np.asarray(stats["impute"], dtype=float)
            mean = np.asarray(stats["mean"], dtype=float)
            std = np.asarray(stats["std"], dtype=float)
        except (KeyError, TypeError, ValueError) as exc:
            raise ModelError("invalid stored feature statistics") from exc
        if any(array.shape != (len(_FEATURE_COLUMNS),) for array in (impute, mean, std)):
            raise ModelError("stored feature statistics have the wrong shape")
        if np.any(std <= 0.0):
            raise ModelError("stored feature standard deviations must be positive")
        filled = np.where(np.isnan(raw), impute, raw)
        result_stats = {"impute": impute.tolist(), "mean": mean.tolist(), "std": std.tolist()}
    return (filled - mean) / std, list(_FEATURE_COLUMNS), result_stats


def fit_linear_quantile_regression(X, y, tau, *, seed=DEFAULT_SEED, iterations=800, lr=0.05) -> np.ndarray:
    """Fit intercept-inclusive linear quantile weights by deterministic full-batch GD."""
    features = np.asarray(X, dtype=float)
    target = np.asarray(y, dtype=float)
    if features.ndim != 2 or len(features) == 0 or target.shape != (len(features),):
        raise ModelError("X must be non-empty 2-D and y must align with X")
    if not 0.0 < tau < 1.0:
        raise ModelError("tau must be between zero and one")
    # The RNG construction fixes the documented seed contract while zero initialisation keeps GD stable.
    np.random.default_rng(seed)
    augmented = np.column_stack((np.ones(len(features)), features))
    weights = np.zeros(augmented.shape[1], dtype=float)
    for _ in range(iterations):
        residual = target - augmented @ weights
        derivative = np.where(residual > 0.0, -tau, 1.0 - tau)
        gradient = augmented.T @ derivative / len(augmented)
        weights -= lr * gradient
    return weights


def predict_linear(X, weights) -> np.ndarray:
    features = np.asarray(X, dtype=float)
    coefficients = np.asarray(weights, dtype=float)
    if features.ndim != 2 or coefficients.shape != (features.shape[1] + 1,):
        raise ModelError("linear weights do not match the feature matrix")
    return np.column_stack((np.ones(len(features)), features)) @ coefficients


def _day_type(row: Mapping[str, Any]) -> str:
    return "holiday" if _value(row, "is_holiday") else ("weekend" if _value(row, "is_weekend") else "weekday")


def _triple(values) -> dict[str, float]:
    numeric = np.asarray(values, dtype=float)
    return {name: float(np.quantile(numeric, tau)) for name, tau in QUANTILES.items()}


def fit_empirical_quantiles(rows) -> dict:
    """Fit grouped conditional quantiles with coarser and global fallback levels."""
    grouped: dict[tuple, list[float]] = {}
    coarse: dict[tuple, list[float]] = {}
    all_items: list[float] = []
    for row in rows:
        full_key = (_value(row, "location_id"), _value(row, "product_category"), _value(row, "obs_hour"), _day_type(row))
        coarse_key = full_key[:3]
        item = _number(_value(row, "items"))
        grouped.setdefault(full_key, []).append(item)
        coarse.setdefault(coarse_key, []).append(item)
        all_items.append(item)
    if not all_items:
        raise ModelError("cannot fit empirical quantiles from no rows")
    return {"groups": {key: _triple(values) for key, values in grouped.items()},
            "fallbacks": {key: _triple(values) for key, values in coarse.items()}, "global": _triple(all_items)}


def predict_empirical(rows, model) -> list[dict]:
    """Predict by full group, then location/category/hour, then global quantiles."""
    predictions = []
    for row in rows:
        key = (_value(row, "location_id"), _value(row, "product_category"), _value(row, "obs_hour"), _day_type(row))
        triple = model["groups"].get(key) or model["fallbacks"].get(key[:3]) or model["global"]
        predictions.append(dict(triple))
    return predictions


def _shrink_triple(primary: dict, backup: dict, n: int, k: float) -> dict:
    """Blend a group's quantiles toward a better-supported backup by count weight ``n/(n+k)``."""
    weight = n / (n + k)
    return {name: weight * float(primary[name]) + (1.0 - weight) * float(backup[name]) for name in QUANTILES}


def fit_shrunk_empirical_quantiles(rows, *, k: float = DEFAULT_SHRINKAGE_K) -> dict:
    """Fit grouped quantiles with count-based shrinkage toward coarser and global estimates.

    Small, noisy ``(loc, cat, hour, day_type)`` groups are pulled toward the better-supported
    ``(loc, cat, hour)`` and global quantiles, reducing the variance of sparse groups while
    keeping the fit deterministic and NumPy-only. It is selected only when it beats the other
    candidates on validation pinball; on the seeded 2023-H1 data it does not (see
    ``docs/decisions/CS-014b-acceptance-metric.md``).
    """
    if not np.isfinite(k) or k < 0.0:
        raise ModelError("shrinkage k must be a finite non-negative number")
    grouped: dict[tuple, list[float]] = {}
    coarse: dict[tuple, list[float]] = {}
    all_items: list[float] = []
    for row in rows:
        full_key = (_value(row, "location_id"), _value(row, "product_category"), _value(row, "obs_hour"), _day_type(row))
        item = _number(_value(row, "items"))
        grouped.setdefault(full_key, []).append(item)
        coarse.setdefault(full_key[:3], []).append(item)
        all_items.append(item)
    if not all_items:
        raise ModelError("cannot fit shrunk empirical quantiles from no rows")
    return {"groups": {key: {"q": _triple(values), "n": len(values)} for key, values in grouped.items()},
            "coarse": {key: {"q": _triple(values), "n": len(values)} for key, values in coarse.items()},
            "global": _triple(all_items), "k": float(k)}


def predict_shrunk_empirical(rows, model) -> list[dict]:
    """Predict with two-level count shrinkage: full group toward ``(loc,cat,hour)`` toward global."""
    k = float(model["k"])
    predictions = []
    for row in rows:
        key = (_value(row, "location_id"), _value(row, "product_category"), _value(row, "obs_hour"), _day_type(row))
        coarse = model["coarse"].get(key[:3])
        backup = _shrink_triple(coarse["q"], model["global"], coarse["n"], k) if coarse else dict(model["global"])
        group = model["groups"].get(key)
        predictions.append(_shrink_triple(group["q"], backup, group["n"], k) if group else dict(backup))
    return predictions


def enforce_monotone_nonneg(lower, p70, upper) -> tuple[float, float, float]:
    """Repair quantile crossing and negative values for the forecast contract."""
    return tuple(max(0.0, value) for value in sorted((float(lower), float(p70), float(upper))))


def select_model(candidate_metrics: dict[str, dict]) -> str:
    """Choose lowest validation pinball loss, retaining declared order for ties."""
    available = [name for name in APPROACHES if name in candidate_metrics]
    if not available:
        raise ModelError("no candidate metrics supplied")
    return min(available, key=lambda name: float(candidate_metrics[name]["validation_pinball"]))


def _json_safe(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return value


def _pack_empirical(payload: dict) -> dict:
    return {"groups": [{"key": list(key), "quantiles": value} for key, value in payload["groups"].items()],
            "fallbacks": [{"key": list(key), "quantiles": value} for key, value in payload["fallbacks"].items()],
            "global": payload["global"]}


def _unpack_empirical(payload: dict) -> dict:
    return {"groups": {tuple(entry["key"]): entry["quantiles"] for entry in payload["groups"]},
            "fallbacks": {tuple(entry["key"]): entry["quantiles"] for entry in payload["fallbacks"]},
            "global": payload["global"]}


def _pack_shrunk(payload: dict) -> dict:
    return {"groups": [{"key": list(key), "q": value["q"], "n": value["n"]} for key, value in payload["groups"].items()],
            "coarse": [{"key": list(key), "q": value["q"], "n": value["n"]} for key, value in payload["coarse"].items()],
            "global": payload["global"], "k": payload["k"]}


def _unpack_shrunk(payload: dict) -> dict:
    return {"groups": {tuple(entry["key"]): {"q": entry["q"], "n": entry["n"]} for entry in payload["groups"]},
            "coarse": {tuple(entry["key"]): {"q": entry["q"], "n": entry["n"]} for entry in payload["coarse"]},
            "global": payload["global"], "k": payload["k"]}


def serialize_params(approach, payload) -> dict:
    """Return a JSON-safe, self-identifying stored representation of a candidate payload."""
    if approach not in APPROACHES:
        raise ModelError(f"unknown approach {approach!r}")
    if approach == "empirical_quantile":
        safe_payload = _pack_empirical(payload)
    elif approach == "shrunk_empirical_quantile":
        safe_payload = _pack_shrunk(payload)
    else:
        safe_payload = _json_safe(payload)
    return {"approach": approach, "payload": safe_payload}


def deserialize_params(record) -> dict:
    """Reconstitute the runtime payload from a DB row or serialized parameter mapping."""
    params = record.get("params") if isinstance(record, Mapping) and "params" in record else record
    if isinstance(params, str):
        params = json.loads(params)
    if not isinstance(params, Mapping) or "approach" not in params or "payload" not in params:
        raise ModelError("invalid model parameters")
    payload = dict(params["payload"])
    if params["approach"] == "empirical_quantile":
        return _unpack_empirical(payload)
    if params["approach"] == "shrunk_empirical_quantile":
        return _unpack_shrunk(payload)
    return payload


def _current_tenant(conn):
    tenant_id = conn.execute("SELECT NULLIF(current_setting('app.tenant_id', true), '')::uuid").fetchone()[0]
    if tenant_id is None:
        raise ModelError("no tenant context set; refusing model operation")
    return tenant_id


def _load_features(conn, feature_version: str, window: tuple[date, date]) -> list[dict]:
    rows = conn.execute(_FEATURE_SQL, (feature_version, *window)).fetchall()
    names = ("location_id", "product_category", "obs_date", "obs_hour", "items", "day_of_week", "is_weekend",
             "is_holiday", "school_in_session", "temperature_2m", "precipitation", "items_lag_1h",
             "items_roll_7d_same_hour", "low_history")
    return [dict(zip(names, row, strict=True)) for row in rows]


def _candidate_predictions(train_rows, validation_rows, seed: int) -> dict[str, tuple[dict, list[dict]]]:
    """Fit every candidate on ``train_rows`` and return ``{approach: (payload, validation_predictions)}``."""
    empirical = fit_empirical_quantiles(train_rows)
    shrunk = fit_shrunk_empirical_quantiles(train_rows)
    X_train, _columns, stats = feature_matrix(train_rows)
    X_validation, _columns, _ = feature_matrix(validation_rows, stats=stats)
    target = np.asarray([_number(row["items"]) for row in train_rows])
    weights = {name: fit_linear_quantile_regression(X_train, target, tau, seed=seed) for name, tau in QUANTILES.items()}
    linear_predictions = [{name: float(predict_linear(X_validation, weights[name])[i]) for name in QUANTILES}
                          for i in range(len(validation_rows))]
    linear = {"weights": {name: value.tolist() for name, value in weights.items()}, "stats": stats}
    return {"empirical_quantile": (empirical, predict_empirical(validation_rows, empirical)),
            "linear_quantile_regression": (linear, linear_predictions),
            "shrunk_empirical_quantile": (shrunk, predict_shrunk_empirical(validation_rows, shrunk))}


def _metrics(rows, predictions) -> dict[str, float]:
    actual = [_number(row["items"]) for row in rows]
    p70 = [prediction["p70"] for prediction in predictions]
    coverage = np.mean(
        [prediction["lower"] <= value <= prediction["upper"] for value, prediction in zip(actual, predictions, strict=True)]
    )
    return {"validation_pinball": pinball_loss(actual, p70, QUANTILES["p70"]), "validation_coverage": float(coverage)}


def train(conn, *, model_version, feature_version=FEATURE_VERSION, taxonomy_version=PINNED_TAXONOMY_VERSION,
          window=SOURCE_WINDOW, validation_days=DEFAULT_VALIDATION_DAYS, seed=DEFAULT_SEED) -> dict:
    """Fit both candidates, persist the selected artifact, and return its selection summary."""
    _current_tenant(conn)
    start, end = window
    if validation_days <= 0 or validation_days >= (end - start).days + 1:
        raise ModelError("validation_days must leave at least one training day")
    rows = _load_features(conn, feature_version, window)
    validation_start = end - timedelta(days=validation_days - 1)
    train_rows = [row for row in rows if row["obs_date"] < validation_start]
    validation_rows = [row for row in rows if row["obs_date"] >= validation_start]
    if not train_rows or not validation_rows:
        raise ModelError("feature window does not contain both training and validation rows")
    candidate_data = _candidate_predictions(train_rows, validation_rows, seed)
    candidates = {name: _metrics(validation_rows, predictions) for name, (_payload, predictions) in candidate_data.items()}
    approach = select_model(candidates)
    params = serialize_params(approach, candidate_data[approach][0])
    conn.execute("DELETE FROM forecast_models WHERE model_version = %s", (model_version,))
    conn.execute(
        """INSERT INTO forecast_models (tenant_id, model_version, feature_version, approach, taxonomy_version, params,
           train_window_start, train_window_end, validation_window_start, validation_window_end, validation_pinball,
           validation_coverage, candidates, notes)
           VALUES (current_setting('app.tenant_id')::uuid, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)""",
        (model_version, feature_version, approach, taxonomy_version, json.dumps(params), start, validation_start - timedelta(days=1),
         validation_start, end, candidates[approach]["validation_pinball"], candidates[approach]["validation_coverage"],
         json.dumps(candidates), "CS-014b: six-month window only; no annual seasonality"),
    )
    return {"model_version": model_version, "feature_version": feature_version, "approach": approach,
            "validation_pinball": candidates[approach]["validation_pinball"], "candidates": candidates}


def load_model(conn, model_version) -> dict:
    """Load one tenant-visible model artifact, including a deserialized runtime payload."""
    row = conn.execute(
        "SELECT model_version, feature_version, approach, taxonomy_version, params, train_window_start, train_window_end, "
        "validation_window_start, validation_window_end, validation_pinball, validation_coverage, candidates "
        "FROM forecast_models WHERE model_version = %s", (model_version,)
    ).fetchone()
    if row is None:
        raise ModelError(f"model {model_version!r} was not found")
    names = ("model_version", "feature_version", "approach", "taxonomy_version", "params", "train_window_start", "train_window_end",
             "validation_window_start", "validation_window_end", "validation_pinball", "validation_coverage", "candidates")
    result = dict(zip(names, row, strict=True))
    result["payload"] = deserialize_params(result)
    return result


def _predict_payload(rows, model: dict) -> list[dict]:
    if model["approach"] == "empirical_quantile":
        return predict_empirical(rows, model["payload"])
    if model["approach"] == "shrunk_empirical_quantile":
        return predict_shrunk_empirical(rows, model["payload"])
    X, _columns, _stats = feature_matrix(rows, stats=model["payload"]["stats"])
    return [{name: float(predict_linear(X, model["payload"]["weights"][name])[i]) for name in QUANTILES}
            for i in range(len(rows))]


def predict(conn, model_version, *, target_window, feature_version=None) -> ForecastSet:
    """Apply a stored model to feature rows and return a fully validated contract envelope."""
    model = load_model(conn, model_version)
    if feature_version is not None and feature_version != model["feature_version"]:
        raise ModelError("requested feature_version differs from the model's stored feature_version")
    rows = _load_features(conn, model["feature_version"], target_window)
    predictions = _predict_payload(rows, model)
    records = []
    for row, raw in zip(rows, predictions, strict=True):
        lower, p70, upper = enforce_monotone_nonneg(raw["lower"], raw["p70"], raw["upper"])
        records.append(ForecastRecord(location_id=int(row["location_id"]), product_category=str(row["product_category"]),
                       forecast_hour=datetime(row["obs_date"].year, row["obs_date"].month, row["obs_date"].day,
                                              int(row["obs_hour"]), tzinfo=ZoneInfo(CANONICAL_TZ)), p70=_to_money(p70),
                       lower=_to_money(lower), upper=_to_money(upper), model_version=model_version,
                       taxonomy_version=model["taxonomy_version"]))
    forecast_set = ForecastSet(CONTRACT_VERSION, model["taxonomy_version"], model_version, datetime.now(UTC), tuple(records))
    validate_set(forecast_set, allowed_categories=load_vocabulary(conn), allowed_locations={3, 5, 8}, store_hours=load_store_hours(conn))
    return forecast_set


def _to_money(value: float) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def persist_forecasts(conn, fs: ForecastSet) -> int:
    """Insert each contract record under the active tenant and return the inserted count."""
    _current_tenant(conn)
    sql = """INSERT INTO forecasts (tenant_id, location_id, product_category, forecast_hour, p70, lower, upper,
             model_version, taxonomy_version) VALUES (current_setting('app.tenant_id')::uuid, %s, %s, %s, %s, %s, %s, %s, %s)"""
    with conn.cursor() as cursor:
        cursor.executemany(sql, [(record.location_id, record.product_category, record.forecast_hour, record.p70, record.lower,
                                  record.upper, record.model_version, record.taxonomy_version) for record in fs.records])
    return len(fs.records)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train the CS-014b demand forecast model.")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--tenant-id", default="00000000-0000-0000-0000-000000000001")
    parser.add_argument("--model-version", required=True)
    parser.add_argument("--feature-version", default=FEATURE_VERSION)
    args = parser.parse_args(argv)
    from data_access.session import tenant_session
    with tenant_session(args.database_url, role="app_rw", tenant_id=args.tenant_id) as conn:
        summary = train(conn, model_version=args.model_version, feature_version=args.feature_version)
    print(f"OK — trained '{summary['model_version']}' with {summary['approach']} (pinball={summary['validation_pinball']:.6f}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
