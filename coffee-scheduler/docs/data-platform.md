# Data platform

PostgreSQL 16 is the Coffee Shop Scheduler's canonical store of record. Excel workbooks are
fixtures: they are ETL input for [CS-044](../Backlog.md#cs-044--idempotent-excel-to-postgres-etl)
and read-only export targets for F46. They are not an application data store. This records the
canonical-path decision made by CS-006/F23: committed `data/*.xlsx` files are fixtures, while
private data remains outside Git under the policy established there.

## Database roles and authorization

The data plane has exactly three non-login application database roles. They do not have
`BYPASSRLS`; tenant isolation is enforced by `FORCE ROW LEVEL SECURITY`.

| Database role | Grants |
| --- | --- |
| `app_ro` | `SELECT` on tenant-scoped domain tables and global reference tables. |
| `app_rw` | `SELECT`, `INSERT`, `UPDATE`, and `DELETE` on tenant-scoped domain tables (with `UPDATE` and `DELETE` revoked for append-only logs); `SELECT` on global reference tables. |
| `secrets_rw` | `SELECT`, `INSERT`, `UPDATE`, and `DELETE` only on restricted `employee_pii` and the future encrypted `tenant_secrets` table. |

“Manager” is not a Postgres role. It is the application-level RBAC capability `rules_manager`
from F61/F34, enforced above `app_ro`/`app_rw` and RLS. Per-request database-role switching is
fragile with transaction pooling (BUG-013), while RLS already isolates tenant rows; human
authorization therefore belongs in the application layer rather than in a new manager DB role.

## Connections and migrations

Applications use `DATABASE_URL`, the pooled PgBouncer endpoint at `127.0.0.1:6432` in transaction
mode. Alembic migrations use `DATABASE_URL_DIRECT`, the direct PostgreSQL endpoint at
`127.0.0.1:5432`: DDL and advisory locks are not pooling-safe. The migration tool is Alembic;
the schema contract, tables, RLS policies, and migration decisions are documented in
[schema.md](schema.md) (F47).

## Canonical reads under RLS

`compliance/engine.py` → `load_snapshot` is the worked proof that an agent reads the canonical
PostgreSQL database rather than an `.xlsx` file. Its transaction-scoped access uses the
pooling-safe `SET LOCAL ROLE` plus `set_config('app.tenant_id', …, true)` pattern, so a reused
PgBouncer connection cannot retain role or tenant context. CS-045 will generalize this pattern
into the typed data-access layer.
