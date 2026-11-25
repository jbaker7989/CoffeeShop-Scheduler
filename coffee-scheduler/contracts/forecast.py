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


class InvalidForecastPayload(ForecastContractError):
    """A forecast payload has an invalid shape or scalar type."""


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


StoreWeekHours = Mapping[int, Mapping[int, StoreOpenWindow]]
StoreHours = Mapping[int, StoreOpenWindow | Mapping[int, StoreOpenWindow]]


def _window_for(
    store_hours: StoreHours, location: int, weekday: int,
) -> StoreOpenWindow | None:
    """Resolve the local weekday in either supported store-hours mapping."""
    hours = store_hours.get(location)
    if isinstance(hours, StoreOpenWindow):
        return hours if weekday in hours.open_weekdays else None
    return hours.get(weekday) if hours is not None else None


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
    store_hours: StoreHours,
) -> None:
    """Validate one forecast record against its approved vocabulary and store hours."""
    if not isinstance(r, ForecastRecord):
        raise InvalidForecastPayload("record must be a ForecastRecord")
    if type(r.location_id) is not int:
        raise InvalidForecastPayload("record.location_id must be an integer")
    if not isinstance(r.product_category, str):
        raise InvalidForecastPayload("record.product_category must be a string")
    if not isinstance(r.model_version, str) or not isinstance(r.taxonomy_version, str):
        raise InvalidForecastPayload("record model_version and taxonomy_version must be strings")
    if not isinstance(allowed_categories, Collection) or not isinstance(allowed_locations, Collection):
        raise InvalidForecastPayload("allowed vocabularies must be collections")
    if not isinstance(store_hours, Mapping):
        raise InvalidForecastPayload("store_hours must be a mapping")
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
    if r.location_id not in store_hours:
        raise StoreHoursError(f"Store has no open window {r.location_id!r}")
    window = _window_for(store_hours, r.location_id, local.weekday())
    if window is None or not window.open_hour <= local.hour < window.close_hour:
        raise StoreHoursError(f"Store is closed for forecast hour {local!r}")


def validate_set(
    fs: ForecastSet,
    *,
    allowed_categories: Collection[str],
    allowed_locations: Collection[int],
    store_hours: StoreHours,
) -> None:
    """Validate envelope versions, uniqueness, and every contained record."""
    if not isinstance(fs, ForecastSet):
        raise InvalidForecastPayload("forecast envelope must be a ForecastSet")
    if not isinstance(fs.contract_version, str):
        raise InvalidForecastPayload("contract_version must be a string")
    if not isinstance(fs.taxonomy_version, str):
        raise InvalidForecastPayload("taxonomy_version must be a string")
    if not isinstance(fs.model_version, str):
        raise InvalidForecastPayload("model_version must be a string")
    if not isinstance(fs.records, (tuple, list)):
        raise InvalidForecastPayload("records must be an array of forecast records")
    if any(not isinstance(record, ForecastRecord) for record in fs.records):
        raise InvalidForecastPayload("records must contain forecast records")
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
        validate_record(record, allowed_categories=allowed_categories,
                        allowed_locations=allowed_locations, store_hours=store_hours)
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


def record_to_dict(r: ForecastRecord) -> dict:
    """Convert a record to its JSON-safe contract representation."""
    if not isinstance(r, ForecastRecord):
        raise InvalidForecastPayload("record must be a ForecastRecord")
    if not isinstance(r.forecast_hour, datetime):
        raise InvalidForecastPayload("record.forecast_hour must be a datetime")
    for field in ("product_category", "model_version", "taxonomy_version"):
        if not isinstance(getattr(r, field), str):
            raise InvalidForecastPayload(f"record.{field} must be a string")
    for field in ("p70", "lower", "upper"):
        _validate_interval_value(getattr(r, field))
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
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise InvalidForecastPayload("timestamp must be an ISO-8601 string")
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise InvalidForecastPayload("timestamp must be a valid ISO-8601 string") from None


def record_from_dict(d: Mapping) -> ForecastRecord:
    """Parse a record structurally, without applying business validation."""
    if not isinstance(d, Mapping):
        raise InvalidForecastPayload("record must be an object")
    required = {"location_id", "product_category", "forecast_hour", "p70", "lower", "upper", "model_version", "taxonomy_version"}
    missing = required - d.keys()
    if missing:
        raise InvalidForecastPayload(f"record is missing required field {sorted(missing)[0]}")
    if type(d["location_id"]) is not int:
        raise InvalidForecastPayload("record.location_id must be an integer")
    for field in ("product_category", "model_version", "taxonomy_version"):
        if not isinstance(d[field], str):
            raise InvalidForecastPayload(f"record.{field} must be a string")
    for field in ("p70", "lower", "upper"):
        if isinstance(d[field], bool) or not isinstance(d[field], (str, int, float, Decimal)):
            raise InvalidForecastPayload(f"record.{field} must be a decimal number or string")
        try:
            Decimal(str(d[field]))
        except Exception:
            raise InvalidForecastPayload(f"record.{field} must be a valid decimal") from None
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
    try:
        payload = json.loads(s)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        raise InvalidForecastPayload("forecast payload must be valid JSON") from None
    if not isinstance(payload, Mapping):
        raise InvalidForecastPayload("forecast envelope must be an object")
    required = {"contract_version", "taxonomy_version", "model_version", "generated_at", "records"}
    missing = required - payload.keys()
    if missing:
        raise InvalidForecastPayload(f"forecast envelope is missing required field {sorted(missing)[0]}")
    for field in ("contract_version", "taxonomy_version", "model_version"):
        if not isinstance(payload[field], str):
            raise InvalidForecastPayload(f"{field} must be a string")
    if not isinstance(payload["records"], list):
        raise InvalidForecastPayload("records must be an array")
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


def load_store_hours(conn) -> dict[int, dict[int, StoreOpenWindow]]:
    """Load per-weekday store windows and round minute offsets up to whole hours."""
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
    grouped: dict[int, dict[int, StoreOpenWindow]] = {}
    for row in rows:
        location_id = _row_value(row, "location_id", 0)
        day_name = str(_row_value(row, "day_of_week", 1)).lower()
        open_time = _row_value(row, "open_time", 2)
        close_time = _row_value(row, "close_time", 3)
        if day_name not in weekdays or open_time is None or close_time is None:
            continue
        open_hour = open_time.hour + (1 if open_time.minute else 0)
        close_hour = close_time.hour + (1 if close_time.minute else 0)
        if close_hour <= open_hour:
            raise StoreHoursError(
                f"Invalid store window for location {location_id!r} on {day_name}: "
                f"{open_time}–{close_time} must produce an opening hour before closing"
            )
        weekday = weekdays[day_name]
        grouped.setdefault(location_id, {})[weekday] = StoreOpenWindow(
            open_hour, close_hour, frozenset({weekday}),
        )
    return grouped
