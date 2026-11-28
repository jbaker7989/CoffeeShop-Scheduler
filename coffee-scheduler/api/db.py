"""Database access for the gateway.

Tenant lookup intentionally runs as the DSN's owner role.  Tenant-scoped reads
switch to the appropriate app role inside a transaction through the shared RLS session helper.
"""

from __future__ import annotations

from datetime import date
from threading import Lock
from uuid import UUID

from psycopg.rows import dict_row
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

    def fetch_schedule(self, tenant_id: str, *, location_id: int, week_start: date | None) -> dict:
        """Read the live published roster and station definitions for one location."""
        with self._pool_for_request().connection() as conn, conn.transaction():
            open_session_context(conn, role="app_ro", tenant_id=tenant_id)
            with conn.cursor(row_factory=dict_row) as cursor:
                rows = cursor.execute(
                    """
                    WITH week AS (
                        SELECT id AS tenant_id, timezone,
                               COALESCE(%s::date,
                                   date_trunc('week', now() AT TIME ZONE timezone)::date) AS week_start
                        FROM tenants
                    )
                    SELECT week.timezone, week.week_start, schedule.schedule_id, schedule.status,
                           shift.shift_id, shift.start_at, shift.end_at, shift.role, shift.station_id,
                           assignment.employee_id, assignment.is_meal_break_covered,
                           NULLIF(concat_ws(' ', employee.first_name, employee.last_name), '') AS employee_name
                    FROM week
                    LEFT JOIN stores AS store
                      ON store.tenant_id = week.tenant_id AND store.location_id = %s
                    LEFT JOIN LATERAL (
                        SELECT schedule_id, status
                        FROM schedules
                        WHERE tenant_id = store.tenant_id AND location_id = store.location_id
                          AND week_start = week.week_start AND status = 'published'
                        ORDER BY created_at DESC, schedule_id DESC LIMIT 1
                    ) AS schedule ON true
                    LEFT JOIN shifts AS shift
                      ON shift.tenant_id = store.tenant_id AND shift.schedule_id = schedule.schedule_id
                     AND shift.location_id = store.location_id
                    LEFT JOIN shift_assignments AS assignment
                      ON assignment.tenant_id = shift.tenant_id AND assignment.shift_id = shift.shift_id
                    LEFT JOIN employees AS employee
                      ON employee.tenant_id = assignment.tenant_id AND employee.employee_id = assignment.employee_id
                    ORDER BY shift.start_at, shift.shift_id, assignment.employee_id
                    """,
                    (week_start, location_id),
                ).fetchall()
                stations = cursor.execute(
                    """
                    SELECT station.station_id, station.station_type, station.staffed_by_role
                    FROM stations AS station JOIN stores AS store USING (tenant_id, location_id)
                    WHERE store.location_id = %s ORDER BY station.station_id
                    """,
                    (location_id,),
                ).fetchall()

        metadata = ("timezone", "week_start", "schedule_id", "status")
        roster = {key: rows[0][key] if rows else None for key in metadata}
        roster["shifts"] = [
            {key: value for key, value in row.items() if key not in ("timezone", "week_start", "schedule_id")}
            for row in rows
            if row["employee_id"] is not None and row["start_at"] is not None and row["end_at"] is not None
        ]
        roster["stations"] = stations
        return roster

    def fetch_shift_rule_evals(self, tenant_id: str, shift_id: UUID) -> list[dict]:
        """Read evidence for a shift in a visible published schedule."""
        with self._pool_for_request().connection() as conn, conn.transaction():
            open_session_context(conn, role="app_ro", tenant_id=tenant_id)
            with conn.cursor(row_factory=dict_row) as cursor:
                return cursor.execute(
                    """
                    SELECT log.rule_id, log.result, log.premium_cost_usd, log.notes AS reason, log.agent
                    FROM rule_eval_log AS log
                    JOIN shifts AS shift USING (tenant_id, shift_id)
                    JOIN schedules AS schedule
                      ON schedule.tenant_id = shift.tenant_id AND schedule.schedule_id = shift.schedule_id
                    JOIN stores AS store
                      ON store.tenant_id = schedule.tenant_id AND store.location_id = schedule.location_id
                    WHERE log.shift_id = %s AND schedule.status = 'published'
                    ORDER BY log.rule_id, log.evaluated_at, log.eval_id
                    """,
                    (shift_id,),
                ).fetchall()
