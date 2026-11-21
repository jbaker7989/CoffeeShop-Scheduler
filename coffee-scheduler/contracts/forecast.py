from __future__ import annotations

import json
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

CONTRACT_VERSION = "forecast-output-v1"
PINNED_TAXONOMY_VERSION = "TAX-2023H1-v1"
CANONICAL_TZ = "America/New_York"
STORE_LOCATION_IDS = frozenset({3, 5, 8})
FORECAST_COLUMNS = (
    "tenant_id",
    "location_id",
    "product_category",
    "forecast_hour",
    "p70",
    "lower",
    "upper",
    "model_version",
    "taxonomy_version",
)


class ForecastContractError(Exception):
    """Base error for a forecast-output contract violation."""


class UnknownCategoryError(ForecastContractError):
    """Raised when a category is absent from the approved vocabulary."""


class VersionMismatchError(ForecastContractError):
    """Raised when an envelope or record has incompatible version metadata."""


class IntervalError(ForecastContractError):
    """Raised when a prediction interval is invalid."""


class TimezoneError(ForecastContractError):
    """Raised when a timestamp is not an aware exact clock hour."""


class StoreHoursError(ForecastContractError):
    """Raised when a forecast is outside a store's open window."""


class DuplicateRecordError(ForecastContractError):
    """Raised when a forecast set repeats a logical forecast key."""


@dataclass(frozen=True)
class StoreOpenWindow:
    open_hour: int
    close_hour: int
    open_weekdays: frozenset[int]


@dataclass(frozen=True)
class ForecastRecord:
    location_id: int
    product_category: str
    forecast_hour: datetime
    p70: Decimal
    lower: Decimal
    upper: Decimal
    model_version: str
    taxonomy_version: str


@dataclass(frozen=True)
class ForecastSet:
    contract_version: str
    taxonomy_version: str
    model_version: str
    generated_at: datetime
    records: tuple[ForecastRecord, ...]


def _is_aware(value: object) -> bool:
    if not isinstance(value, datetime) or value.tzinfo is None:
        return False
    try:
        return value.utcoffset() is not None
    except (TypeError, ValueError):
        return False


def _validate_interval_value(value: object) -> None:
    if value is None or not isinstance(value, Decimal):
        raise IntervalError(f"Invalid interval value {value!r}")
    if value.is_nan() or value.is_infinite():
        raise IntervalError(f"Invalid interval value {value!r}")


def validate_record(
    r: ForecastRecord,
    *,
    allowed_categories: Collection[str],
    allowed_locations: Collection[int],
    store_hours: Mapping[int, StoreOpenWindow],
) -> None:
    """Validate one forecast record against its approved vocabulary and store hours."""
    if not allowed_categories or r.product_category not in allowed_categories:
        raise UnknownCategoryError(f"Unknown product category {r.product_category!r}")

    for value in (r.p70, r.lower, r.upper):
        _validate_interval_value(value)
    if r.lower < 0 or r.lower > r.p70 or r.p70 > r.upper:
        raise IntervalError(f"Invalid prediction interval {(r.lower, r.p70, r.upper)!r}")

    if not _is_aware(r.forecast_hour):
        raise TimezoneError(f"Forecast hour is not timezone-aware {r.forecast_hour!r}")
    local = r.forecast_hour.astimezone(ZoneInfo(CANONICAL_TZ))
    if local.minute != 0 or local.second != 0 or local.microsecond != 0:
        raise TimezoneError(
            f"Forecast hour is not an exact business-timezone hour {r.forecast_hour!r} "
            f"(local {local.isoformat()})"
        )

    if r.location_id not in allowed_locations:
        raise StoreHoursError(f"Unknown store location {r.location_id!r}")
    window = store_hours.get(r.location_id)
    if window is None:
        raise StoreHoursError(f"Store has no open window {r.location_id!r}")
    if local.weekday() not in window.open_weekdays:
        raise StoreHoursError(f"Store is closed for forecast hour {local!r}")
    if not window.open_hour <= local.hour < window.close_hour:
        raise StoreHoursError(f"Store is closed for forecast hour {local!r}")


def validate_set(
    fs: ForecastSet,
    *,
    allowed_categories: Collection[str],
    allowed_locations: Collection[int],
    store_hours: Mapping[int, StoreOpenWindow],
) -> None:
    """Validate envelope versions, uniqueness, and every contained record."""
    if fs.contract_version != CONTRACT_VERSION:
        raise VersionMismatchError(f"Unsupported contract version {fs.contract_version!r}")
    if not fs.taxonomy_version.strip():
        raise VersionMismatchError(f"Blank taxonomy version {fs.taxonomy_version!r}")
    if not fs.model_version.strip():
        raise VersionMismatchError(f"Blank model version {fs.model_version!r}")
    if not _is_aware(fs.generated_at):
        raise TimezoneError(f"Generated timestamp is not timezone-aware {fs.generated_at!r}")

    keys: set[tuple[int, str, datetime]] = set()
    for record in fs.records:
        if record.taxonomy_version != fs.taxonomy_version:
            raise VersionMismatchError(
                f"Record taxonomy version {record.taxonomy_version!r} does not match {fs.taxonomy_version!r}"
            )
        if record.model_version != fs.model_version:
            raise VersionMismatchError(
                f"Record model version {record.model_version!r} does not match {fs.model_version!r}"
            )
        key = (record.location_id, record.product_category, record.forecast_hour)
        if key in keys:
            raise DuplicateRecordError(f"Duplicate forecast record key {key!r}")
        keys.add(key)
        validate_record(
            record,
            allowed_categories=allowed_categories,
            allowed_locations=allowed_locations,
            store_hours=store_hours,
        )


