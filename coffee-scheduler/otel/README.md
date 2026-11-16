# OTel Collector (F38 / CS-038)

The shared OpenTelemetry Collector that every agent ships traces, metrics and logs to.
Config is version-controlled here; the plan is [docs/plans/CS-038-otel-collector.md](../docs/plans/CS-038-otel-collector.md).

## Files
- `collector-config.yaml` — receivers, processors, exporters, extensions.
- `../../docker-compose.observability.yml` (repo root) — the single `otel-collector` service.

## Image pin
`otel/opentelemetry-collector-contrib:0.164.0` (released 2026-10-27). The `-contrib`
distribution is required for `tail_sampling`, `prometheusremotewrite` and `otlphttp`. The
image is **distroless** — no `sh`, `curl` or `wget` — so the container defines **no**
shell/curl healthcheck; health is the `health_check` extension on `13133`, probed by the
orchestrator/host.

## What it does
- **Receivers:** OTLP gRPC `:4317`, OTLP HTTP `:4318`.
- **Processors (traces):** `memory_limiter` → `resource` → `tail_sampling` → `batch`.
  Tail sampling keeps **100% of error traces** and **10%** of the rest. Metrics/logs skip
  tail sampling.
- **Resource attributes:** `service.name` / `service.version` (SDK-provided, with a
  fallback) and `deployment.environment` (set here).
- **Exporters:** `debug` in every pipeline (always-on local verification) plus the backends —
  Tempo via `otlp`, Mimir via `prometheusremotewrite`, Loki via `otlphttp`. Each backend sets
  `retry_on_failure`, so it is **non-fatal**: the Collector runs green even though the LGTM
  backends (F39) are not deployed yet.

## Run it
```sh
docker compose -f docker-compose.observability.yml up        # from the repo root
# health:
curl -s localhost:13133/   # from the HOST (not inside the distroless container)
```

## Validate / test
```sh
# Validate the config with the pinned image (no backends needed):
docker run --rm --entrypoint /otelcol-contrib \
  -v "$PWD/coffee-scheduler/otel/collector-config.yaml:/cfg.yaml:ro" \
  otel/opentelemetry-collector-contrib:0.164.0 validate --config /cfg.yaml

# Full spec suite (static checks run without Docker; AC10/AC11 need the daemon):
python3 -m unittest coffee-scheduler/test/test_cs038_otel_collector.py
```

## Production wiring (F39)
Deploy Tempo/Mimir/Loki (CS-039) next to this Collector; the exporter endpoints
(`tempo:4317`, `mimir:9009`, `loki:3100`) already point at their service names. No Collector
change is needed when the backends come up — the non-fatal exporters start delivering.

---

# SDK bootstrap contract (F36 / CS-036)

The shared OpenTelemetry **SDK** contract every agent follows. F37 copy-pastes
`telemetry.py` per agent; F38 (above) is where these spans/metrics land. Plan:
[docs/plans/CS-036-otel-foundation.md](../docs/plans/CS-036-otel-foundation.md).

## Files
- `telemetry.py` — reusable bootstrap: `init_telemetry` / `shutdown` / `inject_headers` /
  `extract_context` / `build_metrics`.
- `requirements.txt` — the exact SDK pins (install into a venv; never system Python).
- `demo/` — the two-service A2A proof (`service_a.py` client, `service_b.py` server,
  `run_demo.sh`).

## SDK version pins (mirror of `requirements.txt`)
All four move together — api/sdk/exporter/semconv skew is the most common OTel break:
`opentelemetry-api==1.29.0`, `opentelemetry-sdk==1.29.0`,
`opentelemetry-exporter-otlp-proto-grpc==1.29.0`,
`opentelemetry-semantic-conventions==0.50b0` (the 1.29.0 line released 2024-12; > 2 weeks old).

## Public API
- `init_telemetry(service_name, service_version="0.1.0") -> (tracer, meter)` — builds the
  `Resource`, `TracerProvider` (BatchSpanProcessor + OTLP/gRPC exporter,
  `ParentBased(AlwaysOn)` head sampler) and `MeterProvider` (periodic OTLP reader), installs
  the global `tracecontext,baggage` propagator, registers an atexit flush. Rejects a blank
  `service_name` (an anonymous service exports as `unknown_service`).
- `shutdown()` — **force-flushes** traces + metrics *before* provider shutdown; idempotent.
  Short-lived processes otherwise drop their last batch.
- `inject_headers(headers)` / `extract_context(headers)` — wrap the global propagator so A2A
  callers/callees can't get W3C propagation wrong. The server must **extract then parent** its
  SERVER span, or it roots a second trace (normalise header case first — see `service_b.py`).

## Resource & PII schema
`service.name` (required, unique per agent), `service.version`, `service.namespace=coffeeshop`.
`deployment.environment` is set by the **Collector** (F38), never the SDK. `tenant_id` is a
**span** attribute only, never a resource attribute. Employee names/emails are **never**
telemetry attributes (ties F68 AC4) — the demo stamps only a `tenant_id` uuid.

## Metric vocabulary (`build_metrics`)
| name | type | unit | attributes |
|------|------|------|------------|
| `solver.solve_time_ms` | histogram | ms | — |
| `rule_eval.count` | counter | 1 | `rule_id`, `result` |
| `forecast.quantile_p70` | histogram | 1 | — |
| `a2a.server.duration_ms` | histogram | ms | `peer.service` |
| `a2a.calls` | counter | 1 | `peer.service`, `status` |

## Sampling & propagation
Head sampler = `ParentBased(AlwaysOn)` (keep every head span so the Collector's tail sampler —
100% errors / 10% success — makes the real retention call; a head-ratio sampler would drop
spans the tail sampler can't recover). Propagation = W3C `tracecontext,baggage` on every A2A
boundary; client injects `traceparent`, server extracts-and-parents.

## Run the demo
```sh
python3 -m venv coffee-scheduler/otel/.venv
coffee-scheduler/otel/.venv/bin/pip install -r coffee-scheduler/otel/requirements.txt
docker compose -f docker-compose.observability.yml up -d        # the F38 Collector
OTEL_EXPORTER_OTLP_ENDPOINT=localhost:4317 sh coffee-scheduler/otel/demo/run_demo.sh
```
`run_demo.sh` exits 0 only when the CLIENT and SERVER spans share one `trace_id` **in the
Collector `debug` output** (AC16) and an A2A RED metric is observed there (AC17).

## Test
```sh
# Static + unit contracts (no Docker needed):
coffee-scheduler/otel/.venv/bin/python -m unittest coffee-scheduler.test.test_cs036_otel_foundation
# Add the Docker-gated Collector verification (AC16/AC17):
CS036_RUN_COLLECTOR_TESTS=1 OTEL_EXPORTER_OTLP_ENDPOINT=localhost:4317 \
  coffee-scheduler/otel/.venv/bin/python -m unittest coffee-scheduler.test.test_cs036_otel_foundation
```
