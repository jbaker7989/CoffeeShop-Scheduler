"""Demo A2A callee — a stdlib ``http.server`` that joins the caller's trace (F36).

service_b is the SERVER side of the two-service proof. For each request it
**extracts** the incoming W3C context from the request headers and starts its
SERVER span *as a child of that context* — the step that keeps the whole A2A hop
on one ``trace_id`` instead of rooting a second trace. It records the RED metrics
(``a2a.server.duration_ms`` / ``a2a.calls``) and returns the span's trace id so
``run_demo.sh`` can assert CLIENT and SERVER share it. No web framework: only the
OTel SDK is added on top of the standard library.
"""
from __future__ import annotations

import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

# Make the sibling otel/ package importable when run as `python demo/service_b.py`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from opentelemetry.trace import SpanKind

from telemetry import build_metrics, extract_context, init_telemetry, shutdown

HOST = "127.0.0.1"
PORT = 8081

_tracer, _meter = init_telemetry("demo-service-b")
_metrics = build_metrics(_meter)
# A non-PII span attribute only: the tenant uuid, never a staff name or address.
_TENANT_ID = "00000000-0000-0000-0000-0000000000b2"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:  # silence default stderr noise
        return

    def do_GET(self) -> None:
        # Extract the caller's context FIRST, then open the SERVER span inside it.
        # HTTP header names are case-insensitive and urllib title-cases them
        # ("Traceparent"), but the W3C extractor looks up lowercase "traceparent",
        # so normalise keys to lower-case or the server would root a NEW trace.
        carrier = {k.lower(): v for k, v in self.headers.items()}
        ctx = extract_context(carrier)
        start = time.perf_counter()
        with _tracer.start_as_current_span(
            "service_b.handle", context=ctx, kind=SpanKind.SERVER
        ) as span:
            span.set_attribute("tenant_id", _TENANT_ID)
            span.set_attribute("peer.service", "demo-service-a")
            time.sleep(0.01)  # trivial work
            trace_id = f"{span.get_span_context().trace_id:032x}"
            duration_ms = (time.perf_counter() - start) * 1000.0
            _metrics["a2a.server.duration_ms"].record(
                duration_ms, {"peer.service": "demo-service-a"}
            )
            _metrics["a2a.calls"].add(
                1, {"peer.service": "demo-service-a", "status": "ok"}
            )

        body = json.dumps({"trace_id": trace_id, "service": "demo-service-b"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        # Emit the SERVER trace id on stdout so run_demo.sh can compare the two.
        print(f"SERVER trace_id={trace_id}", flush=True)


def main() -> None:
    server = HTTPServer((HOST, PORT), Handler)
    print(f"service_b listening on http://{HOST}:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        shutdown()


if __name__ == "__main__":
    sys.exit(main())
