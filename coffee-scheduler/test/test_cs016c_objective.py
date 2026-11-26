"""Offline and rollback-gated checks for CS-016c soft scheduler objectives."""

from __future__ import annotations

import os
import sys
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ortools.sat.python import cp_model  # noqa: E402

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
from agents.scheduler.objective import (  # noqa: E402
    ObjectiveWeights,
    build_objective,
    required_labour_hours,
)
from agents.scheduler.shifts import ShiftSlot  # noqa: E402
from contracts.forecast import ForecastRecord, ForecastSet, StoreOpenWindow  # noqa: E402

DAY = date(2026, 11, 23)


def _allow_all(*_args, **_kwargs) -> LegalityVerdict:
    return LegalityVerdict(True)


def _inputs(*, demand: Decimal = Decimal("1"), employees=()) -> SolverInputs:
    forecast = ForecastSet(
        "forecast-output-v1", "TAX-2023H1-v1", "test", datetime(2026, 11, 1, tzinfo=ZoneInfo("America/New_York")),
        (ForecastRecord(3, "Coffee", datetime(2026, 11, 23, 8, tzinfo=ZoneInfo("America/New_York")), demand, demand, demand,
                        "test", "TAX-2023H1-v1"),),
    )
    people = employees or (
        EmployeeInput(1, "Barista", 3, 3, False, True, True, True, Decimal("10"), "open", 0, 80, date(1990, 1, 1), False,
                      None, "", False),
        EmployeeInput(2, "Barista", 3, 3, False, True, True, True, Decimal("20"), "open", 0, 80, date(1990, 1, 1), False,
                      None, "", False),
    )
    rules = tuple(CoverageRuleInput(rule, None, rule, "0", "HEADCOUNT", "coverage") for rule in (
        "minimum crew", "COV-KEYHOLDER-OPEN", "COV-KEYHOLDER-CLOSE", "fpc", "peak skill", "break relief",
    )) + (CoverageRuleInput("COV-UTIL-TARGET", None, "utilisation", "1", "PERCENT", "coverage"),)
    return SolverInputs(
        (StoreInput(3, "Test", 2, 2, {0: StoreOpenWindow(8, 10, frozenset({0}))}),), (), (), rules,
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


def _solve(model):
    solver = cp_model.CpSolver()
    solver.parameters.random_seed = 0
    solver.parameters.num_search_workers = 1
    status = solver.Solve(model.model)
    return solver, status


class ObjectiveTests(unittest.TestCase):
    # spec: F11.ac16
    def test_ac16_required_labour_hours_is_pure(self):
        inputs = _inputs()
        self.assertEqual(required_labour_hours(inputs.demand.records, inputs.throughput, utilisation_target=Decimal("0.5")), Decimal("2"))

    # spec: F11.ac16
    def test_ac16_cheaper_legal_assignment_is_preferred(self):
        inputs = _inputs()
        slots = (ShiftSlot(3, DAY, Decimal("8"), Decimal("10")),)
        model = build_model(inputs, slots=slots, legality=_allow_all)
        build_objective(model, inputs, weights=ObjectiveWeights(labour_cost=1, understaffing=1000, overtime=0, premium=0,
                                                                 labour_pct_breach=0))
        solver, status = _solve(model)
        self.assertEqual(status, cp_model.OPTIMAL)
        self.assertEqual(solver.Value(model.assign[(1, 0)]), 1)
        self.assertEqual(solver.Value(model.assign[(2, 0)]), 0)

    # spec: F11.ac17
    def test_ac17_understaffing_is_soft_and_penalised(self):
        inputs = _inputs(employees=(_inputs().employees[0],))
        slots = (ShiftSlot(3, DAY, Decimal("8"), Decimal("10")),)
        model = build_model(inputs, slots=slots, legality=_allow_all)
        terms = build_objective(model, inputs, weights=ObjectiveWeights(labour_cost=1, understaffing=1000, overtime=0,
                                                                          premium=0, labour_pct_breach=0))
        solver, status = _solve(model)
        self.assertEqual(status, cp_model.OPTIMAL)
        self.assertEqual(solver.Value(terms.understaffing_slack[(3, DAY, 8)]), 0)

    # spec: F11.ac18
    def test_ac18_overtime_slack_is_penalised(self):
        employee = replace(_inputs().employees[0], max_hours_week=100)
        inputs = _inputs(demand=Decimal("0"), employees=(employee,))
        days = tuple(DAY + timedelta(days=index) for index in range(25) if index % 6 != 5)
        inputs = replace(
            inputs,
            stores=(StoreInput(3, "Test", 2, 2, {weekday: StoreOpenWindow(8, 10, frozenset({weekday}))
                                                    for weekday in range(7)}),),
            target_window=(DAY, DAY + timedelta(days=24)),
        )
        slots = tuple(ShiftSlot(3, day, Decimal("8"), Decimal("10")) for day in days)
        model = build_model(inputs, slots=slots, legality=_allow_all)
        for index in range(len(slots)):
            model.model.Add(model.assign[(employee.employee_id, index)] == 1)
        terms = build_objective(model, inputs, weights=ObjectiveWeights(labour_cost=0, understaffing=0, overtime=1,
                                                                          premium=0, labour_pct_breach=0))
        solver, status = _solve(model)
        self.assertEqual(status, cp_model.OPTIMAL)
        self.assertEqual(solver.Value(terms.overtime_slack[employee.employee_id]), 4)

    # spec: F11.ac19
    def test_ac19_premium_assignment_is_dispreferred(self):
        inputs = _inputs()
        slots = (ShiftSlot(3, DAY, Decimal("8"), Decimal("10")),)

        def legality(employee, _slot):
            return LegalityVerdict(True, premiums=(("PREMIUM", Decimal("5")),)) if employee.employee_id == 1 else _allow_all()

        model = build_model(inputs, slots=slots, legality=legality)
        build_objective(model, inputs, weights=ObjectiveWeights(labour_cost=0, understaffing=1000, overtime=0, premium=1,
                                                                 labour_pct_breach=0))
        solver, status = _solve(model)
        self.assertEqual(status, cp_model.OPTIMAL)
        self.assertEqual(solver.Value(model.assign[(1, 0)]), 0)
        self.assertEqual(solver.Value(model.assign[(2, 0)]), 1)

    # spec: F11.ac20
    def test_ac20_weights_change_the_chosen_solution(self):
        slots = (ShiftSlot(3, DAY, Decimal("8"), Decimal("10")),)
        inputs = _inputs(employees=(_inputs().employees[0],))
        low = build_model(inputs, slots=slots, legality=_allow_all)
        build_objective(low, inputs, weights=ObjectiveWeights(labour_cost=1001, understaffing=1, overtime=0, premium=0,
                                                               labour_pct_breach=0))
        low_solver, low_status = _solve(low)
        high = build_model(inputs, slots=slots, legality=_allow_all)
        build_objective(high, inputs, weights=ObjectiveWeights(labour_cost=1, understaffing=1001, overtime=0, premium=0,
                                                                labour_pct_breach=0))
        high_solver, high_status = _solve(high)
        self.assertEqual((low_status, high_status), (cp_model.OPTIMAL, cp_model.OPTIMAL))
        self.assertEqual(low_solver.Value(low.assign[(1, 0)]), 0)
        self.assertEqual(high_solver.Value(high.assign[(1, 0)]), 1)

    # spec: F11.ac21
    def test_ac21_objective_remains_feasible_and_deterministic(self):
        inputs = _inputs()
        slots = (ShiftSlot(3, DAY, Decimal("8"), Decimal("10")),)
        values = []
        for _ in range(2):
            model = build_model(inputs, slots=slots, legality=_allow_all)
            build_objective(model, inputs, revenue_priors={(3, "Coffee"): Decimal("4")})
            solver, status = _solve(model)
            self.assertEqual(status, cp_model.OPTIMAL)
            values.append(int(solver.ObjectiveValue()))
        self.assertEqual(values[0], values[1])


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
class OvertimePricingTests(unittest.TestCase):
    # spec: F11.ac19 — overtime is priced at the employee's premium rate, never at the weekly budget.
    def test_ac19_overtime_priced_from_rate_not_budget(self):
        inputs = _inputs()
        model = build_model(inputs, legality=_allow_all)
        weights = ObjectiveWeights()
        terms = build_objective(model, inputs, weights=weights)
        objective = model.model.Proto().objective
        coefficients = dict(zip(objective.vars, objective.coeffs, strict=True))
        budget_cents = 5000  # cost_targets.weekly_overtime_budget_usd is $50 in the fixture
        for employee_id, rate in ((1, Decimal("10")), (2, Decimal("20"))):
            # Half an hour of overtime at the statutory 1.5x premium.
            expected = weights.overtime * int(rate * Decimal("1.5") / Decimal(2) * 100)
            actual = coefficients[terms.overtime_slack[employee_id].Index()]
            self.assertEqual(actual, expected, f"employee {employee_id} overtime coefficient")
            # Pricing an overtime half-hour at the whole weekly budget is the defect this pins.
            self.assertNotEqual(actual, weights.overtime * budget_cents)
        # The budget is a threshold, so it appears as its own breach slack rather than a unit price.
        self.assertIn(3, terms.overtime_budget_slack)


class DatabaseObjectiveTests(unittest.TestCase):
    # spec: F11.ac21
    def test_ac21_real_one_day_objective_builds(self):
        from agents.demand_forecast.feature_build import build_feature_set
        from agents.demand_forecast.inference import build_inference_rows
        from agents.demand_forecast.model import persist_forecasts, predict, train
        from agents.scheduler.inputs import assemble_solver_inputs
        from agents.scheduler.objective import revenue_priors
        from data_access.session import open_session_context

        version = "test-cs016c"
        # A forward day, so the roster is valid for its own era (BUG-069). A future window has no
        # observed demand, so the forecast comes from CS-014d's target-free inference rows; the
        # training table holds nothing for 2026.
        target = (date(2026, 12, 7), date(2026, 12, 7))
        try:
            with psycopg.connect(DSN) as conn, conn.transaction():
                open_session_context(conn, role="app_rw", tenant_id="00000000-0000-0000-0000-000000000001")
                build_feature_set(conn, feature_version=version)
                train(conn, model_version=version, feature_version=version, window=(date(2023, 1, 1), date(2023, 6, 2)))
                rows = build_inference_rows(conn, window=target, history_feature_version=version)
                forecasts = predict(conn, version, target_window=target, rows=rows)
                self.assertEqual(persist_forecasts(conn, forecasts), len(forecasts.records))
                inputs = assemble_solver_inputs(conn, target_window=target, model_version=version)
                self.assertGreater(len(inputs.demand.records), 0)

                model = build_model(inputs, legality=_allow_all)
                terms = build_objective(model, inputs)
                self.assertTrue(all(isinstance(value, int) for value in (terms.weights.labour_cost, terms.weights.overtime)))
                self.assertGreater(terms.weights.labour_cost, 0)
                self.assertIsInstance(terms.omitted, tuple)
                # No revenue prior was supplied, so the labour-percent term must be RECORDED as
                # omitted — with a reason — rather than silently scored against zero sales.
                omitted = [entry for entry in terms.omitted if entry.startswith("labour_pct_breach")]
                self.assertTrue(omitted, terms.omitted)
                self.assertTrue(all("revenue prior unavailable" in entry for entry in omitted), omitted)
                # Supplying a prior derived from the training set makes the term available.
                priors = revenue_priors(conn, version)
                self.assertTrue(priors, "the history feature set should yield revenue-per-item priors")
                with_priors = build_objective(model, inputs, revenue_priors=priors)
                self.assertFalse(
                    [entry for entry in with_priors.omitted if entry.startswith("labour_pct_breach")],
                    with_priors.omitted,
                )
                raise _Rollback()
        except _Rollback:
            pass


if __name__ == "__main__":
    unittest.main()
