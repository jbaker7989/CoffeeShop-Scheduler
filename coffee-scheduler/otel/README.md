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
