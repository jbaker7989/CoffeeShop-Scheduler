"""Bounded, explainable CP-SAT solving for the scheduler (CS-093).

A feasible schedule returned after an explicit limit is **best found**, never silently
represented as optimal. The stopping policy is configuration; the result carries the CP-SAT
status, best bound, relative gap and a stopping reason derived from evidence rather than guessed;
and invalid configuration or a missing incumbent fail closed before anything is published.

See `docs/sdd-cs093.md` for the acceptance criteria and gap-arithmetic convention, and
`reports/cs093_solver_scale.md` for the measurement that sets the production default.
"""

from __future__ import annotations

import math
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from ortools.sat.python import cp_model

from agents.scheduler.constraints import (
    InfeasibleModelError,
    SchedulingModel,
    diagnose_infeasibility,
)

StoppingReason = Literal["optimal", "gap_limit", "time_limit", "limit_or_unknown"]

# Environment overrides for the production default.
ENV_MAX_SECONDS = "SCHEDULER_SOLVE_MAX_SECONDS"
ENV_MAX_GAP = "SCHEDULER_SOLVE_MAX_GAP"

# Production default stopping policy. Chosen from the CS-093 full-week measurement recorded in
# reports/cs093_solver_scale.md, not from the small test fixtures. At a 30s budget the real-scale
# full-week solve reaches an incumbent whose gap to the best bound is ~0.85 — a weak LINEAR BOUND,
# not a 85%-suboptimal schedule — so the publish guard is set to 0.90 to admit a real week with
# margin while still refusing a solve that found essentially nothing. Tightening this guard is a
# tracked follow-up (bound-strengthening), not something a number here can fake.
DEFAULT_MAX_TIME_SECONDS = 30.0
DEFAULT_MAX_RELATIVE_GAP = 0.90

# The accepted gap is a ratio; a value above 1.0 (100% of the incumbent) is meaningless as an
# optimality tolerance and is refused so a typo cannot disable the bound.
MAX_ALLOWED_GAP = 1.0

# Denominator floor for the relative gap, in the integer cost unit (cents). Flooring at one cent
# keeps a zero/near-zero objective finite instead of dividing by zero; see the SDD.
_GAP_DENOMINATOR_FLOOR = 1.0

# A result is "optimal" only when the incumbent meets the best bound to within this ratio.
# CP-SAT reports status OPTIMAL whenever its relative_gap_limit is merely SATISFIED, so a loose
# gap yields an OPTIMAL status over an unproven, far-from-optimal incumbent. The status name is
# therefore never trusted on its own; proven optimality is the computed gap reaching zero.
GAP_EPSILON = 1e-9


class SolvePolicyError(ValueError):
    """A stopping policy whose time budget or accepted gap is not usable."""


class SolveFailure(Exception):
    """A bounded solve that produced no publishable schedule."""


class NoIncumbentError(SolveFailure):
    """CP-SAT returned no incumbent (UNKNOWN / MODEL_INVALID): nothing to publish."""

    def __init__(self, cp_sat_status: str):
        self.cp_sat_status = cp_sat_status
        super().__init__(f"solver returned {cp_sat_status} with no incumbent schedule")


class UnacceptableGapError(SolveFailure):
    """An incumbent whose gap exceeds the accepted limit; not published as a schedule.

    The incumbent and its assignments are carried for inspection and logging, but the solve is
    rejected: an over-gap schedule is never published, and never represented as optimal.
    """

    def __init__(self, *, cp_sat_status: str, objective: int, best_objective_bound: float,
                 relative_gap: float, allowed_gap: float,
                 assignments: tuple[tuple[int, int], ...] = ()):
        self.cp_sat_status = cp_sat_status
        self.objective = objective
        self.best_objective_bound = best_objective_bound
        self.relative_gap = relative_gap
        self.allowed_gap = allowed_gap
        self.assignments = assignments
        super().__init__(
            f"incumbent objective {objective} is {relative_gap:.4f} from the bound "
            f"{best_objective_bound}, outside the accepted gap {allowed_gap}"
        )


def _finite_number(value: Any) -> bool:
    # bool is an int subclass; a True/False limit is a configuration error, not 1.0/0.0.
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


