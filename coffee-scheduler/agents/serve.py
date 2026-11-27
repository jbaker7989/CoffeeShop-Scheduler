"""Run one authenticated A2A boundary, configured entirely on the server."""
from __future__ import annotations

import argparse
import os
from functools import partial
from pathlib import Path

import uvicorn
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key, load_pem_public_key
from fastapi.concurrency import run_in_threadpool

from agents.a2a_transport import SERVICE_IDS, AgentClient, ServiceTrust, create_agent_app, validate_endpoint


def _required(name: str) -> str:
    value = os.environ[name]
    if not value.strip():
        raise ValueError(f"{name} must be configured")
    return value


def build_app(agent: str, *, classify_intent=None):
    url = os.environ["A2A_ENDPOINT"]
    loopback = os.environ.get("A2A_LOOPBACK_TEST") == "1"
    validate_endpoint(url, allow_loopback=loopback)
    trust = ServiceTrust(os.environ["A2A_TRUSTED_ISSUER"],
                         load_pem_public_key(Path(os.environ["A2A_TRUST_PUBLIC_KEY_FILE"]).read_bytes()))
    if agent == "staff_comms":
        from agents.staff_comms.preview import preview

        def handler(identity, data):
            return preview(data)

    elif agent == "labor_compliance":
        from agents.labor_compliance.service import evaluate_proposal
        handler = partial(evaluate_proposal, _required("DATABASE_URL"))

    elif agent == "demand_forecast":
        from agents.demand_forecast.service import adk_handler
        return create_agent_app(agent, url, trust, adk_handler(_required("DATABASE_URL"),
                                _required("SCHEDULE_FORECAST_MODEL")), allow_loopback=loopback)

    elif agent == "scheduler":
        from agents.scheduler.service import generate_draft, make_workflow
        key = load_pem_private_key(Path(os.environ["A2A_FORECAST_PRIVATE_KEY_FILE"]).read_bytes(), password=None)
        if not isinstance(key, RSAPrivateKey) or key.key_size < 2048:
            raise ValueError("forecast delegation requires an RSA key of at least 2048 bits")
        workflow = make_workflow(
            _required("DATABASE_URL"), _required("SCHEDULE_FORECAST_MODEL"),
            AgentClient(os.environ["A2A_FORECAST_ENDPOINT"], allow_loopback=loopback),
            _required("A2A_FORECAST_ISSUER"), key, budget=int(os.environ.get("SCHEDULE_BUDGET_SECONDS", "90")),
            classify=classify_intent,
        )
        handler = partial(generate_draft, workflow)

    else:
        raise ValueError("unknown agent")

    async def invoke(identity, data):
        return await run_in_threadpool(handler, identity, data)

    return create_agent_app(agent, url, trust, invoke, allow_loopback=loopback)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("agent", choices=sorted(SERVICE_IDS))
    parser.add_argument("--port", default=8000, type=int)
    args = parser.parse_args()
    # Bind privately. HTTPS is terminated by the configured trusted edge.
    uvicorn.run(build_app(args.agent), host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
