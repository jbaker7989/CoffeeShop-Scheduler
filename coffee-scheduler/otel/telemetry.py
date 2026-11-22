"""Reusable OpenTelemetry bootstrap for every coffee-shop agent (F36 / CS-036).

This is the *one* shared telemetry contract F37 copy-pastes per agent. It wires a
``TracerProvider`` + ``MeterProvider`` that export over OTLP/gRPC to the shared
Collector (F38/CS-038), installs the W3C ``tracecontext,baggage`` propagator, and
exposes tiny ``inject_headers`` / ``extract_context`` helpers so A2A callers and
callees can't get context propagation subtly wrong.

Design decisions (each dodges a known OTel footgun — see otel/README.md):
- Head sampler is ``ParentBased(ALWAYS_ON)``: keep every head span so the
  Collector's tail sampler can make the real retention decision.
- Propagation is W3C ``tracecontext,baggage``; the client injects ``traceparent``
  and the server extracts-and-parents — the #1 cause of "two trace_ids, not one".
- OTLP gRPC exporter is ``insecure=True`` only for an explicitly recognized
  loopback endpoint (BUG-055); any non-loopback target must present TLS material
  (a readable CA bundle, plus a complete client cert/key pair for mTLS) validated
  before either exporter is constructed. A force-flush on shutdown keeps
  short-lived processes from dropping their last batch of spans.

Only the OTel SDK is required; no web framework. The deployment-environment
resource attribute is owned by the Collector resource processor (F38), never set
here. ``tenant_id`` is a span attribute, never a resource attribute.
"""
from __future__ import annotations

import atexit
import base64
import binascii
import ipaddress
import os
import re
from dataclasses import dataclass
from pathlib import Path

import grpc
from opentelemetry import metrics, trace
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.propagate import extract, inject, set_global_textmap
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ALWAYS_ON, ParentBased
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

#: Resource keys shared by every agent. ``service.name`` must be unique per agent;
#: a blank one is rejected so spans never show up as ``unknown_service``.
SERVICE_NAMESPACE = "coffeeshop"

#: W3C propagators, installed both as the env default and the global text map.
PROPAGATORS = "tracecontext,baggage"

# Module-level provider handles so an explicit or atexit ``shutdown`` can flush them.
_tracer_provider: TracerProvider | None = None
_meter_provider: MeterProvider | None = None
_shutdown_done: bool = False
_atexit_registered: bool = False

# Records the single service/config this process initialized, and the (tracer,
# meter) it returned, so a repeat ``init_telemetry`` is deterministic: identical
# config reuses it, a conflicting one raises instead of silently overwriting the
# process-global providers (which the SDK sets exactly once).
_initialized: dict | None = None

#: OTel-standard environment variables for TLS material (BUG-055). The CA bundle
#: gates every remote endpoint; the client cert/key pair is opt-in mTLS and is
#: all-or-nothing. Paths are read here, never their contents logged.
ENV_OTLP_CA = "OTEL_EXPORTER_OTLP_CERTIFICATE"
ENV_OTLP_CLIENT_CERT = "OTEL_EXPORTER_OTLP_CLIENT_CERTIFICATE"
ENV_OTLP_CLIENT_KEY = "OTEL_EXPORTER_OTLP_CLIENT_KEY"


class TelemetryConfigError(ValueError):
    """Raised for an unsafe/malformed OTLP transport configuration (BUG-055).

    A ``ValueError`` subclass so existing ``init_telemetry`` callers that already
    treat configuration problems as ``ValueError`` keep working, while tests can
    assert the specific type. Messages never include certificate contents, a
    private-key path, or endpoint credentials.
    """


@dataclass(frozen=True)
class _Transport:
    """Immutable, validated transport decision fed identically to both exporters.

    Frozen so it can be part of the initialization identity (BUG-055 AC22): a
    changed endpoint or TLS mode is a different trust boundary and must not
    silently reuse process-global providers built for another one.
    """

    endpoint: str
    remote: bool
    ca_path: str | None
    client_cert_path: str | None
    client_key_path: str | None


