"""Shared fixtures for the CS-093 stopping-rule tests.

A single real-shaped builder: coverage minima are non-zero so the constraint groups actually
bind, matching the CS-016b/CS-092 fixtures the regression guard re-solves. Keep it small and
deterministic; every solve in the suite runs under an explicit cap.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.scheduler.constraints import LegalityVerdict  # noqa: E402
from agents.scheduler.inputs import (  # noqa: E402
    CostTargetInput,
    CoverageRuleInput,
    EmployeeInput,
    ShiftRuleInput,
    SolverInputs,
    StoreInput,
    ThroughputInput,
)
from contracts.forecast import ForecastRecord, ForecastSet, StoreOpenWindow  # noqa: E402

DAY = date(2026, 12, 7)


def allow_all(*_args) -> LegalityVerdict:
    return LegalityVerdict(True)


def real_shape_inputs(days: int = 3, employees: int = 4) -> SolverInputs:
    """The CS-092 three-day / four-employee shape: reaches its optimum fast, proves slowly."""
    dates = tuple(DAY + timedelta(days=offset) for offset in range(days))
    windows = {weekday: StoreOpenWindow(8, 18, frozenset({weekday})) for weekday in range(7)}
    people = tuple(
        EmployeeInput(number, "Barista", 3, 3, False, True, True, True, Decimal(10 + number), "open", 0,
                      40, date(1990, 1, 1), False, None, "", False)
        for number in range(1, employees + 1)
    )
    forecast = ForecastSet(
        "v", "tax", "test", datetime(2026, 12, 1, tzinfo=ZoneInfo("America/New_York")),
        (ForecastRecord(3, "Coffee",
                        datetime(2026, 12, 7, 9, tzinfo=ZoneInfo("America/New_York")),
                        Decimal(2), Decimal(2), Decimal(2), "test", "tax"),),
    )
    rules = tuple(CoverageRuleInput(rule, None, rule, "1", "HEADCOUNT", "coverage") for rule in
                  ("minimum crew", "key holder", "fpc", "peak skill", "break relief"))
    return SolverInputs(
        (StoreInput(3, "Test", 2, 2, windows),), (), (),
        rules + (CoverageRuleInput("COV-UTIL-TARGET", None, "utilisation", "1", "PERCENT", "coverage"),),
        (ShiftRuleInput("SR-MIN-LEN", "min", "4", "HOURS", "shift"),
         ShiftRuleInput("SR-MAX-LEN", "max", "8", "HOURS", "shift"),
         ShiftRuleInput("SR-MAX-CONSEC", "consecutive", "5", "DAYS", "shift"),
         ShiftRuleInput("SR-MIN-REST", "rest", "10", "HOURS", "shift")),
        (CostTargetInput(3, Decimal("0.30"), Decimal(500), Decimal(1000), Decimal(1000)),),
        (ThroughputInput("Coffee", "Barista", Decimal(3600), Decimal(1)),), None, people, (), forecast,
        (dates[0], dates[-1]), "tax", frozenset({"Coffee"}), frozenset({3}), frozenset(),
    )


def tiny_optimal_inputs() -> SolverInputs:
    """A single open hour with a single candidate: trivially solved to proven optimality."""
    windows = {weekday: StoreOpenWindow(8, 12, frozenset(range(7))) for weekday in range(7)}
    people = (EmployeeInput(1, "Barista", 3, 3, False, True, True, True, Decimal(11), "open", 0,
                            40, date(1990, 1, 1), False, None, "", False),)
    forecast = ForecastSet(
        "v", "tax", "test", datetime(2026, 12, 1, tzinfo=ZoneInfo("America/New_York")),
        (ForecastRecord(3, "Coffee",
                        datetime(2026, 12, 7, 9, tzinfo=ZoneInfo("America/New_York")),
                        Decimal(1), Decimal(1), Decimal(1), "test", "tax"),),
    )
    rules = tuple(CoverageRuleInput(rule, None, rule, "0", "HEADCOUNT", "coverage") for rule in
                  ("minimum crew", "key holder", "fpc", "peak skill", "break relief"))
    return SolverInputs(
        (StoreInput(3, "Tiny", 9, 9, windows),), (), (),
        rules + (CoverageRuleInput("COV-UTIL-TARGET", None, "utilisation", "1", "PERCENT", "coverage"),),
        (ShiftRuleInput("SR-MIN-LEN", "min", "4", "HOURS", "shift"),
         ShiftRuleInput("SR-MAX-LEN", "max", "4", "HOURS", "shift"),
         ShiftRuleInput("SR-MAX-CONSEC", "consecutive", "5", "DAYS", "shift"),
         ShiftRuleInput("SR-MIN-REST", "rest", "10", "HOURS", "shift")),
        (CostTargetInput(3, Decimal("0.30"), Decimal(500), Decimal(1000), Decimal(1000)),),
        (ThroughputInput("Coffee", "Barista", Decimal(3600), Decimal(1)),), None, people, (), forecast,
        (DAY, DAY), "tax", frozenset({"Coffee"}), frozenset({3}), frozenset(),
    )
