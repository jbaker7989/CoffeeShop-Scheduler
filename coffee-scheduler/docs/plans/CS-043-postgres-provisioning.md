# CS-043 — Postgres provisioning, migrations & recovery runbook (F43)

> Status: in development (Codex dev / Claude validate+document). Priority P0, size S.
> This plan was reconstructed from the CS-043 backlog story on 2026-11-13; the referenced
> `docs/plans/CS-043-postgres-provisioning.md` did not previously exist.

## Goal
Reproducible local Postgres infrastructure and a recoverable schema lifecycle, so any
contributor can stand the data plane up from a fresh checkout and recover it from backup.

## Acceptance criteria (F43)
- **AC1** — `docker compose up` starts **Postgres 16** and **PgBouncer**; both become healthy.
- **AC2** — From a fresh checkout, `alembic upgrade head` succeeds against the composed database
  (direct connection), bringing the schema to migration `0001`.
- **AC3** — Every migration has a **tested upgrade and downgrade** path: `upgrade → downgrade →
  upgrade` round-trips cleanly (no leftover tables, roles, partitions, or policies; no
  "already exists" / "cannot drop" errors on the second upgrade).
- **AC4** — The application connects **through PgBouncer** (transaction pooling) and the RLS
  session pattern still works: `SET ROLE app_rw` + `set_config('app.tenant_id', …, true)` isolate
  rows correctly when pooled.
- **AC5** — `docs/postgres-backup.md` documents a scheduled `pg_dump` and a **successful restore
  drill** (restore into a scratch database, verify row counts / RLS), with commands that run as-is.

## Scope (dev — Codex)
1. **docker-compose.yml** (promote the untracked file; keep it tracked):
   - `postgres:16` service (exists) + named volume + healthcheck (exists).
   - Add **pgbouncer** service (e.g. `edoburu/pgbouncer` or `bitnami/pgbouncer`), transaction
     pooling, `auth_type` compatible with the `coffeeshop` role, exposing `6432`, depends-on
     postgres healthy. Document `DATABASE_URL` for both direct (5432, migrations) and pooled
     (6432, app) use.
   - Note in a header comment: migrations run over the **direct** 5432 connection (DDL +
     advisory locks are not pooling-safe); the app uses 6432.
2. **Migration downgrade hardening** — make `0001` downgrade fully reversible:
   - Drop in correct dependency order (PII → reversed domain tables → global read-only), including
     partition children (CASCADE), exclusion constraints, and **policies** (dropped with the table).
   - Drop roles only after all owned objects/grants are gone; guard re-create on upgrade so a
     round-trip does not fail on a pre-existing role.
3. **Round-trip test** — `coffee-scheduler/test/test_migrations_roundtrip.py` (stdlib, tags
   `spec: F43.ac3`): programmatically run `alembic upgrade head` → `downgrade base` → `upgrade head`
   against `DATABASE_URL`, asserting the public schema is empty after `base` and the expected tables
   exist after each `head`. Skip cleanly if no `DATABASE_URL`.
4. **docs/postgres-backup.md** — scheduled `pg_dump` (custom format), retention note, and a restore
   drill: create scratch DB → `pg_restore` → verify (`SELECT count(*)` on a seeded table, an RLS
   spot-check). Commands copy-paste runnable against the compose stack.
5. **FEATURES.md** — add the F43 ACs above.

## Validation (Claude)
- Bring the full compose stack up; confirm Postgres **and PgBouncer** healthy (AC1).
- `alembic upgrade head` over 5432 from a clean DB (AC2); run the round-trip test and confirm
  green, and independently verify the DB is empty after `downgrade base` and repopulated after the
  second `upgrade head` (AC3).
- Connect the compliance engine's RLS session path **through PgBouncer (6432)** and re-run the
  tenant-isolation check to prove pooling doesn't leak `app.tenant_id` across transactions (AC4).
- Execute the backup/restore drill end-to-end and confirm row counts + an RLS spot-check (AC5).
- Record results here and in `docs/postgres-backup.md`; update Backlog/FEATURES status.

## Guardrails
- Dev stays within `coffee-scheduler/`, `db/`, `scripts/`, `docs/`, `docker-compose.yml`, and the
  new test. No commits/pushes by Codex. No schema changes (that is CS-047/0001) beyond a reversible
  downgrade. Do not weaken RLS to make pooling "work" — fix the session pattern instead.
- `SET ROLE` + `set_config(..., true)` are **transaction-local**; under transaction pooling this is
  correct only when set inside the same transaction that uses them. The round-trip and RLS checks
  must prove this, not assume it.

## Validation results (Claude, 2026-11-13)
Codex developed the story; validated independently on the host against the live compose stack.

- **AC1 — PASS (after fix).** Codex pinned `bitnami/pgbouncer:1.24.1-debian-12-r0`, which no longer exists
  on Docker Hub (Bitnami removed/relocated tags in 2025) — `docker compose up` failed to pull. Switched to
  `edoburu/pgbouncer:v1.23.1-p2` (its own env contract). `docker compose up` now brings up Postgres 16
  (healthy) and PgBouncer on 6432.
- **AC2 — PASS.** `alembic upgrade head` over direct 5432 reaches `0001`.
- **AC3 — PASS (after fix).** The round-trip test shelled out to `psql`, which is **not installed on the
  host** (every other integration test uses `psycopg`); it errored `FileNotFoundError: 'psql'`. Rewrote its
  introspection to psycopg. `upgrade head → downgrade base → upgrade head` then round-trips clean (domain
  tables and roles absent at `base`, present at `head`).
- **AC4 — PASS.** Through PgBouncer transaction pooling (6432): own-tenant `labor_rules` = 31, other-tenant
  = 0, no-context = 0 (fail-closed); the compliance engine evaluates (8 results) and refuses a wrong tenant.
- **AC5 — PASS.** Ran the backup/restore drill end-to-end: `pg_dump -Fc` (1.7 MB) → restore into
  `coffeeshop_restore_drill` (`--exit-on-error`, clean) → 1 tenant / 31 rules / 149,116 transactions
  restored → RLS spot-check `visible_store_count = 1`.

### Follow-up findings (not blockers; all ACs pass)
1. **Role leak under transaction pooling (hardening).** The engine's `prepare_session` issues a **non-local**
   `SET ROLE app_rw`; PgBouncer in transaction mode does not run `DISCARD ALL` between clients by default
   (`server_reset_query_always = 0`), so a pooled backend keeps `app_rw` — a fresh connection that never set
   a role reports `current_user = app_rw`. **Tenant isolation is not breached** (the transaction-local
   `app.tenant_id` GUC is the real gate; a leaked role alone sees 0 rows), but the pattern is fragile. Fix:
   use `SET LOCAL ROLE app_rw` in the engine (transaction-scoped, matches F43.AC4's wording), or set
   `server_reset_query_always = 1` on PgBouncer. Recommend the engine change (portable, no pooler config).
2. **Round-trip test is destructive to the target DB (operational).** `test_migrations_roundtrip.py` runs
   `downgrade base` against whatever `DATABASE_URL` points at, dropping all domain data and leaving only the
   `0001` seed tenant — it wiped the ETL-loaded data during this validation (reloaded afterward). It should
   create and target a dedicated scratch database, not the working one. Until then, reload via the ETL after
   running it.

Both logged as follow-ups; the data plane itself meets all five acceptance criteria.
