"""Best-effort spans for the CS-041 boundary adapters."""
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress

from opentelemetry.context import Context, attach, detach
from opentelemetry.trace import Span, SpanKind, StatusCode, set_span_in_context
from opentelemetry.trace import get_tracer as _get_tracer
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

_W3C_TRACE_CONTEXT = TraceContextTextMapPropagator()


def incoming_context(headers: Mapping[str, str]) -> Context:
    # The explicit empty context prevents an in-process or reused worker context
    # from becoming the parent of a request without a valid traceparent.
    with suppress(Exception):
        return _W3C_TRACE_CONTEXT.extract({key.lower(): value for key, value in headers.items()}, context=Context())
    return Context()


def inject_trace(headers: dict[str, str]) -> None:
    with suppress(Exception):
        _W3C_TRACE_CONTEXT.inject(headers)


def mark_error(span: Span | None) -> None:
    if span is not None:
        with suppress(Exception):
            span.set_attribute("outcome", "error")
            span.set_status(StatusCode.ERROR)
            # Exception messages and stack frames can contain SQL, URLs or PII.
            span.add_event("operation.error")


@contextmanager
def boundary_span(
    name: str, kind: SpanKind, service: str, operation: str, *, parent: Context | None = None,
) -> Iterator[Span | None]:
    span = token = None
    try:
        with suppress(Exception):
            span = _get_tracer(__name__).start_span(
                name, context=parent, kind=kind,
                attributes={"operation": operation, "service": service, "outcome": "ok"},
            )
            token = attach(set_span_in_context(span, parent))
        try:
            yield span
        except BaseException:
            mark_error(span)
            raise
    finally:
        if token is not None:
            with suppress(Exception):
                detach(token)
        if span is not None:
            with suppress(Exception):
                span.end()
