"""End-to-end scheduler runner and reviewable decoded schedule output (CS-016d)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from time import monotonic
from typing import Any

from agents.scheduler.constraints import LegalityVerdict, SchedulingModel, build_model, legality_from_compliance
from agents.scheduler.inputs import EmployeeInput, SolverInputs, assemble_solver_inputs
from agents.scheduler.objective import _cents, build_objective
from agents.scheduler.shifts import ShiftSlot
from agents.scheduler.solve import SolveResult, SolverStopPolicy, solve_schedule


class RunnerDeadlineExceeded(Exception):
    """Raised when model construction exhausts the orchestration budget."""

class RunnerError(Exception):
    """Raised when a solved assignment cannot safely be decoded for publication."""


@dataclass(frozen=True, slots=True)
class ScheduledShift:
    employee_id: int
    role: str
    location_id: int
    day: date
    start_hour: Decimal
    end_hour: Decimal
    hours: Decimal
    hourly_rate: Decimal
    labour_cost_cents: int
    premium_cents: int
    premium_rule_ids: tuple[str, ...]
    station_id: str | None = None
    is_meal_break_covered: bool | None = None


@dataclass(frozen=True, slots=True)
class ScheduleEconomics:
    labour_cost_cents: int
    premium_cents: int
    paid_half_hours: int
    headcount: int
    labour_cents_by_store: dict[int, int]


@dataclass(frozen=True, slots=True)
class ScheduleOutput:
    target_window: tuple[date, date]
    model_version: str
    shifts: tuple[ScheduledShift, ...]
    economics: ScheduleEconomics
    solve: SolveResult
    objective: int
    omitted_terms: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return a deterministic, JSON-ready representation for downstream consumers."""
        solve = self.solve.to_dict()
        solve.pop("elapsed_seconds")
        return {
            "target_window": [day.isoformat() for day in self.target_window],
            "model_version": self.model_version,
            "shifts": [
                {
                    "employee_id": shift.employee_id,
                    "role": shift.role,
                    "location_id": shift.location_id,
                    "day": shift.day.isoformat(),
                    "start_hour": str(shift.start_hour),
                    "end_hour": str(shift.end_hour),
                    "hours": str(shift.hours),
                    "hourly_rate": str(shift.hourly_rate),
                    "labour_cost_cents": shift.labour_cost_cents,
                    "premium_cents": shift.premium_cents,
                    "premium_rule_ids": list(shift.premium_rule_ids),
                }
                for shift in self.shifts
            ],
            "economics": {
                "labour_cost_cents": self.economics.labour_cost_cents,
                "premium_cents": self.economics.premium_cents,
                "paid_half_hours": self.economics.paid_half_hours,
                "headcount": self.economics.headcount,
                "labour_cents_by_store": {
                    store_id: cents for store_id, cents in sorted(self.economics.labour_cents_by_store.items())
                },
            },
            "solve": solve,
            "objective": self.objective,
            "omitted_terms": list(self.omitted_terms),
        }


Legality = Callable[[EmployeeInput, ShiftSlot], LegalityVerdict]


def decode_shifts(
    model: SchedulingModel,
    inputs: SolverInputs,
    result: SolveResult,
    legality: Legality,
) -> tuple[ScheduledShift, ...]:
    """Purely turn chosen model assignments into sorted, reviewable shifts."""
    employees = {employee.employee_id: employee for employee in inputs.employees}
    stores = {store.location_id for store in inputs.stores}
    decoded: list[ScheduledShift] = []
    for employee_id, slot_index in sorted(result.assignments):
        employee = employees.get(employee_id)
        if employee is None:
            raise RunnerError(f"employee {employee_id}: absent from SolverInputs during decode")
        if slot_index < 0 or slot_index >= len(model.slots):
            raise RunnerError(f"employee {employee_id}: slot index {slot_index} is absent from SchedulingModel")
        slot = model.slots[slot_index]
        if slot.location_id not in stores:
            raise RunnerError(f"store {slot.location_id}: decoded slot is absent from SolverInputs")
        verdict = legality(employee, slot)
        decoded.append(
            ScheduledShift(
                employee_id=employee.employee_id,
                role=employee.role,
                location_id=slot.location_id,
                day=slot.day,
                start_hour=slot.start_hour,
                end_hour=slot.end_hour,
                hours=slot.hours,
                hourly_rate=employee.hourly_rate,
                labour_cost_cents=_cents(
                    slot.hours * employee.hourly_rate,
                    description=f"employee {employee.employee_id} decoded labour cost",
                ),
                premium_cents=_cents(
                    model.premiums.get((employee.employee_id, slot_index), Decimal()),
                    description=f"employee {employee.employee_id} decoded premium",
                ),
                premium_rule_ids=tuple(sorted(rule_id for rule_id, _amount in verdict.premiums)),
            )
        )
    return tuple(sorted(decoded, key=lambda shift: (shift.location_id, shift.day, shift.start_hour, shift.employee_id)))


def _economics(shifts: tuple[ScheduledShift, ...]) -> ScheduleEconomics:
    by_store: dict[int, int] = {}
    for shift in shifts:
        by_store[shift.location_id] = by_store.get(shift.location_id, 0) + shift.labour_cost_cents
    return ScheduleEconomics(
        labour_cost_cents=sum(shift.labour_cost_cents for shift in shifts),
        premium_cents=sum(shift.premium_cents for shift in shifts),
        paid_half_hours=sum(int(shift.hours * 2) for shift in shifts),
        headcount=len({shift.employee_id for shift in shifts}),
        labour_cents_by_store={store_id: cents for store_id, cents in sorted(by_store.items())},
    )


def run_schedule(
    conn: Any,
    *,
    target_window: tuple[date, date],
    model_version: str,
    policy: SolverStopPolicy | None = None,
    legality: Legality | None = None,
) -> ScheduleOutput:
    """Assemble, model, solve, and decode one schedule without masking solve failures."""
    inputs = assemble_solver_inputs(conn, target_window=target_window, model_version=model_version)
    legality = legality or legality_from_compliance(conn, inputs)
    return run_prepared_schedule(inputs, model_version=model_version, policy=policy, legality=legality)


def run_prepared_schedule(
    inputs: SolverInputs, *, model_version: str, policy: SolverStopPolicy | None, legality: Legality,
    deadline: float | None = None,
) -> ScheduleOutput:
    model = build_model(inputs, legality=legality)
    terms = build_objective(model, inputs)
    if deadline is not None:
        seconds = deadline - monotonic()
        if seconds <= 0:
            raise RunnerDeadlineExceeded
        if policy is not None:
            policy = replace(policy, max_time_seconds=min(policy.max_time_seconds, seconds))
    result = solve_schedule(model, policy=policy)
    shifts = decode_shifts(model, inputs, result, legality)
    return ScheduleOutput(
        target_window=inputs.target_window,
        model_version=model_version,
        shifts=shifts,
        economics=_economics(shifts),
        solve=result,
        objective=result.objective,
        omitted_terms=terms.omitted,
    )
