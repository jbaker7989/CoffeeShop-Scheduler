# coffee-scheduler

Repo-wide conventions.

## Stack
- n8n (workflow orchestration / exported workflows in `n8n/`)
- LangGraph (agent orchestration graphs)
- A2A (agent-to-agent protocol; agent cards in `agents/*/agent_card.json`)

## Layer split
- **n8n** — triggers, external I/O, scheduled runs, human-in-the-loop glue.
- **LangGraph** — per-agent reasoning graphs and tool calls.
- **A2A** — contracts between agents (cards, auth, versioning).

## Data rules
- Source data lives in the repo-root `data/` directory (roster, labor_rules, labor_standards, weather, school calendar). See `data/README.md` for the synthetic-fixture / real-PII git policy.
- Agents never hardcode legality; see `agents/labor_compliance`.

## Test commands
- Python project tests: `cd coffee-scheduler && python3 -m unittest discover -s test`.
- Manager UI tests: `cd coffee_shop && npm test`; lint with `npm run lint`; types with `npx tsc --noEmit`.
- Database, Docker, and optional telemetry integration tests have explicit environment gates; run them only with the documented services/dependencies and report skips separately.

## Security and bounded operations
- Scope transaction-local state to the smallest operation. When setting a GUC or temporary role/visibility flag, capture the previous value and restore it in `finally`; test later queries in the same transaction after success and failure.
- Validate type, range, and size at public data-access boundaries before borrowing a connection or executing SQL. Do not coerce user values with `int()` or silently clamp invalid values. Pagination must have a documented maximum.
- Never hardcode fixture tenant/user/JTI identifiers in production service paths. Derive identity from validated claims and reconcile it against canonical records before persistence.
- Keep telemetry development-only plaintext on loopback. Bind published local Collector ports to `127.0.0.1`; remote OTLP endpoints require configured TLS (and client authentication when required). Never commit certificates or keys.
- Preserve fail-closed tenant isolation. Request input must not select a tenant, and helper-level visibility overrides must not persist into later work in a pooled transaction.

## Review and validation evidence
- A review finding must identify the current file/symbol, describe a concrete failure mode, and specify an assertion that would catch it. Record actionable follow-ups in `Backlog.md` with edge/adversarial cases and a plan under `docs/plans/`.
- Report focused tests, full-suite results, and environment-gated skips separately. A skipped database/Docker integration test is not a pass; name the missing service, dependency, or environment variable. Do not call the full suite green when any test errored or was not run.
- For Python unit tests, use `python3 -m unittest discover -s test`; install optional subsystem dependencies from that subsystem's requirements file when validating it (for example `otel/requirements.txt`). Run database- and Docker-backed checks only when their documented services and opt-in variables are available.
- Avoid broad cleanup while fixing a focused issue. Keep one story per commit when practical and include the spec, tests, implementation, and review evidence in the same change series.
