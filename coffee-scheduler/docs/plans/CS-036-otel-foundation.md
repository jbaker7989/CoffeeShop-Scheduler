# CS-036 — OpenTelemetry observability foundation (F36)

**Priority:** P2 · **Size:** M · **Est:** ~3d · **Status:** Planned (2026-11-15)
**Depends on:** CS-038 (a running Collector to export to) · **Unblocks:** CS-037 (per-agent instrumentation)
**Sibling stories:** CS-038 (Collector), CS-039 (LGTM backends + the "one Grafana pane" outcome)

## Context
F36 is the OTel umbrella: define the **one shared contract** every agent will follow, give them a reusable
**bootstrap** so F37 is copy-paste per agent, and **prove** the contract end-to-end with a two-service A2A
trace carrying a single `trace_id` (the F18 goal). There are **no runnable agent services** yet — the seven
`agents/` dirs are spec artifacts only (`agent_card.json`/`AGENTS.md`/`prompt.md`) and the one runnable module
is `compliance/engine.py`. So the proof is a **minimal demo harness** (decided with the user) that doubles as
the reference implementation F37 applies to the Scheduler / Demand-Forecast / Labor-Compliance / Staff-Comms
agents. Backend scope is **Collector-only** (decided): the single `trace_id` is verified by reading the
Collector's `debug` output; Tempo/Mimir/Loki/Grafana and the "one Grafana pane" outcome are **CS-039**.

## Key decisions (each avoids a known OTel footgun)
- **Head sampling = ParentBased(AlwaysOn)**; retention decided by the Collector's **tail sampling** (100%
  errors, 10% success). Head-ratio sampling would drop spans the tail-sampler can't recover.
- **Propagation = `tracecontext,baggage`** (`OTEL_PROPAGATORS`); client **injects** `traceparent`, server
  **extracts-and-parents** — the single most common cause of "two trace_ids instead of one."
- **OTLP gRPC exporter `insecure=True`** for local; **force-flush on shutdown** so short-lived processes don't
  drop spans.
- **Demo uses stdlib `http.server` + `urllib`** — no web framework dependency; only the OTel SDK is added.

## Deliverables

### 1. `coffee-scheduler/otel/README.md` — the contract (normative; F37/F38/F39 cite it)
- **SDK version pins** (exact, ≥2 weeks old; mirrored in `otel/requirements.txt`): `opentelemetry-api`,
  `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-grpc`, `opentelemetry-semantic-conventions`.
- **Propagation**: W3C `traceparent` (+ `tracestate`, `baggage`) on **every** A2A boundary;
  `OTEL_PROPAGATORS=tracecontext,baggage`; client injects, server extracts-and-parents.
- **Resource-attribute schema**: `service.name` (required, unique per agent), `service.version`,
  `service.namespace=coffeeshop`, `deployment.environment` (set by the Collector). `tenant_id` (uuid) is a
  **span attribute**, never a resource attribute. **PII scrub** — employee names/emails never go in attributes
  (ties F68 AC4).
- **Metric vocabulary** (name / type / unit / attributes):
  - `solver.solve_time_ms` — histogram, ms.
  - `rule_eval.count` — counter; attrs `rule_id`, `result`.
  - `forecast.quantile_p70` — histogram.
  - RED per A2A call: `a2a.server.duration_ms` (histogram), `a2a.calls` (counter; attrs `peer.service`,
    `status`).
- **Sampling policy**: head = ParentBased(AlwaysOn); tail = Collector (100% errors / 10% success) — with the
  explanation of why head must be AlwaysOn.

### 2. `coffee-scheduler/otel/telemetry.py` — reusable bootstrap (stdlib + otel only)
- `init_telemetry(service_name, service_version="0.1.0") -> (tracer, meter)`:
  - builds a `Resource` (service.name/version/namespace),
  - `TracerProvider` with **BatchSpanProcessor** + OTLP gRPC exporter (`endpoint` from
    `OTEL_EXPORTER_OTLP_ENDPOINT`, default `localhost:4317`, **`insecure=True`**),
  - `MeterProvider` with a periodic OTLP metric reader,
  - sets the global `tracecontext,baggage` propagator,
  - registers an `atexit`/explicit `shutdown()` that **force-flushes** (short-lived processes otherwise drop
    spans).
