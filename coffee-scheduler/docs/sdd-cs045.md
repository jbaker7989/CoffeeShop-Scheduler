# CS-045: Typed Agent Data-Access Layer (P1 — Size M)

**Status:** Not started · **Feature:** F45 · **Estimated duration:** ~3d

> **Reviewed & corrected 2026-11-14.** The prior draft contained several bugs that were removed: a
> SQL-injection in its own session-context example; a generic `get(id:int)` that assumed a single `id`
> column (the F47 schema uses **composite** PKs); an app-level tenant filter that both duplicated RLS and
> did not compile; a `langgraph_checkpoints` migration with **no RLS**; a checkpoint method that never
> returned; `pytest`-based tests (the repo is **stdlib `unittest` + psycopg**, no pytest); and generic
> `update`/`delete` on **append-only** tables. See §13 for the full list.

---

## 1. Overview

CS-045 gives agents a pooled, role-aware **repository** API over the F47 schema instead of raw SQL. Agents
call typed methods (`repo.get_employee(...)`, `repo.append_rule_eval(...)`) — never SQL strings. Tenant
isolation is **enforced by Postgres RLS** (F60), not re-implemented in Python; the repository's job is a
safe, typed, parameterised, role-correct surface plus the pooling-safe session context. All access is
through SQLAlchemy Core/ORM with bound parameters.

**Key principle:** RLS is the isolation boundary. The repository **sets the session context** (role +
`app.tenant_id`) and **trusts RLS** to scope rows. It must never re-derive a tenant filter in Python
(that duplicates the guarantee, can drift, and masks RLS misconfiguration).

---

## 2. Goals
| Goal | Description |
|---|---|
| **G1** | No raw/ string-built SQL in agent code — all access via the repository, parameterised. |
| **G2** | A pooled, role-aware repository for every tenant-scoped F47 table + the global reference tables. |
| **G3** | Role correctness: the **read-only** repo runs as `app_ro`, the **read-write** repo as `app_rw`, set transaction-locally. |
| **G4** | LangGraph checkpoints persisted in Postgres (an RLS-protected table CS-045 adds), ready for F12/CS-015. |
| **G5** | Stdlib integration tests against a test DB; CI forbids string-built SQL. |

## 2.1 In scope
- A typed model for every **tenant-scoped** F47 table, keyed on its **actual (composite) primary key**.
- A model for each **global reference** table (`weather_codes`) — **no `tenant_id`**, read-only to apps.
- Read-only (`app_ro`) and read-write (`app_rw`) repositories sharing one engine/pool.
- A pooling-safe session-context helper (role + tenant, transaction-local) reused from the proven
  `compliance/rules_admin.py` pattern.
- The `langgraph_checkpoints` table (RLS-protected) + its repository.
- Append-only accessors for `rule_eval_log` / `calibration_log` (insert + read only).
- Stdlib integration tests; a CI lint forbidding string-built SQL.

## 2.2 Out of scope
- Schema for existing tables (owned by F47/CS-047) — CS-045 adds only `langgraph_checkpoints`.
- `employee_pii` general access — it is `secrets_rw`-only; a *separate, restricted* accessor (not the
  standard repos) handles it, out of this story.
- Agent business logic (F10/F34 etc.), auth/JWT issuance (F61/CS-061), the LangGraph graph itself
  (F12/CS-015 — CS-045 only provides the checkpoint **storage**).

---

## 3. Repository design

### 3.1 Models — composite keys, correct types, global carve-out
Each table maps to a SQLAlchemy model keyed on its **real** PK. The F47 tables are **composite-keyed**
(`tenant_id` + natural key); several are **effective-dated**; `weather_codes` is **global (no tenant_id)**.

```python
from sqlalchemy import Column, Integer, Text, Numeric, Date, ForeignKeyConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

class Employee(Base):
    __tablename__ = "employees"
    # Real PK is composite (tenant_id, employee_id) — NOT a surrogate `id`.
    tenant_id   = Column(UUID(as_uuid=True), primary_key=True)
    employee_id = Column(Integer, primary_key=True)
    first_name  = Column(Text, nullable=False)
    last_name   = Column(Text, nullable=False)
    role        = Column(Text, nullable=False)
    hourly_rate = Column(Numeric)            # numeric, not Integer
    home_location_id = Column(Integer)
    # Composite FK to stores(tenant_id, location_id):
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "home_location_id"],
                             ["stores.tenant_id", "stores.location_id"]),
    )

class WeatherCode(Base):          # GLOBAL reference table — no tenant_id, no RLS
    __tablename__ = "weather_codes"
    weather_code = Column(Integer, primary_key=True)
    description  = Column(Text)
    bucket       = Column(Text)
```

Verified PKs (do not assume `id`): `employees (tenant_id, employee_id)`,
`labor_rules (tenant_id, rule_id, effective_from)`, `stores (tenant_id, location_id)`,
`weather_codes (weather_code)`.

