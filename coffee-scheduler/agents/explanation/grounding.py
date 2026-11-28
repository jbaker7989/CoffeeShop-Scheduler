"""Freeze the rule evidence visible for one shift under tenant RLS."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from compliance import engine
from contracts.forecast import VersionMismatchError, load_vocabulary
from data_access.session import open_session_context


class ShiftNotFound(LookupError):
    """The requested shift is absent from the tenant-visible roster."""


@dataclass(frozen=True)
class RuleFact:
    rule_id: str
    result: str
    reason: str | None
    premium_cost_usd: Decimal | None


@dataclass(frozen=True)
class ShiftEvidence:
    facts: frozenset[RuleFact]
    categories: frozenset[str]


def gather_evidence(conn, tenant_id: str | UUID, shift_id: str | UUID) -> ShiftEvidence:
    """Read stored evaluations or obtain live results without persisting them."""
    tenant = str(UUID(str(tenant_id)))
    shift = UUID(str(shift_id))
    # Roll back even on success so a caller's enclosing transaction keeps its scope.
    with conn.transaction(force_rollback=True):
        open_session_context(conn, role="app_ro", tenant_id=tenant)
        row = conn.execute(
            """
            SELECT shift.schedule_id, shift.location_id, shift.role, shift.start_at, shift.end_at,
                   schedule.week_start
            FROM shifts AS shift
            LEFT JOIN schedules AS schedule USING (tenant_id, schedule_id)
            WHERE shift.tenant_id = %s AND shift.shift_id = %s
              AND (shift.schedule_id IS NULL OR schedule.status IN ('draft', 'published'))
            """,
            (tenant, shift),
        ).fetchone()
        if row is None:
            raise ShiftNotFound

        logged = conn.execute(
            """
            SELECT rule_id, result, notes, premium_cost_usd
            FROM rule_eval_log
            WHERE tenant_id = %s AND shift_id = %s
            """,
            (tenant, shift),
        ).fetchall()
        facts = (
            frozenset(RuleFact(*fact) for fact in logged if fact[0] and fact[1])
            if logged else _judge_shift(conn, tenant, shift, row)
        )
        try:
            categories = load_vocabulary(conn)
        except VersionMismatchError:
            # A tenant may have a roster before importing the pinned taxonomy.
            categories = frozenset()
        return ShiftEvidence(facts, categories)


def _judge_shift(conn, tenant: str, shift: UUID, row: tuple) -> frozenset[RuleFact]:
    schedule, location, role, start, end, week_start = row
    if None in row or end <= start:
        return frozenset()
    assignments = conn.execute(
        """
        SELECT assignment.employee_id, employee.hourly_rate IS NOT NULL
        FROM shift_assignments AS assignment
        LEFT JOIN employees AS employee USING (tenant_id, employee_id)
        WHERE assignment.tenant_id = %s AND assignment.shift_id = %s
        ORDER BY assignment.employee_id
        """,
        (tenant, shift),
    ).fetchall()
    if not assignments or not all(has_rate for _, has_rate in assignments):
        return frozenset()

    roster = conn.execute(
        """
        SELECT assignment.employee_id, shift.shift_id, shift.location_id, shift.start_at, shift.end_at,
               employee.food_protection_cert
        FROM shifts AS shift
        JOIN shift_assignments AS assignment USING (tenant_id, shift_id)
        LEFT JOIN employees AS employee USING (tenant_id, employee_id)
        WHERE shift.tenant_id = %s AND shift.schedule_id = %s
        ORDER BY shift.start_at, shift.shift_id, assignment.employee_id
        """,
        (tenant, schedule),
    ).fetchall()
    if any(peer_start is None or peer_end is None or peer_end <= peer_start
           for _, _, _, peer_start, peer_end, _ in roster):
        return frozenset()

    # Match the persisted proposal's full-interval crew semantics.
    crew = {
        engine.CrewMember(employee_id, bool(certified))
        for employee_id, _, peer_location, peer_start, peer_end, certified in roster
        if peer_location == location and peer_start <= start and end <= peer_end
    }
    facts = set()
    for employee_id, _ in assignments:
        proposal = engine.Proposal(
            tenant_id=tenant, location_id=location, employee_id=employee_id, role=role,
            start_at=start, end_at=end, breaks=[], week_start=week_start,
            week_shifts=[
                engine.ShiftSpan(peer_start, peer_end, str(peer_id))
                for worker, peer_id, _, peer_start, peer_end, _ in roster if worker == employee_id
            ],
            week_complete=True, crew=sorted(crew, key=lambda member: member.employee_id), crew_complete=True,
            shift_id=str(shift), schedule_id=str(schedule),
        )
        try:
            snapshot = engine.load_scoped_snapshot(conn, proposal)
            evaluation = engine.judge(snapshot, proposal)
        except engine.ComplianceError:
            # An incomplete or unsupported snapshot cannot yield a trustworthy live verdict.
            return frozenset()
        facts.update(
            RuleFact(result.rule_id, result.result, result.reason, result.premium_cost_usd)
            for result in evaluation.results
        )
    return frozenset(facts)