def _parse_endpoint(raw: object) -> tuple[str, int]:
    """Parse an OTLP ``host:port`` (or bracketed ``[ipv6]:port``) target, fail-closed.

    Accepts only the forms the gRPC exporter supports and rejects URLs, userinfo,
    paths/queries, whitespace, missing/out-of-range ports, and unbracketed IPv6
    *before* any network or DNS I/O, so a bad value can never reach the exporter.
    """
    if not isinstance(raw, str) or not raw:
        raise TelemetryConfigError("OTLP endpoint must be a non-empty string")
    if raw != raw.strip() or any(ch.isspace() for ch in raw):
        raise TelemetryConfigError("OTLP endpoint must not contain whitespace")
    if "://" in raw:
        raise TelemetryConfigError("OTLP endpoint must be host:port, not a URL with a scheme")
    if "@" in raw:
        raise TelemetryConfigError("OTLP endpoint must not embed userinfo credentials")
    if any(sep in raw for sep in ("/", "?", "#")):
        raise TelemetryConfigError("OTLP endpoint must be host:port with no path or query")

    if raw.startswith("["):
        close = raw.find("]")
        if close == -1:
            raise TelemetryConfigError("bracketed IPv6 endpoint is missing a closing ']'")
        host = raw[1:close]
        rest = raw[close + 1 :]
        if not rest.startswith(":"):
            raise TelemetryConfigError("bracketed IPv6 endpoint must be [host]:port")
        port_str = rest[1:]
        try:
            ipaddress.IPv6Address(host)
        except ValueError:
            raise TelemetryConfigError(
                "bracketed endpoint is not a valid IPv6 address"
            ) from None
    else:
        host, sep, port_str = raw.rpartition(":")
        if not sep:
            raise TelemetryConfigError("OTLP endpoint must include a port (host:port)")
        if ":" in host:
            # Multiple colons with no brackets is ambiguous IPv6; require [addr]:port.
            raise TelemetryConfigError("IPv6 endpoints must be bracketed as [addr]:port")
        if not host:
            raise TelemetryConfigError("OTLP endpoint is missing a host")

    if not port_str.isdigit():
        raise TelemetryConfigError("OTLP endpoint port must be numeric")
    port = int(port_str)
    if not (1 <= port <= 65535):
        raise TelemetryConfigError("OTLP endpoint port must be in 1..65535")
    return host, port


def _is_loopback(host: str) -> bool:
    """Return True only for an explicit loopback literal or ``localhost``.

    DNS is never resolved here: a name that merely *resolves* to loopback is a
    time-varying TOCTOU trust decision, so it is treated as remote (BUG-055).
    """
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _resolve_transport(endpoint: str) -> _Transport:
    """Validate the endpoint + TLS env into an immutable transport decision.

    Loopback targets stay plaintext and ignore any TLS material. A non-loopback
    target requires a configured CA bundle, and its client cert/key pair must be
    supplied together or not at all. Only configuration *presence* is checked
    here (no file I/O); readability/emptiness is validated when credentials are
    built, so both happen before any exporter is constructed.
    """
    host, _port = _parse_endpoint(endpoint)
    remote = not _is_loopback(host)
    ca = (os.environ.get(ENV_OTLP_CA) or "").strip() or None
    cert = (os.environ.get(ENV_OTLP_CLIENT_CERT) or "").strip() or None
    key = (os.environ.get(ENV_OTLP_CLIENT_KEY) or "").strip() or None

    if not remote:
        # Loopback is plaintext; drop any TLS material to avoid a mixed signal.
        return _Transport(endpoint, False, None, None, None)

    if ca is None:
        raise TelemetryConfigError(
            "a non-loopback OTLP endpoint requires a CA bundle; set "
            f"{ENV_OTLP_CA} (plaintext is allowed only for loopback endpoints)."
        )
    if (cert is None) != (key is None):
        raise TelemetryConfigError(
            "mutual TLS requires both a client certificate and client key; set "
            f"both {ENV_OTLP_CLIENT_CERT} and {ENV_OTLP_CLIENT_KEY}, or neither."
        )
    return _Transport(endpoint, True, ca, cert, key)


