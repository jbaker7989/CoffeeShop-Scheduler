"""Pooling-safe, parameterised session context (role + tenant), reused from the
proven `compliance/rules_admin.py` pattern.

The context is transaction-local (`SET LOCAL`), so it is correct under PgBouncer
transaction pooling and never leaks to the next pooled client. The tenant id is
**parameterised** via `set_config` — never interpolated into SQL — and the role is a
validated literal, never user input.
"""
from __future__ import annotations

import contextlib
import os
import uuid

import psycopg
from opentelemetry.trace import SpanKind

from otel.boundary import boundary_span

_ROLES = frozenset({"app_ro", "app_rw", "secrets_rw"})


class SessionContextError(RuntimeError):
    pass


def execute_statement(conn, statement, *args, **kwargs):
    """Execute a PostgreSQL statement without exporting its text or parameters."""
    with boundary_span("db.postgresql.execute", SpanKind.CLIENT, "postgresql", "execute"):
        return conn.execute(statement, *args, **kwargs)


def open_session_context(conn, *, role: str, tenant_id) -> None:
    """Apply the transaction-local role + tenant scope on an open connection.

    MUST be called inside an explicit transaction (not autocommit); `SET LOCAL`
    silently evaporates otherwise (BUG-025). RLS then scopes every row.
    """
    if getattr(conn, "autocommit", False):
        raise SessionContextError(
            "session context requires an explicit transaction, not an autocommit connection"
        )
    if role not in _ROLES:
        raise SessionContextError(f"unknown role {role!r}; expected one of {sorted(_ROLES)}")
    if tenant_id is None:
        raise SessionContextError("tenant_id is required")
    try:
        parsed_tenant_id = uuid.UUID(str(tenant_id))
    except (TypeError, ValueError, AttributeError):
        raise SessionContextError(f"invalid tenant_id {tenant_id!r}") from None
    # Role is a validated literal (never user input). Tenant id is PARAMETERISED.
    execute_statement(conn, f"SET LOCAL ROLE {role}")
    who = execute_statement(conn, "SELECT current_user").fetchone()[0]
    if who != role:
        raise SessionContextError(f"refusing to run as {who!r}; expected {role!r} (RLS would be bypassed)")
    execute_statement(conn, "SELECT set_config('app.tenant_id', %s, true)", (str(parsed_tenant_id),))


@contextlib.contextmanager
def tenant_session(dsn: str | None = None, *, role: str, tenant_id, repeatable_read: bool = False):
    """Connect, open a transaction, apply the role + tenant context, yield the conn.

    Commits on clean exit, rolls back on error. The connection is non-autocommit so
    `SET LOCAL` persists for the whole unit of work.
    """
    url = dsn or os.environ.get("DATABASE_URL") or "postgresql://coffeeshop:coffeeshop@localhost:5432/coffeeshop"
    # Named statements cannot follow a logical connection across transaction-pool backends.
    with psycopg.connect(url, prepare_threshold=None, connect_timeout=5) as conn, conn.transaction():
        if repeatable_read:
            execute_statement(conn, "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        open_session_context(conn, role=role, tenant_id=tenant_id)
        yield conn
