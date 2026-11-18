# BUG-054–BUG-056: Codebase quality review follow-ups

## Review scope and evidence

Reviewed the checked-out codebase's API/gateway, manager UI session recovery, auth adapter, repository/data-access layer, tenant session handling, soft-delete helpers and migration, compliance evaluation, OTel SDK/Collector configuration, data-generation and validation scripts, test layout, and dependency manifests. The three findings below are actionable and tied to current implementations. Existing coverage does not pin the missing contracts.

| Order | Story | Finding | Risk | Size |
|---|---|---|---|---|
| 1 | BUG-054 | `restore` and `list_including_deleted` leave `app.show_deleted` on for the rest of the transaction | Deleted-row visibility can bleed into later reads | S |
| 2 | BUG-055 | `Repository.list` allows negative/unbounded LIMIT and lossy coercion | Unbounded tenant queries and surprising pagination | XS |
| 3 | BUG-056 | OTLP is plaintext by default and compose publishes collector ports on all interfaces | Telemetry injection, health disclosure, and unencrypted remote telemetry | M |

## BUG-054 — scope deleted-row visibility

### Current behavior

`soft_delete()` saves the previous `app.show_deleted` setting and restores it in `finally`. `restore()` and `list_including_deleted()` call `enable_show_deleted()` but do not restore the previous setting. Because `SET LOCAL` lasts until transaction completion, a helper call changes subsequent SELECT visibility for the entire transaction.

### Planned implementation

Reuse one context manager that captures the prior transaction-local setting, sets the visibility override, and restores the exact prior value in `finally`. Keep a caller's prior explicit `on` setting intact. Use the helper around only the SQL operation that needs the deleted-row policy bypass. Do not change the policy or grant broader database privileges.

### Verification plan

- Unit-test a recording fake connection for unset, explicit `off`, and explicit `on` prior values.
- Assert reset occurs after normal return and after `execute()` raises.
- Database integration: call `list_including_deleted` or `restore`, then issue an ordinary SELECT in the same `app_rw` transaction and assert deleted rows remain hidden; separately prove caller-preenabled `on` remains on.
- Run `test_soft_delete_lifecycle.py`, RLS tests, and the full suite. DB-backed checks require the configured `DATABASE_URL` test role.

## BUG-055 — validate bounded pagination

### Current behavior

The repository calls `int(limit)` and `int(offset)` directly in SQL parameter construction. Negative LIMIT is interpreted by PostgreSQL as no upper bound; negatives, booleans, floats, and strings may be accepted or truncated unexpectedly. There is no upper limit to protect tenant data reads from accidental large scans/results.

### Planned implementation

Define a maximum page size of 1000 and require actual integer values (`bool` is explicitly rejected despite being an `int` subclass). Accept `1 <= limit <= 1000` and `offset >= 0`. Raise `ValueError` with the invalid field name before acquiring a cursor or sending SQL. Keep limit/offset parameterized. Avoid silently clamping values because that conceals caller mistakes.

### Verification plan

- Fake connection/cursor tests prove each invalid value raises before SQL execution.
- Boundary tests cover limit 1, limit 100, maximum 1000, offset 0, and a positive offset.
- Existing parameterization, RLS, ordering, and filtering tests remain green.
- Run the data-access and isolation suites, then the full suite.

## BUG-056 — secure the observability transport

### Current behavior

The SDK constructs both OTLP exporters with `insecure=True` regardless of endpoint. The compose file publishes ports `4317`, `4318`, and `13133` without a loopback host address, which normally exposes them on every host interface. The local Collector has no receiver authentication. This is acceptable only in a local trusted environment, but the code currently accepts arbitrary endpoint values without signaling a trust-mode change.

### Planned implementation

- Bind Compose host ports to `127.0.0.1`; Docker-internal service discovery remains available to containers on the compose network.
- Parse the endpoint and explicitly classify loopback vs remote. Keep local plaintext only for loopback endpoints. Require TLS configuration (root CA and client certificate/key where mTLS is enabled) for remote endpoints; do not silently fall back to insecure mode.
- Add separate explicit local and production Collector receiver config or deployment overlays. Production listener must support TLS, and operators may require client certificate authentication. Keep credentials out of the repository and redact them from errors.
- Document endpoint and certificate environment variables, file permissions, rotation expectations, and local-only behavior.

### Verification plan

- SDK tests patch exporter constructors and assert loopback endpoints use insecure mode, remote endpoints use TLS credentials, and missing/invalid TLS config fails before exporter construction.
- Compose static tests assert published ports bind only to loopback; confirm service-to-service endpoint names remain unchanged.
- Collector config validation checks the local and production configurations with the pinned Collector image.
- Run CS-036 / CS-038 tests and, where available, a local compose smoke test. No test should transmit telemetry to an external endpoint.

## Delivery order and completion gates

1. Land the BUG-054 spec and tests before implementation; verify transaction visibility cannot leak.
2. Land BUG-055 spec and invalid-input tests before implementation; verify SQL is not executed on invalid pagination.
3. Land BUG-056 spec and config tests before implementation; verify transport mode is selected explicitly and local published ports are loopback-only.
4. For each story, run its focused suite and adjacent regression suites before committing. Run the full suite and static checks after the series.
5. Review database-gated and Docker-gated test skips separately; a skipped environment integration test is not reported as executed.

## Review validation snapshot

The full unittest discovery ran 274 tests and reported 9 errors with 84 skips in this environment. Eight OpenTelemetry foundation cases could not import `opentelemetry` because the SDK requirements are not installed in the active Python environment. One PostgreSQL isolation test could not connect to localhost:5432 because local network access is restricted. Database-gated integration tests were skipped by their environment guards. The focused BUG-051–053 tests passed separately; the full suite is not green in this environment and must be rerun with the OTel requirements installed and the database service available.

## Audit limits

The findings are source-review findings; BUG-054 behavior still needs database-backed proof under the configured RLS role, and BUG-056's deployment transport needs environment-level validation. These three stories are planned for follow-up and are not part of the BUG-051–053 implementation commits.
