"""Persist a store's decoded weekly schedule and its compliance evidence atomically."""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from compliance import engine
from data_access.session import open_session_context
from data_access.soft_delete import soft_delete

from .runner import ScheduleOutput


def _utc_at(day: date, hour: Decimal, zone: ZoneInfo) -> datetime:
    local = datetime.combine(day, time()) + timedelta(microseconds=int(hour * 3_600_000_000))
    # In a fall-back fold, use the first occurrence. Reject a wall time skipped by a spring-forward.
    instant = local.replace(tzinfo=zone, fold=0).astimezone(UTC)
    if instant.astimezone(zone).replace(tzinfo=None) != local:
        raise ValueError(f"{local} does not exist in {zone.key}")
    return instant


def persist_schedule(
    conn, output: ScheduleOutput, *, tenant_id: str | UUID, location_id: int,
    week_start: date, status: str = "published", solver_version: str,
) -> UUID:
    """Replace the live schedule for this store/week inside a scoped write transaction."""
    tenant_id = str(UUID(str(tenant_id)))
    with conn.transaction():
        prior_role, prior_tenant = conn.execute(
            "SELECT current_setting('role'), current_setting('app.tenant_id', true)"
        ).fetchone()
        open_session_context(conn, role="app_rw", tenant_id=tenant_id)
        # A stable lock also serializes the first insert when there is no prior row to lock.
        key = f"schedule:{tenant_id}:{location_id}:{week_start.isoformat()}"
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (key,))
        row = conn.execute("SELECT timezone FROM tenants").fetchone()
        if row is None:
            raise ValueError("tenant timezone is unavailable")
        zone = ZoneInfo(row[0])
        if conn.execute("SELECT 1 FROM stores WHERE location_id = %s", (location_id,)).fetchone() is None:
            raise ValueError("schedule location is not visible to the tenant")

        week_end = week_start + timedelta(days=7)
        spans = defaultdict(list)
        shifts = []
        for shift in output.shifts:
            if not week_start <= shift.day < week_end:
                continue
            start = _utc_at(shift.day, shift.start_hour, zone)
            end = _utc_at(shift.day, shift.end_hour, zone)
            if end <= start:
                raise ValueError("shift end must be after shift start")
            spans[shift.employee_id].append(engine.ShiftSpan(start, end))
            shifts.append((shift, start, end))

        prior_ids = [
            row[0] for row in conn.execute(
                "UPDATE schedules SET status = 'superseded' WHERE location_id = %s AND week_start = %s "
                "AND deleted_at IS NULL RETURNING schedule_id", (location_id, week_start),
            )
        ]
        if prior_ids:
            soft_delete(conn, "schedules", tenant_id=tenant_id, where=("schedule_id = ANY(%s)", (prior_ids,)),
                        actor_id=None)
        schedule_id = conn.execute(
            "INSERT INTO schedules (tenant_id, location_id, week_start, status, solver_version) "
            "VALUES (%s,%s,%s,%s,%s) RETURNING schedule_id",
            (tenant_id, location_id, week_start, status, solver_version),
        ).fetchone()[0]

        certificates = dict(conn.execute("SELECT employee_id, food_protection_cert FROM employees"))
        snapshots = {}
        for shift, start, end in shifts:
            if shift.location_id != location_id:
                continue
            shift_id = conn.execute(
                "INSERT INTO shifts (tenant_id, schedule_id, location_id, station_id, start_at, end_at, role) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING shift_id",
                (tenant_id, schedule_id, location_id, shift.station_id, start, end, shift.role),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO shift_assignments (tenant_id, shift_id, employee_id, is_meal_break_covered) "
                "VALUES (%s,%s,%s,%s)", (tenant_id, shift_id, shift.employee_id, shift.is_meal_break_covered),
            )
            # Crew members must cover the full proposal interval. The decoded output carries no break times.
            crew = {
                peer.employee_id for peer, peer_start, peer_end in shifts
                if peer.location_id == location_id and peer_start <= start and end <= peer_end
            }
            proposal = engine.Proposal(
                tenant_id=tenant_id, location_id=location_id, employee_id=shift.employee_id, role=shift.role,
                start_at=start, end_at=end, breaks=[], week_start=week_start,
                week_shifts=spans[shift.employee_id], week_complete=True,
                crew=[engine.CrewMember(employee_id, bool(certificates[employee_id])) for employee_id in sorted(crew)],
                crew_complete=True, shift_id=str(shift_id), schedule_id=str(schedule_id),
            )
            if shift.employee_id not in snapshots:
                snapshots[shift.employee_id] = engine.load_scoped_snapshot(conn, proposal)
            engine.evaluate(proposal, snapshot=snapshots[shift.employee_id], persist=True, log=None, conn=conn)
        # Releasing a savepoint preserves SET LOCAL values until the outer transaction ends.
        conn.execute(
            "SELECT set_config('role', %s, true), set_config('app.tenant_id', %s, true)",
            (prior_role, prior_tenant or ""),
        )
        return schedule_id