### 3.2 Base repository — PK-agnostic, no `id` assumption
The base repo keys on the model's declared primary key (which may be composite); it never assumes a
single integer `id`. It does **not** add a tenant filter — RLS does that.

```python
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.inspection import inspect

class ReadRepository:
    model = None  # set by subclass

    def __init__(self, session: Session):
        self.session = session

    def get(self, **pk):
        """Fetch by full primary key, e.g. get(tenant_id=t, employee_id=5).
        Keys must be the model's PK columns; RLS still scopes by app.tenant_id."""
        pk_cols = {c.name for c in inspect(self.model).primary_key}
        if set(pk) != pk_cols:
            raise ValueError(f"{self.model.__name__} PK is {sorted(pk_cols)}, got {sorted(pk)}")
        return self.session.get(self.model, pk)        # composite tuple/dict get

    def list(self, *, limit=100, offset=0, **eq):
        stmt = select(self.model).filter_by(**eq).limit(limit).offset(offset)
        return self.session.scalars(stmt).all()

class WriteRepository(ReadRepository):
    def create(self, **values):
        obj = self.model(**values)
        self.session.add(obj); self.session.flush()
        return obj

    def update(self, pk: dict, **changes):
        obj = self.session.get(self.model, pk)
        if obj is None:
            return None
        for k, v in changes.items():
            setattr(obj, k, v)
        self.session.flush()
        return obj

    def delete(self, pk: dict) -> bool:
        obj = self.session.get(self.model, pk)
        if obj is None:
            return False
        self.session.delete(obj); self.session.flush()
        return True
```

### 3.3 Append-only & effective-dated tables
- **Append-only** (`rule_eval_log`, `calibration_log`): `app_rw` holds only `INSERT`/`SELECT` (verified —
  `UPDATE`/`DELETE` revoked in `0001`). These get an **insert-only** repository (`append_rule_eval(...)`,
  reads) — the generic `update`/`delete` are **not** exposed, and would be rejected by the DB anyway.
- **Effective-dated** (`labor_rules`, `consent_waivers`): accessors address a **specific version** by
  natural key **plus `effective_from`**, never a surrogate id (same lesson as BUG-016). A "current" helper
  selects the row in force on a given date.
- **No application-level tenant filter.** RLS scopes every row by `app.tenant_id`; the repository must not
  re-add `tenant_id == …` in Python.

---

## 4. Session context — pooling-safe, parameterised, role-aware

Reuse the **proven** pattern from `compliance/rules_admin.py` (not a new, injectable one). It is
transaction-local (PgBouncer transaction mode, F43), parameterised (no SQL injection), sets the **role**
as well as the tenant, and fails closed.

```python
def open_session_context(conn, *, role: str, tenant_id: str):
    """role is 'app_ro' or 'app_rw'. MUST run inside an explicit transaction."""
    if conn.autocommit:                                  # BUG-025 guard
        raise RuntimeError("session context requires an explicit transaction, not autocommit")
    if role not in ("app_ro", "app_rw"):
        raise ValueError(role)
    conn.execute(f"SET LOCAL ROLE {role}")               # role is a validated literal, never user input
    # tenant_id is PARAMETERISED — never an f-string (that would be SQL injection):
    conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
```

Rules this encodes (fixing the prior draft):
- **Parameterised** `set_config(..., %s, true)` — the earlier `f"SET LOCAL app.tenant_id = '{tenant_id}'"`
  was a SQL-injection sink (a hostile JWT `tenant_id`), and violated the doc's own §7 prohibition.
- **`SET LOCAL ROLE`** (transaction-local) so the role never leaks across pooled backends (BUG-013), and
  the read-only repo genuinely runs as `app_ro`.
- **Explicit transaction + autocommit assertion** (BUG-025): `SET LOCAL` silently evaporates on an
  autocommit connection.
- **Fail-closed:** with no/empty/invalid `app.tenant_id`, RLS (NULLIF policy, F60) returns zero rows.

**SQLAlchemy ⇄ PgBouncer (transaction mode) gotcha:** server-side prepared statements do **not** survive
transaction pooling. Configure the driver accordingly — psycopg3: `prepare_threshold=None`; and disable
SQLAlchemy statement caching where it conflicts (`create_engine(..., pool=NullPool)` or an equivalent)
so the pool is PgBouncer's, not a second competing pool. Document the exact settings in the engine factory.

---

## 5. LangGraph checkpoints (RLS-protected)

CS-045 adds `langgraph_checkpoints`. Because it holds **per-tenant** agent state, it is a tenant-scoped
domain table and therefore gets the **full F60 treatment**: `tenant_id NOT NULL`, `ENABLE`+`FORCE` RLS,
the `tenant_isolation` policy (NULLIF form), and `app_rw`/`app_ro` grants. (The prior draft's migration
omitted RLS entirely — a cross-tenant leak and a repeat of BUG-021.)

