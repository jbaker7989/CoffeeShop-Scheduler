"""Demo A2A caller — a stdlib client that opens the trace (F36).

service_a is the CLIENT side of the two-service proof: a urllib.request client against the http.server callee (service_b).
It starts a CLIENT span, **injects** the
W3C ``traceparent`` into the outgoing request headers, performs the GET, records
the ``a2a.calls`` RED metric, force-flushes on exit, and prints its CLIENT trace
id. ``run_demo.sh`` asserts the CLIENT id here equals the SERVER id service_b
reports — one ``trace_id`` across the A2A boundary (the F18 goal). No web
framework: only the OTel SDK sits on top of the standard library.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

# Make the sibling otel/ package importable when run as `python demo/service_a.py`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from opentelemetry.trace import SpanKind

from telemetry import build_metrics, init_telemetry, inject_headers, shutdown

TARGET_URL = "http://127.0.0.1:8081/"
# A non-PII span attribute only: the tenant uuid, never a staff name or address.
_TENANT_ID = "00000000-0000-0000-0000-0000000000a1"


def main() -> int:
    tracer, meter = init_telemetry("demo-service-a")
    instruments = build_metrics(meter)

    client_trace_id = ""
    server_trace_id = ""
    status = "ok"
    try:
        with tracer.start_as_current_span("service_a.call", kind=SpanKind.CLIENT) as span:
            span.set_attribute("tenant_id", _TENANT_ID)
            span.set_attribute("peer.service", "demo-service-b")
            client_trace_id = f"{span.get_span_context().trace_id:032x}"

            # Inject the current trace context into the outgoing request headers.
            headers = inject_headers({})
            request = urllib.request.Request(TARGET_URL, headers=headers, method="GET")
            with urllib.request.urlopen(request, timeout=5) as response:
                payload = json.loads(response.read().decode())
            server_trace_id = payload.get("trace_id", "")
            instruments["a2a.calls"].add(
                1, {"peer.service": "demo-service-b", "status": status}
            )
    except Exception as exc:  # fail loud, still flush what we have
        status = "error"
        print(f"CLIENT error: {exc}", file=sys.stderr, flush=True)

    print(f"CLIENT trace_id={client_trace_id}", flush=True)
    print(f"RESPONSE trace_id={server_trace_id}", flush=True)

    # Local assertion mirrors the Collector-side check in run_demo.sh. Decide the
    # result BEFORE shutdown so the proof is visible even when no Collector is up
    # (the OTLP exporter then just retries and drops on flush — non-fatal here).
    if client_trace_id and client_trace_id == server_trace_id:
        print(f"TRACE OK single trace_id={client_trace_id}", flush=True)
        rc = 0
    else:
        print(
            f"TRACE MISMATCH client={client_trace_id!r} server={server_trace_id!r}",
            file=sys.stderr,
            flush=True,
        )
        rc = 1

    # Short-lived process: force-flush + shut the providers down before exit.
    shutdown()
    return rc


if __name__ == "__main__":
    sys.exit(main())
