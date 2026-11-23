"""Deterministic CP-SAT hard-constraint encoding for the scheduler."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from functools import cache
from typing import Any
from zoneinfo import ZoneInfo

from ortools.sat.python import cp_model

from agents.scheduler.inputs import CoverageRuleInput, SolverInputs
from agents.scheduler.shifts import (
    RULE_MAX_CONSECUTIVE_DAYS,
    RULE_MIN_REST_HOURS,
    ShiftSlot,
    SolverModelError,
    _shift_lengths,
    build_slot_grid,
    find_rule_value,
)
from compliance.engine import Evaluation, Proposal, ShiftSpan, judge, load_snapshot
from contracts.forecast import CANONICAL_TZ

GROUPS = (
    "legality", "coverage_min_crew", "coverage_key_holder", "coverage_fpc", "coverage_peak_skill",
    "coverage_break_relief", "station_capacity", "shift_rules", "home_location",
)


class InfeasibleModelError(SolverModelError):
    """A model whose assumption core identifies its binding constraint groups."""

    def __init__(self, groups: tuple[str, ...]):
        self.groups = groups
        super().__init__(f"infeasible constraint groups: {', '.join(groups) or 'unknown'}")


@dataclass(frozen=True, slots=True)
class LegalityVerdict:
    allowed: bool
    binding: tuple[tuple[str, str], ...] = ()
    premiums: tuple[tuple[str, Decimal], ...] = ()


@dataclass(frozen=True, slots=True)
class SchedulingModel:
    model: cp_model.CpModel
    assign: dict[tuple[int, int], cp_model.IntVar]
    assumptions: dict[str, cp_model.IntVar]
    excluded: dict[tuple[int, int], LegalityVerdict]


def _coverage_value(row: CoverageRuleInput) -> int:
    try:
        value = Decimal(row.value)
    except (InvalidOperation, ValueError):
        raise SolverModelError(f"coverage_rules rule {row.rule_id!r}: unparseable value {row.value!r}") from None
    if not value.is_finite() or value < 0 or value != value.to_integral_value():
        raise SolverModelError(f"coverage_rules rule {row.rule_id!r}: invalid headcount {row.value!r}")
    return int(value)


def _coverage_rules(inputs: SolverInputs, *terms: str) -> tuple[CoverageRuleInput, ...]:
    terms = tuple(term.lower() for term in terms)
    rows = [
        row for row in inputs.coverage_rules
        if all(term in " ".join((row.rule_id, row.description, row.basis)).lower() for term in terms)
    ]
    if not rows:
        raise SolverModelError(f"coverage_rules rule {'/'.join(terms)!r}: missing")
    for row in rows:
        # FLAG rules are enabled by their presence and intentionally have no numeric value.
        # In particular, the seeded COV-BREAK-RELIEF row is NULL/FLAG.
        if row.unit != "FLAG":
            _coverage_value(row)
    return tuple(rows)


def _applicable(row: CoverageRuleInput, location_id: int) -> bool:
    return row.location_id is None or row.location_id == location_id


def _slot_start(slot: ShiftSlot) -> datetime:
    minutes = int(slot.start_hour * 60)
    return datetime.combine(slot.day, time(minutes // 60, minutes % 60), ZoneInfo(CANONICAL_TZ))


def _slot_end(slot: ShiftSlot) -> datetime:
    minutes = int(slot.end_hour * 60)
    return datetime.combine(slot.day, time(minutes // 60, minutes % 60), ZoneInfo(CANONICAL_TZ))


def _covers(slot: ShiftSlot, hour: int) -> bool:
    return slot.start_hour <= hour and slot.end_hour >= hour + 1


def _peak_hours(inputs: SolverInputs, location_id: int, day) -> set[int]:
    values: dict[int, Decimal] = {}
    for record in inputs.demand.records:
        local = record.forecast_hour.astimezone(ZoneInfo(CANONICAL_TZ))
        if record.location_id == location_id and local.date() == day:
            values[local.hour] = values.get(local.hour, Decimal()) + record.p70
    if not values:
        return set()
    high = max(values.values())
    return {hour for hour, value in values.items() if value == high}


def build_model(
    inputs: SolverInputs,
    *,
    slots: tuple[ShiftSlot, ...] | None = None,
    legality: Callable[[Any, ShiftSlot], LegalityVerdict] | None = None,
) -> SchedulingModel:
    """Encode all hard groups, with each group independently assumable for diagnosis."""
    slots = slots if slots is not None else build_slot_grid(inputs)
    model = cp_model.CpModel()
    assumptions = {name: model.NewBoolVar(f"assume_{name}") for name in GROUPS}
    for name in GROUPS:
        model.AddAssumption(assumptions[name])
    employees = tuple(sorted(inputs.employees, key=lambda item: item.employee_id))
    assign = {
        (employee.employee_id, index): model.NewBoolVar(f"assign_e{employee.employee_id}_s{index}")
        for employee in employees
        for index in range(len(slots))
    }
    excluded: dict[tuple[int, int], LegalityVerdict] = {}
    legality = legality or (lambda _employee, _slot: LegalityVerdict(True))
    for employee in employees:
        for index, slot in enumerate(slots):
            verdict = legality(employee, slot)
            if not verdict.allowed or verdict.binding:
                if not verdict.binding:
                    raise SolverModelError(
                        f"legality employee {employee.employee_id} store {slot.location_id}: exclusion lacks rule_id and reason"
                    )
                excluded[(employee.employee_id, index)] = verdict
                model.Add(assign[(employee.employee_id, index)] == 0).OnlyEnforceIf(assumptions["legality"])

    stores = {store.location_id: store for store in inputs.stores}
    min_crew = _coverage_rules(inputs, "min", "crew")
    key_holder = _coverage_rules(inputs, "key", "holder")
    fpc = _coverage_rules(inputs, "fpc")
    peak_skill = _coverage_rules(inputs, "peak", "skill")
    break_relief = _coverage_rules(inputs, "break", "relief")
    for slot in slots:
        if slot.location_id not in stores:
            raise SolverModelError(f"store {slot.location_id}: slot refers to unknown store")
    for store_id, store in sorted(stores.items()):
        for day in sorted({slot.day for slot in slots if slot.location_id == store_id}):
            window = store.open_windows.get(day.weekday())
            if window is None:
                continue
            for hour in range(window.open_hour, window.close_hour):
                vars_here = [
                    assign[(employee.employee_id, index)]
                    for employee in employees
                    for index, slot in enumerate(slots)
                    if slot.location_id == store_id and slot.day == day and _covers(slot, hour)
                ]
                for rule in min_crew:
                    if _applicable(rule, store_id):
                        model.Add(sum(vars_here) >= _coverage_value(rule)).OnlyEnforceIf(assumptions["coverage_min_crew"])
                for rule in break_relief:
                    if rule.unit != "FLAG" and _applicable(rule, store_id):
                        model.Add(sum(vars_here) >= _coverage_value(rule)).OnlyEnforceIf(assumptions["coverage_break_relief"])
                model.Add(sum(vars_here) <= store.stations).OnlyEnforceIf(assumptions["station_capacity"])
                model.Add(sum(vars_here) <= store.max_staff_on_floor).OnlyEnforceIf(assumptions["station_capacity"])
                for rule in fpc:
                    if not _applicable(rule, store_id):
                        continue
                    fpc_vars = [
                        assign[(employee.employee_id, index)]
                        for employee in employees if employee.food_protection_cert
                        for index, slot in enumerate(slots)
                        if slot.location_id == store_id and slot.day == day and _covers(slot, hour)
                    ]
                    model.Add(sum(fpc_vars) >= _coverage_value(rule)).OnlyEnforceIf(assumptions["coverage_fpc"])
                for rule in peak_skill:
                    if not _applicable(rule, store_id) or hour not in _peak_hours(inputs, store_id, day):
                        continue
                    skilled = [
                        assign[(employee.employee_id, index)]
                        for employee in employees if employee.skill_level == 3
                        for index, slot in enumerate(slots)
                        if slot.location_id == store_id and slot.day == day and _covers(slot, hour)
                    ]
                    model.Add(sum(skilled) >= _coverage_value(rule)).OnlyEnforceIf(assumptions["coverage_peak_skill"])
            for hour in (window.open_hour, window.close_hour - 1):
                key_vars = [
                    assign[(employee.employee_id, index)]
                    for employee in employees if employee.is_key_holder
                    for index, slot in enumerate(slots)
                    if slot.location_id == store_id and slot.day == day and _covers(slot, hour)
                ]
                for rule in key_holder:
                    if _applicable(rule, store_id):
                        model.Add(sum(key_vars) >= _coverage_value(rule)).OnlyEnforceIf(assumptions["coverage_key_holder"])

    min_shift, max_shift = _shift_lengths(inputs)
    # Honour whatever the tenant's shift_rules say; `parse_rule_value` already fails closed on a
    # missing or non-positive row, so these values are used rather than asserted against constants.
    consecutive = find_rule_value(inputs.shift_rules, RULE_MAX_CONSECUTIVE_DAYS)
    rest = find_rule_value(inputs.shift_rules, RULE_MIN_REST_HOURS)
    if consecutive != consecutive.to_integral_value():
        raise SolverModelError(f"shift_rules rule {RULE_MAX_CONSECUTIVE_DAYS!r}: invalid day count {consecutive!r}")
    consecutive_days = int(consecutive)
    rest_duration = timedelta(seconds=float(rest * Decimal(3600)))
    target_start, target_end = inputs.target_window
    calendar_days = tuple(target_start + timedelta(days=offset) for offset in range((target_end - target_start).days + 1))
    ordered_slots = tuple(sorted(
        enumerate(slots),
        key=lambda item: (_slot_start(item[1]), _slot_end(item[1]), item[1].location_id, item[0]),
    ))
    for employee in employees:
        employee_vars = [(index, assign[(employee.employee_id, index)]) for index in range(len(slots))]
        for day in calendar_days:
            daily = [var for index, var in employee_vars if slots[index].day == day]
            model.Add(sum(daily) <= 1).OnlyEnforceIf(assumptions["shift_rules"])
        units = [int(slots[index].hours * 2) * var for index, var in employee_vars]
        model.Add(sum(units) >= employee.min_hours_week * 2).OnlyEnforceIf(assumptions["shift_rules"])
        model.Add(sum(units) <= employee.max_hours_week * 2).OnlyEnforceIf(assumptions["shift_rules"])
        for start in range(max(0, len(calendar_days) - consecutive_days)):
            span = set(calendar_days[start:start + consecutive_days + 1])
            model.Add(sum(var for index, var in employee_vars if slots[index].day in span) <= consecutive_days).OnlyEnforceIf(
                assumptions["shift_rules"]
            )
        for left_position, (left, left_slot) in enumerate(ordered_slots):
            for right, right_slot in ordered_slots[left_position + 1:]:
                gap = _slot_start(right_slot) - _slot_end(left_slot)
                if gap < rest_duration:
                    left_var = assign[(employee.employee_id, left)]
                    right_var = assign[(employee.employee_id, right)]
                    model.Add(left_var + right_var <= 1).OnlyEnforceIf(assumptions["shift_rules"])
        for index, var in employee_vars:
            slot = slots[index]
            if slot.hours < min_shift or slot.hours > max_shift or (slot.hours * 2) != int(slot.hours * 2):
                model.Add(var == 0).OnlyEnforceIf(assumptions["shift_rules"])
            if slot.location_id != employee.home_location_id and not employee.cross_location_eligible:
                model.Add(var == 0).OnlyEnforceIf(assumptions["home_location"])
    return SchedulingModel(model, assign, assumptions, excluded)


def _solver() -> cp_model.CpSolver:
    solver = cp_model.CpSolver()
    solver.parameters.random_seed = 0
    solver.parameters.num_search_workers = 1
    return solver


def diagnose_infeasibility(model: SchedulingModel) -> tuple[str, ...]:
    """Solve only to obtain CP-SAT's assumption core in stable readable labels."""
    solver = _solver()
    if solver.Solve(model.model) != cp_model.INFEASIBLE:
        return ()
    index_to_name = {literal.Index(): name for name, literal in model.assumptions.items()}
    return tuple(sorted({index_to_name[index] for index in solver.SufficientAssumptionsForInfeasibility() if index in index_to_name}))