- Helpers `inject_headers(dict)` / `extract_context(headers)` wrapping the global propagator, so A2A
  callers/callees can't get propagation subtly wrong.

### 3. `coffee-scheduler/otel/demo/` — the two-service A2A harness
- `service_b.py` (callee): stdlib `http.server`; per request **extracts** context from headers, starts a
  `SERVER` span **as the child**, does trivial work, returns.
- `service_a.py` (caller): starts a `CLIENT` span, **injects** `traceparent` into the outgoing `urllib`
  request to service_b, awaits the response.
- `run_demo.sh` (+ README steps): start both against a running Collector; assert the two spans share one
  `trace_id`.

### 4. `coffee-scheduler/otel/requirements.txt`
The pinned SDK set (installed into a venv; do not pollute system Python).

> **Not in scope:** editing each real agent's `AGENTS.md` (that is F37); the LGTM backends / Grafana (CS-039).

## Edge cases & pitfalls
- **Two trace_ids instead of one** (headline bug): the callee MUST extract the incoming context and use it as
  the parent before starting its span, else it roots a new trace. `extract_context` + starting within that
  context prevents it; the demo's `trace_id`-equality assertion catches it.
- **Spans never exported** — a short-lived process exits before BatchSpanProcessor flushes; the bootstrap's
  `shutdown()`/force-flush on exit fixes it; the demo calls it explicitly.
- **gRPC TLS default** — exporter must pass `insecure=True` locally, else handshake/"transport is closing".
- **Head sampling must be AlwaysOn** — a ratio sampler drops spans the Collector tail-sampler can't see.
- **`service.name` missing** → spans show as `unknown_service`; `init_telemetry` requires it.
- **Propagator mismatch** — both services must use `tracecontext,baggage` (env + global propagator).
- **PII in attributes** — only `tenant_id`/ids, never names/emails (F68 AC4); the demo sets only a `tenant_id`.

## Verification
1. `python3 -m venv .venv && .venv/bin/pip install -r coffee-scheduler/otel/requirements.txt` (document; no
   system install).
2. Start the Collector (CS-038): `docker compose -f docker-compose.observability.yml up -d`.
3. `python3 otel/demo/service_b.py &` then `python3 otel/demo/service_a.py` → the Collector `debug` exporter
   prints **two spans with the same `trace_id`** (one CLIENT, one SERVER) — the F36 "one cross-agent trace"
   outcome, proven without LGTM.
4. A demo metric (`a2a.calls` / `a2a.server.duration_ms`) appears in the Collector debug metrics.
5. Contract review: `otel/README.md` pins exact versions and defines propagation, metric vocab, sampling, and
   the resource/PII schema.

## Critical files
- **New:** `coffee-scheduler/otel/README.md`, `otel/telemetry.py`, `otel/demo/service_a.py`,
  `otel/demo/service_b.py`, `otel/demo/run_demo.sh`, `otel/requirements.txt`.
- **Depends on:** CS-038's `otel/collector-config.yaml` + `docker-compose.observability.yml`.
- **Reference:** `compliance/engine.py` (the shape F37 will later instrument); F68 PII-scrub AC.

## Orchestration
Codex develops the bootstrap + demo; Claude authors the verification/assertion (`trace_id` equality) and the
contract review. Build **after** CS-038. Additive only (new `otel/` tree) — does not touch RLS/DB code.

## Risks / notes
- Pin exact SDK versions (≥2 weeks old) and confirm they exist — version skew between
  otel-api/sdk/exporter/semconv is the most common OTel break.
- Full LGTM visualization is **CS-039**; the `debug` exporter is this story's verification surface.
