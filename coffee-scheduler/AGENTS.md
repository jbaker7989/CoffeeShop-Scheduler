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
- TBD
