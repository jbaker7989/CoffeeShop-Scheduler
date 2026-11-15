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

import psycopg

_ROLES = frozenset({"app_ro", "app_rw", "secrets_rw"})


class SessionContextError(RuntimeError):
    pass


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
    if not tenant_id:
        raise SessionContextError("tenant_id is required")
    # Role is a validated literal (never user input). Tenant id is PARAMETERISED.
    conn.execute(f"SET LOCAL ROLE {role}")
    who = conn.execute("SELECT current_user").fetchone()[0]
    if who != role:
        raise SessionContextError(f"refusing to run as {who!r}; expected {role!r} (RLS would be bypassed)")
    conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))


@contextlib.contextmanager
def tenant_session(dsn: str | None = None, *, role: str, tenant_id):
    """Connect, open a transaction, apply the role + tenant context, yield the conn.

    Commits on clean exit, rolls back on error. The connection is non-autocommit so
    `SET LOCAL` persists for the whole unit of work.
    """
    url = dsn or os.environ.get("DATABASE_URL") or "postgresql://coffeeshop:coffeeshop@localhost:5432/coffeeshop"
    with psycopg.connect(url) as conn:  # psycopg3 default: autocommit=False
        with conn.transaction():
            open_session_context(conn, role=role, tenant_id=tenant_id)
            yield conn
