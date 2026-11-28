"""Database access for the gateway.

Tenant lookup intentionally runs as the DSN's owner role.  Tenant-scoped reads
switch to ``app_rw`` inside a transaction through the shared RLS session helper.
"""

from __future__ import annotations

from threading import Lock

from psycopg_pool import ConnectionPool

from data_access.session import open_session_context

from .config import GATEWAY_DATABASE_URL

_STORE_COLUMNS = (
    "location_id",
    "store_location",
    "max_staff_on_floor",
    "stations",
    "profile_type",
    "basis",
)


class GatewayDatabase:
    """Own the store and routing connections for one application."""

    def __init__(self) -> None:
        self._pool: ConnectionPool | None = None
        self._lock = Lock()

    def _pool_for_request(self) -> ConnectionPool:
        with self._lock:
            if self._pool is None:
                # PgBouncer can assign a different backend for each transaction.
                pool = ConnectionPool(
                    conninfo=GATEWAY_DATABASE_URL, kwargs={"prepare_threshold": None}, min_size=1, open=False
                )
                pool.open()
                self._pool = pool
            return self._pool

    def close(self) -> None:
        with self._lock:
            if self._pool is not None:
                self._pool.close()
                self._pool = None

    def resolve_active_slug(self, slug: str) -> str | None:
        """Resolve active and trial tenants for public routing."""
        with self._pool_for_request().connection() as conn, conn.transaction():
            row = conn.execute(
                "SELECT id FROM tenants WHERE slug=%s AND status IN ('active', 'trial')",
                (slug,),
            ).fetchone()
        return str(row[0]) if row else None

    def fetch_stores(self, tenant_id: str) -> list[dict]:
        """Return stores visible to the signed-in tenant under RLS."""
        with self._pool_for_request().connection() as conn, conn.transaction():
            open_session_context(conn, role="app_rw", tenant_id=tenant_id)
            rows = conn.execute(
                "SELECT location_id, store_location, max_staff_on_floor, stations, profile_type, basis FROM stores"
            ).fetchall()
        return [dict(zip(_STORE_COLUMNS, row, strict=True)) for row in rows]