@dataclass(frozen=True, slots=True)
class SolverStopPolicy:
    """An immutable, validated stopping policy applied at the solve boundary."""

    max_time_seconds: float
    max_relative_gap: float

    def __post_init__(self) -> None:
        if not _finite_number(self.max_time_seconds) or self.max_time_seconds <= 0:
            raise SolvePolicyError(
                f"max_time_seconds must be a positive finite number, got {self.max_time_seconds!r}"
            )
        if not _finite_number(self.max_relative_gap) or self.max_relative_gap < 0 \
                or self.max_relative_gap > MAX_ALLOWED_GAP:
            raise SolvePolicyError(
                f"max_relative_gap must be a finite ratio in [0, {MAX_ALLOWED_GAP}], "
                f"got {self.max_relative_gap!r}"
            )

    def apply(self, solver: cp_model.CpSolver) -> None:
        """Write the time budget onto a CP-SAT solver.

        Only the time budget is a CP-SAT limit. The accepted gap is a POST-SOLVE publish guard,
        not CP-SAT's `relative_gap_limit`: coupling them would make CP-SAT stop early at a worse
        incumbent whenever the gap is loose, and reject every real full-week schedule whenever it
        is tight (the bound is weak at scale). `solve_schedule` instead drives `relative_gap_limit`
        to zero so the search pursues optimality for the whole budget and status OPTIMAL stays an
        honest "proven". See `docs/sdd-cs093.md`.
        """
        solver.parameters.max_time_in_seconds = float(self.max_time_seconds)

    def to_dict(self) -> dict[str, float]:
        return {"max_time_seconds": self.max_time_seconds, "max_relative_gap": self.max_relative_gap}


