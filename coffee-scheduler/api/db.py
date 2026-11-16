"""Database access for the gateway.

Tenant lookup intentionally runs as the DSN's owner role.  Tenant-scoped reads
switch to ``app_rw`` inside a transaction through the shared RLS session helper.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from psycopg_pool import ConnectionPool

from data_access.session import open_session_context

from .config import GATEWAY_DATABASE_URL


# PgBouncer is in transaction-pooling mode: psycopg auto-prepared statements
# would otherwise be tied to a different backend connection on a later request.
pool = ConnectionPool(
    conninfo=GATEWAY_DATABASE_URL,
    kwargs={"prepare_threshold": None},
    min_size=1,
    open=False,
)

_STORE_COLUMNS = (
    "location_id",
    "store_location",
    "max_staff_on_floor",
    "stations",
    "profile_type",
    "basis",
)


@contextmanager
def _pooled_connection() -> Iterator:
    """Open the module pool on first use and return one connection."""
    if pool.closed:
        pool.open()
    with pool.connection() as conn:
        yield conn


def resolve_tenant_id(slug: str) -> str | None:
    """Resolve a public tenant slug using the owner connection for login only."""
    with _pooled_connection() as conn:
        # Do not SET LOCAL ROLE here: app_rw is deliberately unable to read tenants.
        with conn.transaction():
            row = conn.execute("SELECT id FROM tenants WHERE slug=%s", (slug,)).fetchone()
    return str(row[0]) if row else None


def fetch_stores(tenant_id: str) -> list[dict]:
    """Return stores visible to the signed-in tenant under RLS."""
    with _pooled_connection() as conn:
        with conn.transaction():
            open_session_context(conn, role="app_rw", tenant_id=tenant_id)
            rows = conn.execute(
                "SELECT location_id, store_location, max_staff_on_floor, stations, profile_type, basis "
                "FROM stores"
            ).fetchall()
    return [dict(zip(_STORE_COLUMNS, row, strict=True)) for row in rows]