def assert_feasible(model: SchedulingModel) -> None:
    groups = diagnose_infeasibility(model)
    if groups:
        raise InfeasibleModelError(groups)


# Metrics decidable from ONE candidate (employee, role, span, day) and therefore safe to
# pre-compute and encode as a fixed variable. Everything else is deliberately excluded:
#
#   * schedule-scoped (`hours_per_week`, `days_per_week`, `rest_hours_between_shifts`) depends on
#     which other assignments are chosen, so it belongs in the CP-SAT `shift_rules` group;
#   * crew-scoped (`certified_staff_on_site`) depends on who else is on site, so it belongs in the
#     `coverage_fpc` / `coverage_key_holder` / `coverage_peak_skill` groups, which already enforce it;
#   * break-dependent (`break_minutes`) depends on breaks the solver does not yet assign;
#   * call-out and posting rules (`hours_paid_min`, `process`, `premium_amount`,
#     `advance_notice_days`) belong to the F14 re-plan and publish flows, not to slot assignment.
#
# Feeding those a synthetic context is what BUG-071 was: the adapter asserted `crew_complete=True`
# over an empty crew and `breaks=[]`, so every crew and meal rule failed for every candidate and
# all 39,600 assignments were excluded. A rule is either decidable here or enforced in the model —
# never "evaluated" against invented facts.
PER_ASSIGNMENT_METRICS = frozenset({
    "role_eligibility", "work_window", "hours_per_day", "span_hours", "rate",
})


