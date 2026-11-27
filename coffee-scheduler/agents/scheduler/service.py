"""Production domain adapters for the schedule-generation graph."""
import json
from time import monotonic

import psycopg

from agents.a2a_transport import AgentClient, AgentError, ServiceIdentity, forecast_token
from agents.demand_forecast.feature_build import FeatureBuildError
from agents.demand_forecast.model import ModelError
from agents.demand_forecast.service import forecast_source
from agents.scheduler.constraints import InfeasibleModelError, legality_from_compliance
from agents.scheduler.draft_runs import DraftRuns
from agents.scheduler.inputs import SolverInputError, assemble_solver_inputs
from agents.scheduler.runner import RunnerDeadlineExceeded, run_prepared_schedule
from agents.scheduler.shifts import SolverModelError
from agents.scheduler.solve import production_stop_policy
from agents.scheduler.workflow import DraftRequest, ScheduleWorkflow
from contracts.forecast import ForecastContractError, from_json
from data_access.session import tenant_session


def make_workflow(dsn, model_version, client: AgentClient, signing_issuer, signing_key, *, budget=90, classify=None):
    policy = production_stop_policy()

    def gather(identity: ServiceIdentity, request: DraftRequest):
        try:
            with tenant_session(dsn, role="app_ro", tenant_id=identity.tenant_id, repeatable_read=True) as conn:
                conn.execute("SET LOCAL statement_timeout = '10s'")
                pins, _ = forecast_source(conn, model_version, request.window)
                return pins
        except (ModelError, FeatureBuildError, ForecastContractError):
            raise AgentError("invalid_forecast_inputs") from None

    def forecast(identity, request, pins, timeout):
        payload = {"version": request.version, "start": request.start, "end": request.end, **pins}
        return client.send(payload, forecast_token(identity, signing_issuer, signing_key), timeout=timeout)

    def prepare(identity, request, pins, data):
        try:
            fs = from_json(json.dumps(data, allow_nan=False))
            with tenant_session(dsn, role="app_ro", tenant_id=identity.tenant_id, repeatable_read=True) as conn:
                conn.execute("SET LOCAL statement_timeout = '10s'")
                current, _ = forecast_source(conn, model_version, request.window)
                if pins != current:
                    raise AgentError("stale_forecast_inputs")
                inputs = assemble_solver_inputs(conn, target_window=request.window, model_version=model_version,
                                                taxonomy_version=pins["taxonomy_version"], forecast=fs)
                legality = legality_from_compliance(conn, inputs, tenant_id=str(identity.tenant_id))
                return inputs, legality
        except (ForecastContractError, SolverInputError, ModelError, FeatureBuildError, ValueError):
            raise AgentError("invalid_schedule_inputs") from None

    def solve(prepared, pins, remaining):
        inputs, legality = prepared
        try:
            return run_prepared_schedule(inputs, model_version=pins["model_version"], policy=policy,
                                         legality=legality, deadline=monotonic() + remaining)
        except RunnerDeadlineExceeded:
            raise AgentError("draft_deadline") from None
        except InfeasibleModelError:
            raise
        except SolverModelError:
            raise AgentError("invalid_schedule_constraints") from None

    return ScheduleWorkflow(gather=gather, forecast=forecast, prepare=prepare, solve=solve,
                            store=DraftRuns(dsn), budget=budget, classify=classify)


def generate_draft(workflow: ScheduleWorkflow, identity: ServiceIdentity, data: dict) -> dict:
    try:
        return workflow.invoke(identity, DraftRequest.parse(data))
    except (psycopg.OperationalError, psycopg.errors.QueryCanceled, psycopg.errors.LockNotAvailable):
        raise AgentError("draft_unavailable", transient=True) from None
