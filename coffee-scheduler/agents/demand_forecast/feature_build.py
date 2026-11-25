"""CS-014a — build the versioned forecast feature dataset (F9 feature slice).

Reads the canonical Postgres tables under an already-opened tenant session and writes one
0-filled `forecast_features` row per open store-hour x taxonomy category over the source
window, plus a `feature_sets` registry row. All derivation (cell universe, 0-fill, calendar,
weather join, lag/rolling, masks) happens in pure Python so it is unit-testable offline; SQL
only loads inputs and persists the result.

The builder is deliberately idempotent: rebuilding a version deletes and re-inserts it inside
the caller's transaction. Tenant scoping is taken from `app.tenant_id` (RLS), never a parameter.
"""
from __future__ import annotations

import argparse
import os
from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping
from datetime import date, timedelta
from decimal import Decimal

from contracts.forecast import (
    PINNED_TAXONOMY_VERSION,
    StoreHours,
    _window_for,
    load_store_hours,
    load_vocabulary,
)

FEATURE_VERSION = "forecast-features-v2"
SOURCE_WINDOW = (date(2023, 1, 1), date(2023, 6, 30))
# A store-hour whose window volume is below this share of the store's busiest hour is flagged
# low_history: the evening tail the model has almost no signal for.
LOW_HISTORY_SHARE = Decimal("0.05")
# low_history is derived from `items` (the target) but is consumed as a PREDICTOR, so it is
# computed only from dates before the reserved holdout tail. This closes the leak for the FINAL
# fold only: a rolling-origin backtest whose origin precedes that cutoff still scores rows whose
# low_history was summed over that fold's own period. The length mirrors the 28-day holdout the
# CS-014c backtest uses; CS-014c is not on main yet, so nothing here enforces that agreement.
HOLDOUT_DAYS = 28
# Holidays in any of these jurisdictions apply to this tenant's NYC stores (tenants carry
# `NY-STATE+NYC`). Scoping the read keeps a future out-of-jurisdiction calendar from flagging
# these dates; `test_ac24_db_holiday_jurisdictions_cover_the_window` trips if a new jurisdiction
# appears, so widening this set stays a conscious decision rather than a silent exclusion.
APPLICABLE_HOLIDAY_JURISDICTIONS = ("US-FED", "NY-STATE", "NYC")

_WEATHER_KEYS = ("temperature_2m", "precipitation", "rain", "snowfall", "weather_bucket", "in_trading_hours")

FEATURE_COLUMNS = (
    "tenant_id",
    "feature_version",
    "location_id",
    "product_category",
    "obs_date",
    "obs_hour",
    "items",
    "revenue",
    "day_of_week",
    "is_weekend",
    "is_holiday",
    "school_in_session",
    "temperature_2m",
    "precipitation",
    "rain",
    "snowfall",
    "weather_bucket",
    "in_trading_hours",
    "items_lag_1h",
    "items_roll_7d_same_hour",
    "low_history",
)

CellKey = tuple[int, str, date, int]


class FeatureBuildError(RuntimeError):
    """Raised when the feature build cannot proceed (e.g. no tenant context)."""


# --- pure derivation helpers (unit-tested offline) -----------------------------


def build_cell_universe(
    store_hours: StoreHours,
    categories: Collection[str],
    window: tuple[date, date],
) -> list[CellKey]:
    """Every (store, category, date-in-window, open clock-hour) cell; closed hours excluded.

    Uses the same open-window semantics as `contracts.forecast.load_store_hours`, so the
    feature grid and the F8 output contract agree on which hours exist.
    """
    start, end = window
    cells: list[CellKey] = []
    ordinal_days = range((end - start).days + 1)
    for location_id in sorted(store_hours):
        for offset in ordinal_days:
            day = date.fromordinal(start.toordinal() + offset)
            window_spec = _window_for(store_hours, location_id, day.weekday())
            if window_spec is None:
                continue
            for hour in range(window_spec.open_hour, window_spec.close_hour):
                for category in sorted(categories):
                    cells.append((location_id, category, day, hour))
    return cells