def _read_pem(path: str, label: str) -> bytes:
    """Read a PEM file, rejecting an unreadable or empty one with a secret-free error.

    The path is never echoed (it can reveal a private-key location) and contents
    are never logged — only the human ``label`` identifies which input failed.
    """
    try:
        data = Path(path).read_bytes()
    except OSError:
        # `from None` also drops the OSError whose str() would echo the secret path.
        raise TelemetryConfigError(f"{label} is not readable") from None
    if not data.strip():
        raise TelemetryConfigError(f"{label} is empty")
    return data


#: A PEM block: a BEGIN/END pair with a matching label and a base64 body. Used to
#: validate TLS material locally so malformed bytes fail closed *before* they reach
#: ``grpc.ssl_channel_credentials`` and before any exporter is constructed.
_PEM_BLOCK = re.compile(
    rb"-----BEGIN ([A-Z0-9 ]+)-----(.*?)-----END \1-----",
    re.DOTALL,
)
#: Labels we accept for a certificate bundle vs. a private key. A CA bundle or a
#: client certificate must contain a CERTIFICATE; a client key must contain a
#: private key. This is a structural check (framing + base64), not a cryptographic
#: one — it adds no dependency yet rejects DER, truncated, or garbage material.
_CERT_LABELS = frozenset({"CERTIFICATE"})
_KEY_LABELS = frozenset(
    {"PRIVATE KEY", "RSA PRIVATE KEY", "EC PRIVATE KEY", "ENCRYPTED PRIVATE KEY"}
)


def _validate_pem(data: bytes, label: str, expected: frozenset[str], kind: str) -> None:
    """Reject TLS material that is not well-formed PEM of the expected kind.

    Scans for PEM blocks, requires each block's base64 body to decode, and requires
    at least one block whose type is in ``expected`` (a CERTIFICATE for a CA/cert, a
    private key for a key). Raises ``TelemetryConfigError`` — naming only the human
    ``label`` and ``kind``, never the path or any byte of the material — so a
    malformed readable file fails closed before any gRPC credential or exporter is
    built. ``validate=True`` makes base64 decoding reject stray/garbage characters.
    """
    found_expected = False
    for match in _PEM_BLOCK.finditer(data):
        block_type = match.group(1).decode("ascii", "replace").strip()
        body = b"".join(match.group(2).split())
        try:
            decoded = base64.b64decode(body, validate=True)
        except (binascii.Error, ValueError):
            raise TelemetryConfigError(
                f"{label} contains a malformed PEM block"
            ) from None
        if not decoded:
            raise TelemetryConfigError(f"{label} contains an empty PEM block")
        if block_type in expected:
            found_expected = True
    if not found_expected:
        raise TelemetryConfigError(f"{label} is not valid PEM ({kind} expected)")


def _build_channel_credentials(transport: _Transport) -> grpc.ChannelCredentials:
    """Build gRPC channel credentials for a remote endpoint (root CA, optional mTLS).

    The same credentials object is handed to both the trace and metric exporters
    so they share one trust boundary. Reading *and structurally validating* the PEM
    files here (before any exporter is constructed) is what makes unreadable, empty,
    or malformed TLS material fail closed rather than reaching gRPC.
    """
    ca = _read_pem(transport.ca_path, "OTLP CA bundle")
    _validate_pem(ca, "OTLP CA bundle", _CERT_LABELS, "a CA certificate")
    if transport.client_cert_path and transport.client_key_path:
        cert = _read_pem(transport.client_cert_path, "OTLP client certificate")
        _validate_pem(cert, "OTLP client certificate", _CERT_LABELS, "a client certificate")
        key = _read_pem(transport.client_key_path, "OTLP client key")
        _validate_pem(key, "OTLP client key", _KEY_LABELS, "a private key")
        return grpc.ssl_channel_credentials(
            root_certificates=ca, private_key=key, certificate_chain=cert
        )
    return grpc.ssl_channel_credentials(root_certificates=ca)


