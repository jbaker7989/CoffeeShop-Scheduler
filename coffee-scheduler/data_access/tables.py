"""Table metadata ("models") for the data-access layer.

Rather than hand-write 28 model classes (error-prone, drifts from the schema), the
registry **introspects the live schema** for each table's primary key and columns —
so every F47 table is covered correctly and automatically — with a small override set
for the two behavioural flags the SQL catalog does not express on its own:

- APPEND_ONLY: tables whose rows are immutable (UPDATE/DELETE revoked in migration 0001).
- GLOBAL: reference tables with no `tenant_id` and no RLS; read-only to app roles.
"""
from __future__ import annotations

from dataclasses import dataclass

APPEND_ONLY = frozenset({"rule_eval_log", "calibration_log"})
GLOBAL = frozenset({"weather_codes"})
# employee_pii is secrets_rw-only and is intentionally NOT reachable through the
# standard repositories (a separate restricted accessor owns it, out of CS-045 scope).
RESTRICTED = frozenset({"employee_pii"})


@dataclass(frozen=True)
class TableSpec:
    name: str
    pk: tuple[str, ...]
    columns: tuple[str, ...]
    append_only: bool
    tenant_scoped: bool  # has a tenant_id column + RLS

    def validate_pk(self, keys) -> None:
        if set(keys) != set(self.pk):
            raise KeyError(f"{self.name} primary key is {list(self.pk)}, got {sorted(keys)}")


def introspect(conn, table: str) -> TableSpec:
    """Build a TableSpec from the live catalog (identifiers only — never user values)."""
    if table in RESTRICTED:
        raise PermissionError(f"{table} is restricted (secrets_rw); not available via the standard repository")
    cols = [r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",
        (table,),
    ).fetchall()]
    if not cols:
        raise KeyError(f"unknown table {table!r}")
    pk = [r[0] for r in conn.execute(
        "SELECT a.attname FROM pg_index i "
        "JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum = ANY(i.indkey) "
        "JOIN pg_class c ON c.oid=i.indrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND c.relname=%s AND i.indisprimary "
        "ORDER BY array_position(i.indkey, a.attnum)",
        (table,),
    ).fetchall()]
    return TableSpec(
        name=table,
        pk=tuple(pk),
        columns=tuple(cols),
        append_only=table in APPEND_ONLY,
        tenant_scoped=("tenant_id" in cols) and (table not in GLOBAL),
    )
