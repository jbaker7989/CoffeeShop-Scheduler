"""Graph sequencing, deadline and draft contract tests."""
import unittest
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

from agents.a2a_transport import AgentError, ServiceIdentity
from agents.scheduler.solve import NoIncumbentError
from agents.scheduler.workflow import DraftRequest, ScheduleWorkflow, route_intent

DAY = date(2026, 11, 27)
REQUEST = {"version": 1, "start": DAY.isoformat(), "end": DAY.isoformat()}
IDENTITY = ServiceIdentity(uuid4(), "test-manager", "test-issuer")


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.stages = []

        def stage(name, result):
            def call(*args):
                self.stages.append(name)
                return result
            return call

        output = SimpleNamespace(to_dict=lambda: {
            "shifts": [{"employee_id": 1, "hourly_rate": "100.00", "labour_cost_cents": 10000}],
            "economics": {"labour_cost_cents": 10000},
            "solve": {"objective": 10000, "assignments": [[1, 0]]},
        })
        self.workflow = ScheduleWorkflow(
            gather=stage("gather", {"model_version": "fixture", "feature_version": "f1",
                                    "taxonomy_version": "t1", "request_start": DAY.isoformat(),
                                    "request_end": DAY.isoformat()}),
            forecast=stage("forecast", {"fixture": "forecast"}),
            prepare=stage("compliance", object()),
            solve=stage("solve", output),
        )

    # spec: F12.ac1 — Only the compiled workflow emits the versioned draft.
    # spec: F12.ac9 — The saved draft excludes private pay and internal solver indices.
    def test_graph_happy_path(self):
        result = self.workflow.invoke(IDENTITY, DraftRequest.parse(REQUEST))
        self.assertEqual(self.stages, ["gather", "forecast", "compliance", "solve"])
        self.assertEqual(result["status"], "draft")
        self.assertEqual(result["version"], 1)
        self.assertEqual(result["schedule"]["economics"], {"labour_cost_cents": 10000})
        self.assertEqual(result["schedule"]["shifts"], [{"employee_id": 1}])
        self.assertNotIn("assignments", result["schedule"]["solve"])

    # spec: F12.ac2 — Invalid requests do not execute any graph stage.
    def test_invalid_window_and_scope(self):
        for change in ({"start": True}, {"start": "20261127"}, {"end": "2026-12-04"},
                       {"end": "2026-11-26"}, {"tenant_id": str(uuid4())}, {"version": True}):
            with self.subTest(change=change), self.assertRaises(AgentError):
                DraftRequest.parse({**REQUEST, **change})
        self.assertEqual(self.stages, [])

    # spec: F12.ac5 — No incumbent has a typed outcome and no draft.
    def test_no_incumbent(self):
        def fail(*_):
            raise NoIncumbentError("UNKNOWN")
        self.workflow.solve = fail
        with self.assertRaisesRegex(AgentError, "no_incumbent"):
            self.workflow.invoke(IDENTITY, DraftRequest.parse(REQUEST))

    # spec: F12.ac6 — Only transient forecast errors are retried.
    def test_one_transient_retry(self):
        def flaky(*_):
            self.stages.append("forecast")
            if self.stages.count("forecast") == 1:
                raise AgentError("forecast_unavailable", transient=True)
            return {"fixture": "forecast"}
        self.workflow.forecast = flaky
        self.workflow.invoke(IDENTITY, DraftRequest.parse(REQUEST))
        self.assertEqual(self.stages, ["gather", "forecast", "forecast", "compliance", "solve"])

    # spec: F12.ac6 — A permanent rejection ends the graph before compliance.
    def test_permanent_forecast_rejection_is_not_retried(self):
        def invalid(*_):
            self.stages.append("forecast")
            raise AgentError("forecast_rejected")

        self.workflow.forecast = invalid
        with self.assertRaisesRegex(AgentError, "forecast_rejected"):
            self.workflow.invoke(IDENTITY, DraftRequest.parse(REQUEST))
        self.assertEqual(self.stages, ["gather", "forecast"])

    # spec: F12.ac6 — The graph cannot skip the total deadline to launch another attempt.
    def test_elapsed_deadline_prevents_compliance(self):
        now = [0.0]
        self.workflow.clock = lambda: now[0]

        def slow(*_):
            self.stages.append("forecast")
            now[0] = 120.0
            return {"forecast": 1}

        self.workflow.forecast = slow
        with self.assertRaisesRegex(AgentError, "draft_deadline"):
            self.workflow.invoke(IDENTITY, DraftRequest.parse(REQUEST))
        self.assertEqual(self.stages, ["gather", "forecast"])

    # spec: F12.ac8 — Model output only chooses from the two supported route values.
    def test_intent_cannot_inject_tool_arguments(self):
        self.assertEqual(route_intent("generate draft", lambda _: "generate"), "generate")
        self.assertEqual(route_intent("generate draft", lambda _: {"tenant_id": str(uuid4())}), "clarify")
        intent = DraftRequest.parse({**REQUEST, "intent": "generate draft"})
        self.workflow.classify = lambda _: {"tenant_id": str(uuid4())}
        with self.assertRaisesRegex(AgentError, "clarification_required"):
            self.workflow.invoke(IDENTITY, intent)
        self.assertEqual(self.stages, [])
        self.workflow.classify = lambda _: "generate"
        self.assertEqual(self.workflow.invoke(IDENTITY, intent)["status"], "draft")


