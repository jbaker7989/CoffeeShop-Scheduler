"""Shared A2A 0.3 JSON-RPC profile for typed request/response skills."""
from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from time import monotonic, time
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
import jwt
from a2a.types import AgentCard, Message, SendMessageRequest, SendMessageResponse, SendMessageSuccessResponse
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from opentelemetry.trace import SpanKind
from pydantic import ValidationError

from otel.boundary import boundary_span, incoming_context, inject_trace, mark_error

PROTOCOL = "0.3.0"
MAX_REQUEST_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
SERVICE_IDS = frozenset({"scheduler", "demand_forecast", "labor_compliance", "staff_comms"})
_AUTHORITY_FIELDS = frozenset({"tenant_id", "tenantId", "subject", "actor", "scope", "role", "roles"})
_LOG = logging.getLogger(__name__)


class AgentError(Exception):
    def __init__(self, reason: str, *, transient: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.transient = transient


@dataclass(frozen=True, slots=True)
class ServiceIdentity:
    tenant_id: UUID
    subject: str
    issuer: str


@dataclass(frozen=True, slots=True)
class ServiceTrust:
    issuer: str
    key: RSAPublicKey

    def __post_init__(self):
        if not self.issuer or not isinstance(self.key, RSAPublicKey) or self.key.key_size < 2048:
            raise ValueError("service trust requires an issuer and RSA public key of at least 2048 bits")

    def verify(self, authorization: str, *, agent: str, skill: str) -> ServiceIdentity:
        scheme, _, encoded = authorization.partition(" ")
        if scheme.lower() != "bearer" or not encoded or len(encoded) > 4096:
            raise AgentError("unauthenticated")
        try:
            data = jwt.decode(
                encoded, self.key, algorithms=["RS256"], audience=agent, issuer=self.issuer,
                options={"require": ["exp", "iat", "aud", "iss", "sub", "tenant_id", "scope"],
                         "strict_aud": True},
            )
            tenant = UUID(data["tenant_id"])
            if tenant.int == 0 or not 0 < len(data["sub"]) <= 128 or not data["sub"].isprintable():
                raise ValueError("invalid identity")
            if (type(data["iat"]) is not int or type(data["exp"]) is not int
                    or not 0 < data["exp"] - data["iat"] <= 120):
                raise ValueError("invalid lifetime")
        except (jwt.InvalidTokenError, TypeError, ValueError, AttributeError):
            raise AgentError("unauthenticated") from None
        if data["scope"] != skill:
            raise AgentError("forbidden")
        return ServiceIdentity(tenant, data["sub"], self.issuer)


def forecast_token(identity: ServiceIdentity, issuer: str, key: RSAPrivateKey) -> str:
    now = int(time())
    return jwt.encode(
        {"iss": issuer, "sub": identity.subject, "tenant_id": str(identity.tenant_id),
         "aud": "demand_forecast", "scope": "predict_demand", "iat": now, "exp": now + 60},
        key, algorithm="RS256",
    )


def validate_endpoint(url: str, *, allow_loopback: bool = False):
    parsed = urlsplit(url)
    insecure_test = allow_loopback and parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "::1")
    if (not (parsed.scheme == "https" or insecure_test) or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment or parsed.path != "/a2a"
            or url != parsed.geturl() or any(c.isspace() or c == "\\" for c in url)):
        raise ValueError("configure an HTTPS agent endpoint ending in /a2a")
    if parsed.port == 0:
        raise ValueError("invalid agent port")
    return parsed


def read_card(agent: str, url: str, *, allow_loopback: bool = False) -> AgentCard:
    validate_endpoint(url, allow_loopback=allow_loopback)
    if agent not in SERVICE_IDS:
        raise ValueError("unknown callable agent")
    data = json.loads((Path(__file__).parent / agent / "agent_card.json").read_text())
    card = AgentCard.model_validate({**data, "url": url})
    scheme = (card.security_schemes or {}).get("serviceBearer")
    if (card.name != agent or card.protocol_version != PROTOCOL or len(card.skills) != 1
            or scheme is None or scheme.root.type != "http" or scheme.root.scheme.lower() != "bearer"
            or card.security != [{"serviceBearer": []}] or card.preferred_transport != "JSONRPC"):
        raise ValueError("invalid runtime card")
    return card


