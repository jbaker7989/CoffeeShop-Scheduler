# Coffee Shop Scheduler project guidance

## Stack and layer ownership

- **n8n** owns triggers, scheduled/external I/O, exported workflows and human-in-the-loop glue.
- **LangGraph** owns per-agent orchestration and tool-call flow.
- **A2A** owns contracts at real service boundaries; cards live under `agents/<agent>/agent_card.json`.
- Domain code remains deterministic and typed: compliance decides legality, forecasting predicts demand,
  and the CP-SAT scheduler produces assignments. An LLM may explain or route work; it must not invent
  canonical employee/location facts, legal verdicts, forecasts, or schedules.
- Follow the nearest `agents/<name>/AGENTS.md` for a component's ownership and boundaries. Shared procedures
  belong in `skills/<name>/SKILL.md`, not duplicated across agent docs.

## Data, privacy, and source-of-truth rules

- Scheduling inputs and synthetic fixtures are documented in the workspace [`data/README.md`](../data/README.md).
  Follow its gitignore/private-data policy and the source attribution in [`ATTRIBUTION.md`](ATTRIBUTION.md).
- Synthetic workbooks and generated examples are test fixtures, never production/customer truth. Preserve
  source, version, timezone, units and missingness; do not silently invent a value for missing data.
- Never commit secrets, credentials, certificates, or real PII. Do not copy private employee/customer data
  into tests, logs, docs, screenshots, or tracked workbooks. Use generated synthetic identities instead.
- Agents never hardcode legality or bypass tenant/authorization checks. Use canonical contracts and
  fail-closed behavior at trust boundaries.

## Test and lint commands

Python dependencies are pinned in [`requirements.txt`](requirements.txt); Ruff's project rules are in
[`ruff.toml`](ruff.toml). Run from `coffee-scheduler/`:

```sh
python3 -m unittest discover -s test
python3 -m pytest test/ -q
ruff check --config ruff.toml <changed-python-files>
```

The unittest discovery is the project's local stdlib baseline. CI additionally runs pytest and traceability
gates; run both when dependencies are installed. Ruff uses the checked-in `ruff.toml` ratchet, so lint
changed Python files rather than claiming the entire legacy tree is clean. Do not use `pip check` as the
dependency contract; see pinned files for intentional subsystem constraints.

For the manager UI, from the workspace directory run `cd coffee_shop && npm test`, `npm run lint`, and
`npx tsc --noEmit` when the UI is in scope.

Database, Docker, migration, and optional telemetry tests are environment-gated. Follow each test's docstring and
`test/SKILL.md`; for the F61 disposable PostgreSQL suites, set `CS061B_DATABASE_INTEGRATION=1` and provide a
disposable `DATABASE_URL`. A skip is not a pass: report which gate/dependency/service was absent and keep
integration evidence separate from local unit results. Never point destructive migration/round-trip tests at
a shared or production database.

## Implementation and safety conventions

- Validate type, range, size and identity at public boundaries before I/O. Reject invalid values rather
  than coercing or silently clamping them.
- Scope transactions, roles, and connection-local state to the smallest operation; restore prior state in a
  `finally` path. Test later operations on the same pooled connection for leakage.
- Derive authority from validated credentials and canonical records; request input cannot select a tenant.
- Keep SQL parameterized, migrations reversible where their contract requires it, and destructive data
  changes explicitly bounded.
- Follow `test/SKILL.md` for spec → test → implementation, acceptance-criterion traceability, and adversarial
  coverage. Add a plan under `docs/plans/` for material bugs/findings.
- When reporting results, name the exact commands, pass/fail/skip counts, and gated omissions. Do not call a
  suite green when it failed or required coverage was skipped.
