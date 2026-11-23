"""Read-only, fail-closed assembly of the inputs consumed by the scheduler."""

from __future__ import annotations

import argparse
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from contracts.forecast import (
    CANONICAL_TZ,
    CONTRACT_VERSION,
    PINNED_TAXONOMY_VERSION,
    ForecastSet,
    StoreOpenWindow,
    from_row,
    load_store_hours,
    load_vocabulary,
    missing_keys,
    validate_set,
)
from data_access.session import open_session_context

OVERHEAD_CATEGORY = "(per transaction overhead)"


class SolverInputError(Exception):
    """Base error for a scheduler input boundary violation."""


class MissingInputError(SolverInputError):
    pass


class InvalidInputError(SolverInputError):
    pass


class ReferentialError(SolverInputError):
    pass


@dataclass(frozen=True, slots=True)
class StoreInput:
    location_id: int
    store_location: str
    max_staff_on_floor: int
    stations: int
    open_windows: dict[int, StoreOpenWindow]


@dataclass(frozen=True, slots=True)
class StationInput:
    station_id: str
    location_id: int
    station_type: str
    staffed_by_role: str


@dataclass(frozen=True, slots=True)
class FixedTaskInput:
    task_id: str
    location_id: int | None
    task: str
    role: str
    duration_minutes: int
    timing: str
    frequency: str


@dataclass(frozen=True, slots=True)
class CoverageRuleInput:
    rule_id: str
    location_id: int | None
    description: str
    value: str
    unit: str
    basis: str


@dataclass(frozen=True, slots=True)
class ShiftRuleInput:
    rule_id: str
    description: str
    value: str
    unit: str
    basis: str


@dataclass(frozen=True, slots=True)
class CostTargetInput:
    location_id: int
    target_labor_pct_of_sales: Decimal
    weekly_overtime_budget_usd: Decimal
    understaffing_penalty_weight: Decimal
    premium_penalty_weight: Decimal


@dataclass(frozen=True, slots=True)
class ThroughputInput:
    product_category: str
    prep_role: str
    seconds_per_unit: Decimal
    units_per_labor_hour: Decimal


@dataclass(frozen=True, slots=True)
class EmployeeInput:
    employee_id: int
    role: str
    skill_level: int
    home_location_id: int
    cross_location_eligible: bool
    is_key_holder: bool
    cash_authorised: bool
    food_protection_cert: bool
    hourly_rate: Decimal
    availability_persona: str
    min_hours_week: int
    max_hours_week: int
    dob: date
    is_enrolled: bool
    school_calendar_id: str | None
    grade_band: str
    has_other_employer: bool


@dataclass(frozen=True, slots=True)
class SchoolCalendarInput:
    calendar_id: str
    days: dict[date, bool]


@dataclass(frozen=True, slots=True)
class DemandCell:
    location_id: int
    product_category: str
    forecast_hour: datetime
    p70: Decimal
    lower: Decimal
    upper: Decimal


@dataclass(frozen=True, slots=True)
class SolverInputs:
    stores: tuple[StoreInput, ...]
    stations: tuple[StationInput, ...]
    fixed_tasks: tuple[FixedTaskInput, ...]
    coverage_rules: tuple[CoverageRuleInput, ...]
    shift_rules: tuple[ShiftRuleInput, ...]
    cost_targets: tuple[CostTargetInput, ...]
    throughput: tuple[ThroughputInput, ...]
    per_transaction_overhead: ThroughputInput | None
    employees: tuple[EmployeeInput, ...]
    school_calendars: tuple[SchoolCalendarInput, ...]
    demand: ForecastSet
    target_window: tuple[date, date]
    taxonomy_version: str
    allowed_categories: frozenset[str]
    allowed_locations: frozenset[int]
    demand_gaps: frozenset[tuple[int, str, datetime]]


def _value(row: Mapping[str, Any], name: str) -> Any:
    try:
        return row[name]
    except KeyError:
        raise MissingInputError(f"input row missing {name!r}: {row!r}") from None


