"""Offline and rollback-gated checks for the CS-016d solver runner."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.scheduler.constraints import InfeasibleModelError, LegalityVerdict, build_model  # noqa: E402
from agents.scheduler.inputs import (  # noqa: E402
    CostTargetInput,
    CoverageRuleInput,
    EmployeeInput,
    ShiftRuleInput,
    SolverInputs,
    StoreInput,
    ThroughputInput,
)
from agents.scheduler.objective import ObjectiveWeights, build_objective  # noqa: E402
from agents.scheduler.runner import RunnerError, _economics, decode_shifts, run_schedule  # noqa: E402
from agents.scheduler.shifts import ShiftSlot  # noqa: E402
from agents.scheduler.solve import (  # noqa: E402
    NoIncumbentError,
    SolveResult,
    SolverStopPolicy,
    UnacceptableGapError,
    solve_schedule,
)
from contracts.forecast import ForecastRecord, ForecastSet, StoreOpenWindow  # noqa: E402

DAY = date(2026, 12, 7)
# Tiny synthetic models solve instantly, so the offline tests cap hard at 5s.
CAPPED = SolverStopPolicy(max_time_seconds=5.0, max_relative_gap=1.0)
# A real one-day, three-store model needs the production-scale budget to reach a first
# incumbent: CS-093 measured a 1-day solve finding an incumbent only within ~30s, and
# solve_schedule runs the whole budget (relative_gap_limit is pinned to 0.0). 5s is enough on
# fast local hardware but not on a slower CI runner, where it returned UNKNOWN. gap 1.0 accepts
# whatever incumbent is found, since this test checks the decode, not the gap guard.
DB_CAPPED = SolverStopPolicy(max_time_seconds=30.0, max_relative_gap=1.0)


def _allow_all(*_args, **_kwargs) -> LegalityVerdict:
    return LegalityVerdict(True)


def _inputs(*, employees=()) -> SolverInputs:
    forecast = ForecastSet(
        "forecast-output-v1", "TAX-2023H1-v1", "test", datetime(2026, 12, 1, tzinfo=ZoneInfo("America/New_York")),
        (ForecastRecord(3, "Coffee", datetime(2026, 12, 7, 8, tzinfo=ZoneInfo("America/New_York")), Decimal("1"),
                        Decimal("1"), Decimal("1"), "test", "TAX-2023H1-v1"),),
    )
    people = employees or (
        EmployeeInput(1, "Barista", 3, 3, False, True, True, True, Decimal("10.01"), "open", 0, 80,
                      date(1990, 1, 1), False, None, "", False),
        EmployeeInput(2, "Barista", 3, 3, False, True, True, True, Decimal("20"), "open", 0, 80,
                      date(1990, 1, 1), False, None, "", False),
    )
    coverage = tuple(CoverageRuleInput(rule, None, rule, "0", "HEADCOUNT", "coverage") for rule in (
        "minimum crew", "key holder", "fpc", "peak skill", "break relief",
    )) + (CoverageRuleInput("COV-UTIL-TARGET", None, "utilisation", "1", "PERCENT", "coverage"),)
    return SolverInputs(
        (StoreInput(3, "Test", 2, 2, {0: StoreOpenWindow(8, 10, frozenset({0}))}),), (), (), coverage,
        (
            ShiftRuleInput("SR-MIN-LEN", "min", "2", "HOURS", "shift"),
            ShiftRuleInput("SR-MAX-LEN", "max", "8", "HOURS", "shift"),
            ShiftRuleInput("SR-MAX-CONSEC", "consecutive", "5", "DAYS", "shift"),
            ShiftRuleInput("SR-MIN-REST", "rest", "10", "HOURS", "shift"),
        ),
        (CostTargetInput(3, Decimal("0.30"), Decimal("50"), Decimal("1000"), Decimal("1000")),),
        (ThroughputInput("Coffee", "Barista", Decimal("3600"), Decimal("1")),), None, people, (), forecast,
        (DAY, DAY), "TAX-2023H1-v1", frozenset({"Coffee"}), frozenset({3}), frozenset(),
    )


def _result(assignments: tuple[tuple[int, int], ...]) -> SolveResult:
    return SolveResult(assignments, 0, "OPTIMAL", 0.0, 0.0, "optimal", 0.01, CAPPED)


def _model(inputs: SolverInputs, *, legality=_allow_all):
    slots = (
        ShiftSlot(3, DAY, Decimal("8"), Decimal("10")),
        ShiftSlot(3, DAY, Decimal("8"), Decimal("9")),
    )
    return build_model(inputs, slots=slots, legality=legality)


class RunnerTests(unittest.TestCase):
    # spec: F11.ac51
    def test_ac51_happy_path_decodes_chosen_assignments_in_schedule_order(self):
        inputs = _inputs(employees=(_inputs().employees[0],))
        model = _model(inputs)
        build_objective(model, inputs, weights=ObjectiveWeights(labour_cost=1, understaffing=10_000, overtime=0,
                                                                premium=0, labour_pct_breach=0))
        result = solve_schedule(model, policy=CAPPED)
        shifts = decode_shifts(model, inputs, result, _allow_all)
        self.assertEqual(tuple((shift.employee_id, model.slots.index(ShiftSlot(
            shift.location_id, shift.day, shift.start_hour, shift.end_hour
        ))) for shift in shifts), result.assignments)
        self.assertEqual(shifts, tuple(sorted(shifts, key=lambda shift: (
            shift.location_id, shift.day, shift.start_hour, shift.employee_id
        ))))

    # spec: F11.ac52
    def test_ac52_labour_cost_reconciles_to_objective_rounding_basis(self):
        inputs = _inputs(employees=(_inputs().employees[0],))
        model = _model(inputs)
        shifts = decode_shifts(model, inputs, _result(((1, 0),)), _allow_all)
        self.assertEqual(shifts[0].labour_cost_cents, 2002)
        self.assertEqual(sum(shift.labour_cost_cents for shift in shifts), 2002)

    # spec: F11.ac53
    def test_ac53_empty_feasible_schedule_has_zero_economics(self):
        inputs = _inputs()
        model = _model(inputs)
        build_objective(model, inputs, weights=ObjectiveWeights(labour_cost=1, understaffing=0, overtime=0,
                                                                premium=0, labour_pct_breach=0))
        result = solve_schedule(model, policy=CAPPED)
        shifts = decode_shifts(model, inputs, result, _allow_all)
        economics = _economics(shifts)
        self.assertEqual(shifts, ())
        self.assertEqual(economics.labour_cost_cents, 0)
        self.assertEqual(economics.premium_cents, 0)
        self.assertEqual(economics.paid_half_hours, 0)
        self.assertEqual(economics.headcount, 0)

    # spec: F11.ac54
    def test_ac54_premium_money_and_rule_ids_attach_to_shift(self):
        inputs = _inputs(employees=(_inputs().employees[0],))

        def premium(_employee, _slot):
            return LegalityVerdict(True, premiums=(("RULE-Z", Decimal("1.25")), ("RULE-A", Decimal("0.75"))))

        model = _model(inputs, legality=premium)
        shift = decode_shifts(model, inputs, _result(((1, 0),)), premium)[0]
        self.assertEqual(shift.premium_cents, 200)
        self.assertEqual(shift.premium_rule_ids, ("RULE-A", "RULE-Z"))

    # spec: F11.ac55
    def test_ac55_decode_and_json_output_are_deterministic(self):
        inputs = _inputs()
        model = _model(inputs)
        result = _result(((2, 0), (1, 1)))
        first = decode_shifts(model, inputs, result, _allow_all)
        second = decode_shifts(model, inputs, result, _allow_all)
        self.assertEqual(first, second)
        self.assertEqual([shift.employee_id for shift in first], [1, 2])
        models = (_model(inputs), _model(inputs))
        with patch("agents.scheduler.runner.assemble_solver_inputs", return_value=inputs), \
                patch("agents.scheduler.runner.build_model", side_effect=models):
            first_run = run_schedule(None, target_window=(DAY, DAY), model_version="test", policy=CAPPED,
                                     legality=_allow_all)
            second_run = run_schedule(None, target_window=(DAY, DAY), model_version="test", policy=CAPPED,
                                      legality=_allow_all)
        self.assertEqual(first_run.to_dict(), second_run.to_dict())

    # spec: F11.ac56
    def test_ac56_phantom_employee_fails_closed(self):
        inputs = _inputs()
        with self.assertRaisesRegex(RunnerError, "employee 999"):
            decode_shifts(_model(inputs), inputs, _result(((999, 0),)), _allow_all)

    # spec: F11.ac57
    def test_ac57_run_schedule_propagates_infeasible_no_incumbent_and_over_gap(self):
        inputs = _inputs()
        model = _model(inputs)
        errors = (
            InfeasibleModelError(("legality",)),
            NoIncumbentError("UNKNOWN"),
            UnacceptableGapError(cp_sat_status="FEASIBLE", objective=2, best_objective_bound=0.0,
                                 relative_gap=1.0, allowed_gap=0.9),
        )
        for error in errors:
            with self.subTest(error=type(error).__name__), \
                    patch("agents.scheduler.runner.assemble_solver_inputs", return_value=inputs), \
                    patch("agents.scheduler.runner.build_model", return_value=model), \
                    patch("agents.scheduler.runner.build_objective", return_value=SimpleNamespace(omitted=())), \
                    patch("agents.scheduler.runner.solve_schedule", side_effect=error):
                with self.assertRaises(type(error)) as raised:
                    run_schedule(None, target_window=(DAY, DAY), model_version="test", policy=CAPPED, legality=_allow_all)
                self.assertIs(raised.exception, error)

    # spec: F11.ac58
    def test_ac58_omitted_objective_terms_pass_through_runner_output(self):
        inputs = _inputs()
        model = _model(inputs)
        result = _result(())
        with patch("agents.scheduler.runner.assemble_solver_inputs", return_value=inputs), \
                patch("agents.scheduler.runner.build_model", return_value=model), \
                patch("agents.scheduler.runner.solve_schedule", return_value=result):
            output = run_schedule(None, target_window=(DAY, DAY), model_version="test", policy=CAPPED, legality=_allow_all)
        self.assertTrue(output.omitted_terms)
        self.assertEqual(output.objective, output.solve.objective)
        self.assertEqual(output.to_dict(), output.to_dict())


DSN = os.environ.get("DATABASE_URL", "postgresql://coffeeshop:coffeeshop@localhost:5432/coffeeshop")
_SKIP = None
try:
    import psycopg

    with psycopg.connect(DSN, connect_timeout=3) as connection:
        connection.execute("SELECT 1")
except Exception as exc:  # pragma: no cover
    _SKIP = f"psycopg or database unavailable: {exc}"


class _Rollback(Exception):
    pass


@unittest.skipIf(_SKIP, _SKIP or "")
class DatabaseRunnerTests(unittest.TestCase):
    # spec: F11.ac59
    def test_ac59_one_forward_day_runs_and_reconciles(self):
        from agents.demand_forecast.feature_build import build_feature_set
        from agents.demand_forecast.inference import build_inference_rows
        from agents.demand_forecast.model import persist_forecasts, predict, train
        from agents.scheduler.inputs import assemble_solver_inputs
        from agents.scheduler.shifts import RULE_MAX_SHIFT_HOURS, RULE_MIN_SHIFT_HOURS, find_rule_value
        from data_access.session import open_session_context

        version = "test-cs016d"
        target = (DAY, DAY)
        try:
            with psycopg.connect(DSN) as conn, conn.transaction():
                open_session_context(conn, role="app_rw", tenant_id="00000000-0000-0000-0000-000000000001")
                build_feature_set(conn, feature_version=version)
                train(conn, model_version=version, feature_version=version, window=(date(2023, 1, 1), date(2023, 6, 2)))
                forecasts = predict(conn, version, target_window=target,
                                    rows=build_inference_rows(conn, window=target, history_feature_version=version))
                persist_forecasts(conn, forecasts)
                inputs = assemble_solver_inputs(conn, target_window=target, model_version=version)
                output = run_schedule(conn, target_window=target, model_version=version, policy=DB_CAPPED)
                self.assertTrue(output.shifts)
                self.assertEqual(output.objective, output.solve.objective)
                self.assertEqual(output.economics.labour_cost_cents,
                                 sum(shift.labour_cost_cents for shift in output.shifts))
                minimum = find_rule_value(inputs.shift_rules, RULE_MIN_SHIFT_HOURS)
                maximum = find_rule_value(inputs.shift_rules, RULE_MAX_SHIFT_HOURS)
                stores = {store.location_id: store for store in inputs.stores}
                for shift in output.shifts:
                    self.assertEqual(shift.day, DAY)
                    window = stores[shift.location_id].open_windows[shift.day.weekday()]
                    self.assertGreaterEqual(shift.start_hour, Decimal(window.open_hour))
                    self.assertLessEqual(shift.end_hour, Decimal(window.close_hour))
                    self.assertGreaterEqual(shift.hours, minimum)
                    self.assertLessEqual(shift.hours, maximum)
                raise _Rollback()
        except _Rollback:
            pass


if __name__ == "__main__":
    unittest.main()