def zero_fill(
    cells: Iterable[CellKey],
    sales: Mapping[CellKey, tuple[int, Decimal]],
) -> dict[CellKey, tuple[int, Decimal]]:
    """Attach (items, revenue) to each cell, defaulting an unsold open hour to zero."""
    return {cell: sales.get(cell, (0, Decimal("0.00"))) for cell in cells}


def calendar_features(obs_date: date, holidays: Collection[date], school_days: Mapping[date, bool]) -> dict:
    """Derive calendar flags from the observation date (never `date.today()`)."""
    day_of_week = obs_date.weekday()  # 0 = Monday
    return {
        "day_of_week": day_of_week,
        "is_weekend": day_of_week >= 5,
        "is_holiday": obs_date in holidays,
        "school_in_session": bool(school_days.get(obs_date, False)),
    }


def weather_features(key: tuple[int, date, int], weather: Mapping[tuple[int, date, int], Mapping]) -> dict:
    """Return the store/date/hour weather row's features, or all-null when it is absent."""
    row = weather.get(key)
    if row is None:
        return dict.fromkeys(_WEATHER_KEYS)
    return {name: row.get(name) for name in _WEATHER_KEYS}


def rolling_features(
    rows: Iterable[tuple[int, str, date, int, int]],
) -> dict[CellKey, tuple[int | None, Decimal | None]]:
    """Per (store, category) series: lag-1 open hour and trailing same-hour mean.

    `items_lag_1h` is the immediately preceding OPEN hour in the series (ordered by date, then
    hour), so it crosses the overnight gap: the lag for a 07:00 cell is the prior day's closing
    hour, not 06:00. Null at the start of a series.

    `items_roll_7d_same_hour` is the mean of up to the 7 most recent prior **same-hour
    observations** — despite the column name it is neither a 7-calendar-day window nor a
    same-weekday mean, so for a store closed on some weekdays it reaches further back than 7
    days. Weekday seasonality is therefore carried by `day_of_week`/`is_weekend`, not by this
    feature. Null when there is no prior same-hour observation; nothing is fabricated, and both
    features use strictly prior observations so neither leaks the row's own target.
    """
    series: dict[tuple[int, str], list[tuple[date, int, int]]] = defaultdict(list)
    for location_id, category, obs_date, obs_hour, items in rows:
        series[(location_id, category)].append((obs_date, obs_hour, items))

    result: dict[CellKey, tuple[int | None, Decimal | None]] = {}
    for (location_id, category), observations in series.items():
        observations.sort(key=lambda row: (row[0], row[1]))
        previous_items: int | None = None
        same_hour_history: dict[int, list[int]] = defaultdict(list)
        for obs_date, obs_hour, items in observations:
            history = same_hour_history[obs_hour]
            if history:
                recent = history[-7:]
                roll: Decimal | None = Decimal(sum(recent)) / Decimal(len(recent))
            else:
                roll = None
            result[(location_id, category, obs_date, obs_hour)] = (previous_items, roll)
            previous_items = items
            history.append(items)
    return result


def low_history_flag(hour_share: Decimal, threshold: Decimal = LOW_HISTORY_SHARE) -> bool:
    """A store-hour is low-history when its share of the store's busiest hour is below threshold."""
    return hour_share < threshold


def provenance_flags() -> dict[str, bool]:
    """The three known dataset limitations, all false for this 6-month 2023 window."""
    return {"has_basket_id": False, "has_evening_history": False, "has_annual_seasonality": False}


# --- DB-backed build -----------------------------------------------------------

_SALES_SQL = """
SELECT store_id, product_category, transaction_date,
       EXTRACT(HOUR FROM transaction_time)::int AS hr,
       SUM(transaction_qty)::int AS items,
       SUM(transaction_qty * unit_price)::numeric(12,2) AS revenue
FROM transactions
WHERE transaction_date BETWEEN %s AND %s AND transaction_time IS NOT NULL
GROUP BY store_id, product_category, transaction_date, EXTRACT(HOUR FROM transaction_time)::int
"""

_WEATHER_SQL = """
SELECT location_id, date, hour, temperature_2m, precipitation, rain, snowfall,
       weather_bucket, in_trading_hours
FROM weather_hourly
WHERE date BETWEEN %s AND %s
"""