def _decimal(table: str, key: object, value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        raise InvalidInputError(f"{table} key {key!r}: expected Decimal value, got {value!r}") from None


def _unique(table: str, rows: Iterable[Mapping[str, Any]], key_name: str) -> tuple[Mapping[str, Any], ...]:
    seen: set[Any] = set()
    result = []
    for row in rows:
        key = _value(row, key_name)
        if key in seen:
            raise InvalidInputError(f"{table} key {key!r}: duplicate key")
        seen.add(key)
        result.append(row)
    return tuple(result)


def build_stores(rows, hours_rows) -> tuple[StoreInput, ...]:
    stores = _unique("stores", rows, "location_id")
    by_id: dict[int, dict[int, StoreOpenWindow]] = {int(_value(r, "location_id")): {} for r in stores}
    weekdays = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}
    for row in hours_rows:
        location = int(_value(row, "location_id"))
        day = str(_value(row, "day_of_week")).lower()
        if location not in by_id:
            raise ReferentialError(f"store_hours key {(location, day)!r}: stores location missing")
        if (
            day not in weekdays
            or not isinstance(_value(row, "open_time"), time)
            or not isinstance(_value(row, "close_time"), time)
        ):
            raise InvalidInputError(f"store_hours key {(location, day)!r}: invalid day or times")
        opening, closing = row["open_time"], row["close_time"]
        close_hour = closing.hour + (1 if closing.minute else 0)
        by_id[location][weekdays[day]] = StoreOpenWindow(opening.hour, close_hour, frozenset({weekdays[day]}))
    result = []
    for row in stores:
        location = int(row["location_id"])
        if not by_id[location]:
            raise MissingInputError(f"store_hours key {location!r}: no open hours")
        result.append(
            StoreInput(
                location,
                str(row["store_location"]),
                int(row["max_staff_on_floor"]),
                int(row["stations"]),
                by_id[location],
            )
        )
    return tuple(result)


def build_stations(rows):
    return tuple(
        StationInput(str(r["station_id"]), int(r["location_id"]), str(r["station_type"]), str(r["staffed_by_role"]))
        for r in _unique("stations", rows, "station_id")
    )


def build_fixed_tasks(rows):
    # `location_id` is NULL for a task that applies to every store (the same convention as
    # coverage_rules); five of the seven seeded tasks are store-agnostic, so coercing it to int
    # unconditionally would reject real data.
    return tuple(
        FixedTaskInput(
            str(r["task_id"]),
            None if r["location_id"] is None else int(r["location_id"]),
            str(r["task"]),
            str(r["role"]),
            int(r["duration_minutes"]),
            str(r["timing"]),
            str(r["frequency"]),
        )
        for r in _unique("fixed_tasks", rows, "task_id")
    )


def build_coverage_rules(rows):
    return tuple(
        CoverageRuleInput(
            str(r["rule_id"]), r["location_id"], str(r["description"]), str(r["value"]), str(r["unit"]), str(r["basis"])
        )
        for r in _unique("coverage_rules", rows, "rule_id")
    )


def build_shift_rules(rows):
    return tuple(
        ShiftRuleInput(str(r["rule_id"]), str(r["description"]), str(r["value"]), str(r["unit"]), str(r["basis"]))
        for r in _unique("shift_rules", rows, "rule_id")
    )


def build_cost_targets(rows):
    return tuple(
        CostTargetInput(
            int(r["location_id"]),
            *(
                _decimal("cost_targets", r["location_id"], r[n])
                for n in (
                    "target_labor_pct_of_sales",
                    "weekly_overtime_budget_usd",
                    "understaffing_penalty_weight",
                    "premium_penalty_weight",
                )
            ),
        )
        for r in _unique("cost_targets", rows, "location_id")
    )


def build_throughput(rows):
    items = []
    for row in _unique("throughput_standards", rows, "product_category"):
        key = str(row["product_category"])
        items.append(
            ThroughputInput(
                key,
                str(row["prep_role"]),
                _decimal("throughput_standards", key, row["seconds_per_unit"]),
                _decimal("throughput_standards", key, row["units_per_labor_hour"]),
            )
        )
    overhead = next((x for x in items if x.product_category == OVERHEAD_CATEGORY), None)
    return tuple(x for x in items if x.product_category != OVERHEAD_CATEGORY), overhead


