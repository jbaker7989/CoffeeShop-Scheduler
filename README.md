# Coffee Shop Scheduler

**Predictive Smart Shift Scheduler** — the multi-agent scheduling hub for Maven Roasters (NYC, 3 stores). Statistical/ML demand forecasting feeds an OR-Tools CP-SAT solver; an LLM converses, routes tools, explains decisions and negotiates with staff.

> **Core principle:** the LLM never produces schedules or forecasts. A statistical/ML model forecasts demand, OR-Tools CP-SAT builds the schedule, and the LLM handles conversations and tool routing. The deterministic engine is the source of legal and operational truth.

---

## Status

**Phase 1 — Multi-agent scheduling hub** in progress. Phase 2/3 (Prep/Waste, Inventory, Loyalty) are design-only with Agent Cards stubbed.

Tracked in [`FEATURES.md`](./coffee-scheduler/FEATURES.md) (51 features, F1–F59) and [`Backlog.md`](./coffee-scheduler/Backlog.md) (per-story tracker, CS-001 onward).

| Done | In progress | Blocked / Not started |
|---|---|---|
| CS-001 (weather join), CS-006 (data-dir re-root), CS-002 (roster fields), CS-003 (typed roster), CS-005 (closing-hours decision) | F4–F59 sequencing | CS-013 (rule engine), CS-016 (solver), CS-019 (n8n), Postgres stack |

---

## Architecture