def init_telemetry(
    service_name: str, service_version: str = "0.1.0"
) -> tuple[trace.Tracer, metrics.Meter]:
    """Bootstrap tracing + metrics for one agent and return ``(tracer, meter)``.

    Builds a ``Resource`` (service.name / service.version / service.namespace),
    a ``TracerProvider`` with a ``BatchSpanProcessor`` + OTLP/gRPC exporter and a
    ``ParentBased(ALWAYS_ON)`` sampler, a ``MeterProvider`` with a periodic OTLP
    metric reader, installs the global ``tracecontext,baggage`` propagator and
    registers a force-flushing ``shutdown`` at exit.

    Raises ``ValueError`` when ``service_name`` is missing or blank — an anonymous
    service would export as ``unknown_service`` and break the per-agent contract.

    Telemetry providers are process-global and installed exactly once. A repeated
    call is deterministic: an identical ``(service_name, service_version)`` reuses
    the first ``(tracer, meter)`` without touching the global providers again, and
    a conflicting service/config raises ``RuntimeError`` rather than silently
    overwriting them (which would emit later telemetry under the first service).
    """
    global _tracer_provider, _meter_provider, _shutdown_done
    global _atexit_registered, _initialized

    if not isinstance(service_name, str) or not service_name.strip():
        raise ValueError("service_name is required and must be a non-empty string")

    # Keep the env var and the "localhost:4317" default on one line: the SDK reads
    # OTEL_EXPORTER_OTLP_ENDPOINT, and local export is plaintext (insecure) gRPC.
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "localhost:4317")
    # Validate + classify the transport before any provider/exporter exists, and
    # make it part of the initialization identity so a changed endpoint/TLS mode
    # cannot silently reuse providers built for another trust boundary (BUG-055).
    transport = _resolve_transport(endpoint)

    config = (service_name, service_version, transport)
    if _initialized is not None:
        if _initialized["config"] == config:
            return _initialized["tracer"], _initialized["meter"]
        prior_name, prior_version, _prior_transport = _initialized["config"]
        raise RuntimeError(
            "init_telemetry is already initialized for service "
            f"{prior_name!r} (version {prior_version!r}); refusing to reinitialize "
            f"for {service_name!r} (version {service_version!r}). Telemetry "
            "providers are process-global and set once (and pinned to one "
            "transport/trust boundary) — run one service per process, or call "
            "shutdown() in a fresh process."
        )

    # Build (and thereby validate) remote TLS credentials before any provider or
    # exporter is constructed; both exporters then share this one trust boundary.
    credentials = _build_channel_credentials(transport) if transport.remote else None

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": service_version,
            "service.namespace": SERVICE_NAMESPACE,
        }
    )

    # Feed the same immutable transport to both exporters: plaintext only for a
    # loopback endpoint, otherwise insecure=False with the shared TLS credentials.
    # insecure is always set explicitly so OTEL_EXPORTER_OTLP_INSECURE can't flip
    # a remote target back to plaintext.
    if transport.remote:
        span_exporter = OTLPSpanExporter(
            endpoint=endpoint, insecure=False, credentials=credentials
        )
        metric_exporter = OTLPMetricExporter(
            endpoint=endpoint, insecure=False, credentials=credentials
        )
    else:
        span_exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
        metric_exporter = OTLPMetricExporter(endpoint=endpoint, insecure=True)

    tracer_provider = TracerProvider(
        resource=resource, sampler=ParentBased(ALWAYS_ON)
    )
    tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))

    metric_reader = PeriodicExportingMetricReader(metric_exporter)
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])

    # Install the W3C propagators globally and advertise them via the env var the
    # SDK also reads, so both ends of an A2A hop agree on the wire format.
    os.environ.setdefault("OTEL_PROPAGATORS", PROPAGATORS)
    set_global_textmap(
        CompositePropagator(
            [TraceContextTextMapPropagator(), W3CBaggagePropagator()]
        )
    )

    trace.set_tracer_provider(tracer_provider)
    metrics.set_meter_provider(meter_provider)

    _tracer_provider = tracer_provider
    _meter_provider = meter_provider
    _shutdown_done = False
    if not _atexit_registered:
        atexit.register(shutdown)
        _atexit_registered = True

    tracer = trace.get_tracer(service_name, service_version)
    meter = metrics.get_meter(service_name, service_version)
    _initialized = {"config": config, "tracer": tracer, "meter": meter}
    return tracer, meter


