"""Typed LangGraph workflow for a tenant's reviewable schedule draft."""
from __future__ import annotations

import logging
from itertools import pairwise
from random import uniform
from time import monotonic, sleep
from typing import TypedDict
from uuid import UUID, uuid4

from langchain_core.prompts import PromptTemplate
from langchain_core.tools import StructuredTool
from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context
from opentelemetry.trace import SpanKind

from agents.a2a_transport import AgentError, ServiceIdentity
from agents.requests import WindowRequest
from agents.scheduler.constraints import InfeasibleModelError
from agents.scheduler.draft_runs import DraftRuns
from agents.scheduler.solve import NoIncumbentError, UnacceptableGapError
from compliance.engine import ComplianceError
from otel.boundary import boundary_span

_LOG = logging.getLogger(__name__)
_INTENT_PROMPT = PromptTemplate.from_template(
    "Classify the request as generate or clarify. Reply with only one of those words.\nRequest: {text}"
)


class DraftRequest(WindowRequest):
    idempotency_key: str | None = None
    intent: str | None = None

    @classmethod
    def parse(cls, data: dict):
        request = super().parse(data)
        if request.intent is not None and not 1 <= len(request.intent.strip()) <= 512:
            raise AgentError("invalid_intent")
        if request.idempotency_key is not None:
            try:
                if str(UUID(request.idempotency_key)) != request.idempotency_key:
                    raise ValueError
            except ValueError:
                raise AgentError("invalid_idempotency_key") from None
        return request


class _State(TypedDict, total=False):
    pins: dict
    forecast: dict
    prepared: object
    schedule: dict
    result: dict


def route_intent(text, classify) -> str:
    """An untrusted model may choose an intent from a fixed vocabulary."""
    if not isinstance(text, str) or not 1 <= len(text) <= 512:
        return "clarify"
    result = classify(_INTENT_PROMPT.format(text=text))
    return "generate" if result == "generate" else "clarify"


def _invoke_tool(tool, state):
    with boundary_span("tool.scheduler.invoke", SpanKind.INTERNAL, "scheduler", "invoke"):
        return tool.invoke({"state": state})


class ScheduleWorkflow:
    def __init__(self, *, gather, forecast, prepare, solve, store: DraftRuns | None = None,
                 budget: int = 90, clock=monotonic, pause=sleep, classify=None):
        if type(budget) is not int or not 2 <= budget <= 120:
            raise ValueError("schedule budget must be between 2 and 120 seconds")
        self.gather = gather
        self.forecast = forecast
        self.prepare = prepare
        self.solve = solve
        self.store = store
        self.budget = budget
        self.clock = clock
        self.pause = pause
        self.classify = classify

    def invoke(self, identity: ServiceIdentity, request: DraftRequest):
        deadline = self.clock() + self.budget
        run_id = UUID(request.idempotency_key) if request.idempotency_key else uuid4()
        claim = self.store.claim(identity, run_id, request.model_dump(), lease_seconds=self.budget + 10) if self.store else None
        if claim and claim.stage == "done":
            return claim.state["result"]

        def remaining():
            seconds = deadline - self.clock()
            if seconds <= 0:
                raise AgentError("draft_deadline")
            return seconds

        def save(stage, state):
            remaining()
            if claim:
                self.store.save(identity, claim, stage, state)

        def gather(_state):
            remaining()
            if request.intent is not None and (
                self.classify is None or route_intent(request.intent, self.classify) != "generate"
            ):
                raise AgentError("clarification_required")
            remaining()
            pins = self.gather(identity, request)
            checkpoint = claim.state if claim else {}
            if checkpoint.get("pins") == pins and "forecast" in checkpoint:
                return checkpoint
            save("gather", {"pins": pins})
            return {"pins": pins}

        def forecast(state):
            if "forecast" in state:
                return {}
            for attempt in range(2):
                try:
                    data = self.forecast(identity, request, state["pins"], min(12.0, remaining()))
                    break
                except AgentError as exc:
                    if not exc.transient or attempt == 1:
                        raise
                    delay = uniform(0.05, 0.15)
                    if remaining() <= delay:
                        raise AgentError("draft_deadline") from None
                    self.pause(delay)
            return {"forecast": data}

        def compliance(state):
            remaining()
            inputs = self.prepare(identity, request, state["pins"], state["forecast"])
            if not claim or claim.stage != "forecast" or claim.state.get("pins") != state["pins"]:
                save("forecast", {"pins": state["pins"], "forecast": state["forecast"]})
            return {"prepared": inputs}

        def solve(state):
            output = self.solve(state["prepared"], state["pins"], remaining()).to_dict()
            return {"schedule": output}

        def present(state):
            output = state["schedule"]
            for shift in output["shifts"]:
                for private in ("hourly_rate", "labour_cost_cents", "premium_cents"):
                    shift.pop(private, None)
            output["solve"].pop("assignments", None)
            # Produce stable JSON through checkpoint/replay even for numeric mapping keys.
            if "labour_cents_by_store" in output["economics"]:
                output["economics"]["labour_cents_by_store"] = {
                    str(key): value for key, value in output["economics"]["labour_cents_by_store"].items()
                }
            result = {"version": 1, "run_id": str(run_id), "status": "draft",
                      "compliance_scope": "per_assignment", "claims_legal_accuracy": False,
                      "forecast": state["pins"], "schedule": output}
            save("done", {"result": result})
            return {"result": result}

        stages = {"gather": gather, "forecast": forecast, "compliance": compliance, "solve": solve, "present": present}
        graph = StateGraph(_State)
        for name, function in stages.items():
            graph.add_node(name, self._node(name, function))

        graph.add_edge(START, "gather")
        for src, dst in pairwise(stages):
            graph.add_edge(src, dst)
        graph.add_edge("present", END)

        try:
            # Tool inputs include operational data. Emit local stage metrics only.
            with tracing_context(enabled=False):
                return graph.compile().invoke({}, config={"recursion_limit": 8})["result"]
        except NoIncumbentError:
            raise AgentError("no_incumbent") from None
        except UnacceptableGapError:
            raise AgentError("unacceptable_gap") from None
        except InfeasibleModelError as exc:
            raise AgentError("infeasible:" + ",".join(exc.groups)) from None
        except ComplianceError:
            raise AgentError("compliance_unavailable") from None
        finally:
            if claim:
                try:
                    self.store.release(identity, claim)
                except Exception as exc:
                    _LOG.warning("Draft %s release failed (%s)", run_id, type(exc).__name__)

    @staticmethod
    def _node(name, function):
        def execute(state: dict):
            """Execute one stage with validated upstream graph state."""
            return function(state)

        tool = StructuredTool.from_function(execute, name=name)

        def invoke(state: _State):
            started = monotonic()
            try:
                return _invoke_tool(tool, state)
            finally:
                _LOG.info("Schedule draft stage %s finished in %.3fs", name, monotonic() - started)

        return invoke
