# CS-042 — PostgreSQL platform decision & canonical-store close-out (F42)

> Status: **in progress — close-out.** Priority P0, size M (remaining work ~S).
> Most of F42 landed implicitly while building CS-043/CS-044/CS-047; this plan closes the
> explicit deliverables that are still open. Reconciled against the live repo on 2026-11-14.

## 1. Goal
Make PostgreSQL the **canonical store** of record and demote Excel to fixtures (ETL input) and
exports (F46), with the platform decisions — version, connection convention, role model, migration
tool, and workbook→table transition — written down and the dependencies pinned so a fresh checkout
is reproducible.

## 2. What already satisfies F42 (do not rebuild)
| F42 deliverable | Status | Evidence |
|---|---|---|
| Pin the Postgres version | ✅ | `docker-compose.yml` → `postgres:16` |
| Migration tool selected | ✅ | Alembic in use — migrations `0001` (schema/RLS/roles/partitions) + `0002` (coverage dating) |
| Data-plane roles exist | ◑ | `0001` creates `app_rw`, `app_ro`, `secrets_rw` with grants + `FORCE` RLS — but the **"manager" role** named in the F42 spec is undecided (see §3.3) |
| Canonical-path / git-policy decision | ✅ | **CS-006 / F23 is Done (2026-11-11)** — Postgres canonical, `data/*.xlsx` are fixtures; `data/private/` git-ignored |
| An agent reads the canonical DB (not xlsx) | ✅ | `compliance/engine.py → load_snapshot` reads Postgres under RLS; `compliance/rules_admin.py` too |
| `psycopg` pinned | ✅ | `coffee-scheduler/requirements.txt` → `psycopg[binary]==3.3.9` |

## 3. Remaining deliverables (the close-out)

### 3.1 Pin the migration tool dependency
`alembic` was installed ad hoc and is **not** in `coffee-scheduler/requirements.txt` (only
`pandas`, `openpyxl`, `requests`, `psycopg` are) — a fresh checkout cannot run migrations.
- **Do:** add `alembic==1.20.1` (the validated version) to `coffee-scheduler/requirements.txt`.
- **Scope note:** the file's own header defers project-wide dependency management to **CS-010**
  (possible `pyproject.toml`); CS-042 only pins the data-plane deps it introduced, not a broader
  restructure.

### 3.2 `.env.example` — connection convention (no secrets committed)
There is no `.env.example`; the connection convention (direct vs pooled) lives only in code comments.
- **Do:** add a root `.env.example` documenting, with placeholder values and no real secrets:
  - `DATABASE_URL` — the **pooled** app connection (PgBouncer, `127.0.0.1:6432`, transaction mode).
  - `DATABASE_URL_DIRECT` — the **direct** connection (`127.0.0.1:5432`) used for Alembic migrations
    (DDL/advisory locks are not pooling-safe — see F43).
  - A comment: real per-tenant secrets (Twilio/Stripe/paid weather) live in the encrypted
    `tenant_secrets` table (F63), **never** in `.env`.
- Confirm `.env*` is git-ignored (it already is, per `.gitignore`).

### 3.3 Role model + resolve the "manager role" question (decision)
The F42 spec lists "read-only, read-write, and **manager** roles," but the data plane has only three
**database** roles (`app_ro`, `app_rw`, `secrets_rw`) and there is no `manager` DB role.
- **Decision (to document):** "Manager" is an **application-level RBAC capability** (F61/F34's
  `rules_manager`), **not** a Postgres role. The data plane stays at three roles; every human role
  (manager, employee, admin) is enforced in the app layer *on top of* `app_rw`/`app_ro` + RLS tenant
  scoping. Rationale: transaction pooling makes per-request DB-role switching fragile (see BUG-013);
  authorization belongs above the connection, and RLS already provides tenant isolation regardless of
  which app role is used.
- **Do:** document the three roles, their grants, and this decision in a short platform doc (§3.4).

### 3.4 Canonical-store & roles documentation (`docs/data-platform.md`)
A single page that states the platform decision and the workbook→table transition, so F42 is
auditable without reading five other files.
- **Do:** add `coffee-scheduler/docs/data-platform.md` covering: Postgres 16 is canonical; Excel is
  fixtures (ETL input, CS-044) + read-only export (F46); the three DB roles + the manager-capability
  decision (§3.3); the direct-vs-pooled connection convention (§3.2); migration tool = Alembic; and a
  pointer to `docs/schema.md` (F47) and the CS-006/F23 git policy. Link it from F42 in `FEATURES.md`.

### 3.5 Formalize "an agent reads the canonical DB"
This is already true; CS-042 should point to it rather than re-demonstrate.
- **Do:** reference `compliance/engine.py → load_snapshot` (reads Postgres under RLS) in
  `docs/data-platform.md` as the worked demonstration; note the pooling-safe session pattern
  (`SET LOCAL ROLE` + `set_config('app.tenant_id', …, true)`) as the standard agent access pattern
  (to be generalized by CS-045, the typed data-access layer).

## 4. Acceptance criteria (F42 close-out)
- **AC1** — From a fresh checkout, `pip install -r coffee-scheduler/requirements.txt` provides
  everything needed to run migrations (alembic present) and the ETL.
- **AC2** — `.env.example` documents the pooled app URL and the direct migration URL, states that
  secrets never live in `.env`, and `.env*` is git-ignored.
- **AC3** — `docs/data-platform.md` states Postgres-is-canonical, the three DB roles + the
  manager-capability decision, the connection convention, and the Excel-as-fixtures/exports
  transition; `FEATURES.md` F42 links it.
- **AC4** — The "agent reads canonical DB" claim is backed by a named code reference
  (`load_snapshot`), not an assertion.

## 5. Files to create / modify
- **Create:** `.env.example` (repo root); `coffee-scheduler/docs/data-platform.md`.
- **Modify:** `coffee-scheduler/requirements.txt` (add `alembic==1.20.1`); `coffee-scheduler/FEATURES.md`
  (F42 → Done, link the platform doc); `coffee-scheduler/Backlog.md` (CS-042 → Done).

## 6. Verification
- `python3 -c "import alembic, psycopg"` succeeds from a venv built off `requirements.txt`.
- `alembic upgrade head` runs using `DATABASE_URL_DIRECT` from a copied `.env`.
- Links in `FEATURES.md`/`Backlog.md` resolve; `docs/data-platform.md` renders.

## 7. Guardrails / non-goals
- **Docs + one dep pin + one env file + one decision** — no schema change (that is CS-047), no new
  roles, no agent refactor (the typed data-access layer is **CS-045**).
- Do not consolidate the whole dependency story here — that is **CS-010**'s scope; CS-042 only pins
  what the data plane introduced.
- Do not weaken RLS or add a `manager` DB role; the decision is explicitly app-level authorization.
- Concurrency note: the working tree currently carries an unrelated in-flight change; CS-042's files
  (`.env.example`, `docs/data-platform.md`, `requirements.txt`) are disjoint from it — commit only
  those.