| Layer | Owns | Tech |
|---|---|---|
| **Triggers / plumbing** | Cron jobs, POS sync, SMS webhooks, payroll export, scheduled re-runs | [n8n](https://n8n.io) (exported workflows in `coffee-scheduler/n8n/`) |
| **Stateful agent graphs** | Per-agent reasoning, tool calls, the call-out workflow with long waits / loops / manager approval | [LangGraph](https://langchain-ai.github.io/langgraph/) |
| **Tool wrappers & prompt templates** | Per-tool interface, prompt management | [LangChain](https://python.langchain.com) |
| **Inter-agent contracts** | Authenticated, versioned boundaries between services | A2A (Agent Cards in `coffee-scheduler/agents/*/agent_card.json`) |
| **Deterministic engine** | Hard-constraint solver for shift assignments | OR-Tools CP-SAT (F11 / CS-016) |
| **Data plane** | Source of truth (roster, rules, standards, sales, weather, audit logs) | PostgreSQL 16 + Alembic (F42–F47) |
| **Observability** | One trace ID across all A2A calls; per-call RED metrics; structured logs | [OpenTelemetry](https://opentelemetry.io) SDK → Collector → [Tempo / Mimir / Loki](https://grafana.com) + Grafana (F36–F41) |
| **Manager surface** | Web UI for store managers and C-Suite (schedule view, call-out approval, forecast review, rules admin, analytics) | Next.js at `coffee_shop/` (F30–F35, F52–F59) |
| **MCP** | Agent-to-tool / agent-to-data plumbing | MCP servers |

The diagram lives in `docs/architecture/` once CS-010 lands; until then this table is the canonical reference.

---

## Repository layout

```
coffeeshop/
├── coffee-scheduler/          # Scheduling hub (this repo's primary work)
│   ├── FEATURES.md            # Feature backlog (51 features, the spec)
│   ├── Backlog.md             # Per-story tracker with developer / duration
│   ├── AGENTS.md              # Repo-wide conventions
│   ├── agents/                # One directory per agent (A2A card + prompt + tools)
│   ├── skills/                # Reusable SKILL.md procedures
│   ├── migration/SKILL.md     # Migration / schema-drift procedure
│   ├── test/SKILL.md          # SDD + TDD workflow (spec → tests → impl → review)
│   ├── n8n/                   # Exported n8n workflows
│   ├── docs/decisions/        # Decision records (CS-### ADR-style)
│   └── requirements.txt       # Python deps for data pipeline scripts
├── coffee_shop/               # Manager-facing Next.js app
│   ├── app/                   # App router pages (F30–F35, F52–F59 land here)
│   ├── AGENTS.md              # Next.js conventions
│   └── package.json
└── data/                      # Synthetic fixtures (committed; real PII policy in data/README.md)
    ├── coffee_shop_sales.xlsx
    ├── coffee_shop_staff.xlsx
    ├── coffee_shop_labor_rules.xlsx
    ├── coffee_shop_labor_standards.xlsx
    ├── weather_forecast.xlsx
    ├── build_weather_forecast.py
    ├── validate_weather_forecast.py
    └── README.md
```

---

## Quick start

```bash
# 1. Clone
git clone https://github.com/jbaker7989/CoffeeShop-Scheduler.git
cd CoffeeShop-Scheduler

# 2. Python tooling (data pipeline scripts)
python3 -m venv .venv && source .venv/bin/activate
pip install -r coffee-scheduler/requirements.txt

# 3. Reproduce the validated weather join
python3 data/validate_weather_forecast.py   # writes coffee-scheduler/VALIDATION.md

# 4. Next.js (manager UI scaffold; F30 onward)
cd coffee_shop && npm install && npm run dev
```

A full system start (Postgres + OTel + agents + UI) is being assembled as CS-016 and CS-036 land; see `coffee-scheduler/docs/` for the up-to-date runbook.

---

## Development process — SDD + TDD

Every story follows the workflow in [`coffee-scheduler/test/SKILL.md`](./coffee-scheduler/test/SKILL.md):

1. **Spec** — `FEATURES.md` has numbered `ACn` acceptance criteria per story.
2. **Tests first** — three failing tests per story (happy / edge / adversarial), each tagged `spec: F#.ac#` for CI traceability.
3. **Implement** — minimum production code, no extras.
4. **Review** — human checklist + CI gates (spec traceability, test-first commit order, lint, review-script).

Migrations follow [`coffee-scheduler/migration/SKILL.md`](./coffee-scheduler/migration/SKILL.md) — pre/post checks against `docs/schema.md` (F47), drift detection in CI.

**Definition of done** is enforced by both the human checklist and the CI machine checks. A PR cannot merge without both passing.

---

## Documentation

| Doc | What it is |
|---|---|
| [`FEATURES.md`](./coffee-scheduler/FEATURES.md) | Feature backlog — 51 features, the product-level spec |
| [`Backlog.md`](./coffee-scheduler/Backlog.md) | Per-story tracker with developer, status, duration |
| [`AGENTS.md`](./coffee-scheduler/AGENTS.md) | Repo-wide conventions (stack, layer split, data rules) |
| `docs/decisions/` | Decision records (ADR-style) for material choices |
| `agents/*/AGENTS.md` | Per-agent conventions |
| `skills/*/SKILL.md` | Reusable procedures (labor-rule evaluation, sales-weather join, etc.) |
| `data/README.md` | Data fixtures + the **real-PII policy** (synthetic tracked; real PII in `data/private/` or `*.pii.*` / `*.real.xlsx`, both gitignored) |
| `Coffee Shop Scheduler — Project Status.md` | Settled decisions and open items (project level) |
| `VALIDATION.md` | Weather×sales join validation report (CS-001) |
| `ATTRIBUTION.md` | Open-Meteo data license + non-commercial constraint |

---

## Data and licensing

- **All committed data is synthetic.** Real PII must never be committed; see `data/README.md` for the policy.
- **Weather data is from [Open-Meteo](https://open-meteo.com/)** under CC BY 4.0. The free API tier is **non-commercial only** — see [`ATTRIBUTION.md`](./coffee-scheduler/ATTRIBUTION.md). Before any commercial deployment, switch to a paid Open-Meteo plan or an alternative licensed weather source.
- Sales, roster, rules and standards are synthetic fixtures generated for this project. Maven Roasters is a stand-in name; the data is not affiliated with any real business.

---

## Jurisdiction

The compliance rules engine (F10 / CS-013) covers **NY State + NYC**: Hospitality Wage Order Part 146, NYC Fair Workweek (per-employer flag), Earned Safe and Sick Time, NYC Health Code FPC requirement, and NY minor-labor rules. Every rule is effective-dated; strictest-wins resolution.

---

## Maintainer

**Jason Baker** — [@jbaker7989](https://github.com/jbaker7989) · [LinkedIn](https://www.linkedin.com/in/jason-baker-78034996)

Project owner and primary contributor. Issues and PRs welcome; please follow the SDD/TDD process documented in `test/SKILL.md` before opening a PR.
