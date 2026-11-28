"""Tenant-scoped forward forecast and ADK execution boundary."""
import json
from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from hashlib import sha256
from zoneinfo import ZoneInfo

import psycopg
from fastapi.concurrency import run_in_threadpool

from agents.a2a_transport import AgentError, ServiceIdentity
from agents.demand_forecast.feature_build import FeatureBuildError
from agents.demand_forecast.inference import build_inference_rows
from agents.demand_forecast.model import ModelError, load_model, predict
from agents.requests import WindowRequest
from contracts.forecast import CANONICAL_TZ, ForecastContractError, to_json
from data_access.session import tenant_session


class ForecastRequest(WindowRequest):
    model_version: str
    feature_version: str
    taxonomy_version: str
    input_revision: str


def forecast_source(conn, approved_model: str, window) -> tuple[dict[str, str], list[dict]]:
    """Pin the model and underlying forward inference facts for this run."""
    if not conn.execute("SELECT 1 FROM tenants WHERE status IN ('active', 'trial') AND timezone = %s",
                        (CANONICAL_TZ,)).fetchone():
        raise AgentError("tenant_calendar_unavailable")
    model = load_model(conn, approved_model)
    rows = build_inference_rows(conn, window=window, history_feature_version=model["feature_version"],
                                taxonomy_version=model["taxonomy_version"])
    zone = ZoneInfo(CANONICAL_TZ)
    for day, hour in {(row["obs_date"], row["obs_hour"]) for row in rows}:
        local = datetime(day.year, day.month, day.day, hour, tzinfo=zone)
        if (local.utcoffset() != local.replace(fold=1).utcoffset()
                or local.astimezone(UTC).astimezone(zone) != local):
            raise AgentError("unsupported_local_hour")
    revision_data = {"approach": model["approach"], "params": model["params"], "inference": rows}
    revision = sha256(json.dumps(revision_data, sort_keys=True, default=str, allow_nan=False).encode()).hexdigest()
    return {**{field: model[field] for field in ("model_version", "feature_version", "taxonomy_version")},
            "input_revision": revision}, rows


def score_forecast(dsn: str, approved_model: str, identity: ServiceIdentity, data: dict) -> dict:
    request = ForecastRequest.parse(data)
    try:
        with tenant_session(dsn, role="app_ro", tenant_id=identity.tenant_id, repeatable_read=True) as conn:
            conn.execute("SET LOCAL statement_timeout = '10s'")
            current, rows = forecast_source(conn, approved_model, request.window)
            if any(getattr(request, field) != value for field, value in current.items()):
                raise AgentError("forecast_version_mismatch")
            result = predict(conn, approved_model, target_window=request.window,
                             feature_version=request.feature_version, rows=rows)
            return json.loads(to_json(result))
    except (psycopg.OperationalError, psycopg.errors.QueryCanceled):
        raise AgentError("forecast_unavailable", transient=True) from None
    except (FeatureBuildError, ModelError, ForecastContractError):
        raise AgentError("invalid_forecast_inputs") from None


def adk_handler(
    dsn: str, approved_model: str,
    *, scorer: Callable[[ServiceIdentity, dict], dict] | None = None,
):
    """ADK owns the remote execution; its agent delegates numeric work to F9."""
    from google.adk.agents import BaseAgent
    from google.adk.events import Event
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    score = scorer or partial(score_forecast, dsn, approved_model)

    async def handle(identity: ServiceIdentity, data: dict) -> dict:
        class ForecastAgent(BaseAgent):
            async def _run_async_impl(self, ctx):
                result = await run_in_threadpool(score, identity, data)
                yield Event(author=self.name, invocation_id=ctx.invocation_id, content=types.Content(
                    role="model", parts=[types.Part(text=json.dumps(result, allow_nan=False))],
                ))

        runner = InMemoryRunner(agent=ForecastAgent(name="demand_forecast"), app_name="demand_forecast")
        user = str(identity.tenant_id)
        try:
            session = await runner.session_service.create_session(app_name=runner.app_name, user_id=user)
            events = runner.run_async(user_id=user, session_id=session.id, new_message=types.Content(
                role="user", parts=[types.Part(text="predict_demand")],
            ))
            async for event in events:
                if event.author == runner.agent.name and event.content and event.content.parts:
                    result = json.loads(event.content.parts[0].text)
                    # Complete the invocation before returning its final event.
                    async for _ in events:
                        pass
                    return result
            raise AgentError("forecast_invalid_response")
        finally:
            await runner.close()

    return handle