def strict_json(raw: bytes) -> object:
    def unique(pairs):
        parsed = {}
        for key, value in pairs:
            if key in parsed:
                raise ValueError("duplicate JSON key")
            parsed[key] = value
        return parsed

    def invalid(_):
        raise ValueError("non-finite JSON value")

    def finite_float(number):
        value = float(number)
        if not isfinite(value):
            raise ValueError("non-finite JSON value")
        return value

    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid, parse_float=finite_float)


class _RPCErrorResponse(JSONResponse):
    """A JSON-RPC error can have a successful HTTP status."""


def _rpc_error(request_id, code: int, reason: str, *, transient: bool = False):
    return _RPCErrorResponse({"jsonrpc": "2.0", "id": request_id, "error": {
        "code": code, "message": reason, "data": {"transient": transient},
    }})


async def _read_request(request: Request):
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_REQUEST_BYTES:
            raise AgentError("request_too_large")
        raw.extend(chunk)
    return strict_json(raw)


def create_agent_app(
    agent: str, url: str, trust: ServiceTrust, handler: Callable[[ServiceIdentity, dict], Awaitable[dict]],
    *, allow_loopback: bool = False,
) -> FastAPI:
    card = read_card(agent, url, allow_loopback=allow_loopback)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/.well-known/agent-card.json")
    async def discover():
        return card.model_dump(by_alias=True, exclude_none=True)

    @app.post("/a2a")
    async def invoke(request: Request):
        with boundary_span(
            "a2a.message.receive", SpanKind.SERVER, agent, "message/send",
            parent=incoming_context(request.headers),
        ) as span:
            response = await dispatch(request)
            if response.status_code >= 400 or isinstance(response, _RPCErrorResponse):
                mark_error(span)
            return response

    async def dispatch(request: Request):
        try:
            authorization = request.headers.getlist("authorization")
            if len(authorization) != 1:
                raise AgentError("unauthenticated")
            identity = trust.verify(authorization[0], agent=agent, skill=card.skills[0].id)
        except AgentError as exc:
            status = 401 if exc.reason == "unauthenticated" else 403
            headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
            return JSONResponse({"error": exc.reason}, status_code=status, headers=headers)

        request_id = None
        try:
            body = await _read_request(request)
            if not isinstance(body, dict):
                return _rpc_error(None, -32600, "invalid_request")
            candidate_id = body.get("id")
            if type(candidate_id) not in (int, str) or len(str(candidate_id)) > 128:
                return _rpc_error(None, -32600, "invalid_request")
            request_id = candidate_id
            if body.get("jsonrpc") != "2.0":
                return _rpc_error(request_id, -32600, "invalid_request")
            if body.get("method") != "message/send":
                method = body.get("method")
                if method == "tasks/get":
                    return _rpc_error(request_id, -32001, "task_not_found")
                if method in ("message/stream", "tasks/cancel", "tasks/resubscribe") or (
                    isinstance(method, str) and method.startswith("tasks/pushNotificationConfig/")
                ):
                    return _rpc_error(request_id, -32004, "unsupported_operation")
                return _rpc_error(request_id, -32601, "method_not_found")
            message = SendMessageRequest.model_validate(body).params
            if (message.metadata or {}).get("protocolVersion", PROTOCOL) != PROTOCOL:
                return _rpc_error(request_id, -32602, "unsupported_protocol")
            parts = message.message.parts
            if (message.message.role != "user" or len(parts) != 1 or parts[0].root.kind != "data"
                    or message.message.task_id is not None or message.message.context_id is not None):
                return _rpc_error(request_id, -32602, "invalid_message")
            data = parts[0].root.data
            if _AUTHORITY_FIELDS.intersection(data):
                return _rpc_error(request_id, -32602, "invalid_message")
        except AgentError as exc:
            return JSONResponse({"error": exc.reason}, status_code=413)
        except (ValueError, RecursionError):
            return _rpc_error(request_id, -32700 if request_id is None else -32602, "invalid_payload")

        try:
            output = await handler(identity, data)
            result = Message.model_validate({"kind": "message", "messageId": str(uuid4()), "role": "agent",
                                             "parts": [{"kind": "data", "data": output}]})
            response = JSONResponse({"jsonrpc": "2.0", "id": request_id,
                                     "result": result.model_dump(by_alias=True, exclude_none=True)})
            if len(response.body) > MAX_RESPONSE_BYTES:
                raise AgentError("response_too_large")
            return response
        except AgentError as exc:
            return _rpc_error(request_id, -32000, exc.reason, transient=exc.transient)
        except Exception as exc:
            _LOG.error("Agent %s failed (%s)", agent, type(exc).__name__)
            return _rpc_error(request_id, -32603, "internal_error")

    return app


