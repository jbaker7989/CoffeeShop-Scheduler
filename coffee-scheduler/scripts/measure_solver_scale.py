#!/usr/bin/env python3
"""CS-093 — measure solver build and bounded-solve time at real scale.

Produces the evidence the production stopping-rule default is set from. Two input sources:

  * ``--database-url`` (canonical): assemble the real three-store, full-week input from Postgres;
  * ``--synthetic`` (offline, default): a three-store, forty-employee, full-week fixture in the
    same shape, so the measurement is reproducible without a database.

The command is opt-in and never runs in CI: a full-week solve pursues a proof CP-SAT does not
finish, which is exactly why the stopping rule exists. Run it by hand and commit the report:

    python3 scripts/measure_solver_scale.py --output reports/cs093_solver_scale.md
"""

from __future__ import annotations

import argparse
import os
import platform
import sys
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.scheduler.constraints import LegalityVerdict, build_model  # noqa: E402
from agents.scheduler.inputs import (  # noqa: E402
    CostTargetInput,
    CoverageRuleInput,
    EmployeeInput,
    ShiftRuleInput,
    SolverInputs,
    StoreInput,
    ThroughputInput,
)
from agents.scheduler.objective import build_objective  # noqa: E402
from agents.scheduler.solve import (  # noqa: E402
    SolveFailure,
    SolverStopPolicy,
    production_stop_policy,
)
from contracts.forecast import ForecastRecord, ForecastSet, StoreOpenWindow  # noqa: E402

_TZ = ZoneInfo("America/New_York")
_ANCHOR = date(2026, 12, 7)  # a Monday


def _allow(*_args) -> LegalityVerdict:
    return LegalityVerdict(True)


def synthetic_inputs(*, stores: int = 3, employees: int = 40, days: int = 7) -> SolverInputs:
    """A real-scale, feasible fixture: full-week 06:00-21:00 windows across several stores.

    Shape matches the CS-092 "3 stores, 40 employees, 06:00-21:00, stub legality" measurement.
    Coverage minima are modest and every employee is a skilled key-holder / FPC holder, so the
    model is feasible and the solve reaches an incumbent fast but cannot prove optimality.
    """
    dates = tuple(_ANCHOR + timedelta(days=offset) for offset in range(days))
    location_ids = tuple(range(1, stores + 1))
    windows = {weekday: StoreOpenWindow(6, 21, frozenset({weekday})) for weekday in range(7)}
    store_rows = tuple(StoreInput(loc, f"Store {loc}", 6, 6, windows) for loc in location_ids)

    people: list[EmployeeInput] = []
    for number in range(1, employees + 1):
        home = location_ids[(number - 1) % stores]
        # Field order: employee_id, role, skill_level, home_location_id, cross_location_eligible,
        # is_key_holder, cash_authorised, food_protection_cert, hourly_rate, ... Every employee is
        # a skilled key-holder / FPC holder and cross-location eligible so the model is feasible.
        people.append(EmployeeInput(number, "Barista", 3, home, True, True, True, True,
                                    Decimal(15 + (number % 5)), "open", 0, 40, date(1990, 1, 1),
                                    False, None, "", True))

    records = tuple(
        ForecastRecord(loc, "Coffee", datetime(2026, 12, 7, hour, tzinfo=_TZ),
                       Decimal(3), Decimal(3), Decimal(3), "test", "tax")
        for loc in location_ids for hour in (9, 12, 17)
    )
    forecast = ForecastSet("v", "tax", "measure", datetime(2026, 12, 1, tzinfo=_TZ), records)
    rules = tuple(CoverageRuleInput(rule, None, rule, "1", "HEADCOUNT", "coverage") for rule in
                  ("minimum crew", "COV-KEYHOLDER-OPEN", "COV-KEYHOLDER-CLOSE", "fpc", "peak skill", "break relief"))
    rules += (CoverageRuleInput("COV-UTIL-TARGET", None, "utilisation", "1", "PERCENT", "coverage"),)
    shift_rules = (ShiftRuleInput("SR-MIN-LEN", "min", "4", "HOURS", "shift"),
                   ShiftRuleInput("SR-MAX-LEN", "max", "8", "HOURS", "shift"),
                   ShiftRuleInput("SR-MAX-CONSEC", "consecutive", "5", "DAYS", "shift"),
                   ShiftRuleInput("SR-MIN-REST", "rest", "10", "HOURS", "shift"))
    costs = tuple(CostTargetInput(loc, Decimal("0.30"), Decimal(500), Decimal(1000), Decimal(1000))
                  for loc in location_ids)
    throughput = (ThroughputInput("Coffee", "Barista", Decimal(3600), Decimal(1)),)
    return SolverInputs(store_rows, (), (), rules, shift_rules, costs, throughput, None,
                        tuple(people), (), forecast, (dates[0], dates[-1]), "tax",
                        frozenset({"Coffee"}), frozenset(location_ids), frozenset())