_HOLIDAY_SQL = """
SELECT DISTINCT date FROM holidays
WHERE date BETWEEN %s AND %s AND date IS NOT NULL AND jurisdiction = ANY(%s)
"""

# All loaded school calendars are NYC-DOE variants covering the same districts, so a date is
# "in session" when any of them says so; bool_or is the deliberate union, not an accident.
_SCHOOL_SQL = """
SELECT date, bool_or(in_session) FROM school_calendar_days
WHERE date BETWEEN %s AND %s GROUP BY date
"""

_FEATURE_SETS_INSERT = """
INSERT INTO feature_sets (tenant_id, feature_version, source_window_start, source_window_end,
    taxonomy_version, row_count, has_basket_id, has_evening_history, has_annual_seasonality, notes)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

_FEATURE_INSERT = (
    "INSERT INTO forecast_features ("
    + ", ".join(FEATURE_COLUMNS)
    + ") VALUES ("
    + ", ".join(["%s"] * len(FEATURE_COLUMNS))
    + ")"
)


def _current_tenant(conn):
    tenant_id = conn.execute("SELECT NULLIF(current_setting('app.tenant_id', true), '')::uuid").fetchone()[0]
    if tenant_id is None:
        raise FeatureBuildError("no tenant context set; refusing to build (RLS would reject the write)")
    return tenant_id


def build_feature_set(
    conn,
    *,
    feature_version: str = FEATURE_VERSION,
    taxonomy_version: str = PINNED_TAXONOMY_VERSION,
    window: tuple[date, date] = SOURCE_WINDOW,
) -> int:
    """Build (or idempotently rebuild) one feature version; return the row count.

    MUST run inside the caller's transaction with `app_rw` + tenant context already applied
    (see `data_access.session.tenant_session`). Tenant scoping comes from `app.tenant_id`.
    """
    tenant_id = _current_tenant(conn)
    start, end = window

    categories = load_vocabulary(conn, taxonomy_version=taxonomy_version)
    store_hours = load_store_hours(conn)

    sales: dict[CellKey, tuple[int, Decimal]] = {}
    for location_id, category, obs_date, hour, items, revenue in conn.execute(_SALES_SQL, (start, end)).fetchall():
        sales[(location_id, category, obs_date, int(hour))] = (int(items), Decimal(revenue))

    weather: dict[tuple[int, date, int], dict] = {}
    for row in conn.execute(_WEATHER_SQL, (start, end)).fetchall():
        location_id, obs_date, hour = row[0], row[1], int(row[2])
        weather[(location_id, obs_date, hour)] = dict(zip(_WEATHER_KEYS, row[3:], strict=True))

    holiday_params = (start, end, list(APPLICABLE_HOLIDAY_JURISDICTIONS))
    holidays = {r[0] for r in conn.execute(_HOLIDAY_SQL, holiday_params).fetchall()}
    school_days = {d: bool(in_session) for d, in_session in conn.execute(_SCHOOL_SQL, (start, end)).fetchall()}

    cells = build_cell_universe(store_hours, categories, window)
    filled = zero_fill(cells, sales)
    rolling = rolling_features([(loc, cat, d, h, filled[(loc, cat, d, h)][0]) for (loc, cat, d, h) in cells])
    low_history = low_history_map(cells, filled, cutoff=history_cutoff(window))

    records = []
    for cell in cells:
        location_id, category, obs_date, hour = cell
        items, revenue = filled[cell]
        calendar = calendar_features(obs_date, holidays, school_days)
        wx = weather_features((location_id, obs_date, hour), weather)
        lag, roll = rolling[cell]
        records.append(
            (
                tenant_id,
                feature_version,
                location_id,
                category,
                obs_date,
                hour,
                items,
                revenue,
                calendar["day_of_week"],
                calendar["is_weekend"],
                calendar["is_holiday"],
                calendar["school_in_session"],
                wx["temperature_2m"],
                wx["precipitation"],
                wx["rain"],
                wx["snowfall"],
                wx["weather_bucket"],
                wx["in_trading_hours"],
                lag,
                roll,
                low_history[(location_id, hour)],
            )
        )

    # Idempotent replace: the cascade clears forecast_features for this version too.
    conn.execute("DELETE FROM feature_sets WHERE feature_version = %s", (feature_version,))
    flags = provenance_flags()
    conn.execute(
        _FEATURE_SETS_INSERT,
        (
            tenant_id,
            feature_version,
            start,
            end,
            taxonomy_version,
            len(records),
            flags["has_basket_id"],
            flags["has_evening_history"],
            flags["has_annual_seasonality"],
            f"CS-014a feature build over {start}..{end}",
        ),
    )
    with conn.cursor() as cur:
        cur.executemany(_FEATURE_INSERT, records)
    return len(records)


def low_history_map(
    cells: Iterable[CellKey],
    filled: Mapping[CellKey, tuple[int, Decimal]],
    *,
    cutoff: date | None = None,
) -> dict[tuple[int, int], bool]:
    """Flag each (store, hour) whose volume is a negligible share of the store's peak hour.

    The flag is per store-hour (identical for every category and date at that store-hour), not
    per cell. Because it is derived from `items` — the target — yet consumed as a predictor, the
    share is summed only over dates strictly BEFORE `cutoff`, so it can never encode demand from
    the reserved holdout tail.

    `cutoff=None` deliberately opts out and scores the whole window; it is target-leaky and is
    meant for offline fixtures, not for a persisted feature set. A `cutoff` that leaves no history
    at all raises `FeatureBuildError` rather than silently falling back, so a short-window rebuild
    can never produce a leaky dataset that looks like a correctly scoped one.

    A store with cells but no pre-cutoff volume has a peak of zero, so every one of its hours is
    flagged low-history. That is the intended reading — there is no signal for that store yet —
    and `low_history` is a NOT NULL boolean with no third state to record it more precisely.
    """
    cell_list = list(cells)
    if cutoff is None:
        scoped = cell_list
    else:
        scoped = [cell for cell in cell_list if cell[2] < cutoff]
        if cell_list and not scoped:
            raise FeatureBuildError(
                f"no feature dates fall before the holdout cutoff {cutoff}; refusing to fall back "
                "to a whole-window low_history that would encode the target. Widen the window, "
                "lower holdout_days, or pass cutoff=None for an offline fixture."
            )
    # Key off every cell so a store-hour absent from the scoped history still gets a flag.
    store_hour_items: dict[tuple[int, int], int] = {
        (location_id, hour): 0 for location_id, _category, _obs_date, hour in cell_list
    }
    for location_id, category, obs_date, hour in scoped:
        store_hour_items[(location_id, hour)] += filled[(location_id, category, obs_date, hour)][0]
    store_peak: dict[int, int] = defaultdict(int)
    for (location_id, _hour), total in store_hour_items.items():
        store_peak[location_id] = max(store_peak[location_id], total)
    flags: dict[tuple[int, int], bool] = {}
    for (location_id, hour), total in store_hour_items.items():
        peak = store_peak[location_id]
        share = Decimal(total) / Decimal(peak) if peak > 0 else Decimal(0)
        flags[(location_id, hour)] = low_history_flag(share)
    return flags


def history_cutoff(window: tuple[date, date], holdout_days: int = HOLDOUT_DAYS) -> date:
    """First date of the reserved holdout tail; target-derived features use only earlier dates.

    `holdout_days` must be at least 1: zero or a negative value would push the cutoff past the
    window's end, silently making every date "history" and re-leaking the target.
    """
    if holdout_days < 1:
        raise ValueError(f"holdout_days must be at least 1, got {holdout_days}")
    _start, end = window
    return end - timedelta(days=holdout_days - 1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the CS-014a forecast feature dataset.")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--tenant-id", default="00000000-0000-0000-0000-000000000001")
    parser.add_argument("--feature-version", default=FEATURE_VERSION)
    args = parser.parse_args(argv)

    from data_access.session import tenant_session

    with tenant_session(args.database_url, role="app_rw", tenant_id=args.tenant_id) as conn:
        count = build_feature_set(conn, feature_version=args.feature_version)
    start, end = SOURCE_WINDOW
    print(f"OK — built '{args.feature_version}': {count} feature rows over {start}..{end}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
