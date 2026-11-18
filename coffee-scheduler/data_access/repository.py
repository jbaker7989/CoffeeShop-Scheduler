"""Typed, parameterised repository over the F47 schema (psycopg3).

- Identifiers (table/column names) come from the schema catalog and are quoted; VALUES
  are always parameterised (`%s`) — there is no SQL built from caller values.
- Keys on the table's **actual primary key** (which is composite for every F47 table);
  no surrogate `id` is assumed.
- Does **not** add a Python tenant filter — RLS (F60) is the isolation boundary. The
  caller establishes the role + `app.tenant_id` via `data_access.session`.
- Append-only tables (`rule_eval_log`, `calibration_log`) reject update/delete; every
  domain deletion is rejected here and uses ``data_access.soft_delete`` instead.

Rows are returned as dicts (`dict_row`).
"""
from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row

from .tables import TableSpec, introspect


class AppendOnlyError(RuntimeError):
    pass


class HardDeleteForbiddenError(RuntimeError):
    pass


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


class Repository:
    """One table's typed access surface. The connection's role (app_ro vs app_rw)
    decides what the DB permits; the repository exposes methods, the DB enforces rights."""

    def __init__(self, conn, table: str, *, spec: TableSpec | None = None):
        self.conn = conn
        self.spec = spec or introspect(conn, table)

    # ----- reads -----
    def get(self, **pk) -> dict | None:
        self.spec.validate_pk(pk)
        where = " AND ".join(f"{_ident(k)} = %s" for k in self.spec.pk)
        sql = f"SELECT * FROM {_ident(self.spec.name)} WHERE {where}"
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, [pk[k] for k in self.spec.pk])
            return cur.fetchone()

    def list(self, *, limit: int = 100, offset: int = 0, order_by: str | None = None, **eq) -> list[dict]:
        for col in (*eq, *( [order_by] if order_by else [])):
            if col not in self.spec.columns:
                raise KeyError(f"{self.spec.name} has no column {col!r}")
        clauses = " AND ".join(f"{_ident(c)} = %s" for c in eq)
        sql = f"SELECT * FROM {_ident(self.spec.name)}"
        if clauses:
            sql += f" WHERE {clauses}"
        if order_by:
            sql += f" ORDER BY {_ident(order_by)}"
        sql += " LIMIT %s OFFSET %s"
        params = [*eq.values(), int(limit), int(offset)]
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    # ----- writes -----
    def insert(self, **values) -> dict:
        self._check_columns(values)
        cols = list(values)
        placeholders = ", ".join(["%s"] * len(cols))
        sql = (f"INSERT INTO {_ident(self.spec.name)} ({', '.join(_ident(c) for c in cols)}) "
               f"VALUES ({placeholders}) RETURNING *")
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, [values[c] for c in cols])
            return cur.fetchone()

    def update(self, pk: dict, **changes) -> dict | None:
        if self.spec.append_only:
            raise AppendOnlyError(f"{self.spec.name} is append-only; UPDATE is not permitted")
        self.spec.validate_pk(pk)
        self._check_columns(changes)
        if not changes:
            return self.get(**pk)
        sets = ", ".join(f"{_ident(c)} = %s" for c in changes)
        where = " AND ".join(f"{_ident(k)} = %s" for k in self.spec.pk)
        sql = f"UPDATE {_ident(self.spec.name)} SET {sets} WHERE {where} RETURNING *"
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, [*changes.values(), *[pk[k] for k in self.spec.pk]])
            return cur.fetchone()

    def delete(self, pk: dict) -> bool:
        if self.spec.append_only:
            raise AppendOnlyError(f"{self.spec.name} is append-only; DELETE is not permitted")
        raise HardDeleteForbiddenError(
            f"{self.spec.name} may not be hard-deleted; use data_access.soft_delete"
        )

    def append(self, **values) -> dict:
        """Insert into an append-only log (the only write it permits)."""
        return self.insert(**values)

    def _check_columns(self, values: dict[str, Any]) -> None:
        unknown = set(values) - set(self.spec.columns)
        if unknown:
            raise KeyError(f"{self.spec.name} has no column(s) {sorted(unknown)}")


class CheckpointRepo(Repository):
    """LangGraph checkpoint storage (F12 consumes this; CS-045 provides it)."""

    def __init__(self, conn):
        super().__init__(conn, "langgraph_checkpoints")

    def save(self, *, tenant_id, agent_id, checkpoint) -> dict:
        from psycopg.types.json import Json
        return self.insert(tenant_id=tenant_id, agent_id=agent_id, checkpoint=Json(checkpoint))

    def get_latest(self, *, tenant_id, agent_id) -> dict | None:
        # Order by the monotonic `seq`, not created_at: now()/created_at is constant within a
        # transaction, so multiple checkpoints saved in one unit of work would be unordered.
        sql = ("SELECT * FROM langgraph_checkpoints WHERE tenant_id = %s AND agent_id = %s "
               "ORDER BY seq DESC LIMIT 1")
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, (tenant_id, agent_id))
            return cur.fetchone()