def build_employees(rows):
    result = []
    for r in _unique("employees", rows, "employee_id"):
        key = int(r["employee_id"])
        enrolled, calendar = bool(r["is_enrolled"]), r["school_calendar_id"]
        if enrolled != (calendar is not None):
            raise InvalidInputError(f"employees key {key!r}: is_enrolled must match school_calendar_id")
        if int(r["min_hours_week"]) > int(r["max_hours_week"]):
            raise InvalidInputError(f"employees key {key!r}: min_hours_week exceeds max_hours_week")
        result.append(
            EmployeeInput(
                key,
                str(r["role"]),
                int(r["skill_level"]),
                int(r["home_location_id"]),
                bool(r["cross_location_eligible"]),
                bool(r["is_key_holder"]),
                bool(r["cash_authorised"]),
                bool(r["food_protection_cert"]),
                _decimal("employees", key, r["hourly_rate"]),
                str(r["availability_persona"]),
                int(r["min_hours_week"]),
                int(r["max_hours_week"]),
                r["dob"],
                enrolled,
                calendar,
                str(r["grade_band"]),
                bool(r["has_other_employer"]),
            )
        )
    return tuple(result)


def build_school_calendars(calendar_rows, day_rows):
    calendars = {str(r["calendar_id"]): {} for r in _unique("school_calendars", calendar_rows, "calendar_id")}
    for r in day_rows:
        key = str(r["calendar_id"])
        if key not in calendars:
            raise ReferentialError(f"school_calendar_days key {(key, r['date'])!r}: school_calendars missing")
        if r["date"] in calendars[key]:
            raise InvalidInputError(f"school_calendar_days key {(key, r['date'])!r}: duplicate key")
        calendars[key][r["date"]] = bool(r["in_session"])
    return tuple(SchoolCalendarInput(key, days) for key, days in calendars.items())


def _rows(conn, table: str, columns: str):
    from psycopg.rows import dict_row

    with conn.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(f"SELECT {columns} FROM {table} WHERE deleted_at IS NULL").fetchall()


def load_stores(conn):
    return build_stores(
        _rows(conn, "stores", "location_id, store_location, max_staff_on_floor, stations"),
        _rows(conn, "store_hours", "location_id, day_of_week, open_time, close_time"),
    )


def load_stations(conn):
    return build_stations(_rows(conn, "stations", "station_id, location_id, station_type, staffed_by_role"))


def load_fixed_tasks(conn):
    return build_fixed_tasks(
        _rows(conn, "fixed_tasks", "task_id, location_id, task, role, duration_minutes, timing, frequency")
    )


def load_coverage_rules(conn):
    return build_coverage_rules(_rows(conn, "coverage_rules", "rule_id, location_id, description, value, unit, basis"))


def load_shift_rules(conn):
    return build_shift_rules(_rows(conn, "shift_rules", "rule_id, description, value, unit, basis"))


def load_cost_targets(conn):
    return build_cost_targets(
        _rows(
            conn,
            "cost_targets",
            "location_id, target_labor_pct_of_sales, weekly_overtime_budget_usd, understaffing_penalty_weight, premium_penalty_weight",
        )
    )


def load_throughput(conn):
    return build_throughput(
        _rows(conn, "throughput_standards", "product_category, prep_role, seconds_per_unit, units_per_labor_hour")
    )


def load_employees(conn):
    return build_employees(
        _rows(
            conn,
            "employees",
            "employee_id, role, skill_level, home_location_id, cross_location_eligible, is_key_holder, cash_authorised, food_protection_cert, hourly_rate, availability_persona, min_hours_week, max_hours_week, dob, is_enrolled, school_calendar_id, grade_band, has_other_employer",
        )
    )


def load_school_calendars(conn):
    return build_school_calendars(
        _rows(conn, "school_calendars", "calendar_id"),
        _rows(conn, "school_calendar_days", "calendar_id, date, in_session"),
    )


def load_demand(conn, *, target_window, model_version: str) -> ForecastSet:
    rows = _rows(
        conn,
        "forecasts",
        "location_id, product_category, forecast_hour, p70, lower, upper, model_version, taxonomy_version",
    )
    records = tuple(
        from_row(row)
        for row in rows
        if row["model_version"] == model_version and target_window[0] <= row["forecast_hour"].date() <= target_window[1]
    )
    if not records:
        raise MissingInputError(f"forecasts key {model_version!r}: no rows in target window")
    fs = ForecastSet(CONTRACT_VERSION, records[0].taxonomy_version, model_version, records[0].forecast_hour, records)
    validate_set(
        fs,
        allowed_categories=load_vocabulary(conn, taxonomy_version=fs.taxonomy_version),
        allowed_locations={r["location_id"] for r in _rows(conn, "stores", "location_id")},
        store_hours=load_store_hours(conn),
    )
    return fs