class StoreHourTests(unittest.TestCase):
    # spec: F12.ac3 — An exact-hour forecast bucket at half-hour close has partial-hour coverage.
    def test_half_hour_close_clips_slots_and_keeps_final_bucket(self):
        from dataclasses import replace
        from datetime import time
        from decimal import Decimal

        from cs093_support import tiny_optimal_inputs

        from agents.scheduler.constraints import build_model
        from agents.scheduler.inputs import build_stores

        inputs = tiny_optimal_inputs()
        day = inputs.target_window[0]
        store = build_stores(
            [{"location_id": 3, "store_location": "fixture", "max_staff_on_floor": 2, "stations": 2}],
            [{"location_id": 3, "day_of_week": day.strftime("%A"),
              "open_time": time(8), "close_time": time(12, 30)}],
        )[0]
        model = build_model(replace(inputs, stores=(store,)))
        self.assertEqual(max(slot.end_hour for slot in model.slots), Decimal("12.5"))
        self.assertTrue(model.index.by_store_day_hour[(3, day, 12)])


    # spec: F12.ac3 — One employee supplies only a half-hour of capacity in the final bucket.
    def test_half_hour_capacity_counts_in_objective(self):
        from dataclasses import replace
        from decimal import Decimal

        from cs093_support import tiny_optimal_inputs
        from ortools.sat.python import cp_model

        from agents.scheduler.constraints import build_model
        from agents.scheduler.objective import build_objective
        from agents.scheduler.shifts import ShiftSlot
        from contracts.forecast import StoreOpenWindow

        inputs = tiny_optimal_inputs()
        day = inputs.target_window[0]
        weekday = day.weekday()
        store = replace(inputs.stores[0], open_windows={weekday: StoreOpenWindow(8, 13, frozenset({weekday}))},
                        partial_close_hours={weekday: Decimal("12.5")})
        demand = inputs.demand.records[0]
        demand = replace(demand, forecast_hour=demand.forecast_hour.replace(hour=12))
        inputs = replace(inputs, stores=(store,), demand=replace(inputs.demand, records=(demand,)))
        slots = (ShiftSlot(3, day, Decimal("8.5"), Decimal("12.5")),)
        model = build_model(inputs, slots=slots)
        terms = build_objective(model, inputs)
        model.model.Add(model.assign[(1, 0)] == 1)
        solver = cp_model.CpSolver()
        self.assertEqual(solver.Solve(model.model), cp_model.OPTIMAL)
        self.assertEqual(solver.Value(terms.understaffing_slack[(3, day, 12)]), 1)

    # spec: F12.ac5 — Different opening and closing key-holder obligations are enforced at their own edge.
    def test_key_holder_edge_is_matched_by_canonical_id(self):
        from dataclasses import replace
        from decimal import Decimal

        from cs093_support import tiny_optimal_inputs

        from agents.scheduler.constraints import assert_feasible, build_model
        from agents.scheduler.shifts import ShiftSlot
        from contracts.forecast import StoreOpenWindow

        inputs = tiny_optimal_inputs()
        day = inputs.target_window[0]
        people = tuple(replace(inputs.employees[0], employee_id=i) for i in (1, 2, 3))
        store = replace(inputs.stores[0], open_windows={day.weekday(): StoreOpenWindow(8, 16, frozenset({day.weekday()}))})
        values = {"COV-KEYHOLDER-OPEN": "2", "COV-KEYHOLDER-CLOSE": "1"}
        rules = tuple(replace(rule, value=values.get(rule.rule_id, rule.value)) for rule in inputs.coverage_rules)
        slots = (ShiftSlot(3, day, Decimal(8), Decimal(12)), ShiftSlot(3, day, Decimal(12), Decimal(16)))
        assert_feasible(build_model(replace(inputs, stores=(store,), employees=people, coverage_rules=rules), slots=slots))


if __name__ == "__main__":
    unittest.main()