```python
class CheckpointRepo(WriteRepository):
    model = LanggraphCheckpoint

    def save(self, *, tenant_id, agent_id, checkpoint: dict):
        return self.create(tenant_id=tenant_id, agent_id=agent_id, checkpoint=checkpoint)

    def get_latest(self, *, tenant_id, agent_id):
        stmt = (select(self.model)
                .where(self.model.tenant_id == tenant_id,
                       self.model.agent_id == agent_id)
                .order_by(self.model.created_at.desc())
                .limit(1))
        return self.session.scalar(stmt)                 # NB: actually returns (prior draft dropped it)
```

> Note: the `tenant_id`/`agent_id` filters above are belt-and-suspenders for a composite lookup; RLS
> already scopes `tenant_id`. The explicit `tenant_id` predicate is acceptable *here* because it is part of
> the natural lookup key, not a substitute for RLS.

---

## 6. Integration tests (stdlib — **no pytest in this repo**)

Per `test/SKILL.md`: stdlib `unittest`, **psycopg** (never `psql`), tagged `spec: F45.acN`, run with
`python3 coffee-scheduler/test/test_data_access.py`, DB tests skipped unless `DATABASE_URL` is set, and
mutations **roll back** (don't alter fixtures). Tenant context is a **DB GUC** set via the §4 helper —
**not** an environment variable — and `tenant_id` is a **UUID**, not the slug `maven-demo`.

```python
TENANT = "00000000-0000-0000-0000-000000000001"   # maven-demo UUID, not the slug

class DataAccessSpec(unittest.TestCase):
    # spec: F45.ac3 — tenant scoping + fail-closed, enforced by RLS
    def test_rls_scopes_and_fails_closed(self):
        with psycopg.connect(DSN) as conn, conn.transaction():
            open_session_context(conn, role="app_rw", tenant_id=TENANT)
            own = conn.execute("SELECT count(*) FROM employees").fetchone()[0]
            self.assertGreater(own, 0)
        with psycopg.connect(DSN) as conn, conn.transaction():
            open_session_context(conn, role="app_rw", tenant_id=str(uuid.uuid4()))
            self.assertEqual(conn.execute("SELECT count(*) FROM employees").fetchone()[0], 0)
        with psycopg.connect(DSN) as conn, conn.transaction():
            conn.execute("SET LOCAL ROLE app_rw")        # no tenant context
            self.assertEqual(conn.execute("SELECT count(*) FROM employees").fetchone()[0], 0)

    # spec: F45.ac7 — the read-only repo cannot write
    def test_app_ro_is_read_only(self): ...              # INSERT via app_ro role → raises

if __name__ == "__main__":
    unittest.main()
```

**CI gate:** run the stdlib suites with `python3`; a lint step (Ruff `S608` *hardcoded-sql-expressions*,
plus a project grep for `execute(f"`/`.format(` SQL in `compliance/`, `agents/`) fails the build on
string-built SQL.

---

## 7. Prohibited patterns (lint + CI)
| Pattern | Why | Enforcement |
|---|---|---|
| `execute(f"… {value} …")` SQL | injection / unparameterised | Ruff `S608` + project grep |
| `text(f"…")` built from input | same | same |
| `execute(text(...))` without bound params | same | same |
| Raw `conn.execute` SQL in agent code (bypassing the repo) | loses typing/role discipline | CI check: agents import `data_access`, not psycopg directly |

---

## 8. Migration (`0003`) — the checkpoint table **with RLS**

Chains from `0002`. Creates the table **and** applies RLS + grants, mirroring every other tenant-scoped
table (F60.AC2). Reversible.

```python
revision = "0003"; down_revision = "0002"

POLICY = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"

def upgrade() -> None:
    op.execute("""
      CREATE TABLE langgraph_checkpoints (
        tenant_id  uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        agent_id   uuid NOT NULL,
        checkpoint jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        id         uuid NOT NULL DEFAULT gen_random_uuid(),   -- gen_random_uuid is core in PG16
        PRIMARY KEY (tenant_id, id)
      )""")
    op.execute("CREATE INDEX ix_lgchk_tenant_agent ON langgraph_checkpoints (tenant_id, agent_id, created_at DESC)")
    op.execute("ALTER TABLE langgraph_checkpoints ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE langgraph_checkpoints FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON langgraph_checkpoints USING ({POLICY}) WITH CHECK ({POLICY})")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON langgraph_checkpoints TO app_rw")
    op.execute("GRANT SELECT ON langgraph_checkpoints TO app_ro")

def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS langgraph_checkpoints CASCADE")
```

Add a round-trip assertion (reuse `test_migrations_roundtrip.py`, `spec: F45.ac4`) that the table **and its
FORCE RLS** exist after `upgrade`, are gone after `downgrade -1`, and return after re-upgrade.

---

## 9. Dependencies (accurate)
| Dependency | Status |
|---|---|
| F47 / CS-047 — schema | ✅ Done |
| F60 / CS-060 — RLS + session context | ◑ Core done via `0001`; hardening BUG-020…025 |
| F43 — PgBouncer transaction mode | ✅ Done |
| F44 — ETL with `--tenant-slug` | ✅ Done |
| F61 / CS-061 — auth/JWT that sets `app.tenant_id` | ◻ Not started (CS-045 accepts the context; a test harness stands in until F61) |
| F12 / CS-015 — LangGraph graph | ◻ **Not started** — CS-045 provides the checkpoint **storage** it will consume; it is **not** a prerequisite |
| BUG-025 | session-context guards folded into §4 |

---

## 10. Acceptance criteria
| AC | Criterion |
|---|---|
| **F45.ac1** | Every tenant-scoped F47 table has a model keyed on its **actual composite PK**; global tables (`weather_codes`) are modelled without `tenant_id`; `employee_pii` is excluded from the standard repos. |
| **F45.ac2** | All access is parameterised SQLAlchemy — no f-string/`.format()`/unbound `text()` SQL anywhere in agent or repo code. |
| **F45.ac3** | Tenant isolation is enforced by **RLS**; the repo sets role + `app.tenant_id` transaction-locally; unset/empty/invalid context → zero rows (never a cast error). The repo adds **no** Python tenant filter. |
| **F45.ac4** | `langgraph_checkpoints` exists **with FORCE RLS + policy + grants**; save/get-latest work and round-trip through migration `0003`. |
| **F45.ac5** | Append-only tables expose insert+read only; effective-dated tables are addressed by natural key + `effective_from`. |
| **F45.ac6** | The read-only repo runs as `app_ro` and **cannot write**; the read-write repo runs as `app_rw`. |
| **F45.ac7** | Stdlib integration tests (psycopg, `spec: F45.acN`, `python3`-runnable, mutations rolled back) cover read/write/fail-closed/role paths; CI forbids string-built SQL. |

---

## 11. Non-goals
Prompt/LLM logic; refactoring existing agents onto the layer (later phase); auth/JWT (F61); schema changes
beyond `langgraph_checkpoints`; performance tuning beyond correct pooling.

## 12. Related
F60/CS-060 (RLS + session foundation) · BUG-013/BUG-017/BUG-025 (session-context correctness, folded into
§4) · BUG-016 (effective-dated addressing, §3.3) · BUG-021 (new-table RLS, §8) · F34 (read-write consumer)
· F33/F52 (read-only consumers).

---

## 13. Review corrections applied (2026-11-14)
1. **SQL injection removed** — §4 now parameterises `set_config('app.tenant_id', %s, true)`; the prior
   `f"SET LOCAL app.tenant_id = '{tenant_id}'"` was injectable and self-contradicted §7.
2. **Composite PKs** — models/repo no longer assume a single `id` column (verified: `employees` =
   `(tenant_id, employee_id)`, `labor_rules` = `(tenant_id, rule_id, effective_from)`, etc.).
3. **No redundant/ buggy app-level tenant filter** — the old `_tenant_filter` didn't compile
   (`session.get("app.tenant_id")`) and duplicated RLS; removed. RLS is the isolation boundary.
4. **`langgraph_checkpoints` now RLS-protected** — the migration adds `ENABLE`+`FORCE` RLS, policy and
   grants (prior draft had none → cross-tenant leak).
5. **`get_latest` returns** a value (prior draft discarded the query) and uses the 2.0 `select()` API;
   `CheckpointRepo` inherits the repo.
6. **Tests are stdlib `unittest` + psycopg**, `spec:`-tagged, `python3`-runnable, roll back mutations; the
   env-var/`TENANT_ID` and slug-vs-UUID errors are fixed; no pytest.
7. **Append-only tables** (`rule_eval_log`, `calibration_log`) get insert+read only (verified grants); no
   generic `update`/`delete`.
8. **Global tables** (`weather_codes`) modelled without `tenant_id` and read-only; **`employee_pii`**
   excluded (secrets_rw-only).
9. **Role mechanism specified** — `SET LOCAL ROLE app_ro`/`app_rw`; the read-only repo cannot write.
10. **SQLAlchemy ⇄ PgBouncer transaction-mode** prepared-statement gotcha documented.
11. **Types corrected** (`hourly_rate` numeric), FK made composite, `gen_random_uuid()` noted core in PG16.
12. **Dependencies corrected** — F12/CS-015 is **Not started** (CS-045 supplies storage, not blocked by
    it); F61 Not started; the `✅ Planned` contradiction removed. Lint rule corrected to Ruff `S608`.
