"""Pure shift-slot construction and fail-closed shift-rule parsing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from agents.scheduler.inputs import ShiftRuleInput, SolverInputs
from contracts.forecast import CANONICAL_TZ


class SolverModelError(Exception):
    """Raised when scheduler model inputs cannot safely be encoded."""


def workweek(day: date) -> tuple[int, int]:
    """Return THE Monday-start ISO workweek for hour caps, overtime, guarantees, and payroll."""
    iso_year, iso_week, _ = day.isocalendar()
    return iso_year, iso_week


def workweek_days(window: tuple[date, ...]) -> dict[tuple[int, int], tuple[date, ...]]:
    """Partition calendar days by the authoritative workweek in deterministic key order."""
    grouped: dict[tuple[int, int], list[date]] = {}
    for day in window:
        grouped.setdefault(workweek(day), []).append(day)
    return {key: tuple(days) for key, days in sorted(grouped.items())}


@dataclass(frozen=True, slots=True)
class ShiftSlot:
    location_id: int
    day: date
    start_hour: Decimal
    end_hour: Decimal

    @property
    def hours(self) -> Decimal:
        return self.end_hour - self.start_hour


@dataclass(frozen=True, slots=True)
class SlotIndex:
    """Deterministic, once-per-build lookup data for a candidate slot grid."""

    start_at: tuple[datetime, ...]
    end_at: tuple[datetime, ...]
    by_day: dict[date, tuple[int, ...]]
    by_store_day_hour: dict[tuple[int, date, int], tuple[int, ...]]
    by_week: dict[tuple[int, int], tuple[int, ...]]
    minutes_from_base: tuple[int, ...]
    duration_minutes: tuple[int, ...]


def parse_rule_value(shift_rules: tuple[ShiftRuleInput, ...], rule_id: str) -> Decimal:
    """Return one typed numeric rule value, refusing missing or malformed rows."""
    rows = [row for row in shift_rules if row.rule_id == rule_id]
    if len(rows) != 1:
        raise SolverModelError(f"shift_rules rule {rule_id!r}: missing or duplicated")
    try:
        value = Decimal(rows[0].value)
    except (InvalidOperation, ValueError):
        raise SolverModelError(f"shift_rules rule {rule_id!r}: unparseable value {rows[0].value!r}") from None
    if not value.is_finite() or value <= 0:
        raise SolverModelError(f"shift_rules rule {rule_id!r}: invalid value {rows[0].value!r}")
    return value


# Canonical `shift_rules.rule_id` values. Matching on rule ids rather than description text is
# deliberate: the free-text descriptions are ambiguous (searching "min" + "shift" hits both
# SR-MIN-LEN "Minimum shift length" and SR-MIN-REST "...minimum rest between shifts"), while the
# ids are the table's stable key.
RULE_MIN_SHIFT_HOURS = "SR-MIN-LEN"
RULE_MAX_SHIFT_HOURS = "SR-MAX-LEN"
RULE_INCREMENT_MINUTES = "SR-INCREMENT"
RULE_MAX_CONSECUTIVE_DAYS = "SR-MAX-CONSEC"
RULE_MIN_REST_HOURS = "SR-MIN-REST"


def find_rule_value(shift_rules: tuple[ShiftRuleInput, ...], rule_id: str) -> Decimal:
    """Return one numeric shift-rule value by its canonical id, failing closed when absent."""
    return parse_rule_value(shift_rules, rule_id)


def _shift_lengths(inputs: SolverInputs) -> tuple[Decimal, Decimal]:
    minimum = parse_rule_value(inputs.shift_rules, RULE_MIN_SHIFT_HOURS)
    maximum = parse_rule_value(inputs.shift_rules, RULE_MAX_SHIFT_HOURS)
    if minimum > maximum:
        raise SolverModelError(f"shift_rules: min shift {minimum} exceeds max shift {maximum}")
    return minimum, maximum


def build_slot_grid(inputs: SolverInputs) -> tuple[ShiftSlot, ...]:
    """Build deterministic contiguous, half-hour candidate spans in each open window."""
    minimum, maximum = _shift_lengths(inputs)
    if minimum * 2 != int(minimum * 2) or maximum * 2 != int(maximum * 2):
        raise SolverModelError("shift_rules: shift lengths must use 30-minute increments")
    start_day, end_day = inputs.target_window
    if end_day < start_day:
        raise SolverModelError(f"target window {start_day}..{end_day}: invalid")
    result: list[ShiftSlot] = []
    day = start_day
    while day <= end_day:
        for store in sorted(inputs.stores, key=lambda item: item.location_id):
            window = store.open_windows.get(day.weekday())
            if window is None or day.weekday() not in window.open_weekdays:
                continue
            opening = Decimal(window.open_hour)
            closing = store.partial_close_hours.get(day.weekday(), Decimal(window.close_hour))
            start = opening
            while start + minimum <= closing:
                length = minimum
                while length <= maximum and start + length <= closing:
                    result.append(ShiftSlot(store.location_id, day, start, start + length))
                    length += Decimal("0.5")
                start += Decimal("0.5")
        day += timedelta(days=1)
    return tuple(result)


def _slot_datetime(day: date, hour: Decimal) -> datetime:
    minutes = hour * Decimal(60)
    if minutes != minutes.to_integral_value():
        raise SolverModelError(f"slot {day} at {hour}: bounds must be exact whole minutes")
    minute = int(minutes)
    if not 0 <= minute <= 24 * 60:
        raise SolverModelError(f"slot {day} at {hour}: bounds fall outside one calendar day")
    return datetime.combine(day, time.min, ZoneInfo(CANONICAL_TZ)) + timedelta(minutes=minute)


def _elapsed_minutes(start: datetime, end: datetime, *, description: str) -> int:
    elapsed = end.astimezone(UTC) - start.astimezone(UTC)
    total_microseconds = ((elapsed.days * 24 * 60 * 60 + elapsed.seconds) * 1_000_000) + elapsed.microseconds
    minute_microseconds = 60 * 1_000_000
    if total_microseconds % minute_microseconds:
        raise SolverModelError(f"{description}: elapsed time is not an exact whole minute")
    return total_microseconds // minute_microseconds


def build_slot_index(
    slots: tuple[ShiftSlot, ...], *, calendar_days: tuple[date, ...], rest: Decimal,
    partial_closes: dict[tuple[int, int], Decimal] | None = None,
) -> SlotIndex:
    """Build all slot lookups once, using elapsed rather than wall-clock time."""
    if not calendar_days:
        raise SolverModelError("slot index requires at least one calendar day")
    if not rest.is_finite() or rest < 0:
        raise SolverModelError(f"shift_rules rest: invalid value {rest!r}")
    base = datetime.combine(min(calendar_days), time.min, ZoneInfo(CANONICAL_TZ))
    starts: list[datetime] = []
    ends: list[datetime] = []
    minutes_from_base: list[int] = []
    duration_minutes: list[int] = []
    by_day: dict[date, list[int]] = {}
    by_store_day_hour: dict[tuple[int, date, int], list[int]] = {}
    by_week: dict[tuple[int, int], list[int]] = {}
    closes = partial_closes or {}
    for index, slot in enumerate(slots):
        start = _slot_datetime(slot.day, slot.start_hour)
        end = _slot_datetime(slot.day, slot.end_hour)
        duration = _elapsed_minutes(start, end, description=f"slot {index} duration")
        if duration < 0:
            raise SolverModelError(f"slot {index}: end precedes start")
        starts.append(start)
        ends.append(end)
        minutes_from_base.append(_elapsed_minutes(base, start, description=f"slot {index} start"))
        duration_minutes.append(duration)
        by_day.setdefault(slot.day, []).append(index)
        by_week.setdefault(workweek(slot.day), []).append(index)
        first_hour = int(slot.start_hour.to_integral_value(rounding=ROUND_CEILING))
        last_hour = int((slot.end_hour - 1).to_integral_value(rounding=ROUND_FLOOR))
        close = closes.get((slot.location_id, slot.day.weekday()))
        if close is not None and slot.end_hour == close:
            # The final clock-hour bucket covers only the portion the store is open.
            last_hour = int(close)
        for hour in range(first_hour, last_hour + 1):
            by_store_day_hour.setdefault((slot.location_id, slot.day, hour), []).append(index)
    return SlotIndex(
        tuple(starts), tuple(ends),
        {key: tuple(sorted(value)) for key, value in sorted(by_day.items())},
        {key: tuple(sorted(value)) for key, value in sorted(by_store_day_hour.items())},
        {key: tuple(sorted(value)) for key, value in sorted(by_week.items())},
        tuple(minutes_from_base), tuple(duration_minutes),
    )