def _env_float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = env.get(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        raise SolvePolicyError(f"environment {key}={raw!r}: not a number") from None


def production_stop_policy(env: Mapping[str, str] | None = None) -> SolverStopPolicy:
    """The named production default, overridable by environment and validated before use."""
    env = os.environ if env is None else env
    return SolverStopPolicy(
        max_time_seconds=_env_float(env, ENV_MAX_SECONDS, DEFAULT_MAX_TIME_SECONDS),
        max_relative_gap=_env_float(env, ENV_MAX_GAP, DEFAULT_MAX_RELATIVE_GAP),
    )


def relative_gap(objective: float, bound: float) -> float:
    """Signed relative gap of an incumbent to its bound; finite even at a zero objective.

    Numerator ``objective - bound`` is non-negative for a minimization except for float noise
    near the optimum, and is left signed so a bound that floats above the incumbent is visible.
    Denominator is ``max(abs(objective), 1.0)`` — the incumbent's magnitude floored at one cost
    cent — so a zero/near-zero objective yields a finite absolute difference rather than a
    division by zero.
    """
    denominator = max(abs(objective), _GAP_DENOMINATOR_FLOOR)
    return (objective - bound) / denominator


@dataclass(frozen=True, slots=True)
class SolveResult:
    """The outcome of a bounded solve: a publishable schedule plus honest solver evidence."""

    assignments: tuple[tuple[int, int], ...]
    objective: int
    cp_sat_status: str
    best_objective_bound: float
    relative_gap: float
    stopping_reason: StoppingReason
    elapsed_seconds: float
    policy: SolverStopPolicy

    @property
    def optimal(self) -> bool:
        # Proven optimal: CP-SAT returned OPTIMAL AND the incumbent actually meets the bound.
        # A gap-tolerance OPTIMAL over an unproven incumbent has a non-zero gap and is not optimal.
        return self.cp_sat_status == "OPTIMAL" and self.relative_gap <= GAP_EPSILON

    def to_dict(self) -> dict[str, Any]:
        return {
            "assignments": [list(key) for key in self.assignments],
            "objective": self.objective,
            "cp_sat_status": self.cp_sat_status,
            "best_objective_bound": self.best_objective_bound,
            "relative_gap": self.relative_gap,
            "stopping_reason": self.stopping_reason,
            "elapsed_seconds": self.elapsed_seconds,
            "policy": self.policy.to_dict(),
        }


def classify_outcome(
    *,
    status: int,
    has_incumbent: bool,
    objective: int,
    bound: float,
    wall_time: float,
    policy: SolverStopPolicy,
    assignments: tuple[tuple[int, int], ...] = (),
) -> StoppingReason:
    """Map a solved model to a stopping reason, or raise the typed failure it warrants.

    INFEASIBLE is handled by the caller (it owns the model needed for diagnosis). Here the
    classification is driven by the computed gap, never by the CP-SAT status name alone:

      - no incumbent (UNKNOWN / MODEL_INVALID) -> NoIncumbentError;
      - gap at zero (incumbent meets the bound) and status OPTIMAL -> ``optimal``;
      - incumbent within the accepted gap, status OPTIMAL (certified within tolerance) -> ``gap_limit``;
      - incumbent within the accepted gap, status FEASIBLE (stopped by time) -> ``time_limit`` or
        ``limit_or_unknown`` by evidence;
      - incumbent outside the accepted gap -> UnacceptableGapError.
    """
    status_name = cp_model.CpSolver().StatusName(status)
    if not has_incumbent:
        raise NoIncumbentError(status_name)
    gap = relative_gap(objective, bound)
    if gap > policy.max_relative_gap:
        raise UnacceptableGapError(
            cp_sat_status=status_name, objective=objective, best_objective_bound=bound,
            relative_gap=gap, allowed_gap=policy.max_relative_gap, assignments=assignments,
        )
    if status == cp_model.OPTIMAL and gap <= GAP_EPSILON:
        return "optimal"
    if status == cp_model.OPTIMAL:
        # OPTIMAL with a non-zero gap: CP-SAT stopped because its gap tolerance was satisfied,
        # not because it proved optimality. This is best found within the accepted gap.
        return "gap_limit"
    # FEASIBLE within the accepted gap: the time budget stopped the search. Label the ambiguous
    # case rather than claim a cause OR-Tools does not report.
    time_limit_reached = wall_time >= policy.max_time_seconds
    return "time_limit" if time_limit_reached else "limit_or_unknown"


def _extract_assignments(model: SchedulingModel, solver: cp_model.CpSolver) -> tuple[tuple[int, int], ...]:
    return tuple(sorted(key for key, var in model.assign.items() if solver.Value(var)))


def solve_schedule(model: SchedulingModel, *, policy: SolverStopPolicy | None = None) -> SolveResult:
    """Solve a built, objective-bearing model under a stopping policy, failing closed.

    The policy is validated before the solve. INFEASIBLE preserves InfeasibleModelError and its
    assumption-core diagnostics; a missing incumbent or an out-of-gap incumbent raise typed
    failures rather than returning an empty or over-optimistic schedule.
    """
    policy = production_stop_policy() if policy is None else policy
    solver = cp_model.CpSolver()
    solver.parameters.random_seed = 0
    solver.parameters.num_search_workers = 1
    # Pursue optimality for the whole time budget; the accepted gap is enforced below as a
    # publish guard, not as a CP-SAT early-stop (see SolverStopPolicy.apply).
    solver.parameters.relative_gap_limit = 0.0
    policy.apply(solver)

    started = time.monotonic()
    status = solver.Solve(model.model)
    elapsed = time.monotonic() - started

    if status == cp_model.INFEASIBLE:
        raise InfeasibleModelError(diagnose_infeasibility(model))

    has_incumbent = status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    objective = round(solver.ObjectiveValue()) if has_incumbent else 0
    bound = solver.BestObjectiveBound() if has_incumbent else 0.0
    assignments = _extract_assignments(model, solver) if has_incumbent else ()

    reason = classify_outcome(
        status=status, has_incumbent=has_incumbent, objective=objective, bound=bound,
        wall_time=solver.WallTime(), policy=policy, assignments=assignments,
    )
    return SolveResult(
        assignments=assignments,
        objective=objective,
        cp_sat_status=solver.StatusName(status),
        best_objective_bound=bound,
        relative_gap=relative_gap(objective, bound),
        stopping_reason=reason,
        elapsed_seconds=elapsed,
        policy=policy,
    )