def legality_from_compliance(conn_or_dsn: Any, inputs: SolverInputs) -> Callable[[Any, ShiftSlot], LegalityVerdict]:
    """Adapt CS-013 for the rules one candidate assignment can actually decide.

    Only `PER_ASSIGNMENT_METRICS` are evaluated. The day-scoped ones (`hours_per_day`,
    `span_hours`) read the proposal's week, and a single-shift week is exact for them because
    `build_model` already constrains an employee to at most one slot per day, so the day's hours
    equal this candidate's span whatever else the week holds.
    """
    dsn = conn_or_dsn if isinstance(conn_or_dsn, str) else None
    tenant_id = "00000000-0000-0000-0000-000000000001"

    @cache
    def evaluate(employee_id: int, location_id: int, day, start: Decimal, end: Decimal) -> LegalityVerdict:
        employee = next((item for item in inputs.employees if item.employee_id == employee_id), None)
        if employee is None:
            raise SolverModelError(f"employee {employee_id}: absent from SolverInputs")
        slot = ShiftSlot(location_id, day, start, end)
        span = ShiftSpan(_slot_start(slot), _slot_end(slot))
        proposal = Proposal(
            tenant_id=tenant_id, location_id=location_id, employee_id=employee_id, role=employee.role,
            start_at=span.start, end_at=span.end, breaks=[],
            week_start=inputs.target_window[0], week_shifts=[span], week_complete=True,
            # No crew-scoped rule is evaluated here, so no crew is claimed. Asserting a complete
            # crew we do not have is precisely the defect this replaces.
            crew=None, crew_complete=False,
        )
        snapshot = load_snapshot(proposal, dsn=dsn)
        decidable = tuple(rule for rule in snapshot.rules if rule.metric in PER_ASSIGNMENT_METRICS)
        evaluation: Evaluation = judge(replace(snapshot, rules=decidable), proposal)
        binding = tuple((row.rule_id, row.reason) for row in evaluation.binding if row.result == "FAIL")
        premiums = tuple((row.rule_id, row.premium_cost_usd or Decimal()) for row in evaluation.results if row.result == "PREMIUM")
        return LegalityVerdict(not binding, binding, premiums)

    return lambda employee, slot: evaluate(employee.employee_id, slot.location_id, slot.day, slot.start_hour, slot.end_hour)
