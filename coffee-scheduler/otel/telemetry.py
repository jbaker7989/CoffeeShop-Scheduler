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
- OTLP gRPC exporter is ``insecure=True`` for local; a force-flush on shutdown
  keeps short-lived processes from dropping their last batch of spans.

Only the OTel SDK is required; no web framework. The deployment-environment
resource attribute is owned by the Collector resource processor (F38), never set
here. ``tenant_id`` is a span attribute, never a resource attribute.
"""
from __future__ import annotations

import atexit
import os
from typing import Dict, Optional, Tuple

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
_tracer_provider: Optional[TracerProvider] = None
_meter_provider: Optional[MeterProvider] = None
_shutdown_done: bool = False
_atexit_registered: bool = False


def init_telemetry(
    service_name: str, service_version: str = "0.1.0"
) -> Tuple[trace.Tracer, metrics.Meter]:
    """Bootstrap tracing + metrics for one agent and return ``(tracer, meter)``.

    Builds a ``Resource`` (service.name / service.version / service.namespace),
    a ``TracerProvider`` with a ``BatchSpanProcessor`` + OTLP/gRPC exporter and a
    ``ParentBased(ALWAYS_ON)`` sampler, a ``MeterProvider`` with a periodic OTLP
    metric reader, installs the global ``tracecontext,baggage`` propagator and
    registers a force-flushing ``shutdown`` at exit.

    Raises ``ValueError`` when ``service_name`` is missing or blank — an anonymous
    service would export as ``unknown_service`` and break the per-agent contract.
    """
    global _tracer_provider, _meter_provider, _shutdown_done, _atexit_registered

    if not isinstance(service_name, str) or not service_name.strip():
        raise ValueError("service_name is required and must be a non-empty string")

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": service_version,
            "service.namespace": SERVICE_NAMESPACE,
        }
    )

    # Keep the env var and the "localhost:4317" default on one line: the SDK reads
    # OTEL_EXPORTER_OTLP_ENDPOINT, and local export is plaintext (insecure) gRPC.
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "localhost:4317")

    tracer_provider = TracerProvider(
        resource=resource, sampler=ParentBased(ALWAYS_ON)
    )
    tracer_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True))
    )

    metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=endpoint, insecure=True)
    )
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
    return tracer, meter


def build_metrics(meter: metrics.Meter) -> Dict[str, object]:
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


def inject_headers(headers: Dict[str, str]) -> Dict[str, str]:
    """Write the current W3C trace context into ``headers`` and return it.

    Mutates the supplied carrier in place (so the exact dict the caller passes is
    the one returned) and hands it back for convenient chaining into a request.
    """
    inject(headers)
    return headers


def extract_context(headers: Dict[str, str]) -> Context:
    """Return a context parented on the incoming ``traceparent`` in ``headers``.

    The callee MUST start its SERVER span inside this context, otherwise it roots
    a brand-new trace and the single-trace-id contract is broken.
    """
    return extract(headers)


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