def record_to_dict(r: ForecastRecord) -> dict:
    """Convert a record to its JSON-safe contract representation."""
    return {
        "location_id": r.location_id,
        "product_category": r.product_category,
        "forecast_hour": r.forecast_hour.isoformat(),
        "p70": str(r.p70),
        "lower": str(r.lower),
        "upper": str(r.upper),
        "model_version": r.model_version,
        "taxonomy_version": r.taxonomy_version,
    }


def _parse_datetime(value: object) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


def record_from_dict(d: Mapping) -> ForecastRecord:
    """Parse a record structurally, without applying business validation."""
    return ForecastRecord(
        location_id=d["location_id"],
        product_category=d["product_category"],
        forecast_hour=_parse_datetime(d["forecast_hour"]),
        p70=Decimal(str(d["p70"])),
        lower=Decimal(str(d["lower"])),
        upper=Decimal(str(d["upper"])),
        model_version=d["model_version"],
        taxonomy_version=d["taxonomy_version"],
    )


def to_json(fs: ForecastSet) -> str:
    """Serialize a version-stamped forecast envelope as indented JSON."""
    payload = {
        "contract_version": fs.contract_version,
        "taxonomy_version": fs.taxonomy_version,
        "model_version": fs.model_version,
        "generated_at": fs.generated_at.isoformat(),
        "records": [record_to_dict(record) for record in fs.records],
    }
    return json.dumps(payload, indent=2)


def from_json(s: str | bytes) -> ForecastSet:
    """Parse an envelope structurally, without applying business validation."""
    payload = json.loads(s)
    return ForecastSet(
        contract_version=payload["contract_version"],
        taxonomy_version=payload["taxonomy_version"],
        model_version=payload["model_version"],
        generated_at=_parse_datetime(payload["generated_at"]),
        records=tuple(record_from_dict(record) for record in payload["records"]),
    )


def to_row(r: ForecastRecord, tenant_id: str) -> dict:
    """Map a contract record to the insertable forecasts-table columns."""
    return {"tenant_id": tenant_id, **r.__dict__}


def from_row(row: Mapping) -> ForecastRecord:
    """Map a forecasts-table row to a contract record, ignoring table metadata."""
    return record_from_dict(row)


def missing_keys(
    fs: ForecastSet, expected: Iterable[tuple[int, str, datetime]]
) -> frozenset[tuple[int, str, datetime]]:
    """Return expected logical forecast keys that are absent from an envelope."""
    present = {(r.location_id, r.product_category, r.forecast_hour) for r in fs.records}
    return frozenset(key for key in expected if key not in present)


def load_vocabulary(conn, *, taxonomy_version: str = PINNED_TAXONOMY_VERSION) -> frozenset[str]:
    """Load the pinned taxonomy's real product categories (excludes the register-overhead row)."""
    rows = conn.execute(
        "SELECT DISTINCT product_category FROM product_taxonomy WHERE taxonomy_version = %s",
        (taxonomy_version,),
    ).fetchall()
    if not rows:
        raise VersionMismatchError(f"no taxonomy vocabulary for version {taxonomy_version!r}")
    return frozenset(row[0] for row in rows)


def _row_value(row: object, name: str, index: int) -> object:
    if isinstance(row, Mapping):
        return row[name]
    return row[index]  # type: ignore[index]


def load_store_hours(conn) -> dict[int, StoreOpenWindow]:
    """Load store open windows, rounding a partial closing hour up to keep it exclusive."""
    rows = conn.execute("SELECT location_id, day_of_week, open_time, close_time FROM store_hours").fetchall()
    weekdays = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    grouped: dict[int, tuple[int, int, set[int]]] = {}
    for row in rows:
        location_id = _row_value(row, "location_id", 0)
        day_name = str(_row_value(row, "day_of_week", 1)).lower()
        open_time = _row_value(row, "open_time", 2)
        close_time = _row_value(row, "close_time", 3)
        if day_name not in weekdays or open_time is None or close_time is None:
            continue
        close_hour = close_time.hour + (1 if close_time.minute else 0)
        # A close such as 20:30 excludes the 20:00 forecast hour, so round up to 21.
        current = grouped.setdefault(location_id, (open_time.hour, close_hour, set()))
        current[2].add(weekdays[day_name])
    return {
        location_id: StoreOpenWindow(open_hour, close_hour, frozenset(open_weekdays))
        for location_id, (open_hour, close_hour, open_weekdays) in grouped.items()
    }
