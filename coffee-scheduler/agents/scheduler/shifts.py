"""Pure shift-slot construction and fail-closed shift-rule parsing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from agents.scheduler.inputs import ShiftRuleInput, SolverInputs


class SolverModelError(Exception):
    """Raised when scheduler model inputs cannot safely be encoded."""


@dataclass(frozen=True, slots=True)
class ShiftSlot:
    location_id: int
    day: date
    start_hour: Decimal
    end_hour: Decimal

    @property
    def hours(self) -> Decimal:
        return self.end_hour - self.start_hour


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
            opening, closing = Decimal(window.open_hour), Decimal(window.close_hour)
            start = opening
            while start + minimum <= closing:
                length = minimum
                while length <= maximum and start + length <= closing:
                    result.append(ShiftSlot(store.location_id, day, start, start + length))
                    length += Decimal("0.5")
                start += Decimal("0.5")
        day += timedelta(days=1)
    return tuple(result)