def build_metrics(meter: metrics.Meter) -> dict[str, object]:
    """Create the shared RED + domain instruments and return them by name.

    This is the single source of truth for the metric vocabulary the contract
    pins. Agents (and the demo) record through these instruments so names, units
    and attributes stay identical across the platform.
    """
    return {
        # Domain instruments (owners: Scheduler / Labor-Compliance / Demand-Forecast).
        "solver.solve_time_ms": meter.create_histogram(
            "solver.solve_time_ms", unit="ms", description="Solver wall time."
        ),
        "rule_eval.count": meter.create_counter(
            "rule_eval.count",
            unit="1",
            description="Rule evaluations; attrs: rule_id, result.",
        ),
        "forecast.quantile_p70": meter.create_histogram(
            "forecast.quantile_p70",
            unit="1",
            description="Demand-forecast p70 quantile value.",
        ),
        # RED instruments, recorded on every A2A server boundary.
        "a2a.server.duration_ms": meter.create_histogram(
            "a2a.server.duration_ms",
            unit="ms",
            description="A2A server handler duration.",
        ),
        "a2a.calls": meter.create_counter(
            "a2a.calls",
            unit="1",
            description="A2A calls; attrs: peer.service, status.",
        ),
    }


def inject_headers(headers: dict[str, str]) -> dict[str, str]:
    """Write the current W3C trace context into ``headers`` and return it.

    Mutates the supplied carrier in place (so the exact dict the caller passes is
    the one returned) and hands it back for convenient chaining into a request.
    """
    inject(headers)
    return headers


def extract_context(headers: dict[str, str]) -> Context:
    """Return a context parented on the incoming ``traceparent`` in ``headers``.

    HTTP header names are case-insensitive, but the W3C extractor looks up the
    lower-case ``traceparent`` / ``tracestate`` / ``baggage`` keys against a plain
    dict. Clients title-case them on the wire (urllib sends ``Traceparent``), so we
    normalise keys to lower-case here — callees rely on this helper and need not
    pre-process headers. Without it the callee would root a brand-new trace and the
    single-trace-id contract would break.
    """
    carrier = {str(key).lower(): value for key, value in headers.items()}
    return extract(carrier)


def shutdown() -> None:
    """Force-flush then shut down the providers; idempotent and atexit-safe.

    Short-lived processes exit before ``BatchSpanProcessor`` flushes on its timer,
    so we force-flush traces and metrics *before* provider shutdown to avoid
    dropping the last batch. Safe to call more than once.
    """
    global _shutdown_done
    if _shutdown_done:
        return
    _shutdown_done = True

    if _tracer_provider is not None:
        _tracer_provider.force_flush()
    if _meter_provider is not None:
        _meter_provider.force_flush()

    if _tracer_provider is not None:
        _tracer_provider.shutdown()
    if _meter_provider is not None:
        _meter_provider.shutdown()
