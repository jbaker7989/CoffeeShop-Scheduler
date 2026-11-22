# BUG-055 — Secure OTLP transport and collector exposure

## Goal

Keep the developer Collector usable only from the local machine while making a remote OTLP target impossible to use without explicit TLS configuration. The change must preserve the existing F36 resource, propagation, sampling, metric, and shutdown contracts.

## Confirmed current state

- `otel/telemetry.py:init_telemetry` passes `insecure=True` to both gRPC exporters for every `OTEL_EXPORTER_OTLP_ENDPOINT`, including a remote hostname.
- `docker-compose.observability.yml` publishes `4317`, `4318`, and `13133` without a host address; Docker therefore publishes them on all host interfaces.
- `otel/collector-config.yaml` is a local-development configuration: receivers and health endpoint listen on all container interfaces, which is correct for Compose network traffic but unsafe when host ports are published broadly.
- Existing F36 AC9 and F38 static tests describe the pre-BUG-055 local transport behavior. Update them only where the new security contract supersedes that behavior; do not weaken unrelated F36/F38 checks.

## Security model and decisions

1. **Local development:** plaintext is allowed only when the parsed OTLP target is a literal IPv4 or IPv6 loopback address (`127.0.0.0/8`, `::1`) or `localhost`. Do not resolve arbitrary DNS names to decide whether plaintext is safe: DNS answers are time-varying and create a TOCTOU trust decision. Bracketed IPv6 (`[::1]:4317`) must be accepted; malformed values must fail closed.
2. **Remote endpoint:** any non-loopback hostname or address requires TLS material before either exporter is constructed. Require a readable CA bundle; mTLS is opt-in only when both client certificate and client key are supplied. Supplying only one of the pair is an error. Never log certificate contents, private-key paths, or full endpoint credentials.
3. **Endpoint parsing:** accept the forms the gRPC exporter supports (`host:port` and bracketed IPv6); reject empty values, userinfo, URL schemes/paths/query fragments, missing port, ports outside `1..65535`, unbracketed IPv6, whitespace-surrounded targets, and ambiguous syntax before network/exporter construction.
4. **Compose boundary:** leave the Collector's in-container listeners reachable at `0.0.0.0`, so containers can use `otel-collector:4317/4318`; bind only host-published ports to `127.0.0.1`. Do not publish a TLS-enabled production receiver from the local Compose file.
5. **Production Collector:** add a separate production overlay/configuration that supplies receiver TLS from mounted secret paths/environment substitution. Support optional client CA validation for mTLS. It must have no committed certificates or private keys and must not silently enable plaintext fallback.
6. **Compatibility:** default `localhost:4317` remains a working plaintext development target. Repeated `init_telemetry` calls retain their existing idempotence/conflicting-config semantics. Make the accepted transport settings part of the initialization identity so a changed endpoint/TLS mode cannot silently reuse providers initialized for another trust boundary.

## Test-first acceptance matrix

All new/changed tests carry the existing `spec: F36.acN` / `spec: F38.acN` tags, or a newly added, numbered BUG-055 acceptance tag if `FEATURES.md` is expanded. Tests patch exporter constructors and must assert failure happens before either constructor is called.

| Case | Expected transport | Assertion |
| --- | --- | --- |
| unset endpoint / `localhost:4317` / `127.0.0.1:4317` / `[::1]:4317` | plaintext local | both exporters receive `insecure=True`; no TLS credential inputs |
| `LOCALHOST:4317` | plaintext local only if hostname normalization is explicitly case-insensitive | deterministic classification test |
| remote IPv4 / hostname | TLS | both exporters receive `insecure=False` and the constructed channel credentials |
| remote with no CA | reject | typed configuration error before either exporter constructor |
| remote with unreadable/empty CA | reject | error is descriptive and contains no secret content |
| remote with cert but no key, or key but no cert | reject | no exporter construction |
| remote with CA + cert + key | TLS/mTLS | credentials include root CA and client certificate/key bytes |
| malformed endpoint / URL / userinfo / out-of-range port | reject | no exporter construction or DNS/network I/O |
| changed transport config after initialisation | reject | cannot reuse global providers across trust boundaries |

Static Collector/Compose tests must prove every published development host port is exactly `127.0.0.1:4317:4317`, `127.0.0.1:4318:4318`, or `127.0.0.1:13133:13133`; container listener tests continue to assert `0.0.0.0` where required for the Compose network. Production-config tests must inspect that TLS is configured, plaintext is not selected, and client authentication is enabled only when a client CA is explicitly configured.

## Implementation sequence

1. Update the BUG-055/F36/F38 specification wording and this plan before production code. State the loopback-only plaintext rule, remote TLS requirements, configuration variable names, and failure type.
2. Add failing unit tests with mocked `OTLPSpanExporter`, `OTLPMetricExporter`, and gRPC credential construction. Cover every matrix row without opening a network connection. Add static Compose/Collector tests for loopback host publications and production TLS configuration.
3. Implement small, pure helpers in `otel/telemetry.py` for endpoint parsing/classification and TLS-file validation/credential construction. Validate all configuration before creating a tracer/meter provider or exporter.
4. Feed the same immutable transport configuration to both exporters. Explicitly set `insecure=False` remotely and supply the same credentials to trace and metric exporters. Preserve exporter endpoint behavior for local development.
5. Add a production-only Collector overlay/config and documentation. Use documented environment variables or mounted paths; provide placeholder variable names only, never files containing a credential. Do not make the local Compose service read production secrets.
6. Update `otel/README.md`, Compose comments, and `FEATURES.md` status/acceptance criteria so an operator can distinguish local Compose from a remote Collector deployment and rotate CA/client material safely.
7. Run focused F36/F38 tests with OTel dependencies installed, then Docker validation/smoke tests when Docker is available. Run relevant static/spec gates and the full Python suite. Record skipped Docker checks separately.

## Edge-case checklist

- IPv4 loopback range, IPv6 loopback, bracketed IPv6, `localhost`, mixed-case hostname, and a hostname merely *resolving* to loopback.
- Remote hostname/IP with omitted CA, unreadable CA, empty PEM, malformed certificate data, only one mTLS component, and all mTLS components.
- Endpoints with credentials, schemes, paths, whitespace, missing ports, invalid ports, and unbracketed IPv6.
- No accidental plaintext override through a permissive environment boolean.
- No port publication on `0.0.0.0`/`::`; Docker-internal Collector access remains functional.
- No certificate/key contents in exceptions, logs, test fixtures, Git history, or documentation.
- Config changes after telemetry initialization do not weaken an already-established global provider boundary.

## Completion evidence

- Tests demonstrate each endpoint class either receives the intended exporter configuration or fails before exporter construction.
- Compose static validation proves all three host ports are loopback-only; Collector container access remains covered.
- Production Collector config validates with the pinned Collector image using non-secret test fixture paths/config substitution, and rejects a deliberately incomplete TLS configuration.
- Focused F36/F38 tests, lint, traceability/spec checks, and applicable Docker tests pass; any unavailable Docker/optional dependency check is reported as skipped, not passed.
