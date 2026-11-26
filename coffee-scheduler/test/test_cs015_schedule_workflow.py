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

    # spec: F12.ac8 — Model output only chooses from the two supported route values.
    def test_intent_cannot_inject_tool_arguments(self):
        self.assertEqual(route_intent("generate draft", lambda _: "generate"), "generate")
        self.assertEqual(route_intent("generate draft", lambda _: {"tenant_id": str(uuid4())}), "clarify")


if __name__ == "__main__":
    unittest.main()
