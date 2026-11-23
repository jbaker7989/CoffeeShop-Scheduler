"""CS-014d — forward inference features (F9 inference slice).

`forecast_features` is a TRAINING table: `items` and `revenue` are `NOT NULL` because every row
carries the observed target. A future week has no observed demand, so writing forward rows into
that table would mean fabricating a target — exactly what CS-014a's honest-masks design refuses.

The model does not need the target at inference time: `model._FEATURE_COLUMNS` reads only the
cyclical hour, the calendar flags, weather, the lag/rolling priors and `low_history`. So this
module builds inference rows in memory, shaped exactly like `model._load_features` output but
with no target, and `model.predict(..., rows=...)` consumes them. No migration, no fabricated
demand, and the training table stays the sole source of supervised data.

Each unknowable field is carried honestly rather than invented:

* `items_lag_1h` is `None`. The previous open hour of a future week has not happened; the model's
  `feature_matrix` imputes it to the training mean.
* `items_roll_7d_same_hour` is the TRAINING mean for that `(location, category, hour)` when the
  history feature set has one. That is a documented climatological prior, not an observation.
* `low_history` is copied from the training set's `(location, hour)` flag, which is a property of
  the store's trading pattern rather than of any particular date.
* Weather is `None` whenever `weather_hourly` has no row for the date (it holds 2023 actuals only;
  forward weather is CS-048). The model imputes missing numerics, so a forward week is scored on
  calendar signal alone and the absence is visible rather than guessed.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal

from agents.demand_forecast.feature_build import (
    _HOLIDAY_SQL,
    _SCHOOL_SQL,
    _WEATHER_KEYS,
    _WEATHER_SQL,
    APPLICABLE_HOLIDAY_JURISDICTIONS,
    FeatureBuildError,
    build_cell_universe,
    calendar_features,
    weather_features,
)
from contracts.forecast import PINNED_TAXONOMY_VERSION, load_store_hours, load_vocabulary

# The columns `model._load_features` yields, minus the target: `items` is never a model feature.
INFERENCE_COLUMNS = (
    "location_id",
    "product_category",
    "obs_date",
    "obs_hour",
    "day_of_week",
    "is_weekend",
    "is_holiday",
    "school_in_session",
    "temperature_2m",
    "precipitation",
    "items_lag_1h",
    "items_roll_7d_same_hour",
    "low_history",
)

_HISTORY_PRIORS_SQL = """
SELECT location_id, product_category, obs_hour, AVG(items)::numeric AS mean_items,
       bool_or(low_history) AS low_history
FROM forecast_features WHERE feature_version = %s
GROUP BY location_id, product_category, obs_hour
"""


def history_priors(conn, feature_version: str) -> dict[tuple[int, str, int], tuple[Decimal, bool]]:
    """Load per (location, category, hour) training means and low-history flags."""
    rows = conn.execute(_HISTORY_PRIORS_SQL, (feature_version,)).fetchall()
    if not rows:
        raise FeatureBuildError(
            f"feature set {feature_version!r} has no rows; train a feature set before building "
            "inference rows so the priors are real"
        )
    return {(row[0], str(row[1]), int(row[2])): (row[3], bool(row[4])) for row in rows}


def build_rows(
    cells,
    *,
    holidays,
    school_days,
    weather: Mapping[tuple[int, date, int], Mapping],
    priors: Mapping[tuple[int, str, int], tuple[Decimal, bool]],
) -> list[dict]:
    """Assemble inference rows for the supplied cells; pure, so it is unit-testable offline."""
    rows: list[dict] = []
    for location_id, category, obs_date, hour in cells:
        calendar = calendar_features(obs_date, holidays, school_days)
        wx = weather_features((location_id, obs_date, hour), weather)
        prior = priors.get((location_id, category, hour))
        rows.append(
            {
                "location_id": location_id,
                "product_category": category,
                "obs_date": obs_date,
                "obs_hour": hour,
                "day_of_week": calendar["day_of_week"],
                "is_weekend": calendar["is_weekend"],
                "is_holiday": calendar["is_holiday"],
                "school_in_session": calendar["school_in_session"],
                "temperature_2m": wx["temperature_2m"],
                "precipitation": wx["precipitation"],
                # The future has no observed previous hour; never invent one.
                "items_lag_1h": None,
                "items_roll_7d_same_hour": None if prior is None else prior[0],
                "low_history": None if prior is None else prior[1],
            }
        )
    return rows


def build_inference_rows(
    conn,
    *,
    window: tuple[date, date],
    history_feature_version: str,
    taxonomy_version: str = PINNED_TAXONOMY_VERSION,
) -> list[dict]:
    """Build in-memory inference rows for a (possibly future) window; writes nothing.

    MUST run inside a tenant session. `history_feature_version` names the training feature set
    whose per-(location, category, hour) means and low-history flags supply the priors.
    """
    start, end = window
    if end < start:
        raise FeatureBuildError(f"inference window {start}..{end}: end precedes start")

    categories = load_vocabulary(conn, taxonomy_version=taxonomy_version)
    store_hours = load_store_hours(conn)
    cells = build_cell_universe(store_hours, categories, window)
    if not cells:
        raise FeatureBuildError(f"inference window {start}..{end} contains no open store-hours")

    holidays = {
        row[0]
        for row in conn.execute(_HOLIDAY_SQL, (start, end, list(APPLICABLE_HOLIDAY_JURISDICTIONS))).fetchall()
    }
    school_days = {d: bool(flag) for d, flag in conn.execute(_SCHOOL_SQL, (start, end)).fetchall()}
    weather: dict[tuple[int, date, int], dict] = {}
    for row in conn.execute(_WEATHER_SQL, (start, end)).fetchall():
        weather[(row[0], row[1], int(row[2]))] = dict(zip(_WEATHER_KEYS, row[3:], strict=True))

    return build_rows(
        cells,
        holidays=holidays,
        school_days=school_days,
        weather=weather,
        priors=history_priors(conn, history_feature_version),
    )