@contextmanager
def _post_stream(url, body, headers, timeout):
    with (
        boundary_span("http.client.post", SpanKind.CLIENT, "agent-peer", "post") as span,
        httpx.Client(trust_env=False, follow_redirects=False, timeout=timeout) as client,
        client.stream("POST", url, json=body, headers=headers) as reply,
    ):
        yield reply
        if reply.status_code >= 400:
            mark_error(span)


class AgentClient:
    """Caller on a configured JSON-RPC endpoint with bounded response bytes."""

    def __init__(self, url: str, *, allow_loopback: bool = False):
        validate_endpoint(url, allow_loopback=allow_loopback)
        self.url = url

    def send(self, data: dict, token: str, *, timeout: float) -> dict:
        with boundary_span("a2a.message.send", SpanKind.CLIENT, "agent-peer", "message/send"):
            return self._send(data, token, timeout=timeout)

    def _send(self, data: dict, token: str, *, timeout: float) -> dict:
        request_id = str(uuid4())
        body = {"jsonrpc": "2.0", "id": request_id, "method": "message/send", "params": {
            "message": {"kind": "message", "messageId": request_id, "role": "user",
                        "parts": [{"kind": "data", "data": data}]},
            "metadata": {"protocolVersion": PROTOCOL},
        }}
        try:
            deadline = monotonic() + timeout
            headers = {"Authorization": f"Bearer {token}"}
            # Propagate the RPC parent across the wire. The local HTTP transport
            # span is a child of the RPC client span alongside the remote server.
            inject_trace(headers)
            with _post_stream(self.url, body, headers, timeout) as reply:
                if reply.status_code in (429, 502, 503, 504):
                    raise AgentError("forecast_unavailable", transient=True)
                if reply.status_code in (401, 403):
                    raise AgentError("forecast_unauthorized")
                reply.raise_for_status()
                raw = bytearray()
                for chunk in reply.iter_bytes(chunk_size=65536):
                    if monotonic() >= deadline:
                        raise AgentError("forecast_unavailable", transient=True)
                    if len(raw) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise AgentError("forecast_invalid_response")
                    raw.extend(chunk)
            result = SendMessageResponse.model_validate(strict_json(raw)).root
            if result.id != request_id:
                raise AgentError("forecast_invalid_response")
            if not isinstance(result, SendMessageSuccessResponse):
                if result.error.message == "forecast_unavailable":
                    raise AgentError("forecast_unavailable", transient=True)
                raise AgentError("forecast_rejected")
            if not isinstance(result.result, Message) or result.result.role != "agent":
                raise AgentError("forecast_invalid_response")
            parts = result.result.parts
            if len(parts) != 1 or parts[0].root.kind != "data":
                raise AgentError("forecast_invalid_response")
            return parts[0].root.data
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError):
            raise AgentError("forecast_unavailable", transient=True) from None
        except (httpx.HTTPError, ValidationError, ValueError, RecursionError):
            raise AgentError("forecast_invalid_response") from None
