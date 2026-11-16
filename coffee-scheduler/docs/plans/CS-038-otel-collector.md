# CS-038 — OTel Collector deployment (F38)

**Priority:** P2 · **Size:** S · **Est:** ~1d · **Status:** Planned (2026-11-15)
**Depends on:** nothing (independently shippable) · **Unblocks:** CS-036 (needs a running Collector), CS-037
**Sibling stories:** CS-036 (OTel foundation/contract), CS-039 (LGTM backends — Tempo/Mimir/Loki/Grafana)

## Context
F36–F40 adopt OpenTelemetry across the agent platform to deliver the F18 goal — "one `trace_id` across all A2A
calls." Nothing OTel exists yet (no `otel/` dir, no telemetry refs). CS-038 stands up the **single shared
Collector** that every agent will export to, versioned in source control, and proves it ingests OTLP and
applies sampling/batch/resource processing **before any backend exists** — so the piece is independently
shippable and testable. The LGTM backends (Tempo/Mimir/Loki/Grafana) are CS-039; here the backend exporters
are configured but inert, and verification uses the Collector's `debug` exporter.

Existing infra to match: `docker-compose.yml` (postgres:16 + edoburu/pgbouncer, pinned image tags,
healthchecks, default network).

## Key decisions (each avoids a known OTel footgun)
1. **Distro = `otel/opentelemetry-collector-contrib`**, NOT the core image — `tail_sampling`,
   `prometheusremotewrite`, and the Loki OTLP path only exist in **contrib**. A core image with this config
   fails to start. Pin an exact released tag ≥2 weeks old and confirm it exists at build.
2. **Loki logs via `otlphttp`** to Loki's native OTLP endpoint (`/otlp`), **not** the deprecated `loki` exporter.
3. **Mimir metrics via `prometheusremotewrite`** to `/api/v1/push` with an `X-Scope-OrgID` header (Mimir rejects
   writes without a tenant/org id).
4. **OTLP gRPC exporter sets `tls.insecure: true`** for local (no TLS).
5. Ports: OTLP gRPC **4317**, OTLP HTTP **4318**; receivers bind `0.0.0.0` so host + other containers can reach.
6. Observability lives in its **own compose file** so `docker compose up` for the DB stack is unaffected.

## Deliverables

### 1. `coffee-scheduler/otel/collector-config.yaml`
- **receivers.otlp**: `protocols: { grpc: { endpoint: 0.0.0.0:4317 }, http: { endpoint: 0.0.0.0:4318 } }`.
- **processors** — order matters: **`memory_limiter` → `resource` → `tail_sampling` → `batch`**:
  - `memory_limiter` **first** (`check_interval: 1s`, memory limits set) — prevents OOM under burst.
  - `resource`: upsert `deployment.environment: demo` (service.name/version arrive on the SDK Resource).
  - `tail_sampling`: `decision_wait: 10s`, `num_traces: 50000`; policies combined so a trace is kept if **any**
    matches — `{ name: errors, type: status_code, status_codes: [ERROR] }` (100% of errors) **and**
    `{ name: sample-success, type: probabilistic, sampling_percentage: 10 }` (≈10% of the rest).
  - `batch` **last** (default sizes).
- **exporters**:
  - `debug` (`verbosity: detailed`) — the standalone verification target for this story.
  - `otlp/tempo` → `tempo:4317` (`tls.insecure: true`) — traces (inert until CS-039).
  - `prometheusremotewrite/mimir` → `http://mimir:9009/api/v1/push`, `headers: { X-Scope-OrgID: anonymous }` —
    metrics (inert until CS-039).
  - `otlphttp/loki` → `http://loki:3100/otlp` — logs via native OTLP (inert until CS-039).
  - Every real exporter gets `sending_queue` + `retry_on_failure` so a down backend never crashes the
    Collector (queues/retries; connection-refused is logged, non-fatal).
- **extensions.health_check** on `0.0.0.0:13133`.
- **service.pipelines**:
  - `traces: [otlp] → [memory_limiter, resource, tail_sampling, batch] → [debug, otlp/tempo]`
  - `metrics: [otlp] → [memory_limiter, resource, batch] → [debug, prometheusremotewrite/mimir]` (no tail_sampling on metrics)
  - `logs: [otlp] → [memory_limiter, resource, batch] → [debug, otlphttp/loki]`
  - `service.extensions: [health_check]`

### 2. `docker-compose.observability.yml` (repo root, new)
One `otel-collector` service:
- `image: otel/opentelemetry-collector-contrib:<pinned tag>`
- `command: ["--config=/etc/otel/collector-config.yaml"]`; mount
  `./coffee-scheduler/otel/collector-config.yaml:/etc/otel/collector-config.yaml:ro`.
- `ports: ["4317:4317", "4318:4318", "13133:13133"]`.
- `healthcheck` against the health_check extension on 13133 — **but** the contrib image is distroless (no
  shell/curl), so a `sh -c`/`curl` healthcheck fails (the exact CS-043 pgbouncer trap). Use a shell-free
  mechanism or omit the healthcheck and verify via `docker logs`.

## Edge cases & pitfalls
- **Contrib vs core** — core image + `tail_sampling`/`prometheusremotewrite` → startup failure. Use contrib.
- **Processor order** — `memory_limiter` first, `batch` last; wrong order drops data or OOMs.
- **`tail_sampling` is stateful per instance** — all spans of a trace must hit the **same** Collector. Fine for
  one instance; horizontal scaling needs a `loadbalancing` exporter in front (production note only).
- **Distroless healthcheck** — no shell; `sh -c`/`curl` healthchecks fail silently-unhealthy.
- **Inert backends** — with LGTM down, real exporters log connection-refused; `retry_on_failure` +
  `sending_queue` keep it non-fatal; `debug` still proves ingestion.
- **Config validation** — run the image's `validate` subcommand on the config before `up`.

## Verification
1. `docker compose -f docker-compose.observability.yml up -d` → collector healthy; `docker logs` shows no
   config/startup errors.
2. Send test OTLP (telemetrygen or the CS-036 demo) and confirm the `debug` exporter prints received spans:
   `docker run --rm --network host ghcr.io/open-telemetry/opentelemetry-collector-contrib/telemetrygen:<tag> traces --otlp-insecure --otlp-endpoint localhost:4317 --traces 3`
3. **Tail-sampling retains errors** — emit one `status=ERROR` span + several `OK`; confirm (debug output) the
   error trace is always present while success is ~10%.
4. `otelcol-contrib validate --config coffee-scheduler/otel/collector-config.yaml` passes.

## Critical files
- **New:** `coffee-scheduler/otel/collector-config.yaml`, `docker-compose.observability.yml`.
- **Reference:** `docker-compose.yml` (image-pin + healthcheck conventions; the CS-043 distroless lesson).

## Orchestration
Codex develops the config + compose service; Claude validates (bring up, telemetrygen, tail-sampling error
retention) and documents. Additive only (new `otel/` tree + new compose file) — does not touch RLS/DB code.

## Risks / notes
- Pin the exact contrib image tag (≥2 weeks old) and confirm it exists — contrib releases ~monthly.
- The full LGTM visualization (Grafana pane, persistence) is **CS-039**; the `debug` exporter is this story's
  verification surface.
