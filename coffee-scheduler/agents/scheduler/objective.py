"""Integer-only soft objective terms for the CP-SAT scheduler."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from ortools.sat.python import cp_model

from agents.scheduler.constraints import SchedulingModel, _covers
from agents.scheduler.inputs import CostTargetInput, SolverInputs, ThroughputInput
from contracts.forecast import CANONICAL_TZ

_CENTS = Decimal("100")
_HALF_HOURS_PER_WEEK = 80  # the statutory 40-hour week, in half-hour units
_OVERTIME_MULTIPLIER = Decimal("1.5")  # statutory overtime premium on the hourly rate
_REVENUE_PRIORS_SQL = """
SELECT location_id, product_category,
       AVG(revenue / NULLIF(items, 0))::numeric AS revenue_per_item
FROM forecast_features
WHERE feature_version = %s AND items > 0
GROUP BY location_id, product_category
"""


class ObjectiveError(Exception):
    """An objective input cannot be represented safely in the integer model."""


@dataclass(frozen=True, slots=True)
class ObjectiveWeights:
    """Default relative weights: cost 1, coverage 100, overtime 10, premium 1, sales breach 10."""

    labour_cost: int = 1
    understaffing: int = 100
    overtime: int = 10
    premium: int = 1
    labour_pct_breach: int = 10


@dataclass(frozen=True, slots=True)
class ObjectiveTerms:
    """Inspectable unweighted term expressions, their slacks, and any honest omissions."""

    labour_cost: Any
    understaffing: Any
    overtime: Any
    premium: Any
    labour_pct_breach: Any
    weights: ObjectiveWeights
    understaffing_slack: dict[tuple[int, object, int], cp_model.IntVar]
    overtime_slack: dict[int, cp_model.IntVar]
    overtime_budget_slack: dict[int, cp_model.IntVar]
    labour_pct_breach_slack: dict[int, cp_model.IntVar]
    omitted: tuple[str, ...]


def _decimal(value: object, *, description: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ObjectiveError(f"{description}: unparseable value {value!r}") from None
    if not result.is_finite():
        raise ObjectiveError(f"{description}: invalid value {value!r}")
    return result


def _integer(value: object, *, description: str, minimum: int = 0) -> int:
    decimal = _decimal(value, description=description)
    if decimal != decimal.to_integral_value() or decimal < minimum:
        raise ObjectiveError(f"{description}: invalid integer value {value!r}")
    return int(decimal)


def _cents(value: Decimal, *, description: str) -> int:
    value = _decimal(value, description=description)
    if not value.is_finite() or value < 0:
        raise ObjectiveError(f"{description}: invalid money value {value!r}")
    return int((value * _CENTS).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def required_labour_hours(demand: Iterable[Any], throughput: Iterable[ThroughputInput], *, utilisation_target: Decimal) -> Decimal:
    """Return p70 preparation hours at the supplied utilisation target, without model state."""
    utilisation = _decimal(utilisation_target, description="coverage_rules rule 'COV-UTIL-TARGET'")
    if not Decimal() < utilisation <= Decimal(1):
        raise ObjectiveError(f"coverage_rules rule 'COV-UTIL-TARGET': invalid value {utilisation_target!r}")
    standards = {row.product_category: row for row in throughput}
    total_seconds = Decimal()
    for record in demand:
        category = str(record.product_category)
        standard = standards.get(category)
        if standard is None:
            raise ObjectiveError(f"throughput_standards category {category!r}: missing")
        units = _decimal(record.p70, description=f"demand category {category!r}")
        seconds = _decimal(standard.seconds_per_unit, description=f"throughput_standards category {category!r}")
        if units < 0 or seconds < 0:
            raise ObjectiveError(f"throughput_standards category {category!r}: negative demand or seconds_per_unit")
        total_seconds += units * seconds
    return total_seconds / Decimal(3600) / utilisation


def revenue_priors(conn, history_feature_version: str) -> dict[tuple[int, str], Decimal]:
    """Load observed training revenue-per-item priors; no rows means no usable prior."""
    rows = conn.execute(_REVENUE_PRIORS_SQL, (history_feature_version,)).fetchall()
    return {(int(row[0]), str(row[1])): _decimal(row[2], description="forecast_features revenue prior") for row in rows}


def _utilisation_target(inputs: SolverInputs) -> Decimal:
    rows = [row for row in inputs.coverage_rules if row.rule_id == "COV-UTIL-TARGET"]
    if len(rows) != 1:
        raise ObjectiveError("coverage_rules rule 'COV-UTIL-TARGET': missing or duplicated")
    return _decimal(rows[0].value, description="coverage_rules rule 'COV-UTIL-TARGET'")


def _cost_targets(inputs: SolverInputs) -> dict[int, CostTargetInput]:
    result = {target.location_id: target for target in inputs.cost_targets}
    for store in inputs.stores:
        if store.location_id not in result:
            raise ObjectiveError(f"cost_targets store {store.location_id}: missing")
        target = result[store.location_id]
        pct = _decimal(target.target_labor_pct_of_sales, description=f"cost_targets store {store.location_id}")
        budget = _decimal(target.weekly_overtime_budget_usd, description=f"cost_targets store {store.location_id}")
        if not Decimal() <= pct <= Decimal(1) or budget < 0:
            raise ObjectiveError(f"cost_targets store {store.location_id}: invalid target or overtime budget")
        _integer(target.understaffing_penalty_weight, description=f"cost_targets store {store.location_id}")
        _integer(target.premium_penalty_weight, description=f"cost_targets store {store.location_id}")
    return result


def _records_here(inputs: SolverInputs, store_id: int, day, hour: int) -> tuple[Any, ...]:
    zone = ZoneInfo(CANONICAL_TZ)
    return tuple(
        record for record in inputs.demand.records
        if record.location_id == store_id
        and record.forecast_hour.astimezone(zone).date() == day
        and record.forecast_hour.astimezone(zone).hour == hour
    )


def build_objective(
    model: SchedulingModel,
    inputs: SolverInputs,
    *,
    weights: ObjectiveWeights = ObjectiveWeights(),
    revenue_priors: Mapping[tuple[int, str], Decimal] | None = None,
) -> ObjectiveTerms:
    """Add weighted soft costs to a feasible hard model and return the separable components."""
    for name, value in (
        ("labour_cost", weights.labour_cost),
        ("understaffing", weights.understaffing),
        ("overtime", weights.overtime),
        ("premium", weights.premium),
        ("labour_pct_breach", weights.labour_pct_breach),
    ):
        if not isinstance(value, int) or value < 0:
            raise ObjectiveError(f"objective weight {name!r}: expected non-negative integer")
    targets = _cost_targets(inputs)
    utilisation = _utilisation_target(inputs)
    employees = tuple(sorted(inputs.employees, key=lambda employee: employee.employee_id))
    slots = model.slots
    labour_parts: list[Any] = []
    labour_by_store: dict[int, list[Any]] = {store.location_id: [] for store in inputs.stores}
    for employee in employees:
        rate = _decimal(employee.hourly_rate, description=f"employee {employee.employee_id} hourly_rate")
        if rate < 0:
            raise ObjectiveError(f"employee {employee.employee_id}: negative hourly_rate")
        for index, slot in enumerate(slots):
            cents = _cents(slot.hours * rate, description=f"employee {employee.employee_id} labour cost")
            part = cents * model.assign[(employee.employee_id, index)]
            labour_parts.append(part)
            labour_by_store[slot.location_id].append(part)

    understaffing_slack: dict[tuple[int, object, int], cp_model.IntVar] = {}
    understaffing_parts: list[Any] = []
    for store in sorted(inputs.stores, key=lambda item: item.location_id):
        penalty = _integer(targets[store.location_id].understaffing_penalty_weight,
                           description=f"cost_targets store {store.location_id}")
        for day in sorted({slot.day for slot in slots if slot.location_id == store.location_id}):
            window = store.open_windows.get(day.weekday())
            if window is None:
                continue
            for hour in range(window.open_hour, window.close_hour):
                vars_here = [
                    model.assign[(employee.employee_id, index)]
                    for employee in employees
                    for index, slot in enumerate(slots)
                    if slot.location_id == store.location_id and slot.day == day and _covers(slot, hour)
                ]
                needed = int(required_labour_hours(
                    _records_here(inputs, store.location_id, day, hour), inputs.throughput,
                    utilisation_target=utilisation,
                ).to_integral_value(rounding=ROUND_CEILING))
                slack = model.model.NewIntVar(0, max(needed, len(vars_here)), f"understaff_s{store.location_id}_{day}_{hour}")
                model.model.Add(slack >= needed - sum(vars_here))
                understaffing_slack[(store.location_id, day, hour)] = slack
                understaffing_parts.append(penalty * slack)

    overtime_slack: dict[int, cp_model.IntVar] = {}
    overtime_budget_slack: dict[int, cp_model.IntVar] = {}
    overtime_parts: list[Any] = []
    overtime_cost: dict[int, list[Any]] = {store.location_id: [] for store in inputs.stores}
    overtime_ceiling: dict[int, int] = {store.location_id: 0 for store in inputs.stores}
    half_hours_available = sum(int(slot.hours * 2) for slot in slots)
    for employee in employees:
        units = sum(int(slots[index].hours * 2) * model.assign[(employee.employee_id, index)] for index in range(len(slots)))
        slack = model.model.NewIntVar(0, half_hours_available, f"overtime_e{employee.employee_id}")
        model.model.Add(slack >= units - _HALF_HOURS_PER_WEEK)
        overtime_slack[employee.employee_id] = slack
        target = targets.get(employee.home_location_id)
        if target is None:
            raise ObjectiveError(f"cost_targets store {employee.home_location_id}: missing for employee {employee.employee_id}")
        rate = _decimal(employee.hourly_rate, description=f"employee {employee.employee_id} hourly_rate")
        # An overtime half-hour costs half the hourly rate at the statutory premium. Pricing it at
        # the weekly BUDGET instead would multiply dollars by hours and make one half-hour cost a
        # whole week's allowance; the budget is a threshold, handled as a breach below.
        per_half_hour = _cents(rate * _OVERTIME_MULTIPLIER / Decimal(2),
                               description=f"employee {employee.employee_id} overtime rate")
        cost = per_half_hour * slack
        overtime_parts.append(cost)
        overtime_cost[employee.home_location_id].append(cost)
        overtime_ceiling[employee.home_location_id] += per_half_hour * half_hours_available

    for store_id, parts in sorted(overtime_cost.items()):
        if not parts:
            continue
        budget = _cents(targets[store_id].weekly_overtime_budget_usd,
                        description=f"cost_targets store {store_id}")
        breach = model.model.NewIntVar(0, max(overtime_ceiling[store_id], budget), f"overtime_budget_s{store_id}")
        model.model.Add(breach >= sum(parts) - budget)
        overtime_budget_slack[store_id] = breach
        overtime_parts.append(breach)

    premium_parts: list[Any] = []
    for (employee_id, index), premium in sorted(model.premiums.items()):
        slot = slots[index]
        penalty = _integer(targets[slot.location_id].premium_penalty_weight,
                           description=f"cost_targets store {slot.location_id}")
        premium_parts.append(penalty * _cents(premium, description=f"premium employee {employee_id}") * model.assign[(employee_id, index)])

    priors = revenue_priors or {}
    labour_pct_breach_slack: dict[int, cp_model.IntVar] = {}
    labour_pct_parts: list[Any] = []
    omitted: list[str] = []
    for store in sorted(inputs.stores, key=lambda item: item.location_id):
        records = tuple(record for record in inputs.demand.records if record.location_id == store.location_id)
        categories = sorted({record.product_category for record in records})
        missing = [category for category in categories if (store.location_id, category) not in priors]
        if missing:
            omitted.append(f"labour_pct_breach store {store.location_id}: revenue prior unavailable for {', '.join(missing)}")
            continue
        sales = sum(
            _cents(_decimal(record.p70, description=f"demand category {record.product_category}")
                   * _decimal(priors[(store.location_id, record.product_category)],
                              description=f"revenue prior store {store.location_id}"),
                   description=f"forecast sales store {store.location_id}")
            for record in records
        )
        target_cents = int((Decimal(sales) * _decimal(targets[store.location_id].target_labor_pct_of_sales,
                                                        description=f"cost_targets store {store.location_id}"))
                           .quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        slack = model.model.NewIntVar(0, sum(_cents(slot.hours * _decimal(employee.hourly_rate,
                                                                            description=f"employee {employee.employee_id} hourly_rate"),
                                                        description="labour cost")
                                           for employee in employees for slot in slots if slot.location_id == store.location_id),
                                           f"labour_pct_s{store.location_id}")
        model.model.Add(slack >= sum(labour_by_store[store.location_id]) - target_cents)
        labour_pct_breach_slack[store.location_id] = slack
        labour_pct_parts.append(slack)

    terms = ObjectiveTerms(
        sum(labour_parts), sum(understaffing_parts), sum(overtime_parts), sum(premium_parts), sum(labour_pct_parts),
        weights, understaffing_slack, overtime_slack, overtime_budget_slack, labour_pct_breach_slack,
        tuple(omitted),
    )
    model.model.Minimize(
        weights.labour_cost * terms.labour_cost
        + weights.understaffing * terms.understaffing
        + weights.overtime * terms.overtime
        + weights.premium * terms.premium
        + weights.labour_pct_breach * terms.labour_pct_breach
    )
    return terms