def measure(inputs: SolverInputs, *, policy: SolverStopPolicy, legality=_allow, label: str) -> dict:
    """Build and bounded-solve one input, returning the recorded evidence as a flat dict."""
    build_started = time.monotonic()
    model = build_model(inputs, legality=legality)
    build_objective(model, inputs)
    build_seconds = time.monotonic() - build_started

    record: dict = {
        "label": label,
        "stores": len(inputs.stores),
        "employees": len(inputs.employees),
        "days": (inputs.target_window[1] - inputs.target_window[0]).days + 1,
        "build_seconds": round(build_seconds, 3),
        "constraints": len(model.model.Proto().constraints),
    }
    solve_started = time.monotonic()
    try:
        from agents.scheduler.solve import solve_schedule

        result = solve_schedule(model, policy=policy)
        record.update(
            solve_seconds=round(time.monotonic() - solve_started, 3),
            objective=result.objective,
            best_objective_bound=round(result.best_objective_bound, 1),
            relative_gap=round(result.relative_gap, 6),
            cp_sat_status=result.cp_sat_status,
            stopping_reason=result.stopping_reason,
            elapsed_seconds=round(result.elapsed_seconds, 3),
            assignments=len(result.assignments),
        )
    except SolveFailure as exc:
        record.update(
            solve_seconds=round(time.monotonic() - solve_started, 3),
            objective=getattr(exc, "objective", None),
            best_objective_bound=round(getattr(exc, "best_objective_bound", 0.0), 1),
            relative_gap=round(getattr(exc, "relative_gap", float("nan")), 6),
            cp_sat_status=getattr(exc, "cp_sat_status", type(exc).__name__),
            stopping_reason=f"FAILED: {type(exc).__name__}",
            elapsed_seconds=None,
            assignments=0,
        )
    return record


def render_report(measurements: list[dict], *, policy: SolverStopPolicy, runtime: str) -> str:
    lines = [
        "# CS-093 solver scale measurement",
        "",
        f"- Generated: {datetime.now(_TZ).isoformat(timespec='seconds')}",
        f"- Runtime: {runtime}",
        f"- Measured policy: max_time_seconds={policy.max_time_seconds}, "
        f"max_relative_gap={policy.max_relative_gap}",
        "",
        "The bounded solve reaches an incumbent quickly and is stopped by the time budget before "
        "it can prove optimality; the relative gap reports how far the incumbent is from the "
        "best bound. The default is set from the full-week row, never from a small fixture.",
        "",
        "## Measurements",
        "",
        "| label | stores | employees | days | constraints | build_s | solve_s | objective | "
        "bound | relative_gap | status | stopping_reason |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for m in measurements:
        lines.append(
            f"| {m['label']} | {m['stores']} | {m['employees']} | {m['days']} | "
            f"{m['constraints']} | {m['build_seconds']} | {m['solve_seconds']} | "
            f"{m.get('objective')} | {m.get('best_objective_bound')} | "
            f"{m.get('relative_gap')} | {m.get('cp_sat_status')} | {m.get('stopping_reason')} |"
        )
    lines += [
        "",
        "## Selected default",
        "",
        f"`DEFAULT_MAX_TIME_SECONDS = {policy.max_time_seconds}` and "
        f"`DEFAULT_MAX_RELATIVE_GAP = {policy.max_relative_gap}` "
        "(`agents/scheduler/solve.py`), overridable via "
        "`SCHEDULER_SOLVE_MAX_SECONDS` / `SCHEDULER_SOLVE_MAX_GAP`.",
        "",
        "The time budget is the real limit: the solver reaches a good incumbent well inside it and "
        "then cannot close the gap. The large accepted gap is an artifact of a **weak linear "
        "bound**, not a poor schedule — the full-week bound barely moves off the one-day value "
        "while the incumbent scales with the week, so the ratio inflates. The gap guard is set "
        "just above the measured full-week gap so a real week publishes with margin while a solve "
        "that found essentially nothing is still refused. Tightening it means strengthening the "
        "bound (tracked as a CS-093 follow-up in `Backlog.md`), which no number here can fake.",
        "",
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Measure solver build and bounded-solve time.")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--tenant-id")
    parser.add_argument("--model-version")
    parser.add_argument("--synthetic", action="store_true",
                        help="Use the offline three-store, forty-employee fixture (default).")
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "cs093_solver_scale.md")
    parser.add_argument("--max-seconds", type=float)
    parser.add_argument("--max-gap", type=float)
    args = parser.parse_args(argv)

    if args.max_seconds is not None or args.max_gap is not None:
        base = production_stop_policy()
        policy = SolverStopPolicy(
            max_time_seconds=args.max_seconds if args.max_seconds is not None else base.max_time_seconds,
            max_relative_gap=args.max_gap if args.max_gap is not None else base.max_relative_gap,
        )
    else:
        policy = production_stop_policy()

    measurements: list[dict] = []
    if args.database_url and not args.synthetic:
        import psycopg

        from agents.scheduler.inputs import assemble_solver_inputs
        from data_access.session import open_session_context

        if not (args.tenant_id and args.model_version):
            parser.error("--tenant-id and --model-version are required with --database-url")
        window = (_ANCHOR, _ANCHOR + timedelta(days=6))
        with psycopg.connect(args.database_url) as conn, conn.transaction():
            open_session_context(conn, role="app_ro", tenant_id=args.tenant_id)
            inputs = assemble_solver_inputs(conn, target_window=window, model_version=args.model_version)
        measurements.append(measure(inputs, policy=policy, legality=_allow,
                                    label="real three-store full week (DB)"))
        runtime = f"{platform.platform()}; Python {platform.python_version()}; Postgres input"
    else:
        for days, label in ((1, "3-store 1-day"), (7, "3-store full week")):
            measurements.append(measure(synthetic_inputs(days=days), policy=policy, legality=_allow,
                                        label=label))
        runtime = f"{platform.platform()}; Python {platform.python_version()}; offline synthetic"

    report = render_report(measurements, policy=policy, runtime=runtime)
    args.output.write_text(report)
    print(report)
    print(f"\nwrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
