"""Deterministic CP-SAT hard-constraint encoding for the scheduler."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from functools import cache
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from ortools.sat.python import cp_model

from agents.scheduler.inputs import CoverageRuleInput, SolverInputs
from agents.scheduler.shifts import (
    RULE_MAX_CONSECUTIVE_DAYS,
    RULE_MIN_REST_HOURS,
    ShiftSlot,
    SlotIndex,
    SolverModelError,
    _shift_lengths,
    build_slot_grid,
    build_slot_index,
    find_rule_value,
    workweek,
    workweek_days,
)
from compliance.engine import Evaluation, Proposal, ShiftSpan, Snapshot, judge, load_scoped_snapshot, load_snapshot
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
    slots: tuple[ShiftSlot, ...]
    index: SlotIndex
    premiums: dict[tuple[int, int], Decimal]
    full_weeks: tuple[tuple[int, int], ...]
    skipped_minimums: tuple[tuple[int, tuple[int, int]], ...]


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
    target_start, target_end = inputs.target_window
    calendar_days = tuple(target_start + timedelta(days=offset) for offset in range((target_end - target_start).days + 1))
    # The per-day, consecutive-day and per-week hour constraints are all keyed off calendar_days,
    # so a slot dated outside the target window would appear in none of them and silently escape
    # every hour bound. `build_slot_grid` never produces one, but `slots=` is caller-supplied.
    stray = sorted({slot.day for slot in slots} - set(calendar_days))
    if stray:
        raise SolverModelError(
            f"slots fall outside target window {target_start}..{target_end}: {stray}; "
            "they would escape the daily, consecutive-day and weekly hour constraints"
        )
    rest = find_rule_value(inputs.shift_rules, RULE_MIN_REST_HOURS)
    partial_closes = {(store.location_id, weekday): hour
                      for store in inputs.stores for weekday, hour in store.partial_close_hours.items()}
    index = build_slot_index(slots, calendar_days=calendar_days, rest=rest, partial_closes=partial_closes)
    model = cp_model.CpModel()
    assumptions = {name: model.NewBoolVar(f"assume_{name}") for name in GROUPS}
    for name in GROUPS:
        model.AddAssumption(assumptions[name])
    employees = tuple(sorted(inputs.employees, key=lambda item: item.employee_id))
    assign = {
        (employee.employee_id, slot_index): model.NewBoolVar(f"assign_e{employee.employee_id}_s{slot_index}")
        for employee in employees
        for slot_index in range(len(slots))
    }
    excluded: dict[tuple[int, int], LegalityVerdict] = {}
    premiums: dict[tuple[int, int], Decimal] = {}
    legality = legality or (lambda _employee, _slot: LegalityVerdict(True))
    for employee in employees:
        for slot_index, slot in enumerate(slots):
            verdict = legality(employee, slot)
            if verdict.allowed and verdict.premiums:
                premiums[(employee.employee_id, slot_index)] = sum((amount for _rule, amount in verdict.premiums), Decimal())
            if not verdict.allowed or verdict.binding:
                if not verdict.binding:
                    raise SolverModelError(
                        f"legality employee {employee.employee_id} store {slot.location_id}: exclusion lacks rule_id and reason"
                    )
                excluded[(employee.employee_id, slot_index)] = verdict
                model.Add(assign[(employee.employee_id, slot_index)] == 0).OnlyEnforceIf(assumptions["legality"])

    stores = {store.location_id: store for store in inputs.stores}
    min_crew = _coverage_rules(inputs, "min", "crew")
    key_holder = {}
    for edge in ("OPEN", "CLOSE"):
        rule_id = f"COV-KEYHOLDER-{edge}"
        rules = tuple(rule for rule in inputs.coverage_rules if rule.rule_id == rule_id)
        if len(rules) != 1:
            raise SolverModelError(f"coverage_rules rule {rule_id!r}: expected one rule")
        key_holder[edge] = rules[0]
        _coverage_value(rules[0])
    fpc = _coverage_rules(inputs, "fpc")
    peak_skill = _coverage_rules(inputs, "peak", "skill")
    break_relief = _coverage_rules(inputs, "break", "relief")
    for slot in slots:
        if slot.location_id not in stores:
            raise SolverModelError(f"store {slot.location_id}: slot refers to unknown store")
    for store_id, store in sorted(stores.items()):
        store_days = tuple(
            day for day in calendar_days
            if (window := store.open_windows.get(day.weekday())) is not None and day.weekday() in window.open_weekdays
        )
        for day in store_days:
            window = store.open_windows.get(day.weekday())
            if window is None:  # pragma: no cover - guaranteed by the open-day predicate above.
                raise SolverModelError(f"store {store_id} weekday {day.weekday()}: missing open window")
            if window.close_hour <= window.open_hour:
                raise SolverModelError(
                    f"store {store_id} weekday {day.weekday()}: close_hour {window.close_hour} "
                    f"must exceed open_hour {window.open_hour}"
                )
            covering_by_hour = {
                hour: index.by_store_day_hour.get((store_id, day, hour), ())
                for hour in range(window.open_hour, window.close_hour)
            }
            for hour, covering in covering_by_hour.items():
                if covering:
                    continue
                required_rule = next(
                    (
                        rule
                        for rule in min_crew
                        if _applicable(rule, store_id) and _coverage_value(rule) > 0
                    ),
                    None,
                )
                if required_rule is None:
                    required_rule = next(
                        (
                            rule
                            for rule in break_relief
                            if rule.unit != "FLAG" and _applicable(rule, store_id) and _coverage_value(rule) > 0
                        ),
                        None,
                    )
                if required_rule is None:
                    required_rule = next(
                        (rule for rule in fpc if _applicable(rule, store_id) and _coverage_value(rule) > 0),
                        None,
                    )
                if required_rule is None:
                    required_rule = next(
                        (key_holder[edge] for edge, edge_hour in
                         (("OPEN", window.open_hour), ("CLOSE", window.close_hour - 1))
                         if hour == edge_hour and _applicable(key_holder[edge], store_id)
                         and _coverage_value(key_holder[edge]) > 0),
                        None,
                    )
                if required_rule is None and hour in _peak_hours(inputs, store_id, day):
                    required_rule = next(
                        (rule for rule in peak_skill if _applicable(rule, store_id) and _coverage_value(rule) > 0),
                        None,
                    )
                if required_rule is not None:
                    raise SolverModelError(
                        f"store {store_id} / day {day} / hour {hour} / rule {required_rule.rule_id}: "
                        "no candidate slot covers this open hour"
                    )
            for hour in range(window.open_hour, window.close_hour):
                slot_indices = covering_by_hour[hour]
                vars_here = [assign[(employee.employee_id, slot_index)] for employee in employees for slot_index in slot_indices]
                if not slot_indices:
                    continue
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
                        assign[(employee.employee_id, slot_index)]
                        for employee in employees if employee.food_protection_cert
                        for slot_index in slot_indices
                    ]
                    model.Add(sum(fpc_vars) >= _coverage_value(rule)).OnlyEnforceIf(assumptions["coverage_fpc"])
                for rule in peak_skill:
                    if not _applicable(rule, store_id) or hour not in _peak_hours(inputs, store_id, day):
                        continue
                    skilled = [
                        assign[(employee.employee_id, slot_index)]
                        for employee in employees if employee.skill_level == 3
                        for slot_index in slot_indices
                    ]
                    model.Add(sum(skilled) >= _coverage_value(rule)).OnlyEnforceIf(assumptions["coverage_peak_skill"])
            for edge, hour in (("OPEN", window.open_hour), ("CLOSE", window.close_hour - 1)):
                slot_indices = covering_by_hour[hour]
                if not slot_indices:
                    continue
                key_vars = [
                    assign[(employee.employee_id, slot_index)]
                    for employee in employees if employee.is_key_holder
                    for slot_index in slot_indices
                ]
                rule = key_holder[edge]
                if _applicable(rule, store_id):
                    model.Add(sum(key_vars) >= _coverage_value(rule)).OnlyEnforceIf(assumptions["coverage_key_holder"])

    min_shift, max_shift = _shift_lengths(inputs)
    # Honour whatever the tenant's shift_rules say; `parse_rule_value` already fails closed on a
    # missing or non-positive row, so these values are used rather than asserted against constants.
    consecutive = find_rule_value(inputs.shift_rules, RULE_MAX_CONSECUTIVE_DAYS)
    if consecutive != consecutive.to_integral_value():
        raise SolverModelError(f"shift_rules rule {RULE_MAX_CONSECUTIVE_DAYS!r}: invalid day count {consecutive!r}")
    consecutive_days = int(consecutive)
    rest_minutes_decimal = rest * Decimal(60)
    if rest_minutes_decimal != rest_minutes_decimal.to_integral_value():
        raise SolverModelError(f"shift_rules rule {RULE_MIN_REST_HOURS!r}: not an exact whole minute")
    rest_minutes = int(rest_minutes_decimal)
    days_by_week = workweek_days(calendar_days)
    full_weeks = tuple(iso_week for iso_week, week_days in days_by_week.items() if len(week_days) == 7)
    skipped_minimums: list[tuple[int, tuple[int, int]]] = []
    for employee in employees:
        if employee.min_hours_week > employee.max_hours_week:
            raise SolverModelError(
                f"employee {employee.employee_id}: min_hours_week {employee.min_hours_week} exceeds "
                f"max_hours_week {employee.max_hours_week}"
            )
        employee_vars = [(slot_index, assign[(employee.employee_id, slot_index)]) for slot_index in range(len(slots))]
        for day in calendar_days:
            daily = [assign[(employee.employee_id, slot_index)] for slot_index in index.by_day.get(day, ())]
            model.Add(sum(daily) <= 1).OnlyEnforceIf(assumptions["shift_rules"])
        for iso_week, week_days in sorted(days_by_week.items()):
            units = [
                int(slots[slot_index].hours * 2) * assign[(employee.employee_id, slot_index)]
                for slot_index in index.by_week.get(iso_week, ())
            ]
            model.Add(sum(units) <= employee.max_hours_week * 2).OnlyEnforceIf(assumptions["shift_rules"])
            if employee.min_hours_week == 0:
                continue
            if len(week_days) != 7:
                skipped_minimums.append((employee.employee_id, iso_week))
        for start in range(max(0, len(calendar_days) - consecutive_days)):
            span_indices = tuple(
                slot_index for day in calendar_days[start:start + consecutive_days + 1]
                for slot_index in index.by_day.get(day, ())
            )
            model.Add(sum(assign[(employee.employee_id, slot_index)] for slot_index in span_indices) <= consecutive_days).OnlyEnforceIf(
                assumptions["shift_rules"]
            )
        intervals = []
        for slot_index, var in employee_vars:
            active = model.NewBoolVar(f"rest_active_e{employee.employee_id}_s{slot_index}")
            model.Add(active <= var)
            model.Add(active <= assumptions["shift_rules"])
            model.Add(active >= var + assumptions["shift_rules"] - 1)
            start_at = index.minutes_from_base[slot_index]
            size = index.duration_minutes[slot_index] + rest_minutes
            intervals.append(model.NewOptionalIntervalVar(start_at, size, start_at + size, active,
                                                          f"rest_e{employee.employee_id}_s{slot_index}"))
        if intervals:
            model.AddNoOverlap(intervals)
        for slot_index, var in employee_vars:
            slot = slots[slot_index]
            if slot.hours < min_shift or slot.hours > max_shift or (slot.hours * 2) != int(slot.hours * 2):
                model.Add(var == 0).OnlyEnforceIf(assumptions["shift_rules"])
            if slot.location_id != employee.home_location_id and not employee.cross_location_eligible:
                model.Add(var == 0).OnlyEnforceIf(assumptions["home_location"])
    return SchedulingModel(model, assign, assumptions, excluded, slots, index, premiums, full_weeks, tuple(skipped_minimums))


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


def legality_from_compliance(
    conn_or_dsn: Any, inputs: SolverInputs, *, tenant_id: str | None = None,
) -> Callable[[Any, ShiftSlot], LegalityVerdict]:
    """Build a run-scoped candidate evaluator over the authenticated tenant's snapshots."""
    dsn = conn_or_dsn if isinstance(conn_or_dsn, str) else None
    if dsn is None:
        scoped = conn_or_dsn.execute("SELECT current_setting('app.tenant_id', true)").fetchone()[0]
        if tenant_id is not None and str(tenant_id) != scoped:
            raise SolverModelError("legality session does not match tenant")
        tenant_id = scoped
    try:
        tenant_id = str(UUID(str(tenant_id)))
    except ValueError:
        raise SolverModelError("legality requires a canonical tenant") from None

    employees = {employee.employee_id: employee for employee in inputs.employees}

    def proposal_for(employee_id, location_id, span, *, week_start):
        return Proposal(
            tenant_id=tenant_id, location_id=location_id, employee_id=employee_id,
            role=employees[employee_id].role, start_at=span.start, end_at=span.end, breaks=[],
            # Candidate evaluation handles only per-assignment metrics. The week is the
            # authoritative one for THIS candidate's day, not the first day of the window.
            week_start=week_start, week_shifts=[span], week_complete=True,
            # No crew-scoped rule is evaluated here, so no crew is claimed. Asserting a complete
            # crew we do not have is precisely the defect this replaces.
            crew=None, crew_complete=False,
        )

    snapshots: dict[tuple[str, str | None, int], Snapshot] = {}

    def snapshot_for(proposal):
        scope = (proposal.tenant_id, proposal.employer_id, proposal.employee_id)
        if scope not in snapshots:
            snapshot = load_snapshot(proposal, dsn=dsn) if dsn is not None else load_scoped_snapshot(conn_or_dsn, proposal)
            snapshots[scope] = replace(snapshot, rules=tuple(
                rule for rule in snapshot.rules if rule.metric in PER_ASSIGNMENT_METRICS
            ))
        return snapshots[scope]

    if dsn is None:
        # Snapshot access finishes while the scope is open. CP-SAT and decoding use only memory.
        example = datetime.combine(inputs.target_window[0], time(12), ZoneInfo(CANONICAL_TZ))
        span = ShiftSpan(example, example + timedelta(hours=1))
        for employee in employees.values():
            snapshot_for(proposal_for(
                employee.employee_id, employee.home_location_id, span,
                week_start=inputs.target_window[0],
            ))

    @cache
    def evaluate(employee_id, location_id, day, start, end):
        if employee_id not in employees:
            raise SolverModelError(f"employee {employee_id}: absent from SolverInputs")
        slot = ShiftSlot(location_id, day, start, end)
        proposal = proposal_for(
            employee_id, location_id, ShiftSpan(_slot_start(slot), _slot_end(slot)),
            week_start=date.fromisocalendar(*workweek(day), 1),
        )
        evaluation: Evaluation = judge(snapshot_for(proposal), proposal)
        binding = tuple((row.rule_id, row.reason) for row in evaluation.binding if row.result == "FAIL")
        premiums = tuple(
            (row.rule_id, row.premium_cost_usd or Decimal()) for row in evaluation.results if row.result == "PREMIUM"
        )
        return LegalityVerdict(not binding, binding, premiums)

    return lambda employee, slot: evaluate(employee.employee_id, slot.location_id, slot.day, slot.start_hour, slot.end_hour)