def assemble_solver_inputs(
    conn, *, target_window, model_version, taxonomy_version=PINNED_TAXONOMY_VERSION
) -> SolverInputs:
    tenant = conn.execute("SELECT current_setting('app.tenant_id', true)").fetchone()[0]
    if not tenant:
        raise SolverInputError("session key app.tenant_id: tenant context is unset")
    stores, stations, tasks = load_stores(conn), load_stations(conn), load_fixed_tasks(conn)
    coverage, shifts, costs = load_coverage_rules(conn), load_shift_rules(conn), load_cost_targets(conn)
    (throughput, overhead), employees, calendars = (
        load_throughput(conn),
        load_employees(conn),
        load_school_calendars(conn),
    )
    demand = load_demand(conn, target_window=target_window, model_version=model_version)
    if demand.taxonomy_version != taxonomy_version:
        raise InvalidInputError(
            f"forecasts key {model_version!r}: taxonomy version {demand.taxonomy_version!r} "
            f"does not match requested {taxonomy_version!r}"
        )
    locations = frozenset(x.location_id for x in stores)
    categories = load_vocabulary(conn, taxonomy_version=taxonomy_version)
    for table, key, location in (
        [("stations", x.station_id, x.location_id) for x in stations]
        # A fixed task with no location applies to every store, so it has nothing to resolve.
        + [("fixed_tasks", x.task_id, x.location_id) for x in tasks if x.location_id is not None]
        + [("cost_targets", x.location_id, x.location_id) for x in costs]
        + [("employees", x.employee_id, x.home_location_id) for x in employees]
    ):
        if location not in locations:
            raise ReferentialError(f"{table} key {key!r}: location_id {location!r} missing from stores")
    if {x.location_id for x in costs} != locations:
        raise MissingInputError("cost_targets key stores: every store requires one cost target")
    for x in demand.records:
        if x.location_id not in locations:
            raise ReferentialError(f"forecasts key {x.location_id!r}: location missing from stores")
        if x.product_category not in categories:
            raise ReferentialError(f"forecasts key {x.product_category!r}: category missing from product_taxonomy")
    throughput_categories = {x.product_category for x in throughput}
    for category in throughput_categories:
        if category not in categories:
            raise ReferentialError(f"throughput_standards key {category!r}: category missing from product_taxonomy")
    for x in demand.records:
        if x.product_category not in throughput_categories:
            raise MissingInputError(
                f"throughput_standards key {x.product_category!r}: forecast category lacks throughput"
            )
    calendar_ids = {x.calendar_id for x in calendars}
    for employee in employees:
        if employee.is_enrolled and employee.school_calendar_id not in calendar_ids:
            raise ReferentialError(
                f"employees key {employee.employee_id!r}: school_calendar_id missing from school_calendars"
            )
    hours = load_store_hours(conn)
    demand_categories = {record.product_category for record in demand.records}
    expected = []
    current_day = target_window[0]
    while current_day <= target_window[1]:
        for location in locations:
            window = hours.get(location)
            if window is None or current_day.weekday() not in window.open_weekdays:
                continue
            for category in demand_categories:
                for hour in range(window.open_hour, window.close_hour):
                    expected.append(
                        (location, category, datetime.combine(current_day, time(hour), ZoneInfo(CANONICAL_TZ)))
                    )
        current_day += timedelta(days=1)
    gaps = missing_keys(demand, expected)
    return SolverInputs(
        stores,
        stations,
        tasks,
        coverage,
        shifts,
        costs,
        throughput,
        overhead,
        employees,
        calendars,
        demand,
        target_window,
        taxonomy_version,
        categories,
        locations,
        gaps,
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Assemble validated scheduler inputs.")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--model-version", required=True)
    parser.add_argument("--target-window-start", required=True)
    parser.add_argument("--target-window-end", required=True)
    args = parser.parse_args(argv)
    import psycopg

    with psycopg.connect(args.database_url) as conn, conn.transaction():
        open_session_context(conn, role="app_ro", tenant_id=args.tenant_id)
        inputs = assemble_solver_inputs(
            conn,
            target_window=(
                date.fromisoformat(args.target_window_start),
                date.fromisoformat(args.target_window_end),
            ),
            model_version=args.model_version,
        )
    print(
        f"stores={len(inputs.stores)} stations={len(inputs.stations)} employees={len(inputs.employees)} demand_cells={len(inputs.demand.records)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
